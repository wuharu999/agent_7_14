from __future__ import annotations

import smtplib
import threading
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect

from ecs.app import database, email_store as store, email_notifications as mail


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DATABASE_PATH', tmp_path / 'mail.db')
    monkeypatch.setenv('EMAIL_ENABLED', 'true')
    database.initialize_database()
    store.initialize()
    with database._connect() as db:
        db.execute("UPDATE users SET email='admin@example.com' WHERE role='admin'")


def settings(**kwargs):
    return replace(mail.Settings(enabled=True, username='sender@qq.com', password='private-auth-code',
                                 sender='sender@qq.com', weekly=False), **kwargs)


def rows():
    with database._connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM email_outbox ORDER BY id')]


def test_disabled_mail_does_not_queue_or_expose_password(monkeypatch):
    monkeypatch.setenv('EMAIL_ENABLED', 'false')
    mail.notify_contradiction('walker', 'conflict')
    assert rows() == []
    assert 'private-auth-code' not in repr(settings())


def test_enabled_configuration_requires_tls_and_valid_sender():
    settings().validate()
    for config in (settings(security='none'), settings(password=''), settings(sender='invalid'),
                   settings(sender_name='robot\nBcc: injected@example.com')):
        with pytest.raises(ValueError):
            config.validate()


def test_admin_recipients_only_and_persistent_deduplication():
    admin = store.admins()[0]
    for username, role, active in [('editor', 'editor', 1), ('inactive', 'admin', 0)]:
        uid = database.create_user_record(username=username, email=username+'@example.com',
            password_hash='hash', password_salt='salt', role=role, teams='')
        with database._connect() as db:
            db.execute('UPDATE users SET is_active=? WHERE id=?', (active, uid))
    mail.notify_contradiction('walker', '48 Nm vs 36 Nm')
    store.initialize()  # Application restart must not reset deduplication.
    mail.notify_contradiction('walker', '48 Nm vs 36 Nm')
    assert len(rows()) == 1
    assert rows()[0]['recipient'] == admin['email']
    assert '48 Nm' in rows()[0]['body']


def test_upload_burst_threshold_and_hourly_suppression(monkeypatch):
    monkeypatch.setattr(database, 'get_recent_upload_count', lambda *a, **k: 5)
    mail.notify_upload_activity(store.admins()[0]['id'], '小王', 'walker')
    assert not rows()
    monkeypatch.setattr(database, 'get_recent_upload_count', lambda *a, **k: 6)
    for _ in range(3):
        mail.notify_upload_activity(store.admins()[0]['id'], '小王', 'walker')
    assert len(rows()) == 1
    assert '小王' in rows()[0]['body'] and '6 次' in rows()[0]['body']


def test_incomplete_scan_is_not_mislabeled_as_attack(monkeypatch):
    monkeypatch.setattr(database, 'get_upload', lambda _: {'team': 'walker'})
    mail.notify_security_warnings('upload', [{'source_identity': 'file.md', 'categories': ['scan_incomplete_size']}])
    assert not rows()
    warning = {'source_identity': 'file.md', 'categories': ['instruction_override', 'scan_incomplete_size']}
    mail.notify_security_warnings('upload', [warning])
    mail.notify_security_warnings('upload', [warning])
    assert len(rows()) == 1
    assert '疑似指令覆盖' in rows()[0]['body']


def test_weekly_schedule_beijing_boundary_and_restart():
    before = datetime(2026, 10, 5, 0, 59, tzinfo=timezone.utc)  # Monday 08:59
    due = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)  # Monday 09:00
    start, end = mail.weekly_window(due)
    assert start.isoformat() == '2026-09-27T16:00:00+00:00'
    assert end.isoformat() == '2026-10-04T16:00:00+00:00'
    assert mail.weekly_window(before)[1] == start
    mail.queue_weekly(due, settings(weekly=True))
    store.initialize()
    mail.queue_weekly(due, settings(weekly=True))
    assert len(rows()) == 1
    assert '2026-09-28 至 2026-10-04' in rows()[0]['subject']
    assert '不等同于独立用户数' in rows()[0]['body']


def test_weekly_stats_use_half_open_window_and_do_not_include_raw_questions():
    start = datetime(2026, 9, 27, 16, tzinfo=timezone.utc)
    end = datetime(2026, 10, 4, 16, tzinfo=timezone.utc)
    with database._connect() as db:
        for i, when in enumerate([start.isoformat(), '2026-10-01T00:00:00+00:00', end.isoformat()]):
            db.execute("INSERT INTO qa_question_records(ip_address,conversation_id,team,topic_label,language,question,asked_at) VALUES(?,?,?,?,?,?,?)",
                       ('192.0.2.1', f'c{i}', 'walker', 'Walker', 'zh-CN', 'private user text', when))
        db.execute("INSERT INTO file_audit_log(user_id,username,action,result,created_at) VALUES(?,?,?,?,?)",
                   (store.admins()[0]['id'], '管理员', 'upload_source', 'accepted', start.isoformat()))
    stats = store.weekly_activity(start, end)
    assert stats['questions'] == 2 and stats['conversations'] == 2 and stats['client_ips'] == 1
    assert stats['active_users'] == 1
    subject, body = mail.weekly_message(start, end, stats)
    assert '管理员' in body and 'private user text' not in body and '192.0.2.1' not in body


def test_queue_failure_does_not_break_worker_event(monkeypatch):
    monkeypatch.setattr(store, 'admins', lambda: (_ for _ in ()).throw(RuntimeError('private data')))
    mail.notify_contradiction('walker', 'conflict')  # Safe boundary; no exception reaches websocket.


