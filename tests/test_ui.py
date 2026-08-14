from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from slicer_uri_bridge.ui import (
    APP_TITLE,
    BUNDLE_HINT,
    MACOS_DIALOG_SCRIPT_NAME,
    _macos_dialog_script_file,
    _show_linux_dialog,
    _show_macos_dialog,
    show_bundle_hint,
    show_error,
)


class BundleHintTests(unittest.TestCase):
    def test_falls_back_to_messagebox_when_custom_dialog_fails(self) -> None:
        with (
            patch("slicer_uri_bridge.ui.tkinter", object()),
            patch("slicer_uri_bridge.ui._show_macos_dialog", return_value=False),
            patch("slicer_uri_bridge.ui._show_bundle_hint_dialog", return_value=False),
            patch("slicer_uri_bridge.ui._show_tk_messagebox", return_value=True) as messagebox,
            patch("slicer_uri_bridge.ui._show_linux_dialog") as linux_dialog,
            patch("slicer_uri_bridge.ui.sys.stderr", StringIO()),
        ):
            show_bundle_hint()

        messagebox.assert_called_once_with(BUNDLE_HINT, "showinfo")
        linux_dialog.assert_not_called()

    def test_skips_messagebox_when_custom_dialog_works(self) -> None:
        with (
            patch("slicer_uri_bridge.ui.tkinter", object()),
            patch("slicer_uri_bridge.ui._show_macos_dialog", return_value=False),
            patch("slicer_uri_bridge.ui._show_bundle_hint_dialog", return_value=True) as dialog,
            patch("slicer_uri_bridge.ui._show_tk_messagebox") as messagebox,
            patch("slicer_uri_bridge.ui._show_linux_dialog") as linux_dialog,
            patch("slicer_uri_bridge.ui.sys.stderr", StringIO()),
        ):
            show_bundle_hint()

        dialog.assert_called_once()
        messagebox.assert_not_called()
        linux_dialog.assert_not_called()

    def test_falls_back_to_linux_dialog_when_tkinter_is_missing(self) -> None:
        with (
            patch("slicer_uri_bridge.ui.tkinter", None),
            patch("slicer_uri_bridge.ui._show_macos_dialog", return_value=False),
            patch("slicer_uri_bridge.ui._show_bundle_hint_dialog") as dialog,
            patch("slicer_uri_bridge.ui._show_tk_messagebox") as messagebox,
            patch("slicer_uri_bridge.ui._show_linux_dialog", return_value=True) as linux_dialog,
            patch("slicer_uri_bridge.ui.sys.stderr", StringIO()),
        ):
            show_bundle_hint()

        dialog.assert_not_called()
        messagebox.assert_not_called()
        linux_dialog.assert_called_once_with(BUNDLE_HINT, "showinfo")

    def test_prefers_macos_dialog_over_tkinter(self) -> None:
        with (
            patch("slicer_uri_bridge.ui.tkinter", object()),
            patch("slicer_uri_bridge.ui._show_macos_dialog", return_value=True) as macos_dialog,
            patch("slicer_uri_bridge.ui._show_bundle_hint_dialog") as dialog,
            patch("slicer_uri_bridge.ui._show_tk_messagebox") as messagebox,
            patch("slicer_uri_bridge.ui._show_linux_dialog") as linux_dialog,
            patch("slicer_uri_bridge.ui.sys.stderr", StringIO()),
        ):
            show_bundle_hint()

        macos_dialog.assert_called_once_with(BUNDLE_HINT, "showinfo")
        dialog.assert_not_called()
        messagebox.assert_not_called()
        linux_dialog.assert_not_called()


class ErrorDialogTests(unittest.TestCase):
    def test_show_error_uses_messagebox(self) -> None:
        message = "A model-pack ZIP must contain at least one STL file"
        with (
            patch("slicer_uri_bridge.ui.tkinter", object()),
            patch("slicer_uri_bridge.ui._show_macos_dialog", return_value=False),
            patch("slicer_uri_bridge.ui._show_tk_messagebox", return_value=True) as messagebox,
            patch("slicer_uri_bridge.ui._show_linux_dialog") as linux_dialog,
            patch("slicer_uri_bridge.ui.sys.stderr", StringIO()),
        ):
            show_error(message)

        messagebox.assert_called_once_with(message, "showerror")
        linux_dialog.assert_not_called()

    def test_show_error_prefers_macos_dialog(self) -> None:
        message = "A model-pack ZIP must contain at least one STL file"
        with (
            patch("slicer_uri_bridge.ui.tkinter", object()),
            patch("slicer_uri_bridge.ui._show_macos_dialog", return_value=True) as macos_dialog,
            patch("slicer_uri_bridge.ui._show_tk_messagebox") as messagebox,
            patch("slicer_uri_bridge.ui._show_linux_dialog") as linux_dialog,
            patch("slicer_uri_bridge.ui.sys.stderr", StringIO()),
        ):
            show_error(message)

        macos_dialog.assert_called_once_with(message, "showerror")
        messagebox.assert_not_called()
        linux_dialog.assert_not_called()

    def test_show_error_falls_back_to_linux_dialog(self) -> None:
        message = "A model-pack ZIP must contain at least one STL file"
        with (
            patch("slicer_uri_bridge.ui.tkinter", object()),
            patch("slicer_uri_bridge.ui._show_macos_dialog", return_value=False),
            patch("slicer_uri_bridge.ui._show_tk_messagebox", return_value=False),
            patch("slicer_uri_bridge.ui._show_linux_dialog", return_value=True) as linux_dialog,
            patch("slicer_uri_bridge.ui.sys.stderr", StringIO()),
        ):
            show_error(message)

        linux_dialog.assert_called_once_with(message, "showerror")


