"""Data storage for the Tailor Shop Management System.

Two interchangeable backends behind one small interface:

1. SQLiteStore   - default. One SQLite file in APP_DATA_DIR. Always works,
                   zero configuration. Used for local testing and as fallback.
2. GoogleSheetsStore - used automatically when GOOGLE_SERVICE_ACCOUNT_JSON and
                   GOOGLE_SHEET_ID are set. Requires the `gspread` package.
                   All Sheets access happens here in the BACKEND only; the
                   service-account JSON is read from an environment variable,
                   never from the frontend.

Sheet tabs (created automatically on first use):
    Clients            - one row per customer (profile + current measurements)
    Measurements       - current measurement set per client
    MeasurementHistory - append-only: every saved measurement session
    Orders             - garment orders and their status
    Appointments       - mirror of website appointment bookings
"""

import datetime as dt
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')


class StoreError(RuntimeError):
    """User-friendly storage failure."""


class DuplicateClient(Exception):
    def __init__(self, existing: dict):
        super().__init__('A customer with this phone number already exists.')
        self.existing = existing


# ---------------------------------------------------------------------------
# Validation helpers (shared by both backends)
# ---------------------------------------------------------------------------

PHONE_RE = re.compile(r'[+()0-9 .-]+')
ID_RE = re.compile(r'[A-Za-z0-9-]{4,40}')
GARMENTS = ('Blouse', 'Shirt', 'Pant', 'Kurta', 'Suit', 'Blazer', 'Custom')
ORDER_STATUSES = ('Pending', 'In Progress', 'Ready', 'Delivered', 'Cancelled')


def clean_text(value, field: str, max_len: int = 200, required: bool = False) -> str:
    text = str(value or '').strip()
    if required and not text:
        raise StoreError('Please enter the %s.' % field)
    if len(text) > max_len or any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        raise StoreError('Please check the %s.' % field)
    return text


def clean_phone(value) -> str:
    phone = str(value or '').strip()
    if not PHONE_RE.fullmatch(phone) or not 7 <= len(re.sub(r'[^0-9]', '', phone)) <= 15:
        raise StoreError('Please enter a valid mobile number.')
    return phone


def normalize_phone(phone: str) -> str:
    """Digits only; for long numbers keep the last 10 (Indian +91 / STD 0)."""
    digits = re.sub(r'[^0-9]', '', str(phone))
    return digits[-10:] if len(digits) > 10 else digits


def new_id(prefix: str) -> str:
    stamp = dt.datetime.now(IST).strftime('%Y%m%d')
    return '%s%s-%s' % (prefix, stamp, uuid.uuid4().hex[:4].upper())


def now_ist() -> str:
    return dt.datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')


def validate_client(data: dict, partial: bool = False) -> dict:
    out = {}
    out['name'] = clean_text(data.get('name'), 'name', 80, required=not partial)
    if 'phone' in data or not partial:
        out['phone'] = clean_phone(data.get('phone'))
    if 'email' in data:
        email = clean_text(data.get('email'), 'email', 120)
        if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
            raise StoreError('Please enter a valid email address.')
        out['email'] = email
    if 'address' in data:
        out['address'] = clean_text(data.get('address'), 'address', 300)
    return out


MEASURE_MIN, MEASURE_MAX = 1.0, 200.0   # inches


def validate_measurements(values: dict) -> dict:
    """All measurement values are numbers stored canonically in inches."""
    out = {}
    if not isinstance(values, dict) or len(values) > 40:
        raise StoreError('Please check the measurement values.')
    for key, raw in values.items():
        name = str(key).strip()
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9 _-]{0,40}', name):
            raise StoreError('Invalid measurement field name.')
        try:
            number = round(float(raw), 2)
        except (TypeError, ValueError):
            raise StoreError('"%s" must be a number.' % name)
        if not MEASURE_MIN <= number <= MEASURE_MAX:
            raise StoreError('"%s" must be between %.0f and %.0f inches.' % (name, MEASURE_MIN, MEASURE_MAX))
        out[name] = number
    return out


