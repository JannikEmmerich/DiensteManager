# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versions follow
[Semantic Versioning](https://semver.org/). How a release is made is described under
"Versions, releases and updates" in the README.

## [Unreleased]

### Added

- Optional report deadline per duty: afterwards units can no longer report helpers or
  withdraw reports themselves (#24).
- Requests to withdraw: units ask the dispatchers to withdraw a confirmed report (or any
  report after the deadline), with an optional reason; the dispatchers get an email and
  take the report back or keep it, and the unit is told either way.

### Changed

- Units can only withdraw reports themselves until they are confirmed; afterwards only
  the dispatchers can. Dispatchers take back confirmed reports instead of declining them.

## [0.2.0] – 2026-10-25

When updating from 0.1.0: admins and dispatchers have to set up two-factor
authentication (an authenticator app) the next time they sign in.

### Added

- Duties: entered by dispatchers, released before the units see them, offered to all
  or to selected units; cancelled instead of deleted once released.
- Reports: units report helpers for duties, dispatchers confirm or decline them.
- Email notifications about duties and reports, switchable per account.
- Emails from dispatchers to the units, with the dispatcher as Reply-To.
- Duty pay per helper hour or as a flat rate per helper, a duty pay overview per unit,
  bank details of the units, billing with numbered PDF statements, and helpers entered
  by dispatchers.
- Change history of duties, reports, billings and bank accounts; an email to the unit
  leads and the dispatchers when the bank details change.
- Retention periods: old helper data is anonymised and old history, emails,
  invitations and billings are deleted daily (`apply_retention`, `worker` container).
- Staffing needs per qualification: a duty lists its positions (e.g. 2 × paramedic,
  4 × first aider, or any qualification), each optionally with its own rate; units
  report each helper for a position and see the staffing per position. Dispatchers
  maintain the qualifications; statements show the qualification of each helper.
- Emails in the background: notifications and emails from dispatchers are queued and
  sent by the `worker` container (`run_worker`) with retries; failed emails can be sent
  again in the admin. Accounts can get news about duties and reports as a daily summary.
- Colours of the German Red Cross (DRK).
- Version number in the page footer, CI with tests on SQLite and PostgreSQL, Docker
  images in the GitHub Container Registry, GitHub releases for version tags, weekly
  clean-up of old images and Dependabot.
- `uv.lock` pins the exact versions of all Python packages for CI and the Docker image.

### Changed

- The pages of the sign-in and of two-factor authentication and their emails say "du"
  like the rest of the portal.

### Security

- Two-factor authentication is mandatory for admins and dispatchers
  (`MFA_REQUIRED_ROLES`): without it they are sent to its setup after signing in, and
  they cannot switch it off.
- Content Security Policy: the pages only load scripts, styles and images from the
  portal itself and run no inline scripts, which blocks injected code even if text
  were ever output unescaped.

### Fixed

- The "all units / only the selected units" choice no longer counts as changed when it
  was not.
- Email subjects showed characters such as `&` and `'` in duty titles or unit names as
  HTML entities (`&amp;`, `&#x27;`).
- Unusual digits in addresses (e.g. `?year=²`) caused a server error on the duty pay
  overview and the email form.
- Retention periods: helpers in an open statement were anonymised, so after cancelling
  the statement they could only be billed without a name. Helpers of paid statements
  that had been deleted after the retention period counted as unbilled again, and
  anonymised helpers became billable when duty pay was added to their duty later.
- Confirmation questions with names containing quotes or angle brackets showed HTML
  entities.

## [0.1.0] – 2026-10-25

### Added

- User management with invitations, sign-in with a password or HiOrg-Server, two-factor
  authentication, units, German and English UI, Docker Compose setup.
