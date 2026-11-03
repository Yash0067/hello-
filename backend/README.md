# Backend

This is the Python appointment API. Deploy this folder to Render or Railway with:

```text
Build command: pip install -r requirements.txt
Start command: python server.py
```

For Render Free production, set `APP_ENV=production` and `DATABASE_URL` to a Neon
PostgreSQL connection string. Production refuses to start without `DATABASE_URL` and
does not use `/var/data`, SQLite, or Excel files for persistent records. SQLite remains
available for local development when `DATABASE_URL` is unset.

To migrate an existing local database, run a dry run first:

```text
python migrate_sqlite_to_postgres.py --source-db data/boutique.sqlite3 \
  --source-appointments data/appointments.sqlite3 \
  --source-auth-db data/auth.sqlite3 --dry-run
```

After reviewing the counts, remove `--dry-run` and run the same command with
`DATABASE_URL` configured. The source SQLite files are read-only during migration;
records use conflict-safe inserts and are never deleted or overwritten.
