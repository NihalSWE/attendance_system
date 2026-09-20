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
venv/bin/python manage.py collectstatic --noinput
systemctl restart attendance_web
```

All four in one line:

```
venv/bin/pip install -r requirements.txt && venv/bin/python manage.py migrate && venv/bin/python manage.py collectstatic --noinput && systemctl restart attendance_web
```

`BIOMETRIC_TEMPLATE_KEY` must be in `/opt/attendance/.env` before templates can
be saved (its own key per server; see `.env.example`), and `cryptography` comes
from `requirements.txt`.

## Restarting the services

| When | Command |
|---|---|
| After code, `.env` or a migration | `systemctl restart attendance_web` |
| After an Nginx config or certificate change | `nginx -t && systemctl reload nginx` |
| Database (rarely) | `systemctl restart postgresql` |
| Everything, in order | `systemctl restart postgresql && systemctl restart attendance_web && systemctl reload nginx` |
| Are they up? | `systemctl status attendance_web nginx postgresql --no-pager` |

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
journalctl -u postgresql -n 100 --no-pager
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
