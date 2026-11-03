"""Explicit, non-destructive Google Sheets to PostgreSQL import.

Configure GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_ID, and DATABASE_URL.
Use --dry-run to read and report counts without changing either data source.
"""

import argparse
import json
import os

from postgres_db import connection, init_schema, reset_sequences
from store import MEASURE_COLS, _row_cells_to_values


def sheet_rows(sheet_id, credentials):
    import gspread
    from google.oauth2.service_account import Credentials

    info = json.loads(credentials)
    scopes = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive']
    client = gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes))
    spreadsheet = client.open_by_key(sheet_id)
    return {
        name: spreadsheet.worksheet(name).get_all_records(default_blank='', head=1)
        for name in ('Clients', 'Measurements', 'MeasurementHistory', 'Orders', 'Appointments')
    }


def import_rows(all_rows, dry_run):
    counts = {}
    if not dry_run:
        init_schema()
    with connection() if not dry_run else _DryConnection() as target:
        counts['Clients'] = insert_table(
            target, 'Clients', all_rows['Clients'],
            'INSERT INTO clients VALUES (%s,%s,%s,%s,%s,%s,%s) '
            'ON CONFLICT (client_id) DO NOTHING',
            lambda row: (row.get('ClientID', ''), row.get('Name', ''), row.get('Phone', ''),
                         row.get('Email', ''), row.get('Address', ''), row.get('CreatedAt', ''),
                         row.get('UpdatedAt', '')))
        counts['Measurements'] = insert_table(
            target, 'Measurements', all_rows['Measurements'],
            'INSERT INTO measurements VALUES (%s,%s,%s,%s,%s,%s) '
            'ON CONFLICT (client_id) DO NOTHING',
            lambda row: (row.get('ClientID', ''), row.get('Garment', ''),
                         json.dumps(_row_cells_to_values(row)), row.get('Notes', ''),
                         row.get('UnitUsed', 'in'), row.get('UpdatedAt', '')))
        counts['MeasurementHistory'] = insert_table(
            target, 'MeasurementHistory', all_rows['MeasurementHistory'],
            'INSERT INTO measurement_history VALUES (%s,%s,%s,%s,%s,%s,%s) '
            'ON CONFLICT (id) DO NOTHING',
            lambda row: (row.get('SessionID', ''), row.get('ClientID', ''),
                         row.get('SavedAt', ''), row.get('Garment', ''),
                         json.dumps(_row_cells_to_values(row)), row.get('Notes', ''),
                         row.get('UnitUsed', 'in')))
        counts['Orders'] = insert_table(
            target, 'Orders', all_rows['Orders'],
            'INSERT INTO orders VALUES (%s,%s,%s,%s,%s,%s,%s,%s) '
            'ON CONFLICT (order_id) DO NOTHING',
            lambda row: (row.get('OrderID', ''), row.get('ClientID', ''),
                         row.get('Garment', ''), row.get('Notes', ''),
                         row.get('DueDate', ''), row.get('Status', 'Pending'),
                         row.get('CreatedAt', ''), row.get('UpdatedAt', '')))
        counts['Appointments'] = insert_table(
            target, 'Appointments', all_rows['Appointments'],
            'INSERT INTO appointments VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) '
            'ON CONFLICT (request_id) DO NOTHING',
            lambda row: (row.get('Request ID', ''), row.get('Received (India time)', ''),
                         row.get('Customer name', ''), row.get('Phone', ''),
                         row.get('Service', ''), row.get('Preferred date', ''),
                         row.get('Preferred time', ''), row.get('Customer query', ''),
                         row.get('Status', 'Pending')))
        if not dry_run:
            reset_sequences(target)
    return counts


def insert_table(target, label, rows, sql, values):
    result = {'source': len(rows), 'inserted': 0, 'skipped': 0, 'failed': 0, 'errors': []}
    for row in rows:
        if isinstance(target, _DryTarget):
            result['inserted'] += 1
            continue
        try:
            with target.transaction():
                changed = target.execute(sql, values(row)).rowcount
            result['inserted'] += changed
            result['skipped'] += 1 - changed
        except Exception as exc:
            result['failed'] += 1
            result['errors'].append('%s row failed: %s' % (label, type(exc).__name__))
    return result


class _DryConnection:
    def __enter__(self):
        return _DryTarget()

    def __exit__(self, *_):
        return False


class _DryTarget:
    def execute(self, *_):
        return _DryResult()


class _DryResult:
    rowcount = 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    credentials = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
    sheet_id = os.environ.get('GOOGLE_SHEET_ID', '').strip()
    if not credentials or not sheet_id or not os.environ.get('DATABASE_URL', '').strip():
        raise SystemExit('GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_ID, and DATABASE_URL are required.')
    counts = import_rows(sheet_rows(sheet_id, credentials), args.dry_run)
    failed = 0
    for label, result in counts.items():
        failed += result['failed']
        print('%s: source=%d inserted=%d skipped=%d failed=%d' %
              (label, result['source'], result['inserted'],
               result['skipped'], result['failed']))
        for error in result['errors']:
            print('ERROR:', error)
    if args.dry_run:
        print('Dry run only: Google Sheets and PostgreSQL were not modified.')
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
