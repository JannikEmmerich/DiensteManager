# DiensteManager

Staff planning with external units: external units see our duties with open staffing
needs and report people for them. The duties are going to be imported from HiOrg-Server.

**Status:** user management, sign-in (local and with HiOrg-Server), invitations, units,
duties with release, staffing needs per qualification and reports, email notifications
(in the background, optionally as a daily summary), emails from dispatchers, duty pay and
billing with PDF statements, change history and retention periods, German/English UI and
the Docker setup are done. Importing duties from HiOrg-Server comes next.

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
| **Admin** (superuser) | everything incl. the Django admin `/admin/`; the only role that invites or blocks dispatchers; edits all data of other accounts in the portal (name, email, role incl. dispatcher, unit, language, notifications) |
| **Dispatcher** (internal) | create and view all units, invite and block unit leads and members, switch accounts of all units between unit lead and member; create, release and cancel duties, confirm or decline reports, add helpers themselves; send emails to the units; create billings; edit the units' names and bank details |
| **Unit lead** | view their own unit, invite and block unit leads and members there and switch them between unit lead and member; report helpers, withdraw any report of the unit; see the duty pay and billings of the unit, enter its bank details |
| **Member** | see the duties offered to their unit, report helpers, withdraw their own reports |

**Changing roles:** on the unit's page (*Units* or *My unit*), the role of each account
can be switched between unit lead and member. Nobody changes their own role. Admins
also get *Edit* for every other account – on the unit's page, and for dispatchers and
admins under *Units → Internal accounts* – to change name, email address (it counts as
confirmed and is used for signing in from then on), role (making someone a dispatcher
or back; internal accounts belong to no unit), unit, language and notifications.
Passwords and the admin status itself stay in the Django admin.

The rules live in `accounts/permissions.py`, `duties/permissions.py` and
`billing/permissions.py`. Admins and dispatchers must use two-factor authentication
(see "Sign-in and accounts").

## Duties and reports

- **Duties** are entered by dispatchers under *Duties → Add duty* (title, location, start,
  end, helpers needed, description). Each duty records its source (*entered manually*,
  later *HiOrg-Server*) for the upcoming import.
- **Helpers needed** are listed per qualification, e.g. 2 × paramedic and 4 × first
  aider, or as a number of helpers with *any qualification*: one row per qualification,
  more with "Add another qualification". Each of these positions can have its own rate
  (see "Duty pay"; the rates are only shown with duty pay). Dispatchers maintain the
  qualifications under *Duties → Qualifications* (name, abbreviation, order; new ones
  come last); qualifications in use cannot be deleted, only deactivated, and a position
  with reported helpers cannot be removed.
- **Also counts as:** a qualification can count as another one, e.g. emergency
  paramedic → paramedic → first aider. Helpers then also fill the places of the lower
  qualifications down the chain, never the other way round: with 2 × first aider and
  1 × paramedic needed, 1 first aider and 2 paramedics fill all places. The staffing
  per position, the open places when reporting and the colours follow this; each
  helper keeps the qualification they were reported with (and its rate). Confirmed
  helpers are placed before pending ones; helpers who fit nowhere overstaff their own
  position.
- **Release:** new duties are drafts that only dispatchers see. Units see a duty once it
  has been released ("Save and release" or "Release" on the duty).
- **Offered to:** all units, or only selected ones. Other units neither see the duty nor
  get notified. A unit that has reported helpers must stay selected.
- **Report deadline** (optional, per duty): until then units report helpers and withdraw
  reports themselves; without one, until the duty starts. It is shown in the list, on
  the duty and in the emails; changing it notifies the units with reports.
- **Reports:** unit leads and members report helpers until the report deadline: name,
  qualification and an optional note, one row per helper, more with "Add another
  helper". The qualification is one of those the duty asks for, each with the places
  still open (e.g. "Paramedic (RS) – 2 open"); the first one with open places is
  preselected. With a single qualification there is nothing to choose; it is named
  above the form. Units only see their own reports plus the staffing per position. A
  person can only be reported once per duty and unit.
