#!/usr/bin/env python3
"""One-time helper: connect the boutique backend to your Google Sheet.

Usage (from backend/):
    python setup_sheets.py service-account.json YOUR_SHEET_ID

It will:
  1. Read your service-account JSON key
  2. Open the spreadsheet and verify permission
  3. Create the 5 tabs with the right headers (Clients, Measurements, ...)
  4. Write + read back one test row, then remove it
  5. Print exactly what to paste as environment variables

Get the JSON key + Sheet ID like this (10 minutes, one time only):
  1. https://console.cloud.google.com  -> create a project (any name)
  2. APIs & Services -> Library -> enable "Google Sheets API" and "Google Drive API"
  3. APIs & Services -> Credentials -> Create credentials -> Service account
     -> open it -> Keys -> Add key -> JSON  (a .json file downloads)
  4. Create your Google Sheet ("Adarsh Boutique Data"), copy the ID from its URL
  5. In the sheet: Share -> paste the service-account email
     (it looks like name@project.iam.gserviceaccount.com) -> give it Editor
  6. Run this script with the file + sheet ID.
"""
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    key_path, sheet_id = Path(sys.argv[1]), sys.argv[2].strip()
    if not key_path.exists():
        sys.exit('Key file not found: %s' % key_path)

    creds_json = key_path.read_text().strip()
    try:
        info = json.loads(creds_json)
        email = info.get('client_email', '?')
    except ValueError:
        sys.exit('That file is not valid service-account JSON.')

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive']
    gc = gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes))
    print('Key OK. Service account:', email)

    try:
        ss = gc.open_by_key(sheet_id)
    except Exception as exc:  # noqa: BLE001
        sys.exit('Could not open sheet %s (%s).\nDid you share it with %s as Editor?'
                 % (sheet_id, type(exc).__name__, email))
    print('Sheet OK:', ss.title)

    from store import (CLIENT_HEADERS, MEASURE_SHEET_HEADERS, HISTORY_HEADERS,
                       ORDER_HEADERS, APPOINTMENT_HEADERS)
    wanted = {'Clients': CLIENT_HEADERS, 'Measurements': MEASURE_SHEET_HEADERS,
              'MeasurementHistory': HISTORY_HEADERS, 'Orders': ORDER_HEADERS,
              'Appointments': APPOINTMENT_HEADERS}
    existing = {ws.title for ws in ss.worksheets()}
    for title, headers in wanted.items():
        if title in existing:
            print('  tab exists:', title)
            continue
        ws = ss.add_worksheet(title=title, rows=1000, cols=len(headers) + 2)
        ws.update([headers], 'A1')
        print('  tab created:', title)

    ws = ss.worksheet('Clients')
    test = ['C-TEST-0000', 'CONNECTION TEST', '0000000000', '', '', 'test', 'test']
    ws.append_row(test, value_input_option='RAW')
    rows = ws.get_all_values()
    found = any(row and row[0] == 'C-TEST-0000' for row in rows)
    for idx, row in enumerate(rows, 1):
        if row and row[0] == 'C-TEST-0000':
            ws.delete_rows(idx)
            break
    print('Write/read test:', 'PASSED ✓' if found else 'FAILED')

    print('\n================ PASTE THESE INTO RENDER/RAILWAY ================')
    print('GOOGLE_SHEET_ID=' + sheet_id)
    print('GOOGLE_SERVICE_ACCOUNT_JSON=<open %s and paste the whole file on one line>'
          % key_path.name)
    print('==================================================================')
    print('Tip: to put the JSON on one line:  python -c "import json,sys;print(json.dumps(json.load(open(\'%s\'))))"' % key_path.name)


if __name__ == '__main__':
    main()
