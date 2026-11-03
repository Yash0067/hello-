"""PostgreSQL implementation of the existing store interface."""

import datetime as dt
import json
import os

from postgres_db import connection, init_schema
from store import (DuplicateClient, GoogleSheetsStore, StoreError,
                   MEASURE_COLS, _values_to_row_cells, normalize_phone, now_ist,
                   new_id)


class PostgresStore:
    """Persistent production store backed by Neon PostgreSQL."""

    def __init__(self):
        init_schema()
        self._mirror = None
        self._sync_error = None
        creds = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
        sheet_id = os.environ.get('GOOGLE_SHEET_ID', '').strip()
        if creds and sheet_id:
            try:
                self._mirror = GoogleSheetsStore(creds, sheet_id)
            except StoreError as exc:
                self._record_sync_error(exc)

    def _record_sync_error(self, exc):
        self._sync_error = str(exc)
        print('Google Sheets synchronization failed: %s' % type(exc).__name__, flush=True)

    def sync_status(self):
        if self._mirror is None:
            return {'enabled': False, 'ok': True, 'error': None}
        return {'enabled': True, 'ok': self._sync_error is None,
                'error': self._sync_error}

    def _mirror_call(self, method, *args):
        if self._mirror is not None:
            try:
                getattr(self._mirror, method)(*args)
            except Exception as exc:
                self._record_sync_error(exc)

    def _mirror_client(self, record):
        if self._mirror is not None:
            try:
                self._mirror._tab('Clients').append_row(
                    [record['client_id'], record['name'], record['phone'], record['email'],
                     record['address'], record['created_at'], record['updated_at']],
                    value_input_option='RAW')
                self._mirror._invalidate('Clients')
            except Exception as exc:
                self._record_sync_error(exc)

    def _mirror_order(self, record):
        if self._mirror is not None:
            try:
                self._mirror._tab('Orders').append_row(
                    [record['order_id'], record['client_id'], record['garment'], record['notes'],
                     record['due_date'], record['status'], record['created_at'], record['updated_at']],
                    value_input_option='RAW')
                self._mirror._invalidate('Orders')
            except Exception as exc:
                self._record_sync_error(exc)

    def _mirror_measurement(self, entry):
        if self._mirror is not None:
            try:
                cells, custom = _values_to_row_cells(entry['values'])
                self._mirror._tab('MeasurementHistory').append_row(
                    [entry['id'], entry['client_id'], entry['garment_type'], entry['saved_at'],
                     *cells, json.dumps(custom), entry['notes'], entry['unit_used']],
                    value_input_option='RAW')
                current = [entry['client_id'], entry['garment_type'], *cells,
                           json.dumps(custom), entry['notes'], entry['unit_used'], entry['saved_at']]
                sheet = self._mirror._tab('Measurements')
                match = sheet.find(entry['client_id'], in_column=1)
                if match:
                    sheet.update([current], 'A%d:%s%d' % (match.row, chr(64 + len(current)), match.row),
                                 value_input_option='RAW')
                else:
                    sheet.append_row(current, value_input_option='RAW')
                self._mirror._invalidate('Measurements', 'MeasurementHistory')
            except Exception as exc:
                self._record_sync_error(exc)

    @staticmethod
    def _client_with_current(client):
        with connection() as conn:
            row = conn.execute(
                'SELECT * FROM measurements WHERE client_id=%s',
                (client['client_id'],)).fetchone()
        client['current_measurement'] = PostgresStore._measurement_from_row(row)
        return client

    @staticmethod
    def _measurement_from_row(row):
        if not row:
            return None
        return {
            'client_id': row['client_id'],
            'garment_type': row['garment_type'],
            'values': json.loads(row['values_json']),
            'notes': row['notes'],
            'unit_used': row['unit_used'],
            'updated_at': row['updated_at'],
        }

    def list_clients(self, query=''):
        with connection() as conn:
            if query.strip():
                like = '%' + query.strip().replace(' ', '%') + '%'
                rows = conn.execute(
                    'SELECT * FROM clients WHERE name ILIKE %s OR phone ILIKE %s '
                    'ORDER BY created_at DESC LIMIT 500', (like, like)).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM clients ORDER BY created_at DESC LIMIT 500').fetchall()
        return [self._client_with_current(dict(row)) for row in rows]

    def _find_by_phone(self, phone_digits):
        with connection() as conn:
            rows = conn.execute('SELECT * FROM clients').fetchall()
        for row in rows:
            if normalize_phone(row['phone']) == phone_digits:
                return self._client_with_current(dict(row))
        return None

    def create_client(self, data):
        record = {'client_id': new_id('C'), 'created_at': now_ist(),
                  'updated_at': now_ist(), 'email': '', 'address': '', **data}
        if self._find_by_phone(normalize_phone(record['phone'])):
            raise DuplicateClient(self._find_by_phone(normalize_phone(record['phone'])))
        try:
            with connection() as conn:
                conn.execute(
                    'INSERT INTO clients VALUES (%s,%s,%s,%s,%s,%s,%s)',
                    (record['client_id'], record['name'], record['phone'],
                     record['email'], record['address'], record['created_at'],
                     record['updated_at']))
        except Exception as exc:
            if 'duplicate' in str(exc).lower() or 'unique' in str(exc).lower():
                raise DuplicateClient(self._find_by_phone(normalize_phone(record['phone']))) from exc
            raise StoreError('Could not save the customer.') from exc
        self._mirror_client(record)
        return record

    def get_client(self, client_id):
        with connection() as conn:
            row = conn.execute('SELECT * FROM clients WHERE client_id=%s',
                               (client_id,)).fetchone()
        return self._client_with_current(dict(row)) if row else None

    def update_client(self, client_id, data):
        current = self.get_client(client_id)
        if not current:
            raise StoreError('Customer not found.')
        if 'phone' in data:
            other = self._find_by_phone(normalize_phone(data['phone']))
            if other and other['client_id'] != client_id:
                raise DuplicateClient(other)
        merged = {**current, **data, 'updated_at': now_ist()}
        with connection() as conn:
            conn.execute(
                'UPDATE clients SET name=%s, phone=%s, email=%s, address=%s, '
                'updated_at=%s WHERE client_id=%s',
                (merged['name'], merged['phone'], merged['email'], merged['address'],
                 merged['updated_at'], client_id))
        self._mirror_call('update_client', client_id, data)
        return merged

    def delete_client(self, client_id):
        with connection() as conn:
            result = conn.execute('DELETE FROM clients WHERE client_id=%s', (client_id,))
        self._mirror_call('delete_client', client_id)
        return result.rowcount > 0

    def save_measurement(self, client_id, session):
        if not self.get_client(client_id):
            raise StoreError('Customer not found.')
        saved_at = now_ist()
        entry = {'id': new_id('M'), 'client_id': client_id, 'saved_at': saved_at,
                 'garment_type': session['garment_type'], 'values': session['values'],
                 'values_json': json.dumps(session['values']), 'notes': session['notes'],
                 'unit_used': session['unit_used']}
        with connection() as conn:
            conn.execute(
                'INSERT INTO measurement_history VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (entry['id'], client_id, saved_at, entry['garment_type'],
                 entry['values_json'], entry['notes'], entry['unit_used']))
            conn.execute(
                'INSERT INTO measurements VALUES (%s,%s,%s,%s,%s,%s) '
                'ON CONFLICT (client_id) DO UPDATE SET garment_type=EXCLUDED.garment_type, '
                'values_json=EXCLUDED.values_json, notes=EXCLUDED.notes, '
                'unit_used=EXCLUDED.unit_used, updated_at=EXCLUDED.updated_at',
                (client_id, entry['garment_type'], entry['values_json'], entry['notes'],
                 entry['unit_used'], saved_at))
            conn.execute('UPDATE clients SET updated_at=%s WHERE client_id=%s',
                         (saved_at, client_id))
        self._mirror_measurement(entry)
        return entry

    def get_measurement(self, client_id):
        with connection() as conn:
            row = conn.execute('SELECT * FROM measurements WHERE client_id=%s',
                               (client_id,)).fetchone()
        return self._measurement_from_row(row)

    def list_history(self, client_id):
        with connection() as conn:
            rows = conn.execute(
                'SELECT * FROM measurement_history WHERE client_id=%s '
                'ORDER BY saved_at DESC, id DESC LIMIT 200', (client_id,)).fetchall()
        return [{'id': row['id'], 'client_id': client_id, 'saved_at': row['saved_at'],
                 'garment_type': row['garment_type'], 'values': json.loads(row['values_json']),
                 'notes': row['notes'], 'unit_used': row['unit_used']} for row in rows]

    def list_orders(self, client_id=None):
        with connection() as conn:
            if client_id:
                rows = conn.execute(
                    'SELECT * FROM orders WHERE client_id=%s ORDER BY created_at DESC LIMIT 300',
                    (client_id,)).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM orders ORDER BY created_at DESC LIMIT 300').fetchall()
        return [dict(row) for row in rows]

    def create_order(self, data):
        record = {'order_id': new_id('O'), 'created_at': now_ist(),
                  'updated_at': now_ist(), 'notes': '', 'due_date': '',
                  'status': 'Pending', **data}
        if not self.get_client(record['client_id']):
            raise StoreError('Customer not found.')
        with connection() as conn:
            conn.execute(
                'INSERT INTO orders VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                (record['order_id'], record['client_id'], record['garment'],
                 record['notes'], record['due_date'], record['status'],
                 record['created_at'], record['updated_at']))
        self._mirror_order(record)
        return record

    def update_order(self, order_id, data):
        with connection() as conn:
            row = conn.execute('SELECT * FROM orders WHERE order_id=%s',
                               (order_id,)).fetchone()
        if not row:
            raise StoreError('Order not found.')
        merged = {**dict(row), **data, 'updated_at': now_ist()}
        with connection() as conn:
            conn.execute(
                'UPDATE orders SET garment=%s, notes=%s, due_date=%s, status=%s, '
                'updated_at=%s WHERE order_id=%s',
                (merged['garment'], merged['notes'], merged['due_date'],
                 merged['status'], merged['updated_at'], order_id))
        self._mirror_call('update_order', order_id, data)
        return merged

    def stats(self):
        today = dt.datetime.now(dt.timezone.utc).astimezone(
            dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime('%Y-%m-%d')
        with connection() as conn:
            total_clients = conn.execute('SELECT COUNT(*) AS c FROM clients').fetchone()['c']
            total_measurements = conn.execute(
                'SELECT COUNT(*) AS c FROM measurement_history').fetchone()['c']
            todays_appointments = conn.execute(
                'SELECT COUNT(*) AS c FROM appointments WHERE date=%s', (today,)).fetchone()['c']
            pending_orders = conn.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE status IN ('Pending','In Progress')"
            ).fetchone()['c']
            recent = conn.execute(
                'SELECT * FROM clients ORDER BY created_at DESC LIMIT 5').fetchall()
        return {
            'total_customers': total_clients,
            'total_measurements': total_measurements,
            'todays_appointments': todays_appointments,
            'pending_orders': pending_orders,
            'recent_customers': [self._client_with_current(dict(row)) for row in recent],
        }

    def create_appointment(self, row):
        with connection() as conn:
            result = conn.execute(
                'INSERT INTO appointments VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                'ON CONFLICT (request_id) DO NOTHING',
                tuple(row))
        return result.rowcount == 0

    def list_appointments(self):
        with connection() as conn:
            return conn.execute(
                'SELECT request_id, received_at, name, phone, service, date, time, notes, status '
                'FROM appointments ORDER BY received_at DESC LIMIT 500').fetchall()