def validate_measurement_session(data: dict) -> dict:
    garment = str(data.get('garment_type') or 'Custom')
    if garment not in GARMENTS:
        raise StoreError('Please choose a valid garment type.')
    return {
        'garment_type': garment,
        'values': validate_measurements(data.get('values') or {}),
        'notes': clean_text(data.get('notes'), 'notes', 500),
        'unit_used': 'cm' if data.get('unit_used') == 'cm' else 'in',
    }


def validate_order(data: dict, partial: bool = False) -> dict:
    out = {}
    if not partial or 'client_id' in data:
        out['client_id'] = clean_text(data.get('client_id'), 'customer', 40, required=True)
        if not ID_RE.fullmatch(out['client_id']):
            raise StoreError('Invalid customer reference.')
    if not partial or 'garment' in data:
        out['garment'] = clean_text(data.get('garment'), 'garment', 80, required=True)
    if not partial or 'notes' in data:
        out['notes'] = clean_text(data.get('notes'), 'order notes', 500)
    if not partial or 'due_date' in data:
        due = str(data.get('due_date') or '').strip()
        if due:
            try:
                dt.date.fromisoformat(due)
            except ValueError:
                raise StoreError('Please choose a valid due date.')
        out['due_date'] = due
    if 'status' in data:
        status = str(data.get('status') or 'Pending')
        if status not in ORDER_STATUSES:
            raise StoreError('Please choose a valid order status.')
        out['status'] = status
    elif not partial:
        out['status'] = 'Pending'
    return out


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

