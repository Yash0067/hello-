"""Authentication for the Adarsh Boutique admin.

Security model (beginner-readable, but production-grade choices):
- Passwords are never stored or compared in plain text when a hash is provided.
  We use PBKDF2-HMAC-SHA256 (a slow, salted hash designed for passwords).
- Sessions are HMAC-signed tokens: base64url(payload) + "." + base64url(signature).
  The signature uses SESSION_SECRET, so a visitor cannot forge or tamper a token.
- Tokens carry an expiry (default 8 hours) and a unique id (jti). Logout adds the
  jti to an in-memory blocklist until the token would have expired anyway.
- Login attempts are rate limited per IP address.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time

try:
    from postgres_db import connection as postgres_connection, postgres_enabled
except ImportError:
    postgres_connection = None
    postgres_enabled = lambda: False

# ---------------------------------------------------------------------------
# Configuration (all from environment variables - never hardcoded).
# Read lazily so the process picks up configuration at any time.
# ---------------------------------------------------------------------------

def _admin_username() -> str:
    return os.environ.get('ADMIN_USERNAME', '').strip()

def _admin_password() -> str:
    return os.environ.get('ADMIN_PASSWORD', '')

def _admin_password_hash() -> str:
    return os.environ.get('ADMIN_PASSWORD_HASH', '')

def _session_ttl_hours() -> float:
    try:
        return float(os.environ.get('SESSION_TTL_HOURS', '8'))
    except ValueError:
        return 8.0

PBKDF2_ITERATIONS = 200_000

_revoked_lock = threading.Lock()
_auth_db_path = None
_failure_lock = threading.Lock()
_failed_logins = {}        # ip -> [timestamps]


class AuthConfigError(RuntimeError):
    """Raised at startup when admin credentials are not configured."""


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Create a storable password hash: pbkdf2$iterations$salthex$hashhex."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return 'pbkdf2$%d$%s$%s' % (iterations, salt.hex(), digest.hex())


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification. Supports 'pbkdf2$...' hashes only."""
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split('$')
        if scheme != 'pbkdf2':
            return False
        digest = hashlib.pbkdf2_hmac(
            'sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def _session_secret() -> bytes:
    """Return the independently configured session secret."""
    secret = os.environ.get('SESSION_SECRET', '').strip()
    if len(secret) < 32:
        raise AuthConfigError('SESSION_SECRET must be configured with at least 32 characters')
    return secret.encode('utf-8')


def init_auth_db(path) -> None:
    """Create the protected server-side session revocation database."""
    global _auth_db_path
    if postgres_enabled():
        _auth_db_path = 'postgresql'
        with postgres_connection() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS revoked_sessions '
                         '(jti TEXT PRIMARY KEY, expires_at BIGINT NOT NULL)')
            conn.execute('CREATE INDEX IF NOT EXISTS revoked_sessions_expiry '
                         'ON revoked_sessions (expires_at)')
        return
    _auth_db_path = str(path)
    with sqlite3.connect(_auth_db_path) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS revoked_sessions '
                     '(jti TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)')
        conn.execute('CREATE INDEX IF NOT EXISTS revoked_sessions_expiry '
                     'ON revoked_sessions (expires_at)')
    try:
        os.chmod(_auth_db_path, 0o600)
    except OSError:
        pass


def _is_revoked(jti: str) -> bool:
    if not _auth_db_path or not jti:
        return False
    now = int(time.time())
    with _revoked_lock:
        if postgres_enabled():
            with postgres_connection() as conn:
                conn.execute('DELETE FROM revoked_sessions WHERE expires_at < %s', (now,))
                return conn.execute(
                    'SELECT 1 FROM revoked_sessions WHERE jti=%s', (jti,)).fetchone() is not None
        with sqlite3.connect(_auth_db_path) as conn:
            conn.execute('DELETE FROM revoked_sessions WHERE expires_at < ?', (now,))
            return conn.execute(
                'SELECT 1 FROM revoked_sessions WHERE jti=?', (jti,)).fetchone() is not None


def check_credentials(username: str, password: str) -> bool:
    """Server-side verification of the admin login form."""
    admin_username, admin_password, admin_hash = _admin_username(), _admin_password(), _admin_password_hash()
    if not admin_username:
        raise AuthConfigError('ADMIN_USERNAME is not configured')
    user_ok = hmac.compare_digest((username or '').encode('utf-8'), admin_username.encode('utf-8'))
    if admin_hash:
        pass_ok = verify_password(password or '', admin_hash)
    elif admin_password:
        pass_ok = hmac.compare_digest((password or '').encode('utf-8'), admin_password.encode('utf-8'))
    else:
        raise AuthConfigError('ADMIN_PASSWORD or ADMIN_PASSWORD_HASH is not configured')
    # Compare both before returning so timing leaks as little as possible.
    return user_ok and pass_ok


