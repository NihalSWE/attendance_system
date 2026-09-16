# Deployment (workforce.iglweb.com)

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

## Devices

A terminal is pointed at the server on the device itself (COMM → Cloud Server:
`workforce.iglweb.com`, port 443, HTTPS on). The software's "Change server
address" only moves a device that already talks to *that* server.

## Logs

```
journalctl -u attendance_web -f
tail -f /var/log/nginx/error.log
```