class SQLiteStore:
    """Default store. Everything lives in one SQLite database file."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        # Make sure the folder for the database exists (otherwise SQLite
        # cannot open the file on a fresh machine / fresh deploy).
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=25)
        conn.execute('PRAGMA busy_timeout=25000')
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self):
        with self._connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS clients (
                    client_id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL,
                    email TEXT DEFAULT '', address TEXT DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS measurement_history (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, saved_at TEXT NOT NULL,
                    garment_type TEXT NOT NULL, values_json TEXT NOT NULL,
                    notes TEXT DEFAULT '', unit_used TEXT DEFAULT 'in');
                CREATE TABLE IF NOT EXISTS measurements (
                    client_id TEXT PRIMARY KEY, garment_type TEXT NOT NULL,
                    values_json TEXT NOT NULL, notes TEXT DEFAULT '',
                    unit_used TEXT DEFAULT 'in', updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, garment TEXT NOT NULL,
                    notes TEXT DEFAULT '', due_date TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'Pending', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_history_client ON measurement_history(client_id);
                CREATE INDEX IF NOT EXISTS idx_orders_client ON orders(client_id);
            ''')

    # -- clients ------------------------------------------------------------
    def list_clients(self, query: str = '') -> list:
        like = '%' + re.sub(r'\s+', '%', query.strip()) + '%' if query.strip() else None
        with self._connect() as conn:
            if like:
                rows = conn.execute(
                    "SELECT * FROM clients WHERE name LIKE ? OR phone LIKE ? "
                    "ORDER BY created_at DESC LIMIT 500", (like, like)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM clients ORDER BY created_at DESC LIMIT 500").fetchall()
        return [self._client_with_current(dict(r)) for r in rows]

    def create_client(self, data: dict) -> dict:
        record = {
            'client_id': new_id('C'), 'created_at': now_ist(), 'updated_at': now_ist(),
            'email': '', 'address': '', **data,
        }
        existing = self._find_by_phone(normalize_phone(record['phone']))
        if existing:
            raise DuplicateClient(existing)
        with self._lock, self._connect() as conn:
            conn.execute(
                'INSERT INTO clients VALUES (?,?,?,?,?,?,?)',
                (record['client_id'], record['name'], record['phone'], record['email'],
                 record['address'], record['created_at'], record['updated_at']))
        return record

    def _find_by_phone(self, phone_digits: str) -> dict | None:
        with self._connect() as conn:
            rows = conn.execute('SELECT * FROM clients').fetchall()
        for row in rows:
            if normalize_phone(row['phone']) == phone_digits:
                return self._client_with_current(dict(row))
        return None

    def get_client(self, client_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM clients WHERE client_id=?', (client_id,)).fetchone()
        return self._client_with_current(dict(row)) if row else None

    def update_client(self, client_id: str, data: dict) -> dict:
        current = self.get_client(client_id)
        if not current:
            raise StoreError('Customer not found.')
        if 'phone' in data:
            other = self._find_by_phone(normalize_phone(data['phone']))
            if other and other['client_id'] != client_id:
                raise DuplicateClient(other)
        merged = {**current, **data, 'updated_at': now_ist()}
        with self._lock, self._connect() as conn:
            conn.execute(
                'UPDATE clients SET name=?, phone=?, email=?, address=?, updated_at=? WHERE client_id=?',
                (merged['name'], merged['phone'], merged['email'], merged['address'],
                 merged['updated_at'], client_id))
        return merged

    def delete_client(self, client_id: str) -> bool:
        with self._lock, self._connect() as conn:
            removed = conn.execute('DELETE FROM clients WHERE client_id=?', (client_id,)).rowcount
            conn.execute('DELETE FROM measurements WHERE client_id=?', (client_id,))
            conn.execute('DELETE FROM measurement_history WHERE client_id=?', (client_id,))
            conn.execute('DELETE FROM orders WHERE client_id=?', (client_id,))
        return removed > 0

    # -- measurements ---------------------------------------------------------
    def save_measurement(self, client_id: str, session: dict) -> dict:
        if not self.get_client(client_id):
            raise StoreError('Customer not found.')
        saved_at = now_ist()
        entry = {
            'id': new_id('M'), 'client_id': client_id, 'saved_at': saved_at,
            'garment_type': session['garment_type'],
            'values': session['values'], 'values_json': json.dumps(session['values']),
            'notes': session['notes'], 'unit_used': session['unit_used'],
        }
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute(
                'INSERT INTO measurement_history VALUES (?,?,?,?,?,?,?)',
                (entry['id'], client_id, saved_at, entry['garment_type'],
                 entry['values_json'], entry['notes'], entry['unit_used']))
            conn.execute('DELETE FROM measurements WHERE client_id=?', (client_id,))
            conn.execute(
                'INSERT INTO measurements VALUES (?,?,?,?,?,?)',
                (client_id, entry['garment_type'], entry['values_json'],
                 entry['notes'], entry['unit_used'], saved_at))
            conn.execute('UPDATE clients SET updated_at=? WHERE client_id=?', (saved_at, client_id))
            conn.commit()
        return entry

    def get_measurement(self, client_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM measurements WHERE client_id=?', (client_id,)).fetchone()
        if not row:
            return None
        return {
            'client_id': client_id, 'garment_type': row['garment_type'],
            'values': json.loads(row['values_json']), 'notes': row['notes'],
            'unit_used': row['unit_used'], 'updated_at': row['updated_at'],
        }

    def _client_with_current(self, client: dict) -> dict:
        current = self.get_measurement(client['client_id'])
        client['current_measurement'] = current
        return client

    def list_history(self, client_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT * FROM measurement_history WHERE client_id=? ORDER BY saved_at DESC, rowid DESC LIMIT 200',
                (client_id,)).fetchall()
        return [{'id': r['id'], 'client_id': client_id, 'saved_at': r['saved_at'],
                 'garment_type': r['garment_type'], 'values': json.loads(r['values_json']),
                 'notes': r['notes'], 'unit_used': r['unit_used']} for r in rows]

    # -- orders ---------------------------------------------------------------
    def list_orders(self, client_id: str | None = None) -> list:
        with self._connect() as conn:
            if client_id:
                rows = conn.execute('SELECT * FROM orders WHERE client_id=? ORDER BY created_at DESC LIMIT 300',
                                    (client_id,)).fetchall()
            else:
                rows = conn.execute('SELECT * FROM orders ORDER BY created_at DESC LIMIT 300').fetchall()
        return [dict(r) for r in rows]

    def create_order(self, data: dict) -> dict:
        record = {'order_id': new_id('O'), 'created_at': now_ist(), 'updated_at': now_ist(),
                  'notes': '', 'due_date': '', 'status': 'Pending', **data}
        if not self.get_client(record['client_id']):
            raise StoreError('Customer not found.')
        with self._lock, self._connect() as conn:
            conn.execute(
                'INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)',
                (record['order_id'], record['client_id'], record['garment'], record['notes'],
                 record.get('due_date', ''), record['status'], record['created_at'], record['updated_at']))
        return record

    def update_order(self, order_id: str, data: dict) -> dict:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM orders WHERE order_id=?', (order_id,)).fetchone()
        if not row:
            raise StoreError('Order not found.')
        merged = {**dict(row), **data, 'updated_at': now_ist()}
        with self._lock, self._connect() as conn:
            conn.execute('UPDATE orders SET garment=?, notes=?, due_date=?, status=?, updated_at=? WHERE order_id=?',
                         (merged['garment'], merged['notes'], merged['due_date'],
                          merged['status'], merged['updated_at'], order_id))
        return merged

    # -- stats ------------------------------------------------------------------
    def stats(self) -> dict:
        today = dt.datetime.now(IST).strftime('%Y-%m-%d')
        with self._connect() as conn:
            total_clients = conn.execute('SELECT COUNT(*) c FROM clients').fetchone()['c']
            total_measurements = conn.execute('SELECT COUNT(*) c FROM measurement_history').fetchone()['c']
            try:
                todays_appointments = conn.execute(
                    "SELECT COUNT(*) c FROM appointments WHERE date=?", (today,)).fetchone()['c']
            except sqlite3.OperationalError:
                todays_appointments = 0  # standalone store without the bookings table
            pending_orders = conn.execute(
                "SELECT COUNT(*) c FROM orders WHERE status IN ('Pending','In Progress')").fetchone()['c']
            recent = [self._client_with_current(dict(r)) for r in conn.execute(
                'SELECT * FROM clients ORDER BY created_at DESC LIMIT 5')]
        return {'total_customers': total_clients, 'total_measurements': total_measurements,
                'todays_appointments': todays_appointments, 'pending_orders': pending_orders,
                'recent_customers': recent}


# ---------------------------------------------------------------------------
# Google Sheets backend (service account, backend only)
# ---------------------------------------------------------------------------

CLIENT_HEADERS = ['ClientID', 'Name', 'Phone', 'Email', 'Address', 'CreatedAt', 'UpdatedAt']
# Human-readable layout: one column per standard measurement, custom fields in CustomJSON.
MEASURE_COLS = ['Neck', 'Shoulder', 'Chest', 'Armhole', 'Bicep', 'Sleeve', 'Wrist',
                'Waist', 'Hip', 'ShirtLength', 'Thigh', 'Knee', 'Calf', 'Ankle', 'Inseam', 'Outseam']
MEASURE_FIELD_MAP = {'neck': 'Neck', 'shoulder': 'Shoulder', 'chest': 'Chest', 'underarm': 'Armhole',
                     'bicep': 'Bicep', 'sleeve': 'Sleeve', 'wrist': 'Wrist', 'waist': 'Waist',
                     'hip': 'Hip', 'shirtlength': 'ShirtLength', 'thigh': 'Thigh', 'knee': 'Knee',
                     'calf': 'Calf', 'ankle': 'Ankle', 'inseam': 'Inseam', 'outseam': 'Outseam'}
MEASURE_SHEET_HEADERS = (['ClientID', 'Garment'] + MEASURE_COLS +
                         ['CustomJSON', 'Notes', 'UnitUsed', 'UpdatedAt'])
HISTORY_HEADERS = (['SessionID', 'ClientID', 'Garment', 'SavedAt'] + MEASURE_COLS +
                   ['CustomJSON', 'Notes', 'UnitUsed'])


def _values_to_row_cells(values: dict) -> tuple[list, dict]:
    """Split a values dict into fixed measurement cells + leftover custom JSON."""
    fixed = [''] * len(MEASURE_COLS)
    custom = {}
    for key, val in (values or {}).items():
        col = MEASURE_FIELD_MAP.get(str(key).lower())
        if col:
            fixed[MEASURE_COLS.index(col)] = val
        else:
            custom[str(key)] = val
    return fixed, custom


def _row_cells_to_values(row: dict) -> dict:
    """Read a sheet row back into a values dict (handles old ValuesJSON format too)."""
    values = {}
    for key, header in MEASURE_FIELD_MAP.items():
        raw = row.get(header, '')
        if raw not in ('', None):
            try:
                values[key] = round(float(raw), 2)
            except (TypeError, ValueError):
                pass
    legacy = str(row.get('ValuesJSON', '') or '')
    if legacy:
        try:
            values.update({k: v for k, v in json.loads(legacy).items() if k not in values})
        except ValueError:
            pass
    custom = str(row.get('CustomJSON', '') or '')
    if custom:
        try:
            values.update(json.loads(custom))
        except ValueError:
            pass
    return values
ORDER_HEADERS = ['OrderID', 'ClientID', 'Garment', 'Notes', 'DueDate', 'Status', 'CreatedAt', 'UpdatedAt']
APPOINTMENT_HEADERS = ['Request ID', 'Received (India time)', 'Customer name', 'Phone',
                       'Service', 'Preferred date', 'Preferred time', 'Customer query', 'Status']


class GoogleSheetsStore(SQLiteStore):
    """Google Sheets as the database, via gspread + a service account.

    Reads/writes go to the private spreadsheet shared with the service-account
    email. Rows are cached in memory for a few seconds to stay friendly to the
    Sheets API quota. On API failure a StoreError with a friendly message is
    raised - routes translate that into a clean HTTP 502/503 for the admin UI.
    """

    CACHE_TTL = 6  # seconds

    def __init__(self, service_account_json: str, sheet_id: str, cache_fallback: SQLiteStore | None = None):
        self.sheet_id = sheet_id
        self._gc = None
        self._ss = None
        self._tabs = {}
        self._cache = {}
        self._cache_time = {}
        self._lock = threading.Lock()
        self.credential_source = service_account_json
        self.fallback = cache_fallback
        self._connect_sheets()
        self._ensure_tabs()

    def _connect_sheets(self):
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as exc:
            raise StoreError('Run: pip install -r requirements.txt (gspread missing)') from exc
        try:
            info = json.loads(self.credential_source)
            scopes = ['https://www.googleapis.com/auth/spreadsheets',
                      'https://www.googleapis.com/auth/drive']
            self._gc = gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes))
            self._ss = self._gc.open_by_key(self.sheet_id)
        except Exception as exc:  # noqa: BLE001 - surface a friendly message
            raise StoreError('Could not open the Google Sheet. Check GOOGLE_SERVICE_ACCOUNT_JSON '
                             'and GOOGLE_SHEET_ID, and share the sheet with the service-account '
                             'email as Editor.') from exc

    def _ensure_tabs(self):
        wanted = {'Clients': CLIENT_HEADERS, 'Measurements': MEASURE_SHEET_HEADERS,
                  'MeasurementHistory': HISTORY_HEADERS, 'Orders': ORDER_HEADERS}
        existing = {ws.title for ws in self._ss.worksheets()}
        for title, headers in wanted.items():
            if title in existing:
                ws = self._ss.worksheet(title)
                if (ws.row_values(1) or [])[:1] != headers[:1]:
                    ws.update([headers], 'A1')
            else:
                ws = self._ss.add_worksheet(title=title, rows=1000, cols=len(headers) + 2)
                ws.update([headers], 'A1')
            self._tabs[title] = ws

    def _tab(self, name: str):
        ws = self._tabs.get(name)
        if ws is None:
            self._ensure_tabs()
            ws = self._tabs[name]
        return ws

    def _rows(self, tab_name: str, force: bool = False):
        """All data rows as list of dicts, cached briefly to respect API quota."""
        now = time.monotonic()
        if not force and now - self._cache_time.get(tab_name, 0) < self.CACHE_TTL:
            return self._cache.get(tab_name, [])
        ws = self._tab(tab_name)
        records = ws.get_all_records(default_blank='', head=1)
        self._cache[tab_name] = records
        self._cache_time[tab_name] = now
        return records

    def _invalidate(self, *tabs):
        for tab in tabs:
            self._cache_time.pop(tab, None)

    @staticmethod
    def _row_to_client(row: dict) -> dict:
        return {
            'client_id': str(row.get('ClientID', '')),
            'name': str(row.get('Name', '')),
            'phone': str(row.get('Phone', '')),
            'email': str(row.get('Email', '')),
            'address': str(row.get('Address', '')),
            'created_at': str(row.get('CreatedAt', '')),
            'updated_at': str(row.get('UpdatedAt', '')),
        }

    # -- clients ---------------------------------------------------------------
    def list_clients(self, query: str = '') -> list:
        rows = self._rows('Clients')
        if query.strip():
            q = query.strip().lower()
            rows = [r for r in rows if q in str(r.get('Name', '')).lower()
                    or q in str(r.get('Phone', ''))]
        rows = [self._row_to_client(r) for r in rows][-500:]
        rows.reverse()
        current_all = {str(r.get('ClientID', '')): r for r in self._rows('Measurements')}
        for client in rows:
            m = current_all.get(client['client_id'])
            client['current_measurement'] = {
                'garment_type': str(m.get('Garment', '')),
                'values': _row_cells_to_values(m),
                'notes': str(m.get('Notes', '')),
                'unit_used': str(m.get('UnitUsed', 'in')) or 'in',
                'updated_at': str(m.get('UpdatedAt', '')),
            } if m else None
        return rows

    def _find_by_phone(self, phone_digits: str) -> dict | None:
        for row in self._rows('Clients', force=True):
            if normalize_phone(str(row.get('Phone', ''))) == phone_digits:
                return self._row_to_client(row)
        return None

    def create_client(self, data: dict) -> dict:
        record = {
            'client_id': new_id('C'), 'created_at': now_ist(), 'updated_at': now_ist(),
            'email': '', 'address': '', **data,
        }
        existing = self._find_by_phone(normalize_phone(record['phone']))
        if existing:
            raise DuplicateClient(existing)
        ws = self._tab('Clients')
        ws.append_row([record['client_id'], record['name'], record['phone'], record['email'],
                       record['address'], record['created_at'], record['updated_at']],
                      value_input_option='RAW')
        self._invalidate('Clients')
        return record

    def get_client(self, client_id: str) -> dict | None:
        for row in self._rows('Clients'):
            if str(row.get('ClientID', '')) == client_id:
                return self._row_to_client(row)
        return None

    def update_client(self, client_id: str, data: dict) -> dict:
        ws = self._tab('Clients')
        cell = ws.find(client_id, in_column=1)
        if cell is None:
            raise StoreError('Customer not found.')
        if 'phone' in data:
            other = self._find_by_phone(normalize_phone(data['phone']))
            if other and other['client_id'] != client_id:
                raise DuplicateClient(other)
        row_index = cell.row
        current = self._row_to_client(self._rows('Clients')[row_index - 2])
        merged = {**current, **data, 'updated_at': now_ist()}
        ws.update([[merged['name'], merged['phone'], merged['email'], merged['address'],
                    current['created_at'], merged['updated_at']]],
                  'B%d:G%d' % (row_index, row_index), value_input_option='RAW')
        self._invalidate('Clients')
        return merged

    def delete_client(self, client_id: str) -> bool:
        ws = self._tab('Clients')
        cell = ws.find(client_id, in_column=1)
        if cell is None:
            return False
        ws.delete_rows(cell.row)
        for tab in ('Measurements', 'MeasurementHistory', 'Orders'):
            sheet = self._tab(tab)
            for _ in range(50):
                match = sheet.find(client_id, in_column=2)
                if match is None:
                    break
                sheet.delete_rows(match.row)
        self._invalidate('Clients', 'Measurements', 'MeasurementHistory')
        return True

    # -- measurements ------------------------------------------------------------
    def save_measurement(self, client_id: str, session: dict) -> dict:
        if not self.get_client(client_id):
            raise StoreError('Customer not found.')
        saved_at = now_ist()
        entry = {'id': new_id('M'), 'client_id': client_id, 'saved_at': saved_at, **session}
        cells, custom = _values_to_row_cells(session['values'])
        custom_json = json.dumps(custom) if custom else ''
        self._tab('MeasurementHistory').append_row(
            [entry['id'], client_id, session['garment_type'], saved_at] + cells +
            [custom_json, session['notes'], session['unit_used']], value_input_option='RAW')
        m_ws = self._tab('Measurements')
        match = m_ws.find(client_id, in_column=1)
        row = [client_id, session['garment_type']] + cells + [custom_json, session['notes'],
               session['unit_used'], saved_at]
        end_col = chr(ord('A') + len(row) - 1)
        if match:
            m_ws.update([row], 'A%d:%s%d' % (match.row, end_col, match.row), value_input_option='RAW')
        else:
            m_ws.append_row(row, value_input_option='RAW')
        self._invalidate('Measurements', 'MeasurementHistory')
        return entry

    def get_measurement(self, client_id: str) -> dict | None:
        for row in self._rows('Measurements'):
            if str(row.get('ClientID', '')) == client_id:
                return {'client_id': client_id, 'garment_type': str(row.get('Garment', '')),
                        'values': _row_cells_to_values(row),
                        'notes': str(row.get('Notes', '')),
                        'unit_used': str(row.get('UnitUsed', 'in')) or 'in',
                        'updated_at': str(row.get('UpdatedAt', ''))}
        return None

    def list_history(self, client_id: str) -> list:
        rows = [r for r in self._rows('MeasurementHistory') if str(r.get('ClientID', '')) == client_id]
        rows.reverse()
        return [{'id': str(r.get('SessionID', '')), 'client_id': client_id,
                 'saved_at': str(r.get('SavedAt', '')), 'garment_type': str(r.get('Garment', '')),
                 'values': _row_cells_to_values(r),
                 'notes': str(r.get('Notes', '')), 'unit_used': str(r.get('UnitUsed', 'in')) or 'in'}
                for r in rows[:200]]

    # -- orders ---------------------------------------------------------------------
    def list_orders(self, client_id: str | None = None) -> list:
        rows = self._rows('Orders')
        if client_id:
            rows = [r for r in rows if str(r.get('ClientID', '')) == client_id]
        result = [{'order_id': str(r.get('OrderID', '')), 'client_id': str(r.get('ClientID', '')),
                   'garment': str(r.get('Garment', '')), 'notes': str(r.get('Notes', '')),
                   'due_date': str(r.get('DueDate', '')), 'status': str(r.get('Status', 'Pending')),
                   'created_at': str(r.get('CreatedAt', '')), 'updated_at': str(r.get('UpdatedAt', ''))}
                  for r in rows]
        result.reverse()
        return result

    def create_order(self, data: dict) -> dict:
        if not self.get_client(data['client_id']):
            raise StoreError('Customer not found.')
        record = {'order_id': new_id('O'), 'created_at': now_ist(), 'updated_at': now_ist(),
                  'notes': '', 'due_date': '', 'status': 'Pending', **data}
        self._tab('Orders').append_row(
            [record['order_id'], record['client_id'], record['garment'], record.get('notes', ''),
             record.get('due_date', ''), record['status'], record['created_at'], record['updated_at']],
            value_input_option='RAW')
        self._invalidate('Orders')
        return record

    def update_order(self, order_id: str, data: dict) -> dict:
        ws = self._tab('Orders')
        cell = ws.find(order_id, in_column=1)
        if cell is None:
            raise StoreError('Order not found.')
        current = [r for r in self._rows('Orders') if str(r.get('OrderID', '')) == order_id][0]
        merged = {'order_id': order_id, 'client_id': str(current.get('ClientID', '')),
                  'garment': str(current.get('Garment', '')), 'notes': str(current.get('Notes', '')),
                  'due_date': str(current.get('DueDate', '')), 'status': str(current.get('Status', 'Pending')),
                  'created_at': str(current.get('CreatedAt', '')), **data, 'updated_at': now_ist()}
        ws.update([[merged['garment'], merged['notes'], merged['due_date'], merged['status'],
                    merged['created_at'], merged['updated_at']]],
                  'C%d:H%d' % (cell.row, cell.row), value_input_option='RAW')
        self._invalidate('Orders')
        return merged

    # -- stats ---------------------------------------------------------------------------
    def stats(self) -> dict:
        clients = self.list_clients()
        history_count = len(self._rows('MeasurementHistory'))
        today = dt.datetime.now(IST).strftime('%Y-%m-%d')
        appointments = []
        try:
            appointments = self._rows('Appointments')
        except Exception:
            pass
        orders = self.list_orders()
        return {
            'total_customers': len(clients),
            'total_measurements': history_count,
            'todays_appointments': sum(1 for a in appointments if str(a.get('Preferred date', '')) == today),
            'pending_orders': sum(1 for o in orders if o['status'] in ('Pending', 'In Progress')),
            'recent_customers': clients[:5],
        }


def append_appointment_to_sheets(sheet_id: str, service_account_json: str, row: list) -> bool:
    """Mirror one appointment row into the 'Appointments' tab. Never raises."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        info = json.loads(service_account_json)
        scopes = ['https://www.googleapis.com/auth/spreadsheets',
                  'https://www.googleapis.com/auth/drive']
        gc = gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes))
        ss = gc.open_by_key(sheet_id)
        try:
            ws = ss.worksheet('Appointments')
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet(title='Appointments', rows=1000, cols=len(APPOINTMENT_HEADERS) + 2)
            ws.update([APPOINTMENT_HEADERS], 'A1')
        if (ws.row_values(1) or [None])[0] != APPOINTMENT_HEADERS[0]:
            ws.update([APPOINTMENT_HEADERS], 'A1')
        # Force literal strings so customer input can never be read as a formula.
        ws.append_row([str(value) for value in row], value_input_option='RAW')
        return True
    except Exception as exc:  # noqa: BLE001 - mirroring must never break bookings
        import sys
        print('Google Sheets appointment mirror failed:', type(exc).__name__, file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Selection: Sheets when configured, SQLite otherwise
# ---------------------------------------------------------------------------

_store = None
_store_lock = threading.Lock()


def get_store():
    """Return PostgreSQL outside explicit local/test mode; SQLite is local-only."""
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is not None:
            return _store
        environment = os.environ.get('APP_ENV', '').strip().lower()
        if os.environ.get('DATABASE_URL', '').strip():
            from postgres_store import PostgresStore
            _store = PostgresStore()
            print('Storage: PostgreSQL', flush=True)
            return _store
        if environment not in ('development', 'local', 'test'):
            raise StoreError('DATABASE_URL is required outside local development.')
        data_dir = Path(os.environ.get('APP_DATA_DIR', str(Path(__file__).resolve().parent / 'data')))
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        sqlite_store = SQLiteStore(data_dir / 'boutique.sqlite3')
        creds = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
        sheet_id = os.environ.get('GOOGLE_SHEET_ID', '').strip()
        if creds and sheet_id:
            _store = GoogleSheetsStore(creds, sheet_id, cache_fallback=sqlite_store)
            print('Storage: Google Sheets (%s...)' % sheet_id[:8], flush=True)
        else:
            _store = sqlite_store
            print('Storage: local SQLite at %s' % sqlite_store.db_path, flush=True)
        return _store


def postgres_selected() -> bool:
    return bool(os.environ.get('DATABASE_URL', '').strip())


def create_appointment(row):
    if postgres_selected():
        return get_store().create_appointment(row)
    return None


def list_appointments():
    if postgres_selected():
        return get_store().list_appointments()
    return None


def reset_store_for_tests():
    global _store
    with _store_lock:
        _store = None
