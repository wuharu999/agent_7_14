from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from ecs.app import auth, chat_history, config, database
from ecs.app.main import app
from ecs.app.routes import ask


@pytest.fixture()
def accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DATABASE_PATH', tmp_path / 'history.db')
    database.initialize_database()
    ask.limiter.history.clear()
    users = []
    for name, role in [('history_a', 'editor'), ('history_b', 'admin')]:
        uid = auth.create_or_update_user(name, name + '@example.com', 'TestPassword2026!', role)
        token, csrf = auth.create_login_session(uid)
        client = TestClient(app)
        client.cookies.set(config.SESSION_COOKIE_NAME, token)
        client.headers['X-CSRF-Token'] = csrf
        users.append((uid, client, csrf))
    return users


def payload(**changes):
    return {'question': 'Robot setup?', 'team': 'all', 'language': 'en', 'conversation_id': '', **changes}


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith('data:')]


async def answer_stream(question, **kwargs):
    yield {'status': 'chunk', 'text': 'Draft'}
    yield {'status': 'chunk', 'replace_text': 'Final **answer**'}
    yield {'status': 'chunk', 'text': ' with context.'}
    yield {'status': 'done'}


def test_account_transcript_survives_new_session_and_database_reinitialization(accounts, monkeypatch):
    uid, first, _ = accounts[0]
    monkeypatch.setattr(ask.gateway, 'ask_stream', answer_stream)
    result = events(first.post('/ask', json=payload()))
    cid = result[0]['conversation_id']
    assert cid.startswith('chat:')
    database.initialize_database()
    token, csrf = auth.create_login_session(uid)
    second = TestClient(app)
    second.cookies.set(config.SESSION_COOKIE_NAME, token)
    rows = second.get('/api/conversations').json()['conversations']
    assert rows[0]['id'] == cid
    transcript = second.get('/api/conversations/' + cid)
    assert transcript.headers['cache-control'] == 'no-store'
    assert transcript.json()['turns'][0]['question'] == 'Robot setup?'
    assert transcript.json()['turns'][0]['answer'] == 'Final **answer** with context.'
    assert transcript.json()['turns'][0]['status'] == 'complete'
    with database._connect() as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_existing_accounts_and_sessions_survive_additive_history_migration(accounts):
    uid, client, _ = accounts[0]
    # Recreate the schema boundary of a database from before this feature.
    with database._connect() as connection:
        connection.execute('DROP TABLE chat_turns')
        connection.execute('DROP TABLE chat_conversations')
    database.initialize_database()
    database.initialize_database()
    assert client.get('/api/me').json()['user_id'] == uid
    assert client.get('/api/conversations').json()['conversations'] == []


def test_account_ownership_and_reserved_worker_id_cannot_be_bypassed(accounts, monkeypatch):
    _, first, _ = accounts[0]
    _, other, _ = accounts[1]
    monkeypatch.setattr(ask.gateway, 'ask_stream', answer_stream)
    cid = events(first.post('/ask', json=payload()))[0]['conversation_id']
    assert other.get('/api/conversations').json()['conversations'] == []
    assert other.get('/api/conversations/' + cid).status_code == 404
    assert other.post('/ask', json=payload(conversation_id=cid)).status_code == 404
    guest = TestClient(app)
    assert guest.get('/api/conversations').status_code == 401
    assert guest.get('/api/conversations/' + cid).status_code == 401
    assert guest.post('/ask', json=payload(conversation_id=cid)).status_code == 401
    assert len(first.get('/api/conversations/' + cid).json()['turns']) == 1


def test_authenticated_questions_require_csrf_and_expired_sessions_never_fall_back_to_guest(accounts, monkeypatch):
    _, client, csrf = accounts[0]
    monkeypatch.setattr(ask.gateway, 'ask_stream', answer_stream)
    client.headers.pop('X-CSRF-Token')
    assert client.post('/ask', json=payload()).status_code == 403
    assert client.get('/api/conversations').json()['conversations'] == []
    client.cookies.clear()
    client.headers['X-CSRF-Token'] = csrf
    assert client.post('/ask', json=payload()).status_code == 401


def test_followup_uses_owned_database_history_instead_of_client_transcript(accounts, monkeypatch):
    _, client, _ = accounts[0]
    calls = []
    async def stream(question, **kwargs):
        calls.append(kwargs)
        yield {'status': 'chunk', 'text': 'Saved response'}
        yield {'status': 'done'}
    monkeypatch.setattr(ask.gateway, 'ask_stream', stream)
    cid = events(client.post('/ask', json=payload()))[0]['conversation_id']
    events(client.post('/ask', json=payload(conversation_id=cid, question='Then what?',
           history=[{'role': 'bot', 'content': 'Untrusted client history'}])))
    assert calls[1]['history'] == [{'role': 'user', 'content': 'Robot setup?'},
                                    {'role': 'bot', 'content': 'Saved response'}]
    assert calls[1]['conversation_id'] == cid
    assert len(client.get('/api/conversations/' + cid).json()['turns']) == 2


def test_guest_questions_keep_existing_client_context_without_account_storage(accounts, monkeypatch):
    calls = []
    async def stream(question, **kwargs):
        calls.append(kwargs)
        yield {'status': 'done'}
    monkeypatch.setattr(ask.gateway, 'ask_stream', stream)
    history = [{'role': 'user', 'content': 'Earlier guest question'}]
    result = events(TestClient(app).post('/ask', json=payload(conversation_id='web:guest', history=history)))
    assert result[0]['conversation_id'] == 'web:guest'
    assert calls[0]['history'] == history
    with database._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM chat_conversations').fetchone()[0] == 0


