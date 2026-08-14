from __future__ import annotations

import importlib.util
import os
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = """# Changelog

## v1.2.0 - 2026-08-01

### Added

- New thing.

## v1.1.0 - 2026-07-01

- Older thing.

## v1.0.0 - 2026-06-01

- First release.
"""


def load_changelog_notes() -> Any:
    path = ROOT / "scripts" / "changelog_notes.py"
    spec = importlib.util.spec_from_file_location("changelog_notes", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


changelog_notes = load_changelog_notes()


class ExtractReleaseNotesTests(unittest.TestCase):
    def test_extracts_matching_section_without_heading(self) -> None:
        self.assertEqual(
            changelog_notes.extract_release_notes(CHANGELOG, "v1.2.0"),
            "### Added\n\n- New thing.\n",
        )

    def test_extracts_last_section(self) -> None:
        self.assertEqual(changelog_notes.extract_release_notes(CHANGELOG, "v1.0.0"), "- First release.\n")

    def test_keeps_first_body_line_when_heading_has_no_blank_line(self) -> None:
        changelog = "## v1.2.0 - 2026-08-01\n### Added\n- New thing.\n"
        self.assertEqual(
            changelog_notes.extract_release_notes(changelog, "v1.2.0"),
            "### Added\n- New thing.\n",
        )

    def test_does_not_match_longer_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "no section for v1.1"):
            changelog_notes.extract_release_notes(CHANGELOG, "v1.1")

    def test_missing_section_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "no section for v9.9.9"):
            changelog_notes.extract_release_notes(CHANGELOG, "v9.9.9")

    def test_empty_section_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "section for v2.0.0 is empty"):
            changelog_notes.extract_release_notes("## v2.0.0 - 2026-08-14\n\n## v1.0.0\n\n- Done.\n", "v2.0.0")

    def test_dev_uses_rolling_notes(self) -> None:
        self.assertEqual(changelog_notes.extract_release_notes(CHANGELOG, "dev"), changelog_notes.DEV_RELEASE_NOTES)

    def test_rejects_unsafe_tag(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe release tag"):
            changelog_notes.extract_release_notes(CHANGELOG, "../v1.2.0")

    def test_write_release_notes_roundtrip(self) -> None:
        with TemporaryDirectory() as temp_dir:
            changelog_path = Path(temp_dir) / "CHANGELOG.md"
            notes_path = Path(temp_dir) / "release-notes.md"
            changelog_path.write_text(CHANGELOG, encoding="utf-8")
            changelog_notes.write_release_notes(changelog_path, "v1.1.0", notes_path)
            self.assertEqual(notes_path.read_text(encoding="utf-8"), "- Older thing.\n")

    def test_main_writes_notes_from_env(self) -> None:
        with TemporaryDirectory() as temp_dir:
            changelog_path = Path(temp_dir) / "CHANGELOG.md"
            notes_path = Path(temp_dir) / "release-notes.md"
            changelog_path.write_text(CHANGELOG, encoding="utf-8")
            env = {
                "CHANGELOG_PATH": str(changelog_path),
                "RELEASE_TAG": "v1.2.0",
                "NOTES_PATH": str(notes_path),
            }
            with patch.dict(os.environ, env, clear=False), patch("sys.stdout"):
                self.assertEqual(changelog_notes.main(), 0)
            self.assertEqual(notes_path.read_text(encoding="utf-8"), "### Added\n\n- New thing.\n")

    def test_main_fails_when_changelog_section_is_missing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            changelog_path = Path(temp_dir) / "CHANGELOG.md"
            notes_path = Path(temp_dir) / "release-notes.md"
            changelog_path.write_text(CHANGELOG, encoding="utf-8")
            env = {
                "CHANGELOG_PATH": str(changelog_path),
                "RELEASE_TAG": "v9.9.9",
                "NOTES_PATH": str(notes_path),
            }
            with patch.dict(os.environ, env, clear=False), patch("sys.stderr"):
                self.assertEqual(changelog_notes.main(), 1)
            self.assertFalse(notes_path.exists())


class ProjectChangelogTests(unittest.TestCase):
    def test_changelog_has_section_for_package_version(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'(?m)^version = "([^"]+)"$', pyproject)
        self.assertIsNotNone(match)
        assert match is not None
        tag = f"v{match.group(1)}"
        notes = changelog_notes.extract_release_notes((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), tag)
        self.assertGreater(len(notes.strip()), 0)

    def test_release_workflow_uses_changelog_notes(self) -> None:
        text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/changelog_notes.py", text)
        self.assertIn("body_path:", text)
        self.assertNotIn("generate_release_notes: true", text)


if __name__ == "__main__":
    unittest.main()
