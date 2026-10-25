"""Checks a release tag and prints its section of CHANGELOG.md (used by the CI workflow).

    python .github/scripts/release_notes.py v1.2.3

Fails if the tag does not match the version in pyproject.toml or if CHANGELOG.md has no
section "## [1.2.3]".
"""

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


def project_version(root=ROOT):
    with (root / "pyproject.toml").open("rb") as pyproject:
        return tomllib.load(pyproject)["project"]["version"]


def changelog_section(version, changelog):
    """The text below "## [version]" up to the next "## [" heading, or None."""
    heading = re.compile(rf"^## \[{re.escape(version)}\]", re.MULTILINE)
    match = heading.search(changelog)
    if match is None:
        return None
    line_end = changelog.find("\n", match.end())
    start = len(changelog) if line_end == -1 else line_end + 1
    following = re.compile(r"^## \[", re.MULTILINE).search(changelog, start)
    return changelog[start : following.start() if following else len(changelog)].strip()


def main(argv, root=ROOT):
    if len(argv) != 2:
        sys.exit(f"usage: {argv[0]} vX.Y.Z")
    tag = argv[1]
    version = tag.removeprefix("v")
    if not tag.startswith("v") or not SEMVER.match(version):
        sys.exit(f"{tag!r} is not a version tag like v1.2.3")
    if version != project_version(root):
        sys.exit(f"Tag {tag} does not match version {project_version(root)} in pyproject.toml")
    section = changelog_section(version, (root / "CHANGELOG.md").read_text(encoding="utf-8"))
    if not section:
        sys.exit(f"CHANGELOG.md has no entries under '## [{version}]'")
    print(section)


if __name__ == "__main__":
    main(sys.argv)
