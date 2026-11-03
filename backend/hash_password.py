#!/usr/bin/env python3
"""Generate a secure hash for ADMIN_PASSWORD (recommended over plain text).

Usage:  python hash_password.py
Then set the printed value in the ADMIN_PASSWORD_HASH environment variable.
"""
import getpass
from auth import hash_password

if __name__ == '__main__':
    password = getpass.getpass('Admin password to hash: ')
    if len(password) < 8:
        raise SystemExit('Use at least 8 characters.')
    print('\nADMIN_PASSWORD_HASH=' + hash_password(password))
