"""Chinese template notifications, encrypted SMTP, and a weekly gateway digest."""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
import smtplib
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ecs.app import database, email_store
from ecs.app.config import PUBLIC_BASE_URL, ROOT_PATH

log = logging.getLogger(__name__)
BEIJING = ZoneInfo('Asia/Shanghai')


def valid_address(value: str) -> bool:
    # Envelope addresses are intentionally plain ASCII; Chinese display names/body use MIME UTF-8.
    return bool(re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value))


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    host: str = 'smtp.qq.com'
    port: int = 465
    security: str = 'ssl'
    username: str = ''
    password: str = field(default='', repr=False)
    sender: str = ''
    sender_name: str = '机器人知识平台'
    weekly: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            enabled=os.getenv('EMAIL_ENABLED', 'false').lower() in {'true', '1', 'yes'},
            host=os.getenv('EMAIL_SMTP_HOST', 'smtp.qq.com'),
            port=int(os.getenv('EMAIL_SMTP_PORT', '465')),
            security=os.getenv('EMAIL_SMTP_SECURITY', 'ssl'),
            username=os.getenv('EMAIL_SMTP_USERNAME', ''),
            password=os.getenv('EMAIL_SMTP_PASSWORD', ''),
            sender=os.getenv('EMAIL_FROM', ''),
            sender_name=os.getenv('EMAIL_FROM_NAME', '机器人知识平台'),
            weekly=os.getenv('EMAIL_WEEKLY_ENABLED', 'true').lower() in {'true', '1', 'yes'},
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.host or not self.username or not self.password or not valid_address(self.sender):
            raise ValueError('Email requires SMTP host, username, authorization code, and a valid EMAIL_FROM')
        if self.security not in {'ssl', 'starttls'} or not 1 <= self.port <= 65535:
            raise ValueError('Email requires SSL or STARTTLS and a valid SMTP port')
        if '\r' in self.sender_name or '\n' in self.sender_name:
            raise ValueError('Invalid EMAIL_FROM_NAME')


def portal_link(path: str) -> str:
    base = PUBLIC_BASE_URL.rstrip('/')
    if urlsplit(base).scheme not in {'http', 'https'} or not urlsplit(base).netloc:
        return '请登录机器人知识平台查看。'
    if ROOT_PATH and not base.endswith(ROOT_PATH):
        base += ROOT_PATH
    return base + path


def text(value: object, limit: int = 1500) -> str:
    return str(value).replace('\x00', '').replace('\r', '')[:limit]


def queue_admins(key: str, kind: str, subject: str, body: str, *, now: float | None = None,
                 settings: Settings | None = None) -> None:
    if not (settings or Settings.from_env()).enabled:
        return
    for user in email_store.admins():
        if valid_address(user['email']):
            email_store.enqueue(key, kind, user, subject, body, time.time() if now is None else now)


def notify_contradiction(team: str, details: str) -> None:
    """Identical alerts within one UTC day share a persistent event key."""
    try:
        now = time.time()
        digest = hashlib.sha256((team + '\0' + details).encode()).hexdigest()
        queue_admins(f'contradiction:{int(now // 86400)}:{digest}', 'contradiction',
            '【知识冲突提醒】请核对机器人参考资料',
            f'机器人 / 团队：{text(team, 100)}\n\n检测到可能存在的资料冲突，尚需人工核对：\n'
            f'{text(details)}\n\n请检查相关资料并修订。\n{portal_link("/manage")}', now=now)
    except Exception:
        log.error('Could not queue contradiction notification')


def notify_upload_activity(user_id: int, username: str, team: str) -> None:
    try:
        if not Settings.from_env().enabled:
            return
        count = database.get_recent_upload_count(user_id, minutes=1)
        if count <= 5:
            return
        now = time.time()
        queue_admins(f'upload_burst:{user_id}:{int(now // 3600)}', 'upload_burst',
            '【异常活动提醒】短时间内上传较多文件',
            f'用户：{text(username, 100)}\n机器人 / 团队：{text(team, 100)}\n'
            f'最近一分钟已接受 {count} 次上传。此提示不代表已确认恶意行为。\n\n'
            f'请核实是否为正常批量导入；必要时检查用户权限。\n{portal_link("/manage")}', now=now)
    except Exception:
        log.error('Could not queue upload activity notification')