class LinuxDialogTests(unittest.TestCase):
    def test_linux_dialog_is_skipped_on_windows(self) -> None:
        with patch("slicer_uri_bridge.ui.sys.platform", "win32"):
            self.assertFalse(_show_linux_dialog("boom", "showinfo"))

    def test_linux_dialog_prefers_zenity(self) -> None:
        run = MagicMock()
        run.return_value.returncode = 0
        with (
            patch("slicer_uri_bridge.ui.sys.platform", "linux"),
            patch("slicer_uri_bridge.ui.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"),
            patch("slicer_uri_bridge.ui.subprocess.run", run),
        ):
            self.assertTrue(_show_linux_dialog("hello", "showwarning"))

        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/zenity")
        self.assertIn("--warning", command)
        self.assertIn("hello", command)
        self.assertIn(APP_TITLE, command)

    def test_linux_dialog_falls_back_to_kdialog(self) -> None:
        run = MagicMock()
        run.return_value.returncode = 0

        def which(name: str) -> str | None:
            return "/usr/bin/kdialog" if name == "kdialog" else None

        with (
            patch("slicer_uri_bridge.ui.sys.platform", "linux"),
            patch("slicer_uri_bridge.ui.shutil.which", side_effect=which),
            patch("slicer_uri_bridge.ui.subprocess.run", run),
        ):
            self.assertTrue(_show_linux_dialog("hello", "showerror"))

        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/kdialog", "--title", APP_TITLE, "--error", "hello"],
        )

    def test_linux_dialog_returns_false_when_no_tool_is_available(self) -> None:
        with (
            patch("slicer_uri_bridge.ui.sys.platform", "linux"),
            patch("slicer_uri_bridge.ui.shutil.which", return_value=None),
        ):
            self.assertFalse(_show_linux_dialog("hello", "showinfo"))


class MacOSDialogTests(unittest.TestCase):
    def test_macos_dialog_is_skipped_off_macos(self) -> None:
        with patch("slicer_uri_bridge.ui.sys.platform", "win32"):
            self.assertFalse(_show_macos_dialog("boom", "showinfo"))

    def test_macos_dialog_uses_osascript_script_file(self) -> None:
        run = MagicMock()
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        with (
            patch("slicer_uri_bridge.ui.sys.platform", "darwin"),
            patch("slicer_uri_bridge.ui.subprocess.run", run),
        ):
            self.assertTrue(_show_macos_dialog('hello "world"\nnext', "showinfo"))

        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/osascript")
        self.assertEqual(Path(command[1]).name, MACOS_DIALOG_SCRIPT_NAME)
        self.assertEqual(command[2:], [APP_TITLE, 'hello "world"\nnext', "informational"])
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_macos_error_dialog_passes_critical_kind(self) -> None:
        run = MagicMock()
        run.return_value.returncode = 0
        with (
            patch("slicer_uri_bridge.ui.sys.platform", "darwin"),
            patch("slicer_uri_bridge.ui.subprocess.run", run),
        ):
            self.assertTrue(_show_macos_dialog("boom", "showerror"))

        self.assertEqual(run.call_args.args[0][4], "critical")

    def test_macos_dialog_script_is_loaded_from_package(self) -> None:
        path = _macos_dialog_script_file()
        source = path.read_text(encoding="utf-8")

        self.assertEqual(path.name, MACOS_DIALOG_SCRIPT_NAME)
        self.assertIn("on run argv", source)
        self.assertIn("display alert", source)
        self.assertIn("as critical", source)
        self.assertIn("as warning", source)
        self.assertIn("as informational", source)

    def test_macos_dialog_treats_user_cancel_as_shown(self) -> None:
        run = MagicMock()
        run.return_value.returncode = 1
        run.return_value.stderr = "User canceled."
        with (
            patch("slicer_uri_bridge.ui.sys.platform", "darwin"),
            patch("slicer_uri_bridge.ui.subprocess.run", run),
        ):
            self.assertTrue(_show_macos_dialog("boom", "showinfo"))

    def test_macos_dialog_fails_when_osascript_cannot_show_ui(self) -> None:
        run = MagicMock()
        run.return_value.returncode = 1
        run.return_value.stderr = "No user interaction allowed."
        with (
            patch("slicer_uri_bridge.ui.sys.platform", "darwin"),
            patch("slicer_uri_bridge.ui.subprocess.run", run),
        ):
            self.assertFalse(_show_macos_dialog("boom", "showinfo"))


@unittest.skipUnless(sys.platform == "darwin", "requires macOS osacompile")
class MacOSDialogCompileTests(unittest.TestCase):
    def test_dialog_script_osacompiles(self) -> None:
        osacompile = shutil.which("osacompile")
        if not osacompile:
            self.skipTest("osacompile was not found")

        with TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "macos-dialog.scpt"
            completed = subprocess.run(
                [osacompile, "-o", str(output_path), str(_macos_dialog_script_file())],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(output_path.is_file())


if __name__ == "__main__":
    unittest.main()
