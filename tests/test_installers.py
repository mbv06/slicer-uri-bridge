from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallerReleasePinTests(unittest.TestCase):
    def test_windows_installer_has_one_pinnable_release_tag(self) -> None:
        text = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")
        self.assertEqual(text.count("$ReleaseTag = 'latest'"), 1)
        self.assertIn("slicer-uri-bridge-python.zip", text)
        self.assertIn("require-hashes", text)
        self.assertNotIn("curl.exe", text)
        self.assertNotIn("resolve_lock", text)

    def test_macos_installer_has_one_pinnable_release_tag(self) -> None:
        text = (ROOT / "install-macos.sh").read_text(encoding="utf-8")
        self.assertEqual(text.count('RELEASE_TAG="latest"'), 1)
        self.assertIn("slicer-uri-bridge-python.zip", text)
        self.assertIn("require-hashes", text)
        self.assertNotIn("resolve_lock", text)


if __name__ == "__main__":
    unittest.main()
