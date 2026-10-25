# DiensteManager

Staff planning with external units: external units see our duties with open staffing
needs and report people for them. The duties are going to be imported from HiOrg-Server.

**Status:** user management, sign-in (local and with HiOrg-Server), invitations, units,
duties with release and reports, email notifications, emails from dispatchers, duty pay
and billing with PDF statements, German/English UI and the Docker setup are done. Importing duties from HiOrg-Server comes next.

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
| **Dispatcher** (internal) | create and view all units, invite and block unit leads and members; create, release and cancel duties, confirm or decline reports, add helpers themselves; send emails to the units; create billings |
| **Unit lead** | view their own unit, invite and block unit leads and members there; report helpers, withdraw any report of the unit; see the duty pay and billings of the unit, enter its bank details |
| **Member** | see the duties offered to their unit, report helpers, withdraw their own reports |

The rules live in `accounts/permissions.py`, `duties/permissions.py` and
`billing/permissions.py`.

## Duties and reports

- **Duties** are entered by dispatchers under *Duties → Add duty* (title, location, start,
  end, number of helpers needed, description). Each duty records its source (*entered
  manually*, later *HiOrg-Server*) for the upcoming import.
- **Release:** new duties are drafts that only dispatchers see. Units see a duty once it
  has been released ("Save and release" or "Release" on the duty).
- **Offered to:** all units, or only selected ones. Other units neither see the duty nor
  get notified. A unit that has reported helpers must stay selected.
- **Reports:** unit leads and members report helpers (name, optional qualification and
  note; several at once) until the duty starts. Units only see their own reports plus
  the overall staffing (confirmed / needed). Members withdraw their own reports, unit
  leads every report of their unit. A person can only be reported once per duty and unit.
- **Decisions:** dispatchers confirm or decline each report (or "Confirm all pending").
- **Helpers who register with a dispatcher directly** (e.g. by email) are entered by the
  dispatcher under "Add helpers" on the duty: unit plus names, confirmed right away; the
  unit leads are notified.
- Released duties are **cancelled** instead of deleted; only drafts can be deleted.

## Email notifications

Every recipient gets their own email in the language of their account (or the default
language). The person who triggered a notification never receives it.

| Event | Recipients |
|---|---|
| Duty released, or offered to additional units | active accounts of those units with *New duties for my unit* switched on |
| Helpers reported or withdrawn | dispatchers and admins with *Reports* switched on |
| Report confirmed or declined; helpers added by a dispatcher | unit leads of the unit and whoever made the report, with *Reports* switched on |
| Title, time, location or duty pay of a released duty changed; duty cancelled | unit leads and reporters of units with active reports, with *Reports* switched on |
| Billing created (PDF attached), paid or cancelled | unit leads of the unit with *Billings* switched on |

The switches are under *My account → Email notifications* and are on by default. The
emails are sent right away during the request; if the mail server is unreachable, the
change is saved anyway and a warning is shown.

## Duty pay and billing

- **Duty pay** ("Dienstgeld") is set per duty: none, an amount **per helper hour**, or a
  **flat rate per helper**. The units see it on the duty and in the duty list.
- **Duty pay overview** (*Duty pay* for unit leads, *Billing → unit* for dispatchers):
  per year every confirmed helper of the unit with the amount and its state – upcoming,
  not billed yet, billed, paid – plus the totals and the unit's statements. Amounts not
  billed yet are estimates (hourly pay with the full length of the duty).
- **Bank details:** unit leads (and dispatchers) enter account holder, IBAN (checked with
  the ISO 13616 check digits), optional BIC and bank. Only dispatchers and the unit leads
  of the unit see them.
- **Billing** (dispatchers, *Billing*): lists the units with something to bill, i.e.
  confirmed helpers of finished, released duties with duty pay that are not billed yet.
  When creating a statement, untick helpers who did not come and correct the hours for
  hourly pay. Statements are numbered per year (`2026-001`, …) and keep copies of the
  items, rates and bank details, so later changes do not alter them. A statement is
  *open* until it is marked as *paid*; open statements can be *cancelled*, which makes
  their helpers billable again.