- **Decisions:** dispatchers confirm or decline each report (or "Confirm all pending").
  Only confirmed helpers count as planned (confirmed / needed); pending ones are shown
  as "+N pending".
- **Withdrawing:** members act on the reports they made, unit leads on every report of
  their unit. Reports that are not confirmed yet they withdraw themselves until the
  report deadline. Confirmed reports (and all after the deadline, until the duty ends)
  only the dispatchers can take back: the unit asks for it ("Ask to withdraw", with an
  optional reason), the dispatchers get an email and see the request on the duty and on
  the overview, and then take the report back or keep it – the unit is told either way.
  Dispatchers can also take back a confirmed report on their own.
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
| Helpers reported or withdrawn; a unit asks to withdraw a report | dispatchers and admins with *Reports* switched on |
| Report confirmed, declined or taken back; request to withdraw refused; helpers added by a dispatcher | unit leads of the unit and whoever made the report, with *Reports* switched on |
| Title, time, report deadline, location or duty pay of a released duty changed; duty cancelled | unit leads and reporters of units with active reports, with *Reports* switched on |
| Billing created (PDF attached) or cancelled (not when it is paid) | unit leads of the unit with *Billings* switched on |

The switches are under *My account → Email notifications* and are on by default.

### Sending in the background and daily summary

With `EMAIL_QUEUE=True` (the default with Docker Compose), notifications and emails from
dispatchers are stored in the database and sent by the `worker` container
(`manage.py run_worker`), so pages do not wait for the mail server:

- An email that cannot be sent is tried again after 1, 5, 15, 60 and 240 minutes, then
  it counts as failed. The Django admin lists the emails under *Outgoing emails*;
  "Send again" queues failed ones once more.
- **Daily summary:** under *My account*, everyone can choose *Once a day as a summary*
  instead of *Immediately*. News about new duties, reports, decisions and added helpers
  then arrives in one email a day at `EMAIL_DIGEST_HOUR` (18:00 by default), in the
  account's language. Changed and cancelled duties, billings, changed bank details and
  emails from dispatchers always come right away.
- Invitations and the emails of the sign-in (e.g. password reset, verification codes)
  are always sent right away.
- `SITE_URL` (with Compose: `https://` + `DOMAIN`) is the address for the links in the
  summary.

Without the queue (e.g. locally with `runserver`), emails are sent during the request; if
the mail server is unreachable, the change is saved anyway and a warning is shown. Run
`manage.py run_worker --once` to send queued emails by hand.

## Duty pay and billing

- **Duty pay** ("Dienstgeld") is set per duty: none, an amount **per helper hour**, or a
  **flat rate per helper**. A position can have its own rate (e.g. more for paramedics);
  otherwise the rate of the duty applies. The units see it on the duty and in the duty
  list; statements show the qualification next to the helper.
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

## Data lifecycle

### Change history

Every change of a duty, a report, a billing or a bank account is recorded: who did what
and when, with the old and the new value of each changed field (`lifecycle` app).

- **Duties:** created, changed (with the fields), released, cancelled, deleted; their
  reports: reported, entered by the dispatchers, confirmed, declined, withdrawn. Shown
  under "Change history" on the duty, to dispatchers only (it names helpers of all units).
- **Billings:** created, marked as paid, voided – on the statement, for everyone who can
  see it.
