

A professional management system for a real tailoring shop, built **on top of the existing
boutique website** (public site and appointment booking are preserved exactly as they were).

```
frontend/   public website (unchanged) + NEW login.html + admin.html/admin.js (dashboard)
backend/    Python API (stdlib only) + NEW auth.py, store.py, hash_password.py, tests/
```

**What's inside**

| Area | Details |
|---|---|
| Public website | Untouched — homepage, WhatsApp enquiry, booking form (Apps Script → private Google Sheet) |
| Admin login | `/login.html` — server-side auth, PBKDF2-hashed password, signed session token (8 h), HttpOnly cookie + Bearer, rate-limited (5 tries / 15 min), secure logout |
| Dashboard | Total customers, measurements saved, today's appointments, pending orders, recent customers, quick actions |
| Customers | Add / edit / delete-with-confirmation, search by name or mobile, duplicate-phone prevention (handles +91 / 0 prefixes), customer profiles |
| **Measurements** | **Visual designer**: SVG body figure with clickable points, connector lines to the matching field, garment selector (Blouse, Shirt, Pant, Kurta, Suit, Blazer, Custom) shows only relevant fields, inches ⇄ cm, custom fields, notes, range validation |
| History | Every save creates a new dated session — **old measurements are never overwritten**. Load any old session back into the form, compare old vs new with per-measurement deltas |
| Orders | Create, due dates, overdue flags, status flow (Pending → In Progress → Ready → Delivered / Cancelled) |
| Appointments | Website bookings listed in the admin (read API is login-protected) |
| Storage | Google Sheets (service account, backend-only) when configured, otherwise local SQLite — the app always works |
| Security | Server-side auth on every /api route, input validation + size limits, CORS allowlist, CSRF header rule for cookie sessions, XSS-escaped UI, no secrets in frontend, formula-safe Sheets writes |

---

## 1 · Run it locally (2 minutes)

```bash
cd backend
pip install -r requirements.txt

# minimal config — create backend/../.env or export these:
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD='a-long-password-you-remember'
export SESSION_SECRET='any-long-random-string'      # e.g. python3 -c "import secrets;print(secrets.token_hex(32))"

python server.py            # serves website + login + admin on http://localhost:8000
```

Open `http://localhost:8000` → website · `http://localhost:8000/login.html` → admin login.

Run the test suite:

```bash
cd backend && python -m unittest discover -s tests -v   # 19 tests
```

## 2 · Google Sheets as the database (recommended)

The backend writes to **your own private Google Sheet** using a **service account**
(the browser never sees any Google credential).

### Step-by-step

1. **Create the sheet** — new Google Spreadsheet, e.g. “Adarsh Boutique Data”. Copy its ID
   from the URL: `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`.
2. **Enable the API** — [Google Cloud Console](https://console.cloud.google.com) → create/select
   a project → *APIs & Services → Library* → enable **Google Sheets API** (and **Google Drive API**).
3. **Service account** — *APIs & Services → Credentials → Create credentials → Service account*.
   Open it → *Keys → Add key → Create new key → JSON*. A `.json` file downloads — **keep it secret**.
4. **Share the sheet** — in the spreadsheet, *Share* → add the service-account email
   (`something@your-project.iam.gserviceaccount.com`) as **Editor**.
5. **Configure the backend** (Render/Railway dashboard → Environment, or your `.env`):

```bash
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"...", ... entire JSON on one line ...}
GOOGLE_SHEET_ID=THIS_PART_from_step_1
```

The backend creates the tabs automatically on first run:

- **Clients** — ClientID, Name, Phone, Email, Address, CreatedAt, UpdatedAt
- **Measurements** — ClientID, GarmentType, ValuesJSON, Notes, UnitUsed, UpdatedAt (current set)
- **MeasurementHistory** — SessionID, ClientID, SavedAt, GarmentType, ValuesJSON, Notes, UnitUsed (append-only)
- **Orders** — OrderID, ClientID, Garment, Notes, DueDate, Status, CreatedAt, UpdatedAt
- **Appointments** — mirror of every website booking that goes through the backend

Without these variables the system uses **local SQLite** instead — everything still works,
data just stays on the server disk (use Render's persistent disk).

## 3 · Deploying

### Backend (Render — existing config preserved)

`backend/render.yaml` already declares the service. In the Render dashboard set:

- `ADMIN_USERNAME`, `ADMIN_PASSWORD` (or `ADMIN_PASSWORD_HASH` — generate with
  `python backend/hash_password.py`, recommended), `SESSION_SECRET`
- `ALLOWED_ORIGINS=https://client-website-theta.vercel.app` (comma-separated list; the
  Vercel preview URL too if you use previews)
- `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEET_ID` (optional — enables Sheets storage)
- Keep the persistent disk mounted (`APP_DATA_DIR=/var/data/adarsh-boutique`) for SQLite + Excel exports.

Build/start commands are unchanged: `pip install -r requirements.txt` / `python server.py`.

### Frontend (Vercel — existing config preserved)

- The public site deploys exactly as before; `appointmentEndpoint` in `config.js` is untouched.
- For the admin pages set `apiBase` in `frontend/config.js` to your backend URL
  (e.g. `https://adarsh-boutique-backend.onrender.com`). Leave `""` if you serve the
  frontend from the backend itself (Render serves `index.html`, `/login.html`, `/admin.html`
  and their assets directly, so one service can host everything).

## 4 · Architecture notes

```
Browser (website)  ──booking──▶ Google Apps Script ──▶ private Google Sheet   (legacy path, preserved)
Browser (admin)    ──Bearer token──▶ Python backend ──▶ Google Sheets (gspread, service account)
                                            └──▶ SQLite + Excel always (fallback + audit trail)
```

- **Auth** — `POST /api/login` verifies against `ADMIN_USERNAME` + `ADMIN_PASSWORD[_HASH]`
  (PBKDF2-SHA256, constant-time compares), issues `base64url(payload).hmac` tokens with
  `exp`/`jti`; logout revokes the token id; sessions auto-expire (default 8 h).
- **Authorization** — every `/api/clients|orders|appointments|stats|me` route requires a
  valid admin token **server-side**; the frontend only decides where to send you.
- **CSRF** — cookie-authenticated write requests must send `X-Requested-With: fetch`
  (cross-site pages cannot add custom headers without passing CORS). Bearer clients are immune.
- **Rate limits** — login 5 failures / 15 min / IP; bookings 40 / h / IP (existing).
- **CORS** — only origins in `ALLOWED_ORIGINS` receive CORS grants; preflight handled.
- **Input validation** — names/phones/emails/measurements validated + length-capped on the
  server; all Sheets cells written as literal strings (no formula injection); UI escapes all
  user data (no XSS).
- **Privacy** — no customer data ever appears in logs; static handler serves an exact
  whitelist of files (no directory traversal).

## 5 · Daily use (for the shop owner)

1. Open the site → **login.html** → sign in.
2. **Customers → Add new customer** (name + mobile is enough to start).
3. Open a customer → pick a garment → click points on the figure or type in the boxes → **Save**.
4. Next visit: open the same profile, adjust values, **Save** again → the old session is kept
   in *Measurement history*; use **Compare** to see what changed (e.g. Chest 36″ → 36.5″, + 0.5″).
5. **Orders** tab: create the order, set a due date, update status as you stitch.

## 6 · Credits & license

Built for Adarsh Boutique (Kandivali West, Mumbai). Python stdlib + openpyxl + gspread +
google-auth only. Fonts are system fonts; the site design is original.
