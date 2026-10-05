import hashlib
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import create_app


class PortalAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / 'portal.sqlite'
        with sqlite3.connect(self.db) as db:
            db.executescript('CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, role TEXT, is_active INTEGER); CREATE TABLE sessions(user_id INTEGER, token_hash TEXT, csrf_token TEXT, expires_at TEXT);')
            for i, role in enumerate(['editor', 'admin', 'viewer'], 1):
                db.execute('INSERT INTO users VALUES (?, ?, ?, 1)', (i, role, role))
                db.execute('INSERT INTO sessions VALUES (?, ?, ?, ?)', (i, hashlib.sha256(role.encode()).hexdigest(), 'csrf-' + role, (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()))
        dist = root / 'dist'
        dist.mkdir()
        (dist / 'index.html').write_text('<h1>Tools</h1>')
        self.environment = patch.dict(os.environ, {'PORTAL_AUTH_DB':str(self.db), 'PORTAL_BASE_URL':'http://portal.test:8000/v1/faq-platform', 'JOB_DIST':str(dist), 'ROBOT_WORKER_TOKEN':'only-workers'})
        self.environment.start()
        self.app = create_app(db_path=str(root/'jobs.sqlite'), upload_dir=str(root/'uploads'), grill_db_path=str(root/'grill.sqlite'), grill_upload_dir=str(root/'grill-uploads'))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.state.store.db.close()
        self.app.state.grill_store.db.close()
        self.environment.stop()
        self.temp.cleanup()

    def test_anonymous_pages_and_all_data_apis_require_login(self):
        for path in ['/', '/log', '/grill', '/grill/s/example']:
            response = self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertTrue(response.headers['location'].startswith('http://portal.test:8000/v1/faq-platform/login?next=/tools/'))
        for path in ['/api/account', '/api/jobs', '/api/grill/sessions', '/api/budget', '/api/jobs/id/events']:
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post('/api/jobs', json={'description':'test', 'language':'en'}).status_code, 401)

    def test_both_roles_can_access_tools_and_mutations_require_csrf(self):
        for role in ['editor', 'admin']:
            self.client.cookies.set('agent1_session', role)
            for path in ['/log', '/grill', '/api/jobs', '/api/grill/sessions']:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['cache-control'], 'no-store')
            account = self.client.get('/api/account').json()
            self.assertEqual(account['role'], role)
            payload = {'description':'test', 'language':'en'}
            self.assertEqual(self.client.post('/api/jobs', json=payload).status_code, 403)
            response = self.client.post('/api/jobs', json=payload, headers={'X-CSRF-Token':'csrf-' + role})
            self.assertEqual(response.status_code, 201)
            job = response.json()
            url = '/api/jobs/' + job['job']['id'] + '/files?name=sample.log'
            headers = {'Authorization':'Bearer ' + job['upload_token']}
            self.assertEqual(self.client.put(url, content=b'test', headers=headers).status_code, 403)
            headers['X-CSRF-Token'] = 'csrf-' + role
            self.assertEqual(self.client.put(url, content=b'test', headers=headers).status_code, 201)

    def test_disabled_expired_logged_out_and_wrong_role_are_rejected(self):
        self.client.cookies.set('agent1_session', 'viewer')
        self.assertEqual(self.client.get('/api/account').status_code, 403)
        self.client.cookies.set('agent1_session', 'editor')
        with sqlite3.connect(self.db) as db: db.execute('UPDATE users SET is_active = 0 WHERE id = 1')
        self.assertEqual(self.client.get('/api/jobs').status_code, 401)
        self.client.cookies.set('agent1_session', 'admin')
        with sqlite3.connect(self.db) as db: db.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00' WHERE user_id = 2")
        self.assertEqual(self.client.get('/api/jobs').status_code, 401)
        with sqlite3.connect(self.db) as db: db.execute('DELETE FROM sessions')
        self.assertEqual(self.client.get('/api/account').status_code, 401)

    def test_worker_tokens_remain_independent_of_browser_login(self):
        payload = {'worker_id':'test-worker', 'capacity':{'cpu_milli':4000,'memory_mb':8192,'disk_mb':32768}}
        self.assertEqual(self.client.post('/api/worker/claim', json=payload).status_code, 401)
        response = self.client.post('/api/worker/claim', json=payload, headers={'Authorization':'Bearer only-workers','X-Worker-ID':'test-worker'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['job'])
        self.assertEqual(self.client.get('/api/jobs', headers={'Authorization':'Bearer only-workers'}).status_code, 401)

    def test_auth_database_failure_fails_closed(self):
        self.client.cookies.set('agent1_session', 'editor')
        self.db.unlink()
        self.assertEqual(self.client.get('/api/jobs').status_code, 503)
