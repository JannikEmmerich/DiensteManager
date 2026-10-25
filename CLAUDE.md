# DiensteManager

Django portal of a DRK district: dispatchers enter duties, external units report
helpers, billing of the duty pay. The README describes features, settings and operation.

## Conventions

- Code, comments, commit messages and translation keys are English; the UI is German
  (default) and English. German texts address people with "du", never "Sie".
- Every user-visible text goes through gettext. After changing texts, update and
  translate `locale/de/LC_MESSAGES/django.po` (no fuzzy or empty entries left).
- New behaviour gets tests in `tests/` (pytest, fixtures in `tests/conftest.py`).
- No inline scripts, `on…=` handlers or `style` attributes in templates: the Content
  Security Policy forbids them (behaviour goes into `static/js/app.js`, e.g.
  `data-confirm`).
- Document user-visible changes in `README.md` and under `[Unreleased]` in
  `CHANGELOG.md`.

## Commands

The dependencies (exact versions from `uv.lock`) are installed into the active Python,
so run the tools directly, not through `uv run`:

```bash
python -m pytest                     # all tests (SQLite); must pass before committing
ruff check . && ruff format .        # lint and format; CI fails on any finding
python manage.py makemigrations      # after model changes; CI checks for missing ones
python manage.py makemessages -l de --ignore=.venv --ignore=staticfiles --ignore=static
python manage.py compilemessages --ignore=.venv
```

Outside DEBUG, `manage.py` needs `DJANGO_SECRET_KEY` (set in CI and in the Claude
workflow). Locally, `uv sync` creates `.venv` and `DJANGO_DEBUG=True` in `.env` is
enough.
