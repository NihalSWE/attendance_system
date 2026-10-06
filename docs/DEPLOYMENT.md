# Deployment (workforce.iglweb.com)

> **DO NOT DEPLOY main after `cc0dd12` to the live server yet (2026-09-19).**
> The company-level departments change (Nihal, `feature/company-departments`)
> has a migration that does not keep existing data: existing departments get
> `company=1, branch=1` and the old links fail, so `migrate` either stops with
> an IntegrityError or needs every table emptied. The live server holds the
> client's data (their SenseFace 3A reports there). Deploy only after a
> data-keeping migration (copy each company's departments/designations to the
> new tables, re-point employees, shifts, device links and permissions, then drop
> the old tables) replaces or follows it. Developer PCs were emptied on purpose.
>
> Also new since the last deploy: `BIOMETRIC_TEMPLATE_KEY` in `.env` (see
> `.env.example`; a fresh key per server) and `pip install -r requirements.txt`
> (`cryptography`).

Set up 2026-09-15 on the Ubuntu 24.04 server at 103.86.193.26, alongside a
stopped chat application that keeps its own files.

| What | Where |
|---|---|
| Code | `/opt/attendance` |
| Settings | `/opt/attendance/.env` (root:www-data, 640) |
| Virtual environment | `/opt/attendance/venv` (Python 3.12; Django 6.1 needs 3.12+) |
| Static files | `/opt/attendance/staticfiles` |
| Uploads (company logos) | `/opt/attendance/media` |
| Web service | `/etc/systemd/system/attendance_web.service` — Gunicorn on 127.0.0.1:8000, as www-data |
| Nginx site | `/etc/nginx/sites-available/attendance` (+ symlink in `sites-enabled`) |
| Certificate | Let's Encrypt, `/etc/letsencrypt/live/workforce.iglweb.com/`, renewed by certbot's timer |
| Database | PostgreSQL `attendance_system`, role `attendance`, extension `btree_gist` |

DNS: an **A record** `workforce` → `103.86.193.26` in cPanel's Zone Editor
(the domain is not hosted on cPanel; a CNAME cannot point at an IP).

## .env on the server

```ini
DEBUG=False
SECRET_KEY=<generated>
ALLOWED_HOSTS=workforce.iglweb.com
CSRF_TRUSTED_ORIGINS=https://workforce.iglweb.com
POSTGRES_DB=attendance_system
POSTGRES_USER=attendance
POSTGRES_PASSWORD=<set on the server>
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
DB_CONN_MAX_AGE=60
```

## Nginx

The site proxies to Gunicorn and serves the two file locations itself. With
company logos (2026-09-16) the `/media/` block is required:

```nginx
location /static/ { alias /opt/attendance/staticfiles/; }
location /media/  { alias /opt/attendance/media/; }
```

`client_max_body_size 20m;` covers device uploads and logo uploads.
`/media/` must be added to the existing site and `media/` must be writable by
www-data (`chown -R www-data:www-data /opt/attendance/media`).

## Deploying a new version

```
cd /opt/attendance
git pull
venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py createcachetable
venv/bin/python manage.py collectstatic --noinput
systemctl restart attendance_web
```

All in one line:

```
venv/bin/pip install -r requirements.txt && venv/bin/python manage.py migrate && venv/bin/python manage.py createcachetable && venv/bin/python manage.py collectstatic --noinput && systemctl restart attendance_web
```

`createcachetable` makes the API's rate-limit table (`api_cache`) the first
time and does nothing after; without it every API request fails.

`BIOMETRIC_TEMPLATE_KEY` must be in `/opt/attendance/.env` before templates can
be saved (its own key per server; see `.env.example`), and `cryptography` comes
from `requirements.txt`.

**Changing `SECRET_KEY` makes every company's saved mail password unreadable.**
Organisation → Email settings encrypts the password with a key derived from
`SECRET_KEY` (`organization/mail_settings.py`), so after a rotation no company
can send email until its owner or admin types the password again. It fails
loudly - sending and the test say "Enter the password again" - but tell the
companies before you rotate. (Rotating `SECRET_KEY` also signs everyone out.)

## The REST API (2026-10-05)

First deploy of the API (`/api/v1/…`, documentation at `/api/docs/`):

1. **`pip install -r requirements.txt` before restarting.** Passwords are now
   hashed with Argon2 (`argon2-cffi`); without it nobody can sign in.
   Existing passwords keep working and are upgraded at the next sign-in.
