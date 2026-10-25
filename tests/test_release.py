"""Version number in the app and the release checks of the CI workflow."""

import importlib.util
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

ROOT = Path(settings.BASE_DIR)
spec = importlib.util.spec_from_file_location(
    "release_notes", ROOT / ".github" / "scripts" / "release_notes.py"
)
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

## [Unreleased]

### Added
- Something new.

## [1.2.0] – 2026-11-01

### Added
- Billing.

### Fixed
- A bug.

## [1.1.0] – 2026-10-01

- Older.
"""


def test_version_comes_from_pyproject():
    assert release_notes.project_version() == settings.VERSION
    assert release_notes.SEMVER.match(settings.VERSION)


def test_changelog_has_a_section_for_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release_notes.changelog_section(settings.VERSION, changelog)
    assert "## [Unreleased]" in changelog


def test_footer_shows_the_version_to_signed_in_users(client, member, settings):
    settings.REVISION = "0123456789abcdef"
    assert settings.VERSION not in client.get(reverse("account_login")).text
    client.force_login(member)
    assert f"{settings.VERSION} (0123456)" in client.get(reverse("home")).text


def test_changelog_section():
    section = release_notes.changelog_section("1.2.0", CHANGELOG)
    assert section == "### Added\n- Billing.\n\n### Fixed\n- A bug."
    assert release_notes.changelog_section("1.1.0", CHANGELOG) == "- Older."
    assert release_notes.changelog_section("1.3.0", CHANGELOG) is None


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    return tmp_path


def test_release_notes_for_a_matching_tag(project, capsys):
    release_notes.main(["release_notes.py", "v1.2.0"], root=project)
    assert capsys.readouterr().out.startswith("### Added\n- Billing.")


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("v1.1.0", "does not match version 1.2.0"),
        ("1.2.0", "is not a version tag"),
        ("v1.2", "is not a version tag"),
    ],
)
def test_release_notes_reject_wrong_tags(project, tag, message):
    with pytest.raises(SystemExit) as exit_info:
        release_notes.main(["release_notes.py", tag], root=project)
    assert message in str(exit_info.value)


def test_release_notes_need_a_changelog_section(project):
    (project / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.3.0"\n')
    with pytest.raises(SystemExit) as exit_info:
        release_notes.main(["release_notes.py", "v1.3.0"], root=project)
    assert "CHANGELOG.md has no entries" in str(exit_info.value)
