"""End-to-end tests for the Tailor Shop Management System backend.

Run:  python -m unittest discover -s tests -v   (from backend/)
Covers: password hashing, tokens, login flow, protected routes, customer CRUD,
duplicate prevention, measurement history, orders, appointments (legacy),
CORS preflight, CSRF header rule, login rate limiting, and the Google Sheets
store against a fully mocked gspread.
"""

import base64
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth
import store as store_module
from store import (SQLiteStore, DuplicateClient, StoreError,
                   validate_client, validate_measurement_session, validate_order)


# ---------------------------------------------------------------------------
# Unit tests: auth
# ---------------------------------------------------------------------------

class AuthTests(unittest.TestCase):
    def test_password_hash_roundtrip(self):
        stored = auth.hash_password('S3cret!pass')
        self.assertTrue(stored.startswith('pbkdf2$'))
        self.assertTrue(auth.verify_password('S3cret!pass', stored))
        self.assertFalse(auth.verify_password('wrong', stored))
        self.assertFalse(auth.verify_password('S3cret!pass', 'garbage'))

    def test_token_roundtrip_tamper_expiry(self):
        issued = auth.issue_token('admin', ttl_hours=1)
        payload = auth.verify_token(issued['token'])
        self.assertEqual(payload['u'], 'admin')
        self.assertEqual(payload['r'], 'admin')
        # Tampering breaks the signature.
        self.assertIsNone(auth.verify_token(issued['token'][:-3] + 'xyz'))
        self.assertIsNone(auth.verify_token('not-a-token'))
        # Expiry is honoured.
        expired = auth.issue_token('admin', ttl_hours=-1)
        self.assertIsNone(auth.verify_token(expired['token']))
        # Logout revokes.
        auth.revoke_token(payload)
        self.assertIsNone(auth.verify_token(issued['token']))

    def test_login_rate_limit(self):
        auth._failed_logins.clear()
        ip = '203.0.113.9'
        for _ in range(auth.MAX_FAILURES):
            auth.record_failed_login(ip)
        self.assertFalse(auth.login_rate_ok(ip))
        self.assertTrue(auth.login_rate_ok('198.51.100.1'))


# ---------------------------------------------------------------------------
# Unit tests: validation + SQLite store
# ---------------------------------------------------------------------------

class ValidationTests(unittest.TestCase):
    def test_client_validation(self):
        clean = validate_client({'name': ' Sita Devi ', 'phone': '+91 98765 43210', 'email': 'a@b.co'})
        self.assertEqual(clean['name'], 'Sita Devi')
        self.assertEqual(clean['phone'], '+91 98765 43210')
        with self.assertRaises(StoreError):
            validate_client({'name': 'A', 'phone': '123'})            # phone too short
        with self.assertRaises(StoreError):
            validate_client({'name': '', 'phone': '9876543210'})      # name required
        with self.assertRaises(StoreError):
            validate_client({'name': 'X' * 90, 'phone': '9876543210'})

    def test_measurement_validation(self):
        with self.assertRaises(StoreError):
            validate_measurement_session({'garment_type': 'Spaceship', 'values': {}})
        with self.assertRaises(StoreError):
            validate_measurement_session({'garment_type': 'Shirt', 'values': {'Chest': 'abc'}})
        with self.assertRaises(StoreError):
            validate_measurement_session({'garment_type': 'Shirt', 'values': {'Chest': 9999}})
        ok = validate_measurement_session({'garment_type': 'Shirt', 'values': {'Chest': 36.5, 'custom field': 10}})
        self.assertEqual(ok['values']['Chest'], 36.5)
        self.assertEqual(ok['values']['custom field'], 10)
        with self.assertRaises(StoreError):
            validate_measurement_session({'garment_type': 'Shirt', 'values': {'bad name!': 5}})

    def test_order_validation(self):
        order = validate_order({'client_id': 'C1-AAAA', 'garment': 'Blouse', 'status': 'Pending'})
        self.assertEqual(order['status'], 'Pending')
        with self.assertRaises(StoreError):
            validate_order({'client_id': 'C1', 'garment': '', 'status': 'Flying'})


class SQLiteStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(os.path.join(self.tmp.name, 'test.sqlite3'))

    def tearDown(self):
        self.tmp.cleanup()

    def _client(self, name='Sita Devi', phone='98765 43210'):
        return self.store.create_client({'name': name, 'phone': phone})

    def test_client_crud_and_duplicate(self):
        record = self._client()
        self.assertTrue(record['client_id'].startswith('C'))
        with self.assertRaises(DuplicateClient):
            self._client(name='Duplicate', phone='+91-98765-43210')  # same digits
        found = self.store.get_client(record['client_id'])
        self.assertEqual(found['name'], 'Sita Devi')
        self.assertEqual(['Sita Devi'], [c['name'] for c in self.store.list_clients('Sita')])
        self.assertEqual(['Sita Devi'], [c['name'] for c in self.store.list_clients('98765')])
        self.assertEqual([], self.store.list_clients('nonexistent'))
        updated = self.store.update_client(record['client_id'], {'email': 'sita@example.com'})
        self.assertEqual(updated['email'], 'sita@example.com')
        self.assertTrue(self.store.delete_client(record['client_id']))
        self.assertIsNone(self.store.get_client(record['client_id']))

    def test_measurement_history_never_overwrites(self):
        client = self._client()
        cid = client['client_id']
        first = self.store.save_measurement(cid, validate_measurement_session(
            {'garment_type': 'Shirt', 'values': {'Chest': 36}, 'notes': 'first visit'}))
        second = self.store.save_measurement(cid, validate_measurement_session(
            {'garment_type': 'Shirt', 'values': {'Chest': 37.5}, 'notes': 'refit'}))
        self.assertNotEqual(first['id'], second['id'])
        history = self.store.list_history(cid)
        self.assertEqual(len(history), 2)                      # both sessions kept
        self.assertEqual(history[0]['values']['Chest'], 37.5)  # newest first
        current = self.store.get_measurement(cid)
        self.assertEqual(current['values']['Chest'], 37.5)     # current = latest
        self.assertIn(first['id'], [h['id'] for h in history])  # old session survives

    def test_orders_and_stats(self):
        client = self._client()
        order = self.store.create_order({'client_id': client['client_id'], 'garment': 'Blouse'})
        self.store.update_order(order['order_id'], {'status': 'Ready'})
        self.assertEqual(self.store.list_orders()[0]['status'], 'Ready')
        self.store.save_measurement(client['client_id'],
                                    validate_measurement_session({'garment_type': 'Pant', 'values': {'Waist': 30}}))
        stats = self.store.stats()
        self.assertEqual(stats['total_customers'], 1)
        self.assertEqual(stats['pending_orders'], 0)  # status changed to Ready
        self.assertEqual(stats['total_measurements'], 1)


# ---------------------------------------------------------------------------
# HTTP end-to-end tests
# ---------------------------------------------------------------------------

class HttpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ['APP_DATA_DIR'] = cls.tmp.name
        os.environ['ADMIN_USERNAME'] = 'admin'
        os.environ['ADMIN_PASSWORD'] = 'boutique-test-pass'
        os.environ['SESSION_SECRET'] = 'test-secret-please-rotate-1234567890'
        os.environ['ALLOWED_ORIGINS'] = 'https://client-website-theta.vercel.app'
        os.environ['ALLOW_INSECURE_COOKIE'] = '1'
        os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'] = ''
        store_module.reset_store_for_tests()
        import importlib
        import server as server_module
        importlib.reload(server_module)
        server_module.init_db()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), server_module.Handler)
        cls.server_module = server_module
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d' % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        # Tests share one server; reset rate limiters so tests don't lock each other out.
        auth._failed_logins.clear()
        self.server_module.rate_events.clear()

    def request(self, method, path, body=None, token=None, origin=None, csrf=True, cookie=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if body is not None:
            req.add_header('Content-Type', 'application/json')
        if token:
            req.add_header('Authorization', 'Bearer ' + token)
        if csrf:
            req.add_header('X-Requested-With', 'fetch')
        if origin:
            req.add_header('Origin', origin)
        if cookie:
            req.add_header('Cookie', cookie)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read() or b'{}'
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = {'raw': raw.decode('utf-8', 'replace')[:200]}
                return resp.status, parsed, dict(resp.headers)
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                parsed = json.loads(raw or b'{}')
            except ValueError:
                parsed = {'raw': raw.decode('utf-8', 'replace')[:200]}
            return err.code, parsed, dict(err.headers)

    def login(self, username='admin', password='boutique-test-pass'):
        return self.request('POST', '/api/login', {'username': username, 'password': password}, csrf=False)

    def test_healthz(self):
        status, body, _ = self.request('GET', '/healthz')
        self.assertEqual(status, 200)
        self.assertEqual(body, {'raw': 'ok'})

    def test_login_success_wrong_password_and_lockout(self):
        status, body, headers = self.login()
        self.assertEqual(status, 200)
        self.assertTrue(body['token'])
        self.assertIn('HttpOnly', headers.get('Set-Cookie', ''))
        # Wrong password -> clear error, no token.
        status, body, _ = self.login(password='nope')
        self.assertEqual(status, 401)
        self.assertIn('Wrong username or password', body['error'])
        # Rate limit after MAX_FAILURES failed attempts.
        auth._failed_logins.clear()
        for _ in range(auth.MAX_FAILURES):
            self.login(password='nope')
        status, body, _ = self.login(password='boutique-test-pass')
        self.assertEqual(status, 429)
        auth._failed_logins.clear()

    def test_protected_routes_require_auth(self):
        for method, path in [('GET', '/api/clients'), ('GET', '/api/stats'),
                             ('POST', '/api/clients'), ('GET', '/api/appointments'),
                             ('GET', '/api/orders'), ('GET', '/api/clients/C-xx')]:
            status, _, _ = self.request(method, path, body={'name': 'x', 'phone': '9999999999'} if method == 'POST' else None)
            self.assertEqual(status, 401, '%s %s should require auth' % (method, path))

    def test_full_customer_measurement_flow(self):
        _, login, _ = self.login()
        token = login['token']
        # Create
        status, client, _ = self.request('POST', '/api/clients',
                                         {'name': 'Meera Shah', 'phone': '9820011223'}, token=token)
        self.assertEqual(status, 201)
        cid = client['client_id']
        # Duplicate rejected with 409 + existing record
        status, dup, _ = self.request('POST', '/api/clients',
                                      {'name': 'Meera S.', 'phone': '+91 98200 11223'}, token=token)
        self.assertEqual(status, 409)
        self.assertEqual(dup['existing']['client_id'], cid)
        # Save two measurement sessions
        for i, chest in enumerate((36, 37.25), 1):
            status, entry, _ = self.request(
                'POST', '/api/clients/%s/measurements' % cid,
                {'garment_type': 'Blouse', 'values': {'Chest': chest, 'Shoulder': 14},
                 'notes': 'visit %d' % i, 'unit_used': 'in'}, token=token)
            self.assertEqual(status, 201)
        # History holds both, newest first; current is latest.
        status, data, _ = self.request('GET', '/api/clients/%s/measurements' % cid, token=token)
        self.assertEqual(status, 200)
        self.assertEqual(len(data['history']), 2)
        self.assertEqual(data['history'][0]['values']['Chest'], 37.25)
        self.assertEqual(data['current']['values']['Chest'], 37.25)
        # Search finds her
        status, listed, _ = self.request('GET', '/api/clients?q=meera', token=token)
        self.assertEqual(len(listed['clients']), 1)
        self.assertEqual(listed['clients'][0]['current_measurement']['garment_type'], 'Blouse')
        # Update profile
        status, updated, _ = self.request('PUT', '/api/clients/%s' % cid,
                                          {'address': 'Charkop, Kandivali West'}, token=token)
        self.assertEqual(updated['address'], 'Charkop, Kandivali West')
        # Delete with confirmation happens in the UI; API deletes.
        status, _, _ = self.request('DELETE', '/api/clients/%s' % cid, token=token)
        self.assertEqual(status, 200)
        status, _, _ = self.request('GET', '/api/clients/%s' % cid, token=token)
        self.assertEqual(status, 404)

    def test_orders_flow(self):
        _, login, _ = self.login()
        token = login['token']
        _, client, _ = self.request('POST', '/api/clients',
                                    {'name': 'Order Test', 'phone': '9820044556'}, token=token)
        status, order, _ = self.request('POST', '/api/orders',
                                        {'client_id': client['client_id'], 'garment': 'Kurta',
                                         'due_date': '2026-11-20', 'notes': 'boat neck'}, token=token)
        self.assertEqual(status, 201)
        status, updated, _ = self.request('PUT', '/api/orders/%s' % order['order_id'],
                                          {'status': 'In Progress'}, token=token)
        self.assertEqual(updated['status'], 'In Progress')
        status, stats, _ = self.request('GET', '/api/stats', token=token)
        self.assertGreaterEqual(stats['pending_orders'], 1)
        self.assertGreaterEqual(stats['total_customers'], 1)

    def test_legacy_appointment_still_works_and_requires_auth_to_read(self):
        form = 'request_id=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee&name=Legacy+Customer&phone=9812345678&service=Blouse+stitching&date=2099-01-01&time=10:30&notes=hello&consent=yes&website='
        req = urllib.request.Request(self.base + '/api/appointments', data=form.encode(), method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn(b'request was saved', resp.read())
        # Duplicate booking id is acknowledged, not double-stored.
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertIn(b'already received', resp.read())
        _, login, _ = self.login()
        status, data, _ = self.request('GET', '/api/appointments', token=login['token'])
        self.assertEqual(status, 200)
        self.assertEqual(data['appointments'][0]['name'], 'Legacy Customer')

    def test_cors_allowlist_and_preflight(self):
        status, _, headers = self.request('POST', '/api/login', {'username': 'x', 'password': 'y'},
                                          csrf=False, origin='https://client-website-theta.vercel.app')
        self.assertEqual(headers.get('Access-Control-Allow-Origin'),
                         'https://client-website-theta.vercel.app')
        # Disallowed origin gets no CORS grant.
        status, _, headers = self.request('POST', '/api/login', {'username': 'x', 'password': 'y'},
                                          csrf=False, origin='https://evil.example')
        self.assertIsNone(headers.get('Access-Control-Allow-Origin'))
        # Preflight
        req = urllib.request.Request(self.base + '/api/clients', method='OPTIONS')
        req.add_header('Origin', 'https://client-website-theta.vercel.app')
        req.add_header('Access-Control-Request-Method', 'POST')
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 204)
            self.assertIn('POST', resp.headers.get('Access-Control-Allow-Methods', ''))

    def test_csrf_header_required_for_cookie_sessions(self):
        _, login, headers = self.login()
        cookie = headers.get('Set-Cookie', '').split(';')[0]
        # Cookie auth WITHOUT the X-Requested-With header is rejected on writes.
        status, _, _ = self.request('POST', '/api/clients',
                                    {'name': 'Csrf Test', 'phone': '9800011122'},
                                    cookie=cookie, csrf=False)
        self.assertEqual(status, 403)
        # With the header it works.
        status, _, _ = self.request('POST', '/api/clients',
                                    {'name': 'Csrf Test', 'phone': '9800011122'},
                                    cookie=cookie, csrf=True)
        self.assertEqual(status, 201)

    def test_static_pages_served(self):
        for path in ('/', '/login.html', '/admin.html', '/admin.js', '/config.js'):
            url = self.base + path
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    self.assertEqual(resp.status, 200, path)
            except urllib.error.HTTPError as err:
                self.fail('%s failed: %s' % (path, err.code))


# ---------------------------------------------------------------------------
# Google Sheets store against a mocked gspread
# ---------------------------------------------------------------------------

class FakeWorksheet:
    def __init__(self, title, rows=None):
        self.title = title
        self.rows = [list(r) for r in (rows or [])]
        self.updates = []

    def row_values(self, row):
        return self.rows[row - 1] if 0 < row <= len(self.rows) else []

    def update(self, values, range_name=None, value_input_option=None):
        if range_name and ':' in range_name:
            start = range_name.split(':')[0]
            col_letters = ''.join(c for c in start if c.isalpha())
            first_col = 0
            for ch in col_letters:
                first_col = first_col * 26 + (ord(ch.upper()) - 64)
            first_col -= 1
            first_row = int(''.join(c for c in start if c.isdigit()) or 1)
            for i, row in enumerate(values):
                target = first_row - 1 + i
                while len(self.rows) <= target:
                    self.rows.append([])
                padded = list(self.rows[target]) + [''] * first_col
                padded[first_col:first_col + len(row)] = list(row)
                self.rows[target] = padded
        else:
            self.rows.append(list(values))

    def append_row(self, values, value_input_option=None):
        self.rows.append([str(v) for v in values])

    def get_all_records(self, default_blank='', head=1):
        if not self.rows:
            return []
        headers = self.rows[0]
        records = []
        for row in self.rows[1:]:
            record = {}
            for i, key in enumerate(headers):
                value = row[i] if i < len(row) else default_blank
                record[key] = value if value != '' else default_blank
            records.append(record)
        return records

    def find(self, query, in_column=None):
        for r, row in enumerate(self.rows, 1):
            col = (in_column or 1) - 1
            if col < len(row) and str(row[col]) == query:
                return types.SimpleNamespace(row=r, col=in_column or 1)
        return None

    def delete_rows(self, index):
        if 0 < index <= len(self.rows):
            self.rows.pop(index - 1)


class FakeSpreadsheet:
    def __init__(self):
        self.sheets = {
            'Clients': FakeWorksheet('Clients', [['ClientID', 'Name', 'Phone', 'Email', 'Address', 'CreatedAt', 'UpdatedAt']]),
            'Measurements': FakeWorksheet('Measurements', [['ClientID', 'Garment', 'Neck', 'Shoulder', 'Chest', 'Armhole', 'Bicep', 'Sleeve', 'Wrist', 'Waist', 'Hip', 'ShirtLength', 'Thigh', 'Knee', 'Calf', 'Ankle', 'Inseam', 'Outseam', 'CustomJSON', 'Notes', 'UnitUsed', 'UpdatedAt']]),
            'MeasurementHistory': FakeWorksheet('MeasurementHistory', [['SessionID', 'ClientID', 'Garment', 'SavedAt', 'Neck', 'Shoulder', 'Chest', 'Armhole', 'Bicep', 'Sleeve', 'Wrist', 'Waist', 'Hip', 'ShirtLength', 'Thigh', 'Knee', 'Calf', 'Ankle', 'Inseam', 'Outseam', 'CustomJSON', 'Notes', 'UnitUsed']]),
            'Orders': FakeWorksheet('Orders', [['OrderID', 'ClientID', 'Garment', 'Notes', 'DueDate', 'Status', 'CreatedAt', 'UpdatedAt']]),
            'Appointments': FakeWorksheet('Appointments', [['Request ID', 'Received (India time)', 'Customer name', 'Phone', 'Service', 'Preferred date', 'Preferred time', 'Customer query', 'Status']]),
        }

    def worksheet(self, title):
        if title not in self.sheets:
            raise KeyError(title)
        return self.sheets[title]

    def worksheets(self):
        return list(self.sheets.values())


class GoogleSheetsStoreTests(unittest.TestCase):
    def setUp(self):
        # Build a fake gspread + google.oauth2 module tree.
        fake_ws = FakeSpreadsheet()
        fake_gspread = types.ModuleType('gspread')
        fake_gspread.WorksheetNotFound = type('WorksheetNotFound', (Exception,), {})
        fake_gspread.authorize = lambda creds: types.SimpleNamespace(open_by_key=lambda key: fake_ws)
        google_mod = types.ModuleType('google')
        oauth2_mod = types.ModuleType('google.oauth2')
        sa_mod = types.ModuleType('google.oauth2.service_account')
        class FakeCreds:
            @staticmethod
            def from_service_account_info(info, scopes=None):
                return 'fake-creds'
        sa_mod.Credentials = FakeCreds
        google_mod.oauth2 = oauth2_mod
        oauth2_mod.service_account = sa_mod
        self._mods = {}
        for name, mod in [('gspread', fake_gspread), ('google', google_mod),
                          ('google.oauth2', oauth2_mod), ('google.oauth2.service_account', sa_mod)]:
            self._mods[name] = sys.modules.get(name)
            sys.modules[name] = mod
        self.fake_ss = fake_ws
        self.store = store_module.GoogleSheetsStore('{"fake": "json"}', 'SHEETID123')

    def tearDown(self):
        for name, mod in self._mods.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def test_sheets_crud_and_history(self):
        client = self.store.create_client({'name': 'Sheet User', 'phone': '9000000001'})
        cid = client['client_id']
        self.store.save_measurement(cid, validate_measurement_session(
            {'garment_type': 'Kurta', 'values': {'chest': 38, 'Length': 44}}))
        self.store.save_measurement(cid, validate_measurement_session(
            {'garment_type': 'Kurta', 'values': {'chest': 38.5, 'Length': 44.25}, 'notes': 'refit'}))
        history = self.store.list_history(cid)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['values']['chest'], 38.5)   # newest first
        self.assertEqual(self.store.get_measurement(cid)['values']['Length'], 44.25)
        order = self.store.create_order({'client_id': cid, 'garment': 'Kurta'})
        self.store.update_order(order['order_id'], {'status': 'Ready'})
        self.assertEqual(self.store.list_orders()[0]['status'], 'Ready')
        stats = self.store.stats()
        self.assertEqual(stats['total_customers'], 1)
        self.assertEqual(stats['total_measurements'], 2)
        # Duplicate prevention works across Sheets too.
        with self.assertRaises(DuplicateClient):
            self.store.create_client({'name': 'Dup', 'phone': '+91 90000 00001'})
        # Search
        self.assertEqual(len(self.store.list_clients('sheet')), 1)
        # Update profile
        updated = self.store.update_client(cid, {'email': 'sheet@example.com'})
        self.assertEqual(updated['email'], 'sheet@example.com')
        self.assertTrue(self.store.delete_client(cid))
        self.assertIsNone(self.store.get_client(cid))


if __name__ == '__main__':
    unittest.main(verbosity=2)
