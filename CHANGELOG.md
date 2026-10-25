# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versions follow
[Semantic Versioning](https://semver.org/). How a release is made is described under
"Versions, releases and updates" in the README.

## [Unreleased]

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
  leads when the bank details change.
- Retention periods: old helper data is anonymised and old history, emails,
  invitations and billings are deleted daily (`apply_retention`, `housekeeping`
  container).
- Colours of the German Red Cross (DRK).
- Version number in the page footer, CI with tests on SQLite and PostgreSQL, Docker
  images in the GitHub Container Registry, GitHub releases for version tags, weekly
  clean-up of old images and Dependabot.

### Fixed

- The "all units / only the selected units" choice no longer counts as changed when it
  was not.

## [0.1.0] – 2026-10-25

### Added

- User management with invitations, sign-in with a password or HiOrg-Server, two-factor
  authentication, units, German and English UI, Docker Compose setup.
