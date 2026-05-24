import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import web_app


SAMPLE_JOB = """Junior Software Developer
HarbourTech Solutions
Sydney NSW / Hybrid
https://example.com/jobs/junior-software-developer

Responsibilities:
- Build frontend features using React and TypeScript
- Develop backend API endpoints using Python and REST APIs
- Write SQL queries and support production issues
"""


class FakeResponse:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class FakeSupabaseClient:
    def __init__(self):
        self.tables = {
            'generations': [],
            'job_leads': [],
        }

    def table(self, name):
        return FakeQuery(self, name)


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.operation = 'select'
        self.payload = None
        self.filters = []
        self.orders = []
        self.limit_value = None
        self.count_mode = None

    def select(self, _columns='*', count=None):
        self.operation = 'select'
        self.count_mode = count
        return self

    def insert(self, payload):
        self.operation = 'insert'
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.operation = 'upsert'
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def update(self, payload):
        self.operation = 'update'
        self.payload = payload
        return self

    def eq(self, column, value):
        self.filters.append(('eq', column, value))
        return self

    def gte(self, column, value):
        self.filters.append(('gte', column, value))
        return self

    def lt(self, column, value):
        self.filters.append(('lt', column, value))
        return self

    def order(self, column, desc=False):
        self.orders.append((column, desc))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        rows = self.client.tables.setdefault(self.table_name, [])
        if self.operation == 'insert':
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            inserted = []
            for payload in payloads:
                row = dict(payload)
                row.setdefault('id', str(uuid4()))
                row.setdefault('created_at', datetime.now(timezone.utc).isoformat())
                rows.append(row)
                inserted.append(row)
            return FakeResponse(inserted, count=len(inserted))

        if self.operation == 'upsert':
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            saved = []
            for payload in payloads:
                row = dict(payload)
                row.setdefault('id', str(uuid4()))
                row.setdefault('created_at', datetime.now(timezone.utc).isoformat())
                row.setdefault('updated_at', row['created_at'])
                existing = next(
                    (
                        item for item in rows
                        if item.get('user_id') == row.get('user_id')
                        and item.get('job_hash') == row.get('job_hash')
                    ),
                    None,
                )
                if existing:
                    existing.update(row)
                    saved.append(existing)
                else:
                    rows.append(row)
                    saved.append(row)
            return FakeResponse(saved, count=len(saved))

        matched = self._filtered_rows(rows)
        if self.operation == 'update':
            for row in matched:
                row.update(self.payload)
            return FakeResponse(matched, count=len(matched))

        for column, desc in reversed(self.orders):
            matched.sort(key=lambda row: row.get(column) or '', reverse=desc)
        count = len(matched) if self.count_mode == 'exact' else None
        if self.limit_value is not None:
            matched = matched[: self.limit_value]
        return FakeResponse([dict(row) for row in matched], count=count)

    def _filtered_rows(self, rows):
        matched = list(rows)
        for op, column, value in self.filters:
            if op == 'eq':
                matched = [row for row in matched if row.get(column) == value]
            elif op == 'gte':
                matched = [row for row in matched if str(row.get(column) or '') >= str(value)]
            elif op == 'lt':
                matched = [row for row in matched if str(row.get(column) or '') < str(value)]
        return matched


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.fake_db = FakeSupabaseClient()
        self.original_service_client = web_app.SUPABASE_SERVICE_CLIENT
        self.original_auth_client = web_app.SUPABASE_AUTH_CLIENT
        self.original_output_dir = web_app.OUTPUT_DIR
        self.output_tmp = tempfile.TemporaryDirectory()
        web_app.OUTPUT_DIR = self.output_tmp.name
        web_app.SUPABASE_SERVICE_CLIENT = self.fake_db
        web_app.SUPABASE_AUTH_CLIENT = object()
        web_app.app.config.update(TESTING=True, SECRET_KEY='test-secret')
        self.client = web_app.app.test_client()

    def tearDown(self):
        web_app.SUPABASE_SERVICE_CLIENT = self.original_service_client
        web_app.SUPABASE_AUTH_CLIENT = self.original_auth_client
        web_app.OUTPUT_DIR = self.original_output_dir
        self.output_tmp.cleanup()

    def sign_in(self, user_id='user-123', email='tester@example.com'):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['user_email'] = email
            session['sb_access_token'] = 'access-token'
            session['sb_refresh_token'] = 'refresh-token'

    def csrf_token(self, path='/jobs'):
        self.client.get(path)
        with self.client.session_transaction() as session:
            return session['csrf_token']

    def post_with_csrf(self, path, data=None, token_path=None, **kwargs):
        payload = dict(data or {})
        payload['csrf_token'] = self.csrf_token(token_path or path)
        return self.client.post(path, data=payload, **kwargs)

    def test_jobs_requires_login(self):
        response = self.client.get('/jobs')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_jobs_post_saves_shortlist_and_status_persists(self):
        self.sign_in()

        response = self.post_with_csrf('/jobs', data={'job_posts': SAMPLE_JOB})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Saved 1 ranked job lead', response.data)
        self.assertIn(b'Saved shortlist', response.data)
        self.assertEqual(len(self.fake_db.tables['job_leads']), 1)

        lead = self.fake_db.tables['job_leads'][0]
        self.assertEqual(lead['user_id'], 'user-123')
        self.assertEqual(lead['status'], 'shortlisted')
        self.assertIn('Junior', lead['title'])

        lead_id = lead['id']
        response = self.post_with_csrf(f'/jobs/{lead_id}/status', data={'status': 'applied'}, token_path=f'/jobs/{lead_id}')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.fake_db.tables['job_leads'][0]['status'], 'applied')

        response = self.client.get('/jobs')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Applied', response.data)
        self.assertIn(b'Open workspace', response.data)

    def test_dashboard_shows_shortlist_snapshot(self):
        self.sign_in()
        self.post_with_csrf('/jobs', data={'job_posts': SAMPLE_JOB})

        response = self.client.get('/dashboard')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Application command centre', response.data)
        self.assertIn(b'Saved job leads', response.data)
        self.assertIn(b'Shortlist snapshot', response.data)
        self.assertIn(b'Junior Software Developer', response.data)

    def test_tokenless_post_is_rejected_across_key_routes(self):
        self.sign_in()
        self.post_with_csrf('/jobs', data={'job_posts': SAMPLE_JOB})
        lead_id = self.fake_db.tables['job_leads'][0]['id']

        cases = [
            ('/login', {'email': 'tester@example.com', 'password': 'password'}),
            ('/signup', {'email': 'new@example.com', 'password': 'password'}),
            ('/', {'job_text': SAMPLE_JOB}),
            (f'/jobs/{lead_id}/status', {'status': 'applied'}),
            ('/', {'job_text': SAMPLE_JOB, 'job_lead_id': lead_id}),
        ]
        for path, data in cases:
            with self.subTest(path=path, data=data):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b'CSRF', response.data)

    def test_output_files_require_current_user_ownership(self):
        self.sign_in()
        output_dir = Path(web_app.OUTPUT_DIR)
        (output_dir / 'owned.html').write_text('<html><body>owned</body></html>', encoding='utf-8')
        (output_dir / 'other.html').write_text('<html><body>other</body></html>', encoding='utf-8')
        self.fake_db.tables['job_leads'].extend([
            {'id': 'lead-owned', 'user_id': 'user-123', 'generated_resume_html': 'owned.html'},
            {'id': 'lead-other', 'user_id': 'user-456', 'generated_resume_html': 'other.html'},
        ])

        owner_response = self.client.get('/outputs/owned.html')
        owner_preview_response = self.client.get('/preview/owned.html')
        cross_user_output_response = self.client.get('/outputs/other.html')
        cross_user_preview_response = self.client.get('/preview/other.html')
        traversal_response = self.client.get('/preview/../owned.html')

        try:
            self.assertEqual(owner_response.status_code, 200)
            self.assertEqual(owner_preview_response.status_code, 200)
            self.assertEqual(cross_user_output_response.status_code, 404)
            self.assertEqual(cross_user_preview_response.status_code, 404)
            self.assertEqual(traversal_response.status_code, 404)
        finally:
            owner_response.close()
            owner_preview_response.close()
            cross_user_output_response.close()
            cross_user_preview_response.close()
            traversal_response.close()

    def test_security_headers_are_set(self):
        response = self.client.get('/login')

        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        csp = response.headers['Content-Security-Policy']
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("'unsafe-inline'", csp)
        self.assertIn("style-src 'self' 'nonce-", csp)
        self.assertIn("script-src 'self' 'nonce-", csp)
        self.assertIn(b'<style nonce="', response.data)
        self.assertIn('camera=()', response.headers['Permissions-Policy'])

    def test_production_secret_fails_closed_for_missing_or_default_secret(self):
        for value in (None, '', 'change-me-in-production'):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    web_app._validated_secret_key(value, True)
        self.assertEqual(web_app._validated_secret_key('strong-production-secret', True), 'strong-production-secret')

    def test_secure_cookie_and_hsts_when_production_secure_enabled(self):
        original_secure = web_app.app.config['SESSION_COOKIE_SECURE']
        try:
            web_app.app.config['SESSION_COOKIE_SECURE'] = True
            response = self.client.get('/login')
        finally:
            web_app.app.config['SESSION_COOKIE_SECURE'] = original_secure

        self.assertEqual(web_app.app.config['SESSION_COOKIE_HTTPONLY'], True)
        self.assertEqual(web_app.app.config['SESSION_COOKIE_SAMESITE'], 'Lax')
        self.assertIn('max-age=31536000', response.headers['Strict-Transport-Security'])

    def test_cross_user_status_update_is_rejected(self):
        self.fake_db.tables['job_leads'].append({
            'id': 'other-lead',
            'user_id': 'user-456',
            'status': 'shortlisted',
        })
        self.sign_in(user_id='user-123')

        response = self.post_with_csrf('/jobs/other-lead/status', data={'status': 'applied'}, token_path='/jobs')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.fake_db.tables['job_leads'][0]['status'], 'shortlisted')

    def test_dashboard_hides_unlimited_fraction_for_unlimited_users(self):
        original_unlimited = web_app.UNLIMITED_USAGE_EMAILS
        web_app.UNLIMITED_USAGE_EMAILS = {'tester@example.com'}
        try:
            self.sign_in()
            response = self.client.get('/dashboard')
        finally:
            web_app.UNLIMITED_USAGE_EMAILS = original_unlimited

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'used', response.data)
        self.assertNotIn(b'/Unlimited', response.data)

    def test_record_generation_counts_current_month(self):
        self.sign_in()
        with self.client:
            self.client.get('/dashboard')
            web_app.record_generation('Junior Software Developer', 'software_engineering', 'success')
            self.assertEqual(web_app.get_current_month_generation_count(), 1)


if __name__ == '__main__':
    unittest.main()
