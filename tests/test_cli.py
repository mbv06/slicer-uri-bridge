from __future__ import annotations

import shutil
import subprocess
import unittest
import uuid
from io import StringIO
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from slicer_uri_bridge import cli


TEMP_ROOT = Path(__file__).resolve().parent / ".tmp"


@contextmanager
def temporary_directory() -> Iterator[Path]:
    TEMP_ROOT.mkdir(exist_ok=True)
    path = TEMP_ROOT / f"case-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class FakeTty:
    def isatty(self) -> bool:
        return True


class InteractiveOnboardingTests(unittest.TestCase):
    def test_no_args_requires_tty(self) -> None:
        class FakePipe:
            def isatty(self) -> bool:
                return False

        with patch("sys.stdin", FakePipe()), patch("sys.stderr", new_callable=StringIO):
            self.assertEqual(cli.interactive_onboarding(), 2)

    def test_missing_config_can_be_created_then_manager_runs(self) -> None:
        with temporary_directory() as temp_dir:
            config_path = temp_dir / "config.toml"

            with (
                patch("sys.stdin", FakeTty()),
                patch("sys.stdout", new_callable=StringIO),
                patch("builtins.input", side_effect=[""]),
                patch("slicer_uri_bridge.cli.user_config_path", return_value=config_path),
                patch("slicer_uri_bridge.cli.init_user_config", return_value=(config_path, True)) as init_config,
                patch("slicer_uri_bridge.cli.manager_main", return_value=0) as manager,
            ):
                result = cli.interactive_onboarding()

        self.assertEqual(result, 0)
        init_config.assert_called_once_with(force=False)
        manager.assert_called_once_with([])

    def test_custom_config_is_kept_and_interactive_register_runs(self) -> None:
        with temporary_directory() as temp_dir:
            config_path = temp_dir / "config.toml"
            config_path.write_text("custom = true\n", encoding="utf-8")

            with (
                patch("sys.stdin", FakeTty()),
                patch("sys.stdout", new_callable=StringIO),
                patch("builtins.input", side_effect=["n"]),
                patch("slicer_uri_bridge.cli.user_config_path", return_value=config_path),
                patch("slicer_uri_bridge.cli.config_matches_default", return_value=False),
                patch("slicer_uri_bridge.cli.init_user_config") as init_config,
                patch("slicer_uri_bridge.cli.manager_main", return_value=0) as manager,
            ):
                result = cli.interactive_onboarding()

        self.assertEqual(result, 0)
        init_config.assert_not_called()
        manager.assert_called_once()
        manager.assert_called_once_with([])

    def test_init_config_warns_about_missing_linux_bambu_target_with_fallback_note(self) -> None:
        with temporary_directory() as temp_dir:
            config_path = temp_dir / "config.toml"
            config_path.write_text(
                "[bambu_studio]\nlinux = \"MissingBambuStudio.AppImage\"\n",
                encoding="utf-8",
            )

            with (
                patch("slicer_uri_bridge.cli.IS_WINDOWS", False),
                patch("slicer_uri_bridge.cli.IS_MACOS", False),
                patch("slicer_uri_bridge.cli.init_user_config", return_value=(config_path, True)),
                patch("slicer_uri_bridge.cli.shutil.which", return_value=None),
                patch("sys.stdout", new_callable=StringIO),
                patch("sys.stderr", new_callable=StringIO) as stderr,
            ):
                self.assertEqual(cli.main(["init-config"]), 0)

        text = stderr.getvalue()
        self.assertIn("Bambu Studio path from config was not found", text)
        self.assertIn("[bambu_studio].linux", text)
        self.assertIn("default application", text)

    def test_init_config_warns_about_missing_windows_bambu_target_without_fallback_note(self) -> None:
        with temporary_directory() as temp_dir:
            config_path = temp_dir / "config.toml"
            config_path.write_text(
                "[bambu_studio]\nwindows = 'Z:\\Missing\\bambu-studio.exe'\n",
                encoding="utf-8",
            )

            with (
                patch("slicer_uri_bridge.cli.IS_WINDOWS", True),
                patch("slicer_uri_bridge.cli.IS_MACOS", False),
                patch("slicer_uri_bridge.cli.configured_bambu_target_exists", return_value=False),
                patch("slicer_uri_bridge.cli.init_user_config", return_value=(config_path, True)),
                patch("sys.stdout", new_callable=StringIO),
                patch("sys.stderr", new_callable=StringIO) as stderr,
            ):
                self.assertEqual(cli.main(["init-config"]), 0)

        text = stderr.getvalue()
        self.assertIn("[bambu_studio].windows", text)
        self.assertNotIn("default application", text)

    def test_init_config_checks_existing_config_too(self) -> None:
        with temporary_directory() as temp_dir:
            config_path = temp_dir / "config.toml"
            config_path.write_text(
                "[bambu_studio]\nlinux = \"MissingBambuStudio.AppImage\"\n",
                encoding="utf-8",
            )

            with (
                patch("slicer_uri_bridge.cli.IS_WINDOWS", False),
                patch("slicer_uri_bridge.cli.IS_MACOS", False),
                patch("slicer_uri_bridge.cli.init_user_config", return_value=(config_path, False)),
                patch("slicer_uri_bridge.cli.shutil.which", return_value=None),
                patch("sys.stdout", new_callable=StringIO),
                patch("sys.stderr", new_callable=StringIO) as stderr,
            ):
                self.assertEqual(cli.main(["init-config"]), 0)

        self.assertIn("Bambu Studio path from config was not found", stderr.getvalue())

    def test_manager_subcommand_delegates_empty_argv(self) -> None:
        with patch("slicer_uri_bridge.cli.manager_main", return_value=0) as manager:
            self.assertEqual(cli.main(["manager"]), 0)

        manager.assert_called_once_with([])

    def test_test_subcommand_opens_known_uri(self) -> None:
        with (
            patch("sys.stdout", new_callable=StringIO),
            patch("slicer_uri_bridge.cli.open_system_uri") as open_uri,
        ):
            self.assertEqual(cli.main(["test"]), 0)

        open_uri.assert_called_once_with(cli.TEST_URI)

    def test_linux_uri_opener_prefers_xdg_open(self) -> None:
        with (
            patch("slicer_uri_bridge.cli.IS_WINDOWS", False),
            patch("slicer_uri_bridge.cli.IS_MACOS", False),
            patch("slicer_uri_bridge.cli.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name == "xdg-open" else None),
            patch("slicer_uri_bridge.cli.subprocess.Popen") as popen,
        ):
            cli.open_system_uri(cli.TEST_URI)

        popen.assert_called_once_with(
            ["/usr/bin/xdg-open", cli.TEST_URI],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def test_windows_uri_opener_uses_startfile(self) -> None:
        with (
            patch("slicer_uri_bridge.cli.IS_WINDOWS", True),
            patch("slicer_uri_bridge.cli.os.startfile", create=True) as startfile,
        ):
            cli.open_system_uri(cli.TEST_URI)

        startfile.assert_called_once_with(cli.TEST_URI)


if __name__ == "__main__":
    unittest.main()
