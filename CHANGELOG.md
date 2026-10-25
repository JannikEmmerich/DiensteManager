# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versions follow
[Semantic Versioning](https://semver.org/). How a release is made is described under
"Versions, releases and updates" in the README.

## [Unreleased]

### Added

- Calendar subscription (iCal): under *My account → Calendar subscription* every account
  creates a personal calendar address for its calendar app. Units get the duties offered
  to them (optionally only those with their reports), dispatchers all duties (drafts
  optionally); cancelled duties are marked as cancelled. No helpers in the feed; the
  address can be renewed and stops working when the account is blocked.
- Roles can be changed: unit leads switch the accounts of their unit between unit lead
  and member, dispatchers those of all units (on the unit's page). Admins edit all data
  of other accounts in the portal – name, email address, role (also dispatcher), unit,
  language and notifications; the internal accounts are listed under *Units*.
- Duties can be copied (button "Copy" on the duty page), e.g. for recurring events.
  The new duty form is prefilled with title, location, description, duty pay, offered
  units and positions; only date and times have to be entered. The copy is a draft
  without reports.
- Qualifications can count as another one ("Also counts as" in the catalogue), e.g.
  emergency paramedic → paramedic → first aider. Helpers with a higher qualification
  then also fill places for the lower ones: with 2 × first aider and 1 × paramedic
  needed, 1 first aider and 2 paramedics staff the duty fully. Set it up under
  *Duties → Qualifications* after updating.

### Changed

- Every position needs a qualification; "any qualification" is gone. Existing
  positions without one get the qualification "Helfer" when updating.
- Confirmed helpers fill a fixed position, and their pay follows it: the position
  they were reported for if a place is free, otherwise the free one closest down their
  "Also counts as" chain. Dispatchers can change it under *Assigned to* on the duty,
  e.g. which of two reported paramedics takes the paramedic's and which the first
  aider's place. Already confirmed helpers keep the position they were reported for.
- A position with reported helpers can no longer get another qualification.
- Changing the helpers needed of a released duty (e.g. one fewer because you found a
  helper yourself) no longer emails the units with reports; it is still recorded in the
  change history. If more helpers are then confirmed than needed, a hint says so.
- Reporting helpers is simpler: the form starts with one row, "Add another helper"
  adds more. For each helper the unit chooses one of the qualifications the duty asks
  for, with the places still open; the separate free-text qualification is gone (older
  reports keep theirs). The dispatchers' "Add helpers" works the same way. "Report and
  add more" is gone.
- The positions of a duty ("Helpers needed") and the catalogue of qualifications
  likewise start with the rows they need and get more with "Add another
  qualification", instead of empty rows (or one new row per save). Positions read
  "number × qualification"; the rates are only shown with duty pay. New qualifications
  come last in the order.

### Removed

- The email to the unit leads when a statement is marked as paid. They still get one
  when a statement is created (with the PDF) or cancelled; the duty pay overview shows
  which statements are paid.

### Fixed

- The positions on a duty's page are listed in the order of the qualifications.

## [0.3.0] – 2026-10-25

When updating from 0.2.0: units can no longer withdraw confirmed reports themselves;
they ask the dispatchers instead, who get an email. You may want to tell the units.

### Added

- Optional report deadline per duty: afterwards units can no longer report helpers or
  withdraw reports themselves (#24).
- Requests to withdraw: units ask the dispatchers to withdraw a confirmed report (or any
  report after the deadline), with an optional reason; the dispatchers get an email and
  take the report back or keep it, and the unit is told either way.
- Running without Caddy behind your own reverse proxy or a Cloudflare Tunnel:
  `COMPOSE_FILE=compose.yaml:compose.tunnel.yaml` in `.env` serves the app on
  `127.0.0.1:8080`; the client IP for the sign-in rate limits comes from
  `CF-Connecting-IP` (`ALLAUTH_TRUSTED_CLIENT_IP_HEADER`).

### Changed

- Units can only withdraw reports themselves until they are confirmed; afterwards only
  the dispatchers can. Dispatchers take back confirmed reports instead of declining them.
- Duties with billed helpers can no longer be cancelled (until the billing is cancelled);
  duties that have started ask for confirmation first (#19).

### Fixed

- An `EMAIL_DIGEST_HOUR` outside 0–23 stopped the worker from sending any email; it is
  now rejected at start-up. More than 32767 helpers per duty caused a server error on
  PostgreSQL; duties now have at most 10000. Queued emails to accounts blocked in the
  meantime are no longer sent (#18).

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