# ---------------------------------------------------------------------------
# Signed session tokens
# ---------------------------------------------------------------------------

def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _b64decode(text: str) -> bytes:
    padding = '=' * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def issue_token(username: str, ttl_hours: float | None = None) -> dict:
    ttl_hours = _session_ttl_hours() if ttl_hours is None else ttl_hours
    now = int(time.time())
    payload = {
        'u': username,
        'r': 'admin',                      # role claim -> server-side authorization
        'iat': now,
        'exp': now + int(ttl_hours * 3600),
        'jti': secrets.token_hex(12),
    }
    payload_b64 = _b64encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
    signature = hmac.new(_session_secret(), payload_b64.encode('ascii'), hashlib.sha256).digest()
    token = payload_b64 + '.' + _b64encode(signature)
    return {'token': token, 'expires_at': payload['exp'], 'expires_in': payload['exp'] - now}


def verify_token(token: str) -> dict | None:
    """Return the payload dict when valid+unexpired, else None."""
    if not token or '.' not in token or len(token) > 1024:
        return None
    payload_b64, _, signature_b64 = token.rpartition('.')
    expected = hmac.new(_session_secret(), payload_b64.encode('ascii'), hashlib.sha256).digest()
    try:
        supplied = _b64decode(signature_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(expected, supplied):
        return None
    try:
        payload = json.loads(_b64decode(payload_b64).decode('utf-8'))
    except (ValueError, UnicodeError):
        return None
    now = int(time.time())
    if not isinstance(payload, dict) or payload.get('exp', 0) < now:
        return None
    if not isinstance(payload.get('u'), str) or payload.get('r') != 'admin':
        return None
    if _is_revoked(payload.get('jti', '')):
        return None
    return payload


def revoke_token(payload: dict) -> None:
    """Logout: remember the token id until its natural expiry."""
    jti, exp = payload.get('jti'), payload.get('exp', 0)
    if not jti:
        return
    if not _auth_db_path:
        raise AuthConfigError('Authentication database is not configured')
    with _revoked_lock:
        if postgres_enabled():
            with postgres_connection() as conn:
                conn.execute(
                    'INSERT INTO revoked_sessions (jti, expires_at) VALUES (%s, %s) '
                    'ON CONFLICT (jti) DO UPDATE SET expires_at=EXCLUDED.expires_at',
                    (jti, int(exp)))
        else:
            with sqlite3.connect(_auth_db_path) as conn:
                conn.execute('INSERT OR REPLACE INTO revoked_sessions (jti, expires_at) VALUES (?, ?)',
                             (jti, int(exp)))


# ---------------------------------------------------------------------------
# Login rate limiting: max 5 failed attempts per IP per 15 minutes
# ---------------------------------------------------------------------------

MAX_FAILURES = 5
FAILURE_WINDOW = 900  # seconds


def login_rate_ok(ip: str) -> bool:
    """True when this IP is still allowed to try logging in."""
    now = time.monotonic()
    with _failure_lock:
        events = [t for t in _failed_logins.get(ip, []) if t > now - FAILURE_WINDOW]
        _failed_logins[ip] = events
        return len(events) < MAX_FAILURES


def record_failed_login(ip: str) -> None:
    with _failure_lock:
        _failed_logins.setdefault(ip, []).append(time.monotonic())
        # Keep the dict small: drop stale IPs entirely.
        for ip_key in [k for k, v in _failed_logins.items()
                       if not [t for t in v if t > time.monotonic() - FAILURE_WINDOW]]:
            _failed_logins.pop(ip_key, None)


def init_security_tables(db_path):
    """Create tables for passkeys and two-factor authentication."""
    import sqlite3
    import os

    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                totp_secret TEXT,
                totp_enabled INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS passkey_credentials (
                credential_id TEXT PRIMARY KEY,
                admin_id INTEGER NOT NULL,
                public_key TEXT NOT NULL,
                sign_count INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(admin_id) REFERENCES admin_accounts(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_challenges (
                challenge_id TEXT PRIMARY KEY,
                challenge TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            )
        """)

    try:
        os.chmod(db_path, 0o600)
    except OSError:
        pass