def test_claim_lease_retry_and_stale_claim_cannot_finish():
    store.enqueue('event', 'contradiction', store.admins()[0], '主题', '正文', 100)
    first = store.claim(100)
    assert store.claim(101) is None
    second = store.claim(701)
    assert second['attempts'] == 2 and second['lease_token'] != first['lease_token']
    store.finish(first, 702)
    assert rows()[0]['status'] == 'sending'
    store.finish(second, 702, error='SMTPServerDisconnected')
    assert store.claim(703) is None
    for attempt in range(3, 6):
        now = rows()[0]['available_at']
        item = store.claim(now)
        assert item['attempts'] == attempt
        store.finish(item, now, error='SMTPServerDisconnected')
    assert rows()[0]['status'] == 'failed'
    assert store.claim(99999) is None


def test_deactivated_recipient_is_skipped_before_network(monkeypatch):
    mail.notify_contradiction('walker', 'conflict')
    with database._connect() as db:
        db.execute("UPDATE users SET is_active=0")
    monkeypatch.setattr(mail, 'deliver', lambda *a: pytest.fail('Should not send'))
    mail.run_tick(settings(), threading.Event())
    assert rows()[0]['status'] == 'skipped'


def test_failed_smtp_retries_without_logging_credentials(monkeypatch, caplog):
    mail.notify_contradiction('walker', 'conflict')
    def fail(*args):
        raise smtplib.SMTPServerDisconnected('private-auth-code')
    monkeypatch.setattr(mail, 'deliver', fail)
    mail.run_tick(settings(), threading.Event())
    assert rows()[0]['status'] == 'pending' and rows()[0]['attempts'] == 1
    assert 'private-auth-code' not in caplog.text
    with database._connect() as db:
        db.execute('UPDATE email_outbox SET available_at=0')
    monkeypatch.setattr(mail, 'deliver', lambda *a: None)
    mail.run_tick(settings(), threading.Event())
    assert rows()[0]['status'] == 'sent'


@pytest.mark.parametrize('security', ['ssl', 'starttls'])
def test_unicode_mime_html_escape_tls_and_single_recipient(monkeypatch, security):
    calls = []
    class SMTP:
        def __init__(self, *args, **kwargs):
            calls.append(('connect', args, kwargs))
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ehlo(self): calls.append(('ehlo',))
        def starttls(self, **kwargs): calls.append(('tls', kwargs))
        def login(self, *args): calls.append(('login',))
        def send_message(self, message, **kwargs): calls.append(('send', message, kwargs))
    monkeypatch.setattr(mail.smtplib, 'SMTP_SSL', SMTP)
    monkeypatch.setattr(mail.smtplib, 'SMTP', SMTP)
    store.enqueue('fixture', 'contradiction', store.admins()[0], '【提醒】机器人', '<script>不执行</script>', 100)
    mail.deliver(rows()[0], settings(security=security))
    _, message, envelope = calls[-1]
    assert message['Subject'] == '【提醒】机器人'
    assert '不执行' in message.get_body(preferencelist=('plain',)).get_content()
    assert '<script>' not in message.get_body(preferencelist=('html',)).get_content()
    assert '&lt;script&gt;' in message.get_body(preferencelist=('html',)).get_content()
    assert envelope['to_addrs'] == ['admin@example.com']
    if security == 'starttls':
        assert next(i for i, c in enumerate(calls) if c[0] == 'tls') < next(i for i, c in enumerate(calls) if c[0] == 'login')
    else:
        assert calls[0][2]['context'].check_hostname


@pytest.mark.anyio
async def test_worker_contradiction_keeps_processing_messages(monkeypatch):
    from ecs.app.routes import worker_socket as route
    class WS:
        headers = {'x-worker-secret': 'fixture-secret'}
        accepted = False
        events = iter([{'type': 'contradiction_alert', 'team': 'walker', 'details': 'conflict'},
                       {'type': 'answer', 'id': 'next', 'text': 'still online'}])
        async def accept(self): self.accepted = True
        async def receive_json(self):
            try: return next(self.events)
            except StopIteration: raise WebSocketDisconnect()
    gateway = type('Gateway', (), {'online': False, 'websocket': None, 'attach': AsyncMock(),
        'detach': AsyncMock(), 'resolve_answer': lambda self, qid, text: setattr(self, 'last', text)})()
    monkeypatch.setattr(route, 'gateway', gateway)
    monkeypatch.setattr(route, 'WORKER_SHARED_SECRET', 'fixture-secret')
    monkeypatch.setattr(route, '_dispatch_waiting_uploads', AsyncMock())
    ws = WS()
    await route.worker_socket(ws)
    assert ws.accepted and gateway.last == 'still online'
    assert len(rows()) == 1 and database.get_recent_wiki_contradictions()[0]['details'] == 'conflict'


@pytest.mark.parametrize(('code', 'expected'), [(450, 'pending'), (550, 'failed')])
def test_recipient_rejection_distinguishes_temporary_and_permanent(monkeypatch, code, expected):
    mail.notify_contradiction('walker', 'conflict')
    def fail(*args):
        raise smtplib.SMTPRecipientsRefused({'admin@example.com': (code, b'rejected')})
    monkeypatch.setattr(mail, 'deliver', fail)
    mail.run_tick(settings(), threading.Event())
    assert rows()[0]['status'] == expected


@pytest.mark.anyio
async def test_service_runs_smtp_cycle_off_event_loop_and_stops(monkeypatch):
    stop = threading.Event()
    main_thread = threading.get_ident()
    seen = []
    def tick(_settings, _stop):
        seen.append(threading.get_ident())
        _stop.set()
    monkeypatch.setattr(mail, 'run_tick', tick)
    await mail.run_service(settings(), stop)
    assert seen and seen[0] != main_thread
