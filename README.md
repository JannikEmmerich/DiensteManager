# DiensteManager

Staff planning with external units: external units see our duties with open staffing
needs and report people for them. The duties are going to be imported from HiOrg-Server.

**Status: skeleton.** User management, sign-in (local and with HiOrg-Server),
invitations, units, German/English UI and the Docker setup are done. Duties, staffing
needs and reports come next.

## Stack

- **Django 6.1** (Python ≥ 3.12, developed with 3.14), server-rendered templates,
  **Bootstrap 5.3** and **htmx 2** (both vendored under `static/vendor/`, no CDN calls).
- **django-allauth** for sign-in, password reset, email confirmation, two-factor
  authentication (TOTP) and "Sign in with HiOrg-Server" (OpenID Connect).
- **PostgreSQL 18** in production, SQLite locally.
- **Docker Compose** with gunicorn and Caddy as reverse proxy with automatic HTTPS.

## Roles and permissions

| Role | May |
|---|---|
| **Admin** (superuser) | everything incl. the Django admin `/admin/`; the only role that invites or blocks dispatchers |
| **Dispatcher** (internal) | create and view all units, invite and block unit leads and members |
| **Unit lead** | view their own unit, invite and block unit leads and members there |
| **Member** | overview and own account (later: see duties, report people) |

The rules live in `accounts/permissions.py`.

## Sign-in and accounts

- **No public sign-up.** Accounts are only created through invitation links sent by
  email: valid for 14 days (`INVITATION_VALIDITY_DAYS`), single use, only a hash of the
  link is stored.
- When accepting, the person chooses **their own password** or **"Sign in with
  HiOrg-Server"**.
- **Linking works both ways:** local accounts link their HiOrg account under
  *My account → HiOrg link* (after re-entering the password); accounts created via HiOrg
  can additionally set a password. Unlinking requires a password.
- **No automatic linking via matching email addresses** (HiOrg reports addresses as
  unverified). A HiOrg sign-in without an invitation and without a link is rejected.
- **Data minimisation:** of the HiOrg sign-in only `sub`, name, user name and email are
  stored; date of birth, address, phone and gender are discarded; HiOrg tokens are not
  stored.
- The Django admin uses the same sign-in including two-factor authentication.

## Languages

- The UI is available in **German** (default) and **English**. Code, comments and
  translation keys are English; the German texts live in
  `locale/de/LC_MESSAGES/django.po`.
- Visitors get the language of their browser and can switch it in the navigation bar.
  Signed-in users can set a language (or "automatic") under *My account*; the switch in
  the navigation bar stores the choice on the account as well. Emails to an account
  (password reset, notifications) use that account's language.
- Invitations have a language: it is used for the invitation email and becomes the
  initial language of the new account.

After changing texts:

```bash
.venv/bin/python manage.py makemessages -l de --ignore=.venv --ignore=staticfiles --ignore=static
# translate the new entries in locale/de/LC_MESSAGES/django.po, then:
.venv/bin/python manage.py compilemessages --ignore=.venv
```

The compiled `.mo` files are not committed; the Docker build compiles them. The tests
fail if the German catalogue has untranslated or fuzzy entries or if the compiled
catalogue is older than the `.po` file.

## Local development

Requires Python ≥ 3.12 and GNU gettext (`msgfmt`, e.g. `sudo dnf install gettext`).

```bash
cd ~/PycharmProjects/DiensteManager
python3 -m venv .venv                 # if it does not exist yet
.venv/bin/pip install -e . --group dev
cp .env.example .env                  # locally: DJANGO_DEBUG=True
.venv/bin/python manage.py compilemessages --ignore=.venv
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

Then open <http://localhost:8000>. Without `EMAIL_HOST`, all emails are printed to the
console – including invitation links.

**PyCharm:** the project was renamed; the configured interpreter still points to
`~/PycharmProjects/PythonProject/.venv`. Select
`~/PycharmProjects/DiensteManager/.venv/bin/python` under *Settings → Python
Interpreter → Add Interpreter → Existing*.

### Tests and code style

```bash
.venv/bin/python -m pytest
# Against PostgreSQL (row locking behaves differently from SQLite):
DATABASE_URL=postgres://user:pass@localhost:5432/db .venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The tests run with English as the default language; `tests/test_i18n.py` covers German.
HiOrg-Server is simulated in `tests/conftest.py`, so the complete OpenID Connect flow is
tested without real credentials.

## Setting up "Sign in with HiOrg-Server"

HiOrg-Server is an OpenID Connect provider
(discovery: `https://api.hiorg-server.de/.well-known/openid-configuration`).
Client credentials are only issued on request by email to **support@hiorg-server.de**.
The request needs:

- client name: e.g. *DiensteManager*
- redirect URI: `https://<your-domain>/accounts/oidc/hiorg/login/callback/`
- contact email address
- organisation code(s), only if access should be restricted to them – do **not**
  restrict it if members of other HiOrg organisations should be able to sign in
- purpose and environment: sign-in for a portal in which external units report staff
  for our duties; scopes `openid`, `profile`; own server, Django app behind HTTPS

Then set `HIORG_CLIENT_ID` and `HIORG_CLIENT_SECRET` in `.env`. The HiOrg button then
appears on the sign-in page, on the invitation page and under *My account*.
`HIORG_OV` optionally preselects an organisation code on the HiOrg sign-in page.

## Running with Docker Compose

Requires a server with Docker, a DNS record pointing to it and free ports 80/443.

```bash
cp .env.example .env
# Set at least:
#   DJANGO_DEBUG=False
#   DJANGO_SECRET_KEY=<long and random>
#   DOMAIN=duties.example.org
#   POSTGRES_PASSWORD=<letters and digits only>
#   EMAIL_HOST=...   (otherwise emails only end up in the log)
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
docker compose logs -f web            # logs (and emails, without SMTP)
```

- Migrations run automatically on start; Caddy obtains the certificate by itself.
- **Update:** `git pull && docker compose up -d --build`.
- **Backup:** `docker compose exec db pg_dump -U dienstemanager dienstemanager > backup.sql`.
- **HSTS** is set to one year (`DJANGO_SECURE_HSTS_SECONDS`); set it to `0` for a local
  test with `DOMAIN=localhost`.
- `manage.py check --deploy` only reports W005/W021 (HSTS for subdomains/preload). Both
  affect the whole domain and are off on purpose; enable them via
  `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` if appropriate.

## Project layout

```
config/      settings (all via environment variables), URLs, WSGI
accounts/    users, units, invitations, permissions, HiOrg sign-in adapter, language
core/        overview, health check (/healthz), form helpers
templates/   layout, own pages, Bootstrap versions of the allauth pages
locale/      German translations
static/      app.css and vendored libraries (Bootstrap, htmx)
tests/       pytest tests incl. a simulated HiOrg-Server
docker/      container entry point
```

## Next steps

1. **Import duties from HiOrg-Server.** The public REST API has no duties. The EFS
   interface is an option: API key under *System → Settings → Manage API keys*, each
   duty must be released for retrieval by connected EFS. It returns period, location,
   positions and assigned staff, but no staffing target – clarify its use with HiOrg
   support first.
2. **Staffing needs per duty** (number, qualification) maintained in the portal.
3. **Reports:** units report people for duties, the dispatchers confirm.
