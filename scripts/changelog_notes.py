from __future__ import annotations

import os
import re
import sys
from pathlib import Path

DEV_RELEASE_NOTES = "Rolling development build from the `develop` branch.\n"
TAG_PATTERN = re.compile(r"^v[0-9][0-9A-Za-z.-]*$")


def extract_release_notes(changelog: str, tag: str) -> str:
    if tag == "dev":
        return DEV_RELEASE_NOTES
    if TAG_PATTERN.fullmatch(tag) is None:
        raise ValueError(f"Refusing unsafe release tag: {tag}")

    heading = re.compile(rf"^## {re.escape(tag)}(?:[ \t].*)?$", re.MULTILINE)
    match = heading.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG.md has no section for {tag}")

    start = match.end()
    next_heading = re.search(r"^## ", changelog[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(changelog)
    body = changelog[start:end].strip()
    if not body:
        raise ValueError(f"CHANGELOG.md section for {tag} is empty")
    return f"{body}\n"


def write_release_notes(changelog_path: Path, tag: str, notes_path: Path) -> None:
    notes_path.write_text(extract_release_notes(changelog_path.read_text(encoding="utf-8"), tag), encoding="utf-8")


def main() -> int:
    try:
        notes_path = Path(os.environ["NOTES_PATH"])
        write_release_notes(
            Path(os.environ.get("CHANGELOG_PATH", "CHANGELOG.md")),
            os.environ["RELEASE_TAG"],
            notes_path,
        )
        print(f"Wrote {notes_path}")
    except (KeyError, ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