def notify_security_warnings(upload_id: str, warnings: list[dict]) -> None:
    try:
        if not Settings.from_env().enabled:
            return
        suspicious = [w for w in warnings if any(
            not str(c).startswith('scan_incomplete_') for c in w.get('categories', []))]
        if not suspicious:
            return
        upload = database.get_upload(upload_id)
        if upload is None:
            return
        categories = {
            'instruction_override': '疑似指令覆盖', 'prompt_exfiltration': '疑似提示词窃取',
            'secret_exfiltration': '疑似凭据窃取', 'tool_escalation': '疑似工具越权',
            'encoded_execution': '疑似编码执行指令',
        }
        lines = [text(w['source_identity'], 200) + '：' + '、'.join(
            categories[c] for c in w['categories'] if c in categories) for w in suspicious[:20]]
        queue_admins('upload_security:' + upload_id, 'upload_security',
            '【安全提醒】上传内容包含可疑指令',
            f'机器人 / 团队：{text(upload["team"], 100)}\n上传编号：{text(upload_id, 100)}\n'
            f'受影响文件：{len(suspicious)} 个（最多列出 20 个）\n\n' + '\n'.join(lines) +
            f'\n\n这是扫描提示，尚未确认攻击；请在平台核查。邮件不包含原始可疑文本。\n{portal_link("/manage")}')
    except Exception:
        log.error('Could not queue upload security notification')


def weekly_window(now: datetime) -> tuple[datetime, datetime]:
    """Latest report due Monday 09:00 Beijing; window is the preceding full calendar week."""
    local = now.astimezone(BEIJING)
    monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    if local < monday + timedelta(hours=9):
        monday -= timedelta(days=7)
    return (monday - timedelta(days=7)).astimezone(timezone.utc), monday.astimezone(timezone.utc)


def weekly_message(start: datetime, end: datetime, stats: dict) -> tuple[str, str]:
    dates = f'{start.astimezone(BEIJING):%Y-%m-%d} 至 {(end - timedelta(days=1)).astimezone(BEIJING):%Y-%m-%d}'
    body = (f'统计周期：{dates}（北京时间，自周一 00:00 起）\n\n'
        f'问答提问：{stats["questions"]} 次\n会话：{stats["conversations"]} 个\n'
        f'客户端 IP：{stats["client_ips"]} 个（不等同于独立用户数）\n'
        f'有操作记录的登录用户：{stats["active_users"]} 人\n'
        f'上传：{stats["uploads"]} 次，共 {stats["upload_bytes"] / 1024**2:.2f} MiB\n'
        f'知识冲突：{stats["contradictions"]} 条\n'
        f'源文件扫描警告（含扫描未完成）：{stats["warnings"]} 条\n\n用户操作（最多 20 人）：\n')
    body += '\n'.join(f'- {text(row["username"], 100)}：{row["actions"]} 次操作，{row["uploads"]} 次上传'
                      for row in stats['users']) or '本周没有用户操作记录。'
    action_labels = {'login': '登录', 'logout': '退出', 'upload_source': '上传资料',
                     'delete_source': '删除资料', 'create_robot': '创建机器人', 'delete_robot': '删除机器人',
                     'create_user': '创建用户', 'delete_disabled_user': '删除停用用户',
                     'toggle_user_active': '调整账户启用状态', 'update_user': '修改用户',
                     'update_own_settings': '修改账户设置', 'create_chat_robot_option': '创建问答机器人',
                     'delete_chat_robot_option': '删除问答机器人', 'update_chat_robot_option': '修改问答机器人',
                     'update_chat_robot_order': '调整问答机器人顺序', 'update_robot_order': '调整机器人顺序',
                     'update_robot_display_names': '修改机器人名称', 'sync_robots_from_source_tree': '同步机器人目录'}
    body += '\n\n操作类型：\n' + ('\n'.join(
        f'- {action_labels.get(row["action"], text(row["action"], 100))}：{row["count"]}'
        for row in stats['actions']) or '无记录。')
    body += ('\n\n范围：知识平台已记录的问答、上传和账户/管理操作；不包含 Grill 和日志分析服务的独立数据库。'
             '\n这是固定模板统计，不使用模型生成；不附带问题原文或客户端 IP 明细。\n' + portal_link('/manage'))
    return '【每周用户活动报告】' + dates, body


