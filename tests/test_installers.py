from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_RELEASE_PIN = "$ReleaseTag = 'latest'"
MACOS_RELEASE_PIN = 'RELEASE_TAG="latest"'


class InstallerReleasePinTests(unittest.TestCase):
    def test_windows_installer_has_one_pinnable_release_tag(self) -> None:
        text = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")
        self.assertEqual(text.count(WINDOWS_RELEASE_PIN), 1)
        self.assertIn("slicer-uri-bridge-python.zip", text)
        self.assertIn("require-hashes", text)
        self.assertNotIn("curl.exe", text)
        self.assertNotIn("resolve_lock", text)

    def test_macos_installer_has_one_pinnable_release_tag(self) -> None:
        text = (ROOT / "install-macos.sh").read_text(encoding="utf-8")
        self.assertEqual(text.count(MACOS_RELEASE_PIN), 1)
        self.assertIn("slicer-uri-bridge-python.zip", text)
        self.assertIn("require-hashes", text)
        self.assertNotIn("resolve_lock", text)

    def test_release_workflow_pins_the_same_literals(self) -> None:
        text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count(WINDOWS_RELEASE_PIN), 1)
        self.assertEqual(text.count(MACOS_RELEASE_PIN), 1)


if __name__ == "__main__":
    unittest.main()