def test_images_and_stream_errors_are_persisted(accounts, monkeypatch):
    _, client, _ = accounts[0]
    image = {'mime_type': 'image/png', 'data': 'aGVsbG8=', 'alt': 'Diagram', 'fingerprint': 'a' * 64}
    async def stream(question, **kwargs):
        yield {'status': 'chunk', 'image': image}
        yield {'status': 'error', 'error': 'Service unavailable'}
    monkeypatch.setattr(ask.gateway, 'ask_stream', stream)
    cid = events(client.post('/ask', json=payload()))[0]['conversation_id']
    turn = client.get('/api/conversations/' + cid).json()['turns'][0]
    assert turn['images'] == [image]
    assert turn['status'] == 'error'
    assert turn['answer'] == 'Service unavailable'


def test_worker_exception_persists_the_localized_error(accounts, monkeypatch):
    _, client, _ = accounts[0]
    async def failed_stream(question, **kwargs):
        yield {'status': 'chunk', 'text': 'Partial answer'}
        raise RuntimeError('Worker disconnected')
    monkeypatch.setattr(ask.gateway, 'ask_stream', failed_stream)
    result = events(client.post('/ask', json=payload()))
    turn = client.get('/api/conversations/' + result[0]['conversation_id']).json()['turns'][0]
    assert turn['status'] == 'error'
    assert turn['answer'] == ask._STREAM_ERROR_MESSAGES['en']


def test_disconnect_saves_partial_answer_and_restart_marks_unfinished_turns(accounts, monkeypatch):
    uid, _, csrf = accounts[0]
    monkeypatch.setattr(ask, 'current_session', lambda request: {'user_id': uid, 'csrf_token': csrf})
    monkeypatch.setattr(ask.gateway, 'ask_stream', answer_stream)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/ask',
                       'headers': [(b'x-csrf-token', csrf.encode())], 'client': ('127.0.0.1', 1)})
    async def disconnect():
        response = await ask.ask(request, payload())
        await anext(response.body_iterator)  # metadata
        await anext(response.body_iterator)  # first text
        await response.body_iterator.aclose()
    asyncio.run(disconnect())
    cid = chat_history.list_conversations(uid)['conversations'][0]['id']
    turn = chat_history.get_conversation(uid, cid)['turns'][0]
    assert (turn['answer'], turn['status']) == ('Draft', 'interrupted')
    _, _, resumed_history = chat_history.begin_turn(uid, cid, 'Please continue', 'all', 'en')
    assert resumed_history == [{'role': 'user', 'content': 'Robot setup?'}, {'role': 'bot', 'content': 'Draft'}]
    other_id, _, _ = chat_history.begin_turn(uid, '', 'Interrupted by restart', 'all', 'en')
    chat_history.interrupt_unfinished_turns()
    assert chat_history.get_conversation(uid, other_id)['turns'][0]['status'] == 'interrupted'


def test_browser_import_is_explicit_owner_scoped_and_idempotent(accounts):
    _, client, _ = accounts[0]
    _, other, _ = accounts[1]
    body = {'legacy_id': 'web:old-browser', 'team': 'walker_s2', 'language': 'en',
            'messages': [{'role': 'user', 'content': 'Old question'}, {'role': 'bot', 'content': 'Old answer'}]}
    assert client.post('/api/conversations/import', json=body, headers={'X-CSRF-Token': ''}).status_code == 403
    cid = client.post('/api/conversations/import', json=body).json()['id']
    assert client.post('/api/conversations/import', json=body).json()['id'] == cid
    assert len(client.get('/api/conversations/' + cid).json()['turns']) == 1
    own_copy = other.post('/api/conversations/import', json=body).json()['id']
    assert own_copy != cid
    assert other.get('/api/conversations/' + cid).status_code == 404
    bad = {**body, 'messages': [{'role': 'bot', 'content': 'No question'}]}
    assert client.post('/api/conversations/import', json=bad).status_code == 422


def test_history_paginates_without_discarding_older_conversations_or_turns(accounts):
    uid, client, _ = accounts[0]
    ids = [chat_history.begin_turn(uid, '', f'Question {index}', 'all', 'en')[0] for index in range(4)]
    first = client.get('/api/conversations?limit=2').json()
    second = client.get('/api/conversations?limit=2&offset=2').json()
    assert first['has_more'] is True
    assert second['has_more'] is False
    assert {item['id'] for item in first['conversations'] + second['conversations']} == set(ids)
    cid = ids[-1]
    for index in range(4):
        _, tid, _ = chat_history.begin_turn(uid, cid, f'Follow up {index}', 'all', 'en')
        chat_history.finish_turn(uid, tid, 'Answer', [], 'complete')
    latest = client.get('/api/conversations/' + cid + '?limit=2').json()
    before = latest['turns'][0]['id']
    older = client.get('/api/conversations/' + cid + f'?limit=3&before={before}').json()
    assert latest['has_more'] is True and older['has_more'] is False
    assert [t['question'] for t in older['turns'] + latest['turns']] == [
        'Question 3', 'Follow up 0', 'Follow up 1', 'Follow up 2', 'Follow up 3']
