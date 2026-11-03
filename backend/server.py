#!/usr/bin/env python3
"""Adarsh Boutique - Tailor Shop Management System backend.

Preserved from the original project:
  * POST /api/appointments  - website booking form (SQLite + Excel, unchanged)
  * GET  /healthz           - monitoring endpoint
  * Rate limiting, strict validation, formula-injection-safe Excel export

Added in this upgrade:
  * POST /api/login, /api/logout, GET /api/me   - admin session auth
  * /api/clients, /api/clients/<id>, measurements, history, orders, stats
  * Google Sheets storage when GOOGLE_SERVICE_ACCOUNT_JSON + GOOGLE_SHEET_ID set
  * CORS allowlist (ALLOWED_ORIGINS) so the Vercel frontend can call the API
  * Exact-whitelist static serving of the frontend files (no arbitrary files)

The web server serves only the routes and whitelisted files defined below,
never arbitrary files.
"""
import base64
import binascii
import datetime as dt
import hmac
import html
import io
import json
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import auth
import store as store_module
from postgres_db import init_schema, postgres_enabled
from store import (DuplicateClient, StoreError, get_store, validate_client,
                   validate_measurement_session, validate_order, now_ist,
                   append_appointment_to_sheets)

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
except ImportError:
    sys.exit('Install the dependency first: pip install -r requirements.txt')

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT.parent / 'frontend'
LOCAL_ENVIRONMENTS = ('development', 'local', 'test')
APP_ENV = os.environ.get('APP_ENV', '').strip().lower()
IS_LOCAL_ENVIRONMENT = APP_ENV in LOCAL_ENVIRONMENTS
default_data_dir = '/tmp/adarsh-boutique-data' if os.environ.get('VERCEL') else str(ROOT / 'data')
if IS_LOCAL_ENVIRONMENT:
    DATA = Path(os.environ.get('APP_DATA_DIR', default_data_dir)).resolve()
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
else:
    # Production must use PostgreSQL and must not inspect or create a local data path.
    DATA = ROOT / 'data'
DB = DATA / 'appointments.sqlite3'
AUTH_DB = DATA / 'auth.sqlite3'
if IS_LOCAL_ENVIRONMENT:
    XLSX_PATH = Path(os.environ.get(
        'APPOINTMENTS_XLSX', str(DATA / 'Adarsh_Appointments.xlsx')
    ).strip()).expanduser().resolve()
else:
    XLSX_PATH = ROOT / 'Adarsh_Appointments.xlsx'
SERVICES = {'Blouse stitching', 'Fancy dresses', 'Gowns & western wear', 'Custom co-ord sets', 'Uniform stitching', 'Salwar suits & kurtis', 'Lehenga & bridal wear', 'Petticoat & saree fall', 'Alterations & fittings', 'Something else'}
COLUMNS = ['Request ID', 'Received (India time)', 'Customer name', 'Phone', 'Service', 'Preferred date', 'Preferred time', 'Customer query', 'Status']
SHEETS_MIRROR = (os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
                 and os.environ.get('GOOGLE_SHEET_ID', '').strip())
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get('ALLOWED_ORIGINS', '').split(',') if o.strip()]
SESSION_COOKIE = 'ab_session'
MAX_JSON_BODY = 262_144  # 256 KB

rate_lock = threading.Lock()
rate_events = defaultdict(deque)
xlsx_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Whitelisted static files (exact names only - never built from user input)
# ---------------------------------------------------------------------------
STATIC_FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/login.html': ('login.html', 'text/html; charset=utf-8'),
    '/admin.html': ('admin.html', 'text/html; charset=utf-8'),
    '/admin.js': ('admin.js', 'text/javascript; charset=utf-8'),
    '/config.js': ('config.js', 'text/javascript; charset=utf-8'),
}