def queue_weekly(now: datetime, settings: Settings) -> None:
    if not settings.enabled or not settings.weekly:
        return
    start, end = weekly_window(now)
    key = f'weekly:{start.date()}:{end.date()}'
    # Only query aggregates once per due week if every current admin is already queued.
    recipients = [u for u in email_store.admins() if valid_address(u['email'])]
    with database._DB_LOCK, database._connect() as db:
        existing = {row[0] for row in db.execute('SELECT recipient FROM email_outbox WHERE event_key=?', (key,))}
    if all(u['email'] in existing for u in recipients):
        return
    subject, body = weekly_message(start, end, email_store.weekly_activity(start, end))
    for user in recipients:
        email_store.enqueue(key, 'weekly', user, subject, body, now.timestamp())


def deliver(item: dict, settings: Settings) -> None:
    message = EmailMessage()
    message['From'] = formataddr((settings.sender_name, settings.sender))
    message['To'] = item['recipient']
    message['Subject'] = item['subject']
    message['Date'] = format_datetime(datetime.fromtimestamp(item['created_at'], timezone.utc))
    identity = hashlib.sha256((item['event_key'] + '\0' + item['recipient']).encode()).hexdigest()
    message['Message-ID'] = f'<{identity}@{settings.sender.split("@")[1]}>'
    message.set_content(item['body'], charset='utf-8')
    message.add_alternative('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="font-family:Arial,Microsoft YaHei,sans-serif;line-height:1.7">'
        f'<h2>{html.escape(item["subject"])}</h2>'
        f'<div style="white-space:pre-wrap">{html.escape(item["body"])}</div></body></html>', subtype='html', charset='utf-8')
    context = ssl.create_default_context()
    if settings.security == 'ssl':
        client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=15, context=context)
    else:
        client = smtplib.SMTP(settings.host, settings.port, timeout=15)
    with client:
        if settings.security == 'starttls':
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(settings.username, settings.password)
        client.send_message(message, from_addr=settings.sender, to_addrs=[item['recipient']])


def run_tick(settings: Settings, stop: threading.Event) -> None:
    if not settings.enabled:
        return
    queue_weekly(datetime.now(timezone.utc), settings)
    for _ in range(5):
        if stop.is_set():
            break
        item = email_store.claim(time.time())
        if item is None:
            break
        if not email_store.recipient_active(item):
            email_store.finish(item, time.time(), skipped=True)
            continue
        try:
            deliver(item, settings)
        except Exception as exc:
            # Never log exception text: SMTP errors can echo recipient data or credentials.
            permanent = False
            if isinstance(exc, smtplib.SMTPRecipientsRefused):
                permanent = bool(exc.recipients) and all(500 <= code < 600 for code, _ in exc.recipients.values())
            if isinstance(exc, smtplib.SMTPResponseException):
                permanent = 500 <= exc.smtp_code < 600 and not isinstance(exc, smtplib.SMTPAuthenticationError)
            email_store.finish(item, time.time(), error=type(exc).__name__, permanent=permanent)
            log.warning('Email delivery attempt failed (outbox_id=%s, category=%s)', item['id'], type(exc).__name__)
        else:
            email_store.finish(item, time.time())


async def run_service(settings: Settings, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_tick, settings, stop)
        except Exception:
            log.error('Email notification cycle failed; retrying next minute')
        # Short waits allow prompt shutdown without cancelling an in-flight SMTP thread.
        for _ in range(60):
            if stop.is_set():
                return
            await asyncio.sleep(1)
