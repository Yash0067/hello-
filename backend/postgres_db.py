"""PostgreSQL connection and idempotent schema for production deployments."""

import os
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - reported when PostgreSQL is selected
    psycopg = None
    dict_row = None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS measurement_history (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    saved_at TEXT NOT NULL,
    garment_type TEXT NOT NULL,
    values_json TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    unit_used TEXT NOT NULL DEFAULT 'in'
);
CREATE TABLE IF NOT EXISTS measurements (
    client_id TEXT PRIMARY KEY REFERENCES clients(client_id) ON DELETE CASCADE,
    garment_type TEXT NOT NULL,
    values_json TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    unit_used TEXT NOT NULL DEFAULT 'in',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    garment TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    due_date TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS appointments (
    request_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    service TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    notes TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revoked_sessions (
    jti TEXT PRIMARY KEY,
    expires_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_accounts (
    id BIGSERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    totp_secret TEXT,
    totp_enabled INTEGER NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS passkey_credentials (
    credential_id TEXT PRIMARY KEY,
    admin_id BIGINT NOT NULL REFERENCES admin_accounts(id) ON DELETE CASCADE,
    public_key TEXT NOT NULL,
    sign_count BIGINT NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_challenges (
    challenge_id TEXT PRIMARY KEY,
    challenge TEXT NOT NULL,
    expires_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_client ON measurement_history(client_id);
CREATE INDEX IF NOT EXISTS idx_orders_client ON orders(client_id);
CREATE INDEX IF NOT EXISTS idx_revoked_sessions_expiry ON revoked_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_appointments_date ON appointments(date);
"""


def database_url() -> str:
    return os.environ.get('DATABASE_URL', '').strip()


def require_database_url() -> str:
    value = database_url()
    if not value:
        raise RuntimeError('DATABASE_URL must be configured for production PostgreSQL storage.')
    if not psycopg:
        raise RuntimeError('Install PostgreSQL support with: pip install -r requirements.txt')
    parts = urlsplit(value)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault('sslmode', 'require')
    query.setdefault('connect_timeout', '10')
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@contextmanager
def connection():
    conn = psycopg.connect(require_database_url(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    with connection() as conn:
        for statement in SCHEMA_SQL.split(';'):
            statement = statement.strip()
            if statement:
                conn.execute(statement)
        _ensure_phone_constraint(conn)


def _ensure_phone_constraint(conn) -> None:
    duplicates = conn.execute(
        "SELECT RIGHT(regexp_replace(phone, '[^0-9]', '', 'g'), 10) AS normalized, "
        "COUNT(*) AS count FROM clients GROUP BY normalized HAVING COUNT(*) > 1"
    ).fetchall()
    if duplicates:
        values = ', '.join(str(row['normalized']) for row in duplicates[:10])
        raise RuntimeError(
            'Duplicate normalized phone numbers must be resolved before PostgreSQL '
            'phone uniqueness can be enabled: ' + values)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_clients_phone_normalized "
        "ON clients (RIGHT(regexp_replace(phone, '[^0-9]', '', 'g'), 10))")


def reset_sequences(conn) -> None:
    conn.execute(
        "SELECT setval(pg_get_serial_sequence('admin_accounts', 'id'), "
        "COALESCE((SELECT MAX(id) FROM admin_accounts), 1), "
        "(SELECT COUNT(*) > 0 FROM admin_accounts))")


def postgres_enabled() -> bool:
    return bool(database_url())