@contextmanager
def db_connect():
    conn = sqlite3.connect(DB, timeout=25)
    conn.execute('PRAGMA busy_timeout=25000')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    if postgres_enabled():
        init_schema()
        return
    if not IS_LOCAL_ENVIRONMENT:
        raise RuntimeError('DATABASE_URL is required outside local development.')
    with db_connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS appointments (
            request_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, name TEXT NOT NULL,
            phone TEXT NOT NULL, service TEXT NOT NULL, date TEXT NOT NULL,
            time TEXT NOT NULL, notes TEXT NOT NULL, status TEXT NOT NULL)''')
    try:
        os.chmod(DB, 0o600)
    except OSError:
        pass


def bounded_rate(key, maximum, period):
    now = time.monotonic()
    with rate_lock:
        q = rate_events[key]
        while q and q[0] <= now - period:
            q.popleft()
        if len(q) >= maximum:
            return False
        q.append(now)
        return True


def message_page(title, message, success=False):
    return ('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>''' + html.escape(title) + '''</title>
<style>body{background:#f8f3eb;color:#352725;font:16px/1.6 Arial,sans-serif;margin:0;padding:30px 18px}.card{max-width:510px;margin:7vh auto;background:#fffdf8;padding:34px;border:1px solid #e7d9cb}h1{font:32px/1.15 Georgia,serif;color:#632c35}a{color:#632c35}.tag{font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#957857}</style>
</head><body><main class="card"><div class="tag">Adarsh Boutique · Kandivali West</div><h1>''' + html.escape(title) + '</h1><p>' + html.escape(message) + '''</p><p><a href="/">Return to the website</a> · <a href="https://wa.me/919967004638">Contact us on WhatsApp</a></p></main></body></html>''').encode('utf-8')


def valid_submission(fields):
    def get(name, minlen, maxlen):
        values = fields.get(name, [])
        if len(values) != 1:
            raise ValueError('Please check your details and try again.')
        value = values[0].strip()
        if len(value) < minlen or len(value) > maxlen or any(ord(c) < 32 and c not in '\n\r\t' for c in value):
            raise ValueError('Please check your details and try again.')
        return value

    if fields.get('website', [''])[0].strip():
        raise ValueError('Please contact the boutique directly.')
    request_id = get('request_id', 16, 80)
    if not re.fullmatch(r'[A-Za-z0-9-]+', request_id):
        raise ValueError('Please check your details and try again.')
    name = get('name', 2, 80)
    phone = get('phone', 7, 30)
    if not re.fullmatch(r'[+()0-9 .-]+', phone) or not 7 <= len(re.sub(r'[^0-9]', '', phone)) <= 15:
        raise ValueError('Please enter a valid phone number.')
    service = get('service', 2, 80)
    if service not in SERVICES:
        raise ValueError('Please choose a valid service.')
    date = get('date', 10, 10)
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            raise ValueError()
        chosen_date = dt.date.fromisoformat(date)
        if chosen_date < dt.datetime.now(ZoneInfo('Asia/Kolkata')).date():
            raise ValueError()
    except ValueError:
        raise ValueError('Please choose a valid date that is not in the past.')
    appointment_time = get('time', 5, 5)
    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', appointment_time):
        raise ValueError('Please choose a valid time.')
    notes = get('notes', 0, 1000) if 'notes' in fields else ''
    if get('consent', 1, 10) != 'yes':
        raise ValueError('Please agree to the contact details being used for your request.')
    return request_id, name, phone, service, date, appointment_time, notes


def xlsx_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Appointments'
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='632C35')
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = 'A1:I' + str(max(len(rows) + 1, 1))
    for row in rows:
        ws.append([str(value) for value in row])
        # Force literal strings, never formulas, including customer-supplied values.
        for cell in ws[ws.max_row]:
            cell.data_type = 's'
            cell.number_format = '@'
    for index, width in enumerate([39, 23, 25, 20, 25, 19, 19, 42, 16], 1):
        ws.column_dimensions[chr(64 + index)].width = width
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