- **Bank accounts:** created and changed, on the duty pay overview. IBANs appear masked
  (`DE89 **** **** **** **30 00`) in the history. A change of account holder, IBAN or
  BIC is emailed to the unit leads and the dispatchers (switch "Billings" or "Changed
  bank details" under *My account*), and creating a statement warns if the bank details
  changed within the last 30 days – a changed IBAN does not go unnoticed, even if the
  only unit lead changed it.

### Retention periods

`manage.py apply_retention` (`--dry-run` shows what it would do) anonymises and deletes
data whose time has come. With Docker Compose, the `worker` container runs it once a
day. The periods are set in `.env`; `0` keeps the data forever.

| Data | What happens | Setting (default) |
|---|---|---|
| Reports (helper name, qualification, note, reason for a withdrawal) | anonymised after the end of the duty; the number of helpers stays. Helpers that can still be billed are kept until their statement is paid – the statement keeps its own copy | `RETENTION_REPORTS_DAYS` (365) |
| Change history | deleted; for anonymised reports right away | `RETENTION_HISTORY_DAYS` (730) |
| Emails from dispatchers | deleted after sending | `RETENTION_MAILINGS_DAYS` (365) |
| Accepted, revoked and expired invitations | deleted after acceptance, revocation or expiry | `RETENTION_INVITATIONS_DAYS` (180) |
| Paid and cancelled billings | deleted after this many full calendar years (accounting records); open ones never. Helpers of duties that old can no longer be billed | `RETENTION_BILLINGS_YEARS` (10) |
| Sent and failed emails of the email queue | deleted; queued ones never | `RETENTION_OUTBOX_DAYS` (30) |

Duties, units, accounts and bank accounts are current data and not deleted
automatically; blocked accounts can be deleted in the Django admin.

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
  under *Emails*; an email is only recorded if it could be sent (or queued, see
  "Sending in the background").

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
- **Two-factor authentication** (authenticator app, plus recovery codes) is available
  to everyone under *My account → Two-factor authentication*. It is **mandatory for
  admins and dispatchers**, who see the bank details of all units: without it they are
  sent to its setup after signing in and can open nothing else, and they cannot switch
  it off. `MFA_REQUIRED_ROLES` sets the roles (`dispatcher`, `unit_lead`, `member`,
  separated by commas; empty: nobody is forced).
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
- The German texts address people with **"du"**. django-allauth's own German texts
  (sign-in, two-factor authentication, its emails) say "Sie"; those are replaced in
  `locale_allauth/de/LC_MESSAGES/django.po`. `tests/test_i18n.py` fails if an allauth
  update brings new ones – add them there.

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

Requires Python ≥ 3.12, GNU gettext (`msgfmt`) and [uv](https://docs.astral.sh/uv/),
e.g. `sudo dnf install gettext uv`.

```bash
cd ~/PycharmProjects/DiensteManager
uv sync                               # .venv with exactly the versions in uv.lock
cp .env.example .env                  # locally: DJANGO_DEBUG=True
.venv/bin/python manage.py compilemessages --ignore=.venv
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

Then open <http://localhost:8000>. Without `EMAIL_HOST`, all emails are printed to the
console – including invitation links.

**Dependencies:** `pyproject.toml` names the packages and the allowed versions,
`uv.lock` pins the exact versions (with hashes) that CI, the Docker image and `uv sync`
install. After changing `pyproject.toml`, run `uv lock` and commit both files (CI fails
otherwise); `uv lock --upgrade` updates everything within the allowed versions.
Dependabot proposes such updates weekly.

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
docker compose logs -f web worker     # logs (and emails, without SMTP)
```

- Three containers run the app: `web` (gunicorn), `worker` (sends the emails and applies
  the retention periods) and `caddy`, plus the database `db`. Without Caddy, behind
  your own proxy or a Cloudflare Tunnel: see below.

- The image is `ghcr.io/jannikemmerich/dienstemanager`; pin a version with
  `DIENSTEMANAGER_VERSION` in `.env` (see "Versions, releases and updates"). While the
  repository is private, the server needs `docker login ghcr.io` with a personal access
  token that has the `read:packages` scope.
- Migrations run automatically on start; Caddy obtains the certificate by itself.
- **Update:** see "Versions, releases and updates" below.
- **Backup:** `docker compose exec db pg_dump -U dienstemanager dienstemanager > backup.sql`.
- **HSTS** is set to one year (`DJANGO_SECURE_HSTS_SECONDS`); set it to `0` for a local
  test with `DOMAIN=localhost`.
- A **Content Security Policy** (`SECURE_CSP` in `config/settings.py`, also during
  development) only allows scripts, styles and images from the portal itself: no
  inline scripts, no `onclick`/`onsubmit` or `style` attributes (behaviour goes into
  `static/js/app.js`, e.g. `data-confirm="Question?"` on forms). `tests/test_pages.py`
  checks the pages for it.
- `manage.py check --deploy` only reports W005/W021 (HSTS for subdomains/preload). Both
  affect the whole domain and are off on purpose; enable them via
  `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` if appropriate.

### Without Caddy: own reverse proxy or Cloudflare Tunnel

If HTTPS ends somewhere else, e.g. at Cloudflare with `cloudflared`, Caddy is not
needed. Add this line to `.env`:

```bash
COMPOSE_FILE=compose.yaml:compose.tunnel.yaml
```

`docker compose` then starts `db`, `web` and `worker` but not `caddy`, and the app
listens on **`127.0.0.1:8080`** (plain HTTP, only on this machine). Ports 80/443 are not
used. A Caddy container that is already running is stopped with
`docker compose stop caddy && docker compose rm -f caddy`.

- **Cloudflare Tunnel:** a public hostname with the value of `DOMAIN` and the service
  `http://localhost:8080` (with `cloudflared` on the same machine). `DOMAIN` stays set:
  it is the allowed host name and the address in emails.
- The app trusts the proxy's headers: `X-Forwarded-Proto` (HTTPS, otherwise it
  redirects to HTTPS) and the client IP for the sign-in rate limits, by default from
  `CF-Connecting-IP` (`ALLAUTH_TRUSTED_CLIENT_IP_HEADER`; empty: `X-Forwarded-For`).
  Therefore only the proxy may reach the port. `WEB_PORT` changes address and port,
  e.g. `WEB_PORT=127.0.0.1:9000`; with `0.0.0.0:8080` (e.g. for `cloudflared` in a
  container), a firewall has to keep everyone else out.
- **400 Bad Request:** the proxy does not pass the public host name. Pass it on (for
  `cloudflared`: *HTTP Host Header* = `DOMAIN` in the origin settings).

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

Writing `@claude` and a request in an issue or pull request (comment) starts Claude Code
(`.github/workflows/claude.yml`, for accounts with write access). It works on a branch
`claude/issue-…`, may run the tests, ruff and the translation and migration commands,
and answers with a link to open the pull request. It follows `CLAUDE.md` (conventions
and commands of this project). `claude-code-review.yml` reviews every pull request.

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

1. Pull request "Release 1.2.3": set `version = "1.2.3"` in `pyproject.toml`, run
   `uv lock` (`uv.lock` contains the version too) and rename `## [Unreleased]` in
   `CHANGELOG.md` to `## [1.2.3] – <date>` (start a new empty `## [Unreleased]` above
   it). Merge it.
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
lifecycle/   change history and retention periods (apply_retention)
outbox/      email queue, retries, daily summary and the worker (run_worker)
core/        overview, health check (/healthz), form helpers
templates/   layout, own pages, Bootstrap versions of the allauth pages
locale/      German translations
locale_allauth/ allauth's German texts with "du" instead of "Sie"
static/      app.css, app.js and vendored libraries (Bootstrap, htmx)
tests/       pytest tests incl. a simulated HiOrg-Server
docker/      container entry point
.github/     CI and release workflow, image clean-up, Dependabot, release notes script
CLAUDE.md    conventions and commands for Claude (sessions and the @claude workflow)
```

## Next steps

1. **Import duties from HiOrg-Server.** The public REST API has no duties. The EFS
   interface is an option: API key under *System → Settings → Manage API keys*, each
   duty must be released for retrieval by connected EFS. It returns period, location,
   positions and assigned staff, but no staffing target – clarify its use with HiOrg
   support first. Imported duties get `source = hiorg` and the HiOrg ID in
   `external_id` (unique per source), arrive as drafts and are released as usual.
2. **Positions from HiOrg-Server:** map the positions of imported duties to the
   qualifications here, so that imported duties arrive with their staffing needs.