- **PDF:** every statement can be downloaded as a PDF (A4, in the language of the
  person downloading it) and is attached to the email to the unit leads. It is created
  with [ReportLab](https://www.reportlab.com/) and embeds DejaVu Sans
  (`billing/fonts/`, Bitstream Vera licence), so that names like "Şahin" or "Łukasz"
  print correctly.

## Emails from dispatchers

Dispatchers write emails to the units under *Emails → Write an email*, or with the
buttons on a duty ("Email the units") and on a unit.

- **Recipients:** the unit leads only, or all accounts (unit leads and members); of all
  units or only selected ones. Blocked accounts are left out; the notification settings
  do not apply. The form shows how many people the email reaches.
- **Sender:** every recipient gets their own email from `DEFAULT_FROM_EMAIL`, shown as
  "*Name of the dispatcher* via DiensteManager". **Reply-To is the dispatcher's
  address**, so answers go directly to them. (Sending from the dispatcher's own address
  would fail SPF/DMARC checks.)
- Opened from a duty, the units with reported helpers (otherwise those the duty is
  offered to) are preselected and the email links to the duty.
- Optionally a copy goes to the dispatcher. Sent emails are listed with their recipients
  under *Emails*; an email is only recorded if it could be sent.

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
docker compose pull                   # the released image from ghcr.io …
docker compose up -d                  # … or build it yourself: docker compose up -d --build
docker compose exec web python manage.py createsuperuser
docker compose logs -f web            # logs (and emails, without SMTP)
```

- The image is `ghcr.io/jannikemmerich/dienstemanager`; pin a version with
  `DIENSTEMANAGER_VERSION` in `.env` (see "Versions, releases and updates"). While the
  repository is private, the server needs `docker login ghcr.io` with a personal access
  token that has the `read:packages` scope.
- Migrations run automatically on start; Caddy obtains the certificate by itself.
- **Update:** see "Versions, releases and updates" below.
- **Backup:** `docker compose exec db pg_dump -U dienstemanager dienstemanager > backup.sql`.
- **HSTS** is set to one year (`DJANGO_SECURE_HSTS_SECONDS`); set it to `0` for a local
  test with `DOMAIN=localhost`.
- `manage.py check --deploy` only reports W005/W021 (HSTS for subdomains/preload). Both
  affect the whole domain and are off on purpose; enable them via
  `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` if appropriate.

## Versions, releases and updates

The version follows [Semantic Versioning](https://semver.org/) and is kept in one place
only: `version` in `pyproject.toml`. The app shows it in the page footer (with the Git
commit for images built by CI). All changes are listed in `CHANGELOG.md`.

- **MAJOR** (1.0.0 → 2.0.0): operators must act, e.g. renamed or new required settings,
  removed features, migrations that need manual steps.
- **MINOR** (0.2.0 → 0.3.0): new features; updating needs nothing but the new image.
- **PATCH** (0.2.0 → 0.2.1): bug fixes only.

### Development and CI

`master` is always deployable; changes arrive as pull requests. The workflow
`.github/workflows/ci.yml` runs for every pull request and push: ruff, compiling the
translations, a check for missing migrations and the tests (Python 3.12 with SQLite,
Python 3.14 with PostgreSQL 18). Then it builds the Docker image; pull requests only
check that it builds.

### Docker images

Pushes to `master` and version tags publish the image for `linux/amd64` and
`linux/arm64` to the GitHub Container Registry:

| Tag | Points to | Kept |
|---|---|---|
| `latest` | the newest release (not pre-releases) | always |
| `1.2.3`, `1.2`, `1` | that release, the newest patch of 1.2, the newest 1.x (`1` only from 1.0 on) | always |
| `edge` | the newest commit on `master` | always |
| `sha-abc1234` | one commit on `master` | 30 days |

`.github/workflows/cleanup-images.yml` deletes the `sha-…` tags and untagged leftovers
older than 30 days every Monday (start it by hand with "dry run" to see what it would
do). The workflow needs the package's *Manage Actions access* to give this repository
the *Admin* role. Dependabot (`.github/dependabot.yml`) proposes updates of the Python
packages, the GitHub Actions and the base image weekly.

### Making a release

1. Pull request "Release 1.2.3": set `version = "1.2.3"` in `pyproject.toml` and rename
   `## [Unreleased]` in `CHANGELOG.md` to `## [1.2.3] – <date>` (start a new empty
   `## [Unreleased]` above it). Merge it.
2. Tag the merge commit and push the tag:
   `git tag -a v1.2.3 -m "1.2.3" && git push origin v1.2.3`
   (or create the release with the new tag `v1.2.3` on GitHub).
3. CI checks that the tag matches `pyproject.toml` and `CHANGELOG.md`, runs the tests,
   publishes the image `1.2.3` (plus `1.2`, `1`, `latest`) and creates the GitHub release
   with the changelog section as release notes. Tags like `v1.3.0-rc.1` become
   pre-releases without `latest`.

### Updating an installation

1. Read the changelog between your version and the new one, especially for a new MAJOR
   version.
2. Back up the database (see above).
3. Set `DIENSTEMANAGER_VERSION` in `.env` to the new version (or keep `latest`), then
   `docker compose pull && docker compose up -d`. Migrations run on start.
4. Going back: migrations cannot be undone automatically. Restore the backup and start
   the previous version.

Only the newest release gets fixes; there are no maintenance branches for older versions.

## Project layout

```
config/      settings (all via environment variables), URLs, WSGI
accounts/    users, units, invitations, permissions, HiOrg sign-in adapter, language
duties/      duties, reports, their permissions and email notifications
mailings/    emails from dispatchers to the units (Reply-To: the dispatcher)
billing/     duty pay overview, bank details, billings and their PDF
core/        overview, health check (/healthz), form helpers
templates/   layout, own pages, Bootstrap versions of the allauth pages
locale/      German translations
static/      app.css and vendored libraries (Bootstrap, htmx)
tests/       pytest tests incl. a simulated HiOrg-Server
docker/      container entry point
.github/     CI and release workflow, image clean-up, Dependabot, release notes script
```

## Next steps

1. **Import duties from HiOrg-Server.** The public REST API has no duties. The EFS
   interface is an option: API key under *System → Settings → Manage API keys*, each
   duty must be released for retrieval by connected EFS. It returns period, location,
   positions and assigned staff, but no staffing target – clarify its use with HiOrg
   support first. Imported duties get `source = hiorg` and the HiOrg ID in
   `external_id` (unique per source), arrive as drafts and are released as usual.
2. **Staffing needs per qualification** (e.g. 2 paramedics, 4 first aiders) instead of
   a single number, possibly with a rate per qualification.
3. **Sending emails in the background** (queue or digest) once there are many units
   and accounts.