2. **`createcachetable`** once (see above).
3. **`.env`** - add:

   ```ini
   # Nginx is in front: read the client's address one proxy deep
   # (the login lockout is per address - without it everyone shares one).
   API_PROXY_COUNT=1
   # Nginx terminates HTTPS: trust its X-Forwarded-Proto.
   SECURE_PROXY_SSL=True
   # Browsers use https only. Start with an hour; raise to 31536000 (a year)
   # once the site has run on https without trouble.
   SECURE_HSTS_SECONDS=3600
   ```

   Optional: `API_CORS_ORIGINS` (a React/Vue/Next frontend's address, when
   there is one), `API_PASSWORD_RESET_URL` (that frontend's reset page), and
   `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` /
   `EMAIL_USE_TLS` / `DEFAULT_FROM_EMAIL` so password-reset and two-step
   codes by email are sent (without them the API answers that email is not
   available).
4. Check: `curl -s https://workforce.iglweb.com/api/v1/ping` answers
   `{"status": "ok", …}`; `/api/docs/` opens; the panel still signs in.

An owner or company administrator who uses the API must turn on two-step
login there (an authenticator app, or email codes); the panels are unchanged.

## Attendance is read from what is saved (2026-10-06)

The attendance screens no longer work the whole month out again on every load:
they read the saved days. Every change - a scan, a fix, leave, a schedule -
rewrites its days at once. What time alone changes is waited for one
employee-day at a time: each day not final records its next change (its
shift's end, then its close; table `attendance_due`), and only the people whose
moment has come are rebuilt - about twice a day each, however many people
there are. A device's check-in runs this in the background (a check costs
under a millisecond when nothing is due).

Deploying it needs only the usual `migrate` (tables `attendance_day_build` and
`attendance_due`; the first load of each month builds it once).
**Optional**, for companies whose devices are offline: a timer that runs
`settle_attendance` every minute (cheap: it does only what is due).

```
cat > /etc/systemd/system/attendance_settle.service <<'UNIT'
[Unit]
Description=Attendance: build today's and yesterday's days

[Service]
Type=oneshot
User=www-data
WorkingDirectory=/opt/attendance
ExecStart=/opt/attendance/venv/bin/python manage.py settle_attendance
UNIT

cat > /etc/systemd/system/attendance_settle.timer <<'UNIT'
[Unit]
Description=Attendance: rebuild the days whose shift ended or which closed

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload && systemctl enable --now attendance_settle.timer
```

Check it: `systemctl list-timers attendance_settle.timer` and
`journalctl -u attendance_settle -n 20 --no-pager`.

## Restarting the services

| When | Command |
|---|---|
| After code, `.env` or a migration | `systemctl restart attendance_web` |
| After an Nginx config or certificate change | `nginx -t && systemctl reload nginx` |
| Database (rarely) | `systemctl restart postgresql@16-main` — **not** `postgresql`, see below |
| Everything, in order | `systemctl restart postgresql@16-main && systemctl restart attendance_web && systemctl reload nginx` |
| Are they up? | `systemctl status attendance_web nginx postgresql@16-main --no-pager` |

**`systemctl restart postgresql` does nothing.** On Ubuntu that unit is an empty
wrapper (`ExecStart=/bin/true`, and it reports `active (exited)`); the cluster
itself is `postgresql@16-main`. Restarting the wrapper looks like it worked and
leaves the database exactly as it was — which cost twenty minutes on 2026-09-26
while the site was down. `pg_lsclusters` prints the version if it ever differs.

## Devices

A terminal is pointed at the server on the device itself (COMM → Cloud Server:
`workforce.iglweb.com`, port 443, HTTPS on). The software's "Change server
address" only moves a device that already talks to *that* server.

A device is refused (401) until it is registered here with its exact serial, so
after rebuilding the database register the terminals promptly; a 2.x device
hands over the scans it kept back when asked (Fetch attendance history).

## Logs

Recent, then errors only, then since the last restart or a time:

```
journalctl -u attendance_web -n 200 --no-pager
journalctl -u attendance_web -n 500 --no-pager | grep -iE "error|traceback|exception|warning"
journalctl -u attendance_web -b --no-pager
journalctl -u attendance_web --since "1 hour ago" --no-pager
tail -n 100 /var/log/nginx/error.log
tail -n 100 /var/log/nginx/access.log
journalctl -u postgresql@16-main -n 100 --no-pager
```

Live (Ctrl+C stops):

```
journalctl -u attendance_web -f
journalctl -u attendance_web -f | grep -iE "error|traceback|exception"
tail -f /var/log/nginx/error.log
journalctl -u attendance_web -f & tail -f /var/log/nginx/error.log
```

**Device traffic only** — each check-in, upload and command answer as it
happens; keep this running in one SSH window while testing a terminal:

```
journalctl -u attendance_web -f | grep -i iclock
```

## Rebuilding the database (destructive)

Needed once for the company-level departments change (see the warning at the
top). It keeps nothing: companies, employees, device *registrations*,
attendance, leave and salary all go. The device catalogue (vendors and models)
is re-created by the migrations themselves, and the terminals keep their own
users and templates.

```
cd /opt/attendance
sudo -u postgres pg_dump attendance_system > /opt/attendance_backup_$(date +%F).sql
git pull
venv/bin/pip install -r requirements.txt
# add BIOMETRIC_TEMPLATE_KEY=<new key> to .env first:
venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
sudo -u postgres psql attendance_system -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION attendance; GRANT ALL ON SCHEMA public TO attendance;"
sudo -u postgres psql attendance_system -c "CREATE EXTENSION IF NOT EXISTS btree_gist;"
venv/bin/python manage.py migrate
venv/bin/python manage.py createsuperuser
venv/bin/python manage.py collectstatic --noinput
systemctl restart attendance_web
```

Then sign in, create the company and branch, register each terminal with its
exact serial, and press Refresh user list, Fetch attendance history and Save
fingerprints and faces on each.


## "could not open shared memory segment" — every page 500s

```
FATAL: could not open shared memory segment "/PostgreSQL.160964730":
No such file or directory
```

PostgreSQL is running and answering — that FATAL comes from it — but no backend
can attach its shared memory, so every query dies and Django returns 500. The
services are all up; they simply have no database.

**Recover:**

```bash
systemctl restart postgresql@16-main && systemctl restart attendance_web
```

**Why it happened (2026-09-26).** systemd's `RemoveIPC` defaults to **yes**, and
`/etc/systemd/logind.conf` had the line commented out, so the default applied.
logind then deletes a user's IPC objects once their last session ends — so
logging in as the `postgres` user (`su - postgres`, `sudo -iu postgres`) and
logging out again wipes the running cluster's segments from under it. Set on the
server so it cannot recur:

```
RemoveIPC=no
```

Check `/dev/shm` before believing anything else about size: this looks like a
space problem and is not one. Here it was 63G with 1% used.
