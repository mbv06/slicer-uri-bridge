from __future__ import annotations

import shutil
import unittest
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from slicer_uri_bridge.manager import DESKTOP_ID, LinuxXdgManager


TEMP_ROOT = Path(__file__).resolve().parent / ".tmp"


@contextmanager
def temporary_directory() -> Iterator[str]:
    TEMP_ROOT.mkdir(exist_ok=True)
    path = TEMP_ROOT / f"case-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class LinuxMimeAppsTests(unittest.TestCase):
    def test_update_mimeapps_default_replaces_existing_default(self) -> None:
        with temporary_directory() as temp_dir:
            path = Path(temp_dir) / "mimeapps.list"
            path.write_text(
                "\n".join(
                    (
                        "[Default Applications]",
                        "x-scheme-handler/cura=vendor.desktop;",
                        "x-scheme-handler/orcaslicer=orca.desktop;",
                        "[Added Associations]",
                        "x-scheme-handler/cura=vendor.desktop;",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            LinuxXdgManager.update_mimeapps_default(path, "x-scheme-handler/cura", DESKTOP_ID)

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "\n".join(
                    (
                        "[Default Applications]",
                        f"x-scheme-handler/cura={DESKTOP_ID};",
                        "x-scheme-handler/orcaslicer=orca.desktop;",
                        "[Added Associations]",
                        "x-scheme-handler/cura=vendor.desktop;",
                        "",
                    )
                ),
            )

    def test_update_mimeapps_default_creates_default_section(self) -> None:
        with temporary_directory() as temp_dir:
            path = Path(temp_dir) / "config" / "mimeapps.list"

            LinuxXdgManager.update_mimeapps_default(path, "x-scheme-handler/prusaslicer", DESKTOP_ID)

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "\n".join(
                    (
                        "[Default Applications]",
                        f"x-scheme-handler/prusaslicer={DESKTOP_ID};",
                        "",
                    )
                ),
            )

    def test_remove_from_mimeapps_only_removes_bridge_desktop_id(self) -> None:
        with temporary_directory() as temp_dir:
            path = Path(temp_dir) / "mimeapps.list"
            path.write_text(
                "\n".join(
                    (
                        "[Default Applications]",
                        f"x-scheme-handler/cura=vendor.desktop;{DESKTOP_ID};other.desktop;",
                        f"x-scheme-handler/orcaslicer={DESKTOP_ID};",
                        "[Added Associations]",
                        f"x-scheme-handler/cura={DESKTOP_ID};vendor.desktop;",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            LinuxXdgManager.remove_from_mimeapps(path, "x-scheme-handler/cura", DESKTOP_ID)

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "\n".join(
                    (
                        "[Default Applications]",
                        "x-scheme-handler/cura=vendor.desktop;other.desktop;",
                        f"x-scheme-handler/orcaslicer={DESKTOP_ID};",
                        "[Added Associations]",
                        "x-scheme-handler/cura=vendor.desktop;",
                        "",
                    )
                ),
            )


if __name__ == "__main__":
    unittest.main()
