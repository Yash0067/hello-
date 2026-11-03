"""Migrate local SQLite data to PostgreSQL without modifying the source.

Examples:
    python migrate_sqlite_to_postgres.py --source-db backend/data/boutique.sqlite3 \
        --source-appointments backend/data/appointments.sqlite3 --dry-run
    python migrate_sqlite_to_postgres.py --source-db backend/data/boutique.sqlite3 \
        --source-appointments backend/data/appointments.sqlite3
"""

import argparse
import sqlite3
from collections import OrderedDict
from pathlib import Path

from postgres_db import connection, init_schema, reset_sequences


def rows(conn, table):
    try:
        return conn.execute('SELECT * FROM ' + table).fetchall()
    except sqlite3.OperationalError:
        return []


def readonly_connection(path):
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError('SQLite source does not exist: %s' % source_path)
    conn = sqlite3.connect('file:%s?mode=ro' % source_path.resolve().as_posix(),
                           uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def migrate(source_db, source_appointments, source_auth, dry_run):
    sources = []
    try:
        source = readonly_connection(source_db)
        sources.append(source)
        appointment_source = readonly_connection(source_appointments) if source_appointments else source
        if appointment_source is not source:
            sources.append(appointment_source)
        auth_source = readonly_connection(source_auth) if source_auth else None
        if auth_source:
            auth_source.row_factory = sqlite3.Row
            sources.append(auth_source)

        if not dry_run:
            init_schema()
        counts = OrderedDict()
        with connection() if not dry_run else _dry_connection() as target:
            for table, sql, values in (
                ('clients', 'INSERT INTO clients VALUES (%s,%s,%s,%s,%s,%s,%s) '
                 'ON CONFLICT (client_id) DO NOTHING',
                 lambda r: tuple(r)),
                ('measurement_history', 'INSERT INTO measurement_history VALUES (%s,%s,%s,%s,%s,%s,%s) '
                 'ON CONFLICT (id) DO NOTHING',
                 lambda r: tuple(r)),
                ('measurements', 'INSERT INTO measurements VALUES (%s,%s,%s,%s,%s,%s) '
                 'ON CONFLICT (client_id) DO NOTHING',
                 lambda r: tuple(r)),
                ('orders', 'INSERT INTO orders VALUES (%s,%s,%s,%s,%s,%s,%s,%s) '
                 'ON CONFLICT (order_id) DO NOTHING',
                 lambda r: tuple(r)),
            ):
                source_rows = rows(source, table)
                inserted = failed = 0
                skipped = 0
                for row in source_rows:
                    if dry_run:
                        inserted += 1
                    else:
                        try:
                            result = target.execute(sql, values(row))
                            inserted += result.rowcount
                            skipped += 1 - result.rowcount
                        except Exception as exc:
                            failed += 1
                            counts.setdefault('_errors', []).append('%s: %s' % (table, exc))
                counts[table] = {'source': len(source_rows), 'inserted': inserted,
                                 'skipped': skipped, 'failed': failed}
            appointment_rows = rows(appointment_source, 'appointments')
            inserted = skipped = failed = 0
            for row in appointment_rows:
                if dry_run:
                    inserted += 1
                else:
                    try:
                        result = target.execute(
                            'INSERT INTO appointments VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                            'ON CONFLICT (request_id) DO NOTHING', tuple(row))
                        inserted += result.rowcount
                        skipped += 1 - result.rowcount
                    except Exception as exc:
                        failed += 1
                        counts.setdefault('_errors', []).append('appointments: %s' % exc)
            counts['appointments'] = {'source': len(appointment_rows), 'inserted': inserted,
                                      'skipped': skipped, 'failed': failed}
            if auth_source:
                for table, sql in (
                    ('revoked_sessions', 'INSERT INTO revoked_sessions VALUES (%s,%s) '
                     'ON CONFLICT (jti) DO NOTHING'),
                    ('admin_accounts', 'INSERT INTO admin_accounts '
                     '(id,username,password_hash,totp_secret,totp_enabled,created_at) '
                     'VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING'),
                    ('passkey_credentials', 'INSERT INTO passkey_credentials VALUES (%s,%s,%s,%s,%s) '
                     'ON CONFLICT (credential_id) DO NOTHING'),
                    ('auth_challenges', 'INSERT INTO auth_challenges VALUES (%s,%s,%s) '
                     'ON CONFLICT (challenge_id) DO NOTHING'),
                ):
                    source_rows = rows(auth_source, table)
                    inserted = skipped = failed = 0
                    for row in source_rows:
                        if dry_run:
                            inserted += 1
                            continue
                        try:
                            result = target.execute(sql, tuple(row))
                            inserted += result.rowcount
                            skipped += 1 - result.rowcount
                        except Exception as exc:
                            failed += 1
                            counts.setdefault('_errors', []).append('%s: %s' % (table, exc))
                    counts[table] = {'source': len(source_rows), 'inserted': inserted,
                                     'skipped': skipped, 'failed': failed}
            if not dry_run:
                for table in ('clients', 'measurement_history', 'measurements',
                              'orders', 'appointments', 'revoked_sessions'):
                    target_count = target.execute(
                        'SELECT COUNT(*) AS count FROM ' + table).fetchone()['count']
                    counts.setdefault('_verified', {})[table] = target_count
                reset_sequences(target)
        return counts
    finally:
        for source in sources:
            source.close()


class _dry_connection:
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
    parser.add_argument('--source-db', required=True)
    parser.add_argument('--source-appointments')
    parser.add_argument('--source-auth-db')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    counts = migrate(args.source_db, args.source_appointments, args.source_auth_db, args.dry_run)
    for table, result in counts.items():
        if table == '_errors':
            continue
        print('%s: source=%d inserted=%d skipped=%d failed=%d' %
              (table, result['source'], result['inserted'],
               result['skipped'], result['failed']))
    for error in counts.get('_errors', []):
        print('ERROR:', error)
    for table, count in counts.get('_verified', {}).items():
        print('%s: verified_target_count=%d' % (table, count))
    if args.dry_run:
        print('Dry run only: PostgreSQL was not changed.')
    if counts.get('_errors'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