def append_to_xlsx(row):
    with xlsx_lock:
        if XLSX_PATH.exists():
            workbook = load_workbook(XLSX_PATH)
            worksheet = workbook.active
        else:
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = 'Appointments'
            worksheet.append(COLUMNS)

        headers = [cell.value for cell in worksheet[1]]
        if not headers or all(value is None for value in headers):
            worksheet.delete_rows(1, worksheet.max_row)
            worksheet.append(COLUMNS)
            headers = COLUMNS
        elif len(headers) < len(COLUMNS):
            for index, value in enumerate(COLUMNS[len(headers):], len(headers) + 1):
                worksheet.cell(row=1, column=index, value=value)
            headers = [cell.value for cell in worksheet[1]]
        if headers[7] in ('Details', 'Query', 'Notes'):
            worksheet.cell(row=1, column=8, value='Customer query')

        worksheet.append([str(value) for value in row])
        for cell in worksheet[worksheet.max_row]:
            cell.data_type = 's'
            cell.number_format = '@'
        worksheet.auto_filter.ref = 'A1:I' + str(worksheet.max_row)
        workbook.save(XLSX_PATH)


def xlsx_file_bytes():
    with xlsx_lock:
        if not XLSX_PATH.exists():
            return xlsx_bytes([])
        return XLSX_PATH.read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = 'AdarshWebsite'
    sys_version = ''

    def log_message(self, fmt, *args):
        # Avoid logging submitted data or query strings.
        sys.stderr.write('%s - %s\n' % (self.client_address[0], fmt % args))

    # ------------------------------------------------------------- helpers
    def reply(self, status, body, content_type='text/html; charset=utf-8', headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def reply_json(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.reply(status, body, 'application/json; charset=utf-8', headers)

    def origin_allowed(self):
        origin = self.headers.get('Origin', '')
        return origin if origin and (origin in ALLOWED_ORIGINS) else ''

    def cors_headers(self):
        origin = self.origin_allowed()
        if not origin:
            return {}
        return {
            'Access-Control-Allow-Origin': origin,
            'Access-Control-Allow-Credentials': 'true',
            'Vary': 'Origin',
        }

    def session_cookie(self):
        cookie_header = self.headers.get('Cookie', '')
        for part in cookie_header.split(';'):
            name, _, value = part.strip().partition('=')
            if name == SESSION_COOKIE and value:
                return value
        return ''

    def auth_token(self):
        header = self.headers.get('Authorization', '')
        if header.startswith('Bearer '):
            return header[7:].strip()
        return self.session_cookie()

    def current_admin(self):
        """Server-side authorization for every protected route.
        Tries the Bearer header first, then the login cookie. Some embedded
        preview browsers lose the token between pages, so the cookie keeps
        the session alive (login always sets it)."""
        header = self.headers.get('Authorization', '')
        if header.startswith('Bearer '):
            payload = auth.verify_token(header[7:].strip())
            if payload and payload.get('r') == 'admin':
                return payload
        payload = auth.verify_token(self.session_cookie())
        return payload if payload and payload.get('r') == 'admin' else None

    def require_admin(self):
        admin = self.current_admin()
        if admin:
            return admin
        self.reply_json(401, {'error': 'Please sign in to continue.'},
                        {'Cache-Control': 'no-store', **self.cors_headers()})
        return None

    def read_json_body(self, max_len=MAX_JSON_BODY):
        length = int(self.headers.get('Content-Length', '0'))
        if length < 1 or length > max_len:
            raise ValueError('Invalid request size.')
        raw = self.rfile.read(length).decode('utf-8', errors='strict')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('Invalid request body.')
        return data

    def read_form_body(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length < 1 or length > 8192:
            raise ValueError('Invalid request size.')
        raw = self.rfile.read(length).decode('utf-8', errors='strict')
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True,
                                             max_num_fields=30).items()}

    def set_session_cookie(self, token, max_age):
        local = os.environ.get('APP_ENV', '').lower() in ('development', 'local')
        local = local or self.headers.get('Host', '').split(':')[0] in ('localhost', '127.0.0.1')
        secure = '' if local else '; Secure'
        self.send_header('Set-Cookie',
                         '%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s' %
                         (SESSION_COOKIE, token, max_age, secure))

    def csrf_ok(self):
        """When a browser uses the session cookie, require a custom header.
        Cross-site pages cannot add custom headers without a successful CORS
        preflight, so this blocks CSRF for cookie-based sessions."""
        if self.auth_token() != self.session_cookie():
            return True  # Bearer-token clients are not CSRF-able.
        return self.headers.get('X-Requested-With') in ('fetch', 'XMLHttpRequest')

    # ------------------------------------------------------------- routes
    def do_OPTIONS(self):
        if self.origin_allowed():
            self.reply(204, b'', headers={
                'Access-Control-Allow-Origin': self.origin_allowed(),
                'Access-Control-Allow-Credentials': 'true',
                'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
                'Access-Control-Allow-Headers': 'Authorization, Content-Type, X-Requested-With',
                'Access-Control-Max-Age': '600',
                'Vary': 'Origin',
            })
        else:
            self.reply(204, b'')

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == '/healthz':
                self.reply(200, b'ok', 'text/plain; charset=utf-8')
                return
            if path == '/api/me':
                admin = self.require_admin()
                if admin:
                    selected_store = get_store()
                    self.reply_json(200, {'username': admin['u'], 'role': admin['r'],
                                          'storage': type(selected_store).__name__,
                                          'sheets': bool(SHEETS_MIRROR),
                                          'sheets_sync': getattr(
                                              selected_store, 'sync_status',
                                              lambda: {'enabled': bool(SHEETS_MIRROR), 'ok': True,
                                                       'error': None})()},
                                    {'Cache-Control': 'no-store', **self.cors_headers()})
                return
            if path == '/api/clients':
                admin = self.require_admin()
                if admin:
                    query = parse_qs(urlsplit(self.path).query).get('q', [''])[0][:100]
                    self.reply_json(200, {'clients': get_store().list_clients(query)},
                                    {'Cache-Control': 'no-store', **self.cors_headers()})
                return
            if path.startswith('/api/clients/'):
                self.route_client_get(path)
                return
            if path == '/api/orders':
                admin = self.require_admin()
                if admin:
                    self.reply_json(200, {'orders': get_store().list_orders()},
                                    {'Cache-Control': 'no-store', **self.cors_headers()})
                return
            if path == '/api/stats':
                admin = self.require_admin()
                if admin:
                    stats = get_store().stats()
                    # "Today's appointments" lives in THIS server's bookings DB
                    # (the legacy appointment table), so count it here directly.
                    try:
                        today = dt.datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d')
                        if postgres_enabled():
                            stats['todays_appointments'] = get_store().stats()['todays_appointments']
                        else:
                            with db_connect() as conn:
                                row = conn.execute(
                                    'SELECT COUNT(*) FROM appointments WHERE date=?', (today,)).fetchone()
                            stats['todays_appointments'] = row[0]
                    except sqlite3.Error:
                        pass  # keep the store's own value if the bookings DB is unavailable
                    self.reply_json(200, stats,
                                    {'Cache-Control': 'no-store', **self.cors_headers()})
                return
            if path == '/api/appointments':
                admin = self.require_admin()
                if admin:
                    rows = store_module.list_appointments()
                    if rows is None:
                        with db_connect() as conn:
                            rows = conn.execute(
                                'SELECT * FROM appointments ORDER BY received_at DESC LIMIT 500').fetchall()
                    keys = ['request_id', 'received_at', 'name', 'phone', 'service',
                            'date', 'time', 'notes', 'status']
                    self.reply_json(200, {'appointments': [
                        dict(row) if isinstance(row, dict) else dict(zip(keys, row))
                        for row in rows]},
                                    {'Cache-Control': 'no-store', **self.cors_headers()})
                return
            if path in STATIC_FILES:
                self.serve_static(path)
                return
            self.reply(404, b'Not found', 'text/plain; charset=utf-8')
        except (BrokenPipeError, ConnectionResetError):
            pass
        except StoreError as exc:
            self.reply_json(502, {'error': str(exc)}, self.cors_headers())
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.reply_json(500, {'error': 'Something went wrong. Please try again.'},
                            self.cors_headers())

    def route_client_get(self, path):
        admin = self.require_admin()
        if not admin:
            return
        parts = [p for p in path.split('/') if p]  # ['api','clients', id, sub?]
        client_id = parts[2]
        if len(parts) == 3:
            client = get_store().get_client(client_id)
            if not client:
                self.reply_json(404, {'error': 'Customer not found.'}, self.cors_headers())
            else:
                self.reply_json(200, client, {'Cache-Control': 'no-store', **self.cors_headers()})
            return
        if len(parts) == 4 and parts[3] == 'measurements':
            if not get_store().get_client(client_id):
                self.reply_json(404, {'error': 'Customer not found.'}, self.cors_headers())
                return
            self.reply_json(200, {
                'current': get_store().get_measurement(client_id),
                'history': get_store().list_history(client_id),
            }, {'Cache-Control': 'no-store', **self.cors_headers()})
            return
        if len(parts) == 4 and parts[3] == 'orders':
            self.reply_json(200, {'orders': get_store().list_orders(client_id)},
                            {'Cache-Control': 'no-store', **self.cors_headers()})
            return
        self.reply(404, b'Not found', 'text/plain; charset=utf-8')

    def serve_static(self, path):
        filename, content_type = STATIC_FILES[path]
        file_path = FRONTEND / filename
        try:
            body = file_path.read_bytes()
        except OSError:
            self.reply(404, b'File not deployed', 'text/plain; charset=utf-8')
            return
        headers = {'Cache-Control': 'no-store', 'X-Frame-Options': 'SAMEORIGIN',
                   'Content-Security-Policy': "default-src 'self'; img-src 'self' data: https:; "
                       "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' https://script.google.com; "
                       "connect-src 'self' https://script.google.com http: https:; "
                       "frame-src https://www.google.com https://maps.google.com"}
        if path == '/':
            headers.pop('X-Frame-Options', None)  # the public site may be embedded in viewers
        self.reply(200, body, content_type, headers)

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            if path == '/api/appointments':
                self.handle_appointment()
                return
            if path == '/api/login':
                self.handle_login()
                return
            if path == '/api/logout':
                payload = self.current_admin()
                if payload:
                    auth.revoke_token(payload)
                self.reply_json(200, {'ok': True}, {'Cache-Control': 'no-store',
                                                    'Set-Cookie': '%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0%s' %
                                                                  (SESSION_COOKIE, '' if os.environ.get('APP_ENV', '').lower() in ('development', 'local') or self.headers.get('Host', '').split(':')[0] in ('localhost', '127.0.0.1') else '; Secure'),
                                                    **self.cors_headers()})
                return
            if path == '/api/clients':
                admin = self.require_admin()
                if not admin:
                    return
                if not self.csrf_ok():
                    self.reply_json(403, {'error': 'Invalid request origin.'}, self.cors_headers())
                    return
                record = get_store().create_client(validate_client(self.read_json_body()))
                self.reply_json(201, record, {'Cache-Control': 'no-store', **self.cors_headers()})
                return
            if path.startswith('/api/clients/') and path.count('/') == 4:
                admin = self.require_admin()
                if not admin:
                    return
                if not self.csrf_ok():
                    self.reply_json(403, {'error': 'Invalid request origin.'}, self.cors_headers())
                    return
                client_id = path.split('/')[3]
                session = validate_measurement_session(self.read_json_body())
                entry = get_store().save_measurement(client_id, session)
                self.reply_json(201, entry, {'Cache-Control': 'no-store', **self.cors_headers()})
                return
            if path == '/api/orders':
                admin = self.require_admin()
                if not admin:
                    return
                if not self.csrf_ok():
                    self.reply_json(403, {'error': 'Invalid request origin.'}, self.cors_headers())
                    return
                record = get_store().create_order(validate_order(self.read_json_body()))
                self.reply_json(201, record, {'Cache-Control': 'no-store', **self.cors_headers()})
                return
            self.reply(404, b'Not found', 'text/plain; charset=utf-8')
        except (ValueError, UnicodeError, json.JSONDecodeError, binascii.Error):
            self.reply_json(400, {'error': 'Please check the details and try again.'},
                            self.cors_headers())
        except DuplicateClient as exc:
            self.reply_json(409, {'error': 'A customer with this phone number already exists.',
                                  'existing': exc.existing}, self.cors_headers())
        except StoreError as exc:
            self.reply_json(422, {'error': str(exc)}, self.cors_headers())
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.reply_json(500, {'error': 'Something went wrong. Please try again.'},
                            self.cors_headers())

    def do_PUT(self):
        path = urlsplit(self.path).path
        try:
            admin = self.require_admin()
            if not admin:
                return
            if not self.csrf_ok():
                self.reply_json(403, {'error': 'Invalid request origin.'}, self.cors_headers())
                return
            if path.startswith('/api/clients/') and path.count('/') == 3:
                client_id = path.split('/')[3]
                record = get_store().update_client(client_id, validate_client(self.read_json_body(), partial=True))
                self.reply_json(200, record, {'Cache-Control': 'no-store', **self.cors_headers()})
                return
            if path.startswith('/api/orders/') and path.count('/') == 3:
                record = get_store().update_order(path.split('/')[3],
                                                  validate_order(self.read_json_body(), partial=True))
                self.reply_json(200, record, {'Cache-Control': 'no-store', **self.cors_headers()})
                return
            self.reply(404, b'Not found', 'text/plain; charset=utf-8')
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self.reply_json(400, {'error': 'Please check the details and try again.'},
                            self.cors_headers())
        except DuplicateClient as exc:
            self.reply_json(409, {'error': 'Another customer already uses this phone number.',
                                  'existing': exc.existing}, self.cors_headers())
        except StoreError as exc:
            self.reply_json(422, {'error': str(exc)}, self.cors_headers())
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.reply_json(500, {'error': 'Something went wrong. Please try again.'},
                            self.cors_headers())

    def do_DELETE(self):
        path = urlsplit(self.path).path
        try:
            admin = self.require_admin()
            if not admin:
                return
            if not self.csrf_ok():
                self.reply_json(403, {'error': 'Invalid request origin.'}, self.cors_headers())
                return
            if path.startswith('/api/clients/') and path.count('/') == 3:
                client_id = path.split('/')[3]
                if get_store().delete_client(client_id):
                    self.reply_json(200, {'ok': True}, {'Cache-Control': 'no-store', **self.cors_headers()})
                else:
                    self.reply_json(404, {'error': 'Customer not found.'}, self.cors_headers())
                return
            self.reply(404, b'Not found', 'text/plain; charset=utf-8')
        except StoreError as exc:
            self.reply_json(422, {'error': str(exc)}, self.cors_headers())
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.reply_json(500, {'error': 'Something went wrong. Please try again.'},
                            self.cors_headers())

    # ------------------------------------------------------------ handlers
    def handle_login(self):
        if not bounded_rate(('login', self.client_address[0]), 10, 900):
            self.reply_json(429, {'error': 'Too many attempts. Please wait 15 minutes.'},
                            {'Retry-After': '900', 'Cache-Control': 'no-store', **self.cors_headers()})
            return
        if not auth.login_rate_ok(self.client_address[0]):
            self.reply_json(429, {'error': 'Too many failed attempts. Please wait 15 minutes.'},
                            {'Retry-After': '900', 'Cache-Control': 'no-store', **self.cors_headers()})
            return
        try:
            data = self.read_json_body(max_len=2048)
        except (ValueError, json.JSONDecodeError):
            data = self.read_form_body()
        username = str(data.get('username', ''))[:80]
        password = str(data.get('password', ''))[:256]
        try:
            ok = auth.check_credentials(username, password)
        except auth.AuthConfigError as exc:
            self.reply_json(503, {'error': str(exc)}, self.cors_headers())
            return
        if not ok:
            auth.record_failed_login(self.client_address[0])
            self.reply_json(401, {'error': 'Wrong username or password.'},
                            {'Cache-Control': 'no-store', **self.cors_headers()})
            return
        issued = auth.issue_token(username)
        local = os.environ.get('APP_ENV', '').lower() in ('development', 'local')
        local = local or self.headers.get('Host', '').split(':')[0] in ('localhost', '127.0.0.1')
        secure = '' if local else '; Secure'
        cookie = ('%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s' %
                  (SESSION_COOKIE, issued['token'], issued['expires_in'], secure))
        self.reply_json(200, {'ok': True, 'username': username, 'expires_at': issued['expires_at'],
                              'expires_in': issued['expires_in']},
                        {'Cache-Control': 'no-store', 'Set-Cookie': cookie,
                         **self.cors_headers()})

    def handle_appointment(self):
        if not bounded_rate(('request', self.client_address[0]), 40, 3600):
            self.reply(429, message_page('Request could not be saved', 'Too many requests. Please try again later or contact the boutique.'))
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 1 or length > 8192 or self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                raise ValueError('Please check your details and try again.')
            raw = self.rfile.read(length).decode('utf-8', errors='strict')
            fields = parse_qs(raw, keep_blank_values=True, max_num_fields=30, encoding='utf-8', errors='strict')
            request_id, name, phone, service, date, appointment_time, notes = valid_submission(fields)
            now = now_ist()
            row = (request_id, now, name, phone, service, date, appointment_time, notes, 'Pending')
            if postgres_enabled():
                duplicate = store_module.create_appointment(row)
            else:
                with db_connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    result = conn.execute('INSERT OR IGNORE INTO appointments VALUES (?,?,?,?,?,?,?,?,?)', row)
                    duplicate = result.rowcount == 0
                    conn.commit()  # Only report success after the database commit succeeds.
            if not duplicate:
                if os.environ.get('APP_ENV', '').lower() != 'production':
                    append_to_xlsx((request_id, now, name, phone, service, date, appointment_time, notes, 'Pending'))
                if SHEETS_MIRROR:
                    # Best-effort mirror to the private Google Sheet; never blocks a booking.
                    append_appointment_to_sheets(os.environ.get('GOOGLE_SHEET_ID', ''),
                                                 os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', ''),
                                                 (request_id, now, name, phone, service, date, appointment_time, notes, 'Pending'))
            message = ('This request was already received. ' if duplicate else 'Your request was saved. ') + 'The boutique will contact you to confirm the date and time. Your appointment is not confirmed yet.'
            self.reply(200, message_page('Appointment request received', message, True), headers={'Cache-Control': 'no-store'})
        except (ValueError, UnicodeError):
            self.reply(400, message_page('Request could not be saved', 'Please check the form details and try again, or contact the boutique on WhatsApp.'), headers={'Cache-Control': 'no-store'})
        except Exception as exc:
            print('Appointment save failed:', type(exc).__name__, file=sys.stderr)
            self.reply(500, message_page('Request could not be saved', 'Please try again later or contact the boutique on WhatsApp.'), headers={'Cache-Control': 'no-store'})


if __name__ == '__main__':
    if os.environ.get('APP_ENV', '').lower() not in ('development', 'local', 'test') and not postgres_enabled():
        raise SystemExit('DATABASE_URL is required outside local development.')
    init_db()
    auth.init_auth_db(AUTH_DB)
    get_store()  # Fail fast (and log) if the configured store cannot be opened.
    port = int(os.environ.get('PORT', '8000'))
    host = os.environ.get('HOST', '0.0.0.0')
    server = ThreadingHTTPServer((host, port), Handler)
    print('Adarsh Boutique listening on port', port, flush=True)
    server.serve_forever()
