from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from changelog_notes import DEV_RELEASE_NOTES, extract_release_notes, main, write_release_notes  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(__file__).resolve().parent / ".tmp"
CHANGELOG = """# Changelog

## v1.2.0 - 2026-08-01

### Added

- New thing.

## v1.1.0 - 2026-07-01

- Older thing.

## v1.0.0 - 2026-06-01

- First release.
"""


class ExtractReleaseNotesTests(unittest.TestCase):
    def test_extracts_matching_section_without_heading(self) -> None:
        self.assertEqual(
            extract_release_notes(CHANGELOG, "v1.2.0"),
            "### Added\n\n- New thing.\n",
        )

    def test_extracts_last_section(self) -> None:
        self.assertEqual(extract_release_notes(CHANGELOG, "v1.0.0"), "- First release.\n")

    def test_does_not_match_longer_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "no section for v1.1"):
            extract_release_notes(CHANGELOG, "v1.1")

    def test_missing_section_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "no section for v9.9.9"):
            extract_release_notes(CHANGELOG, "v9.9.9")

    def test_empty_section_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "section for v2.0.0 is empty"):
            extract_release_notes("## v2.0.0 - 2026-08-14\n\n## v1.0.0\n\n- Done.\n", "v2.0.0")

    def test_dev_uses_rolling_notes(self) -> None:
        self.assertEqual(extract_release_notes(CHANGELOG, "dev"), DEV_RELEASE_NOTES)

    def test_rejects_unsafe_tag(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe release tag"):
            extract_release_notes(CHANGELOG, "../v1.2.0")

    def test_write_release_notes_roundtrip(self) -> None:
        TEMP_ROOT.mkdir(exist_ok=True)
        changelog_path = TEMP_ROOT / "CHANGELOG.md"
        notes_path = TEMP_ROOT / "release-notes.md"
        changelog_path.write_text(CHANGELOG, encoding="utf-8")
        write_release_notes(changelog_path, "v1.1.0", notes_path)
        self.assertEqual(notes_path.read_text(encoding="utf-8"), "- Older thing.\n")

    def test_main_writes_notes_from_env(self) -> None:
        TEMP_ROOT.mkdir(exist_ok=True)
        changelog_path = TEMP_ROOT / "script-CHANGELOG.md"
        notes_path = TEMP_ROOT / "script-release-notes.md"
        changelog_path.write_text(CHANGELOG, encoding="utf-8")
        env = {
            "CHANGELOG_PATH": str(changelog_path),
            "RELEASE_TAG": "v1.2.0",
            "NOTES_PATH": str(notes_path),
        }
        with patch.dict(os.environ, env, clear=False), patch("sys.stdout"):
            self.assertEqual(main(), 0)
        self.assertEqual(notes_path.read_text(encoding="utf-8"), "### Added\n\n- New thing.\n")

    def test_main_fails_when_changelog_section_is_missing(self) -> None:
        TEMP_ROOT.mkdir(exist_ok=True)
        changelog_path = TEMP_ROOT / "missing-CHANGELOG.md"
        notes_path = TEMP_ROOT / "missing-release-notes.md"
        changelog_path.write_text(CHANGELOG, encoding="utf-8")
        env = {
            "CHANGELOG_PATH": str(changelog_path),
            "RELEASE_TAG": "v9.9.9",
            "NOTES_PATH": str(notes_path),
        }
        with patch.dict(os.environ, env, clear=False), patch("sys.stderr"):
            self.assertEqual(main(), 1)
        self.assertFalse(notes_path.exists())


class ProjectChangelogTests(unittest.TestCase):
    def test_changelog_has_section_for_package_version(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'(?m)^version = "([^"]+)"$', pyproject)
        self.assertIsNotNone(match)
        assert match is not None
        tag = f"v{match.group(1)}"
        notes = extract_release_notes((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), tag)
        self.assertGreater(len(notes.strip()), 0)

    def test_release_workflow_uses_changelog_notes(self) -> None:
        text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("python tests/changelog_notes.py", text)
        self.assertIn("body_path:", text)
        self.assertNotIn("generate_release_notes: true", text)


if __name__ == "__main__":
    unittest.main()
