from __future__ import annotations

import os
import plistlib
import shutil
import sys
import unittest
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from slicer_uri_bridge.manager import (
    APP_NAME,
    MACOS_BUNDLE_ID,
    MacOSLaunchServicesManager,
    resolve_protocols,
)


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


class FakeLaunchServices:
    def __init__(self, defaults: dict[str, str | None], handlers: dict[str, list[str]]) -> None:
        self.defaults = dict(defaults)
        self.handlers = {key: list(value) for key, value in handlers.items()}
        self.set_calls: list[tuple[str, str]] = []

    def default_handler(self, scheme: str) -> str | None:
        return self.defaults.get(scheme)

    def all_handlers(self, scheme: str) -> list[str]:
        return self.handlers.get(scheme, [])

    def set_default_handler(self, scheme: str, bundle_id: str) -> None:
        self.defaults[scheme] = bundle_id
        self.set_calls.append((scheme, bundle_id))


def make_manager(script_dir: Path, app_dir: Path, *, dry_run: bool = False) -> MacOSLaunchServicesManager:
    with patch.dict(os.environ, {"URI_BRIDGE_MACOS_APP_DIR": str(app_dir)}):
        return MacOSLaunchServicesManager(script_dir, sys.executable, dry_run)


def create_fake_app(manager: MacOSLaunchServicesManager, protocols: list[str]) -> None:
    manager.contents_dir.mkdir(parents=True, exist_ok=True)
    manager.resources_dir.mkdir(parents=True, exist_ok=True)
    with manager.info_plist.open("wb") as handle:
        plistlib.dump({"CFBundleName": "Fake AppleScript Applet"}, handle)

    definitions = resolve_protocols(protocols)
    manager.applescript_source_file.write_text(manager.expected_applescript_source(), encoding="utf-8")
    manager.write_info_plist(definitions)


def read_plist(path: Path) -> dict:
    with path.open("rb") as handle:
        value = plistlib.load(handle)
    if not isinstance(value, dict):
        raise AssertionError("Expected plist dictionary")
    return value


class MacOSPlistAndStateTests(unittest.TestCase):
    def test_info_plist_contains_expected_bundle_metadata(self) -> None:
        with temporary_directory() as temp_dir:
            manager = make_manager(temp_dir / "project", temp_dir / "Applications")
            create_fake_app(manager, ["bambu", "prusa"])

            info = read_plist(manager.info_plist)

        self.assertEqual(info["CFBundleIdentifier"], MACOS_BUNDLE_ID)
        self.assertEqual(info["CFBundleName"], APP_NAME)
        self.assertIs(info["LSUIElement"], True)
        self.assertEqual(
            info["CFBundleURLTypes"],
            [
                {
                    "CFBundleURLName": APP_NAME,
                    "CFBundleTypeRole": "Viewer",
                    "CFBundleURLSchemes": ["bambustudioopen", "prusaslicer"],
                }
            ],
        )
        self.assertEqual(set(info["SlicerURIBridgeManagedSchemes"]), {"bambustudioopen", "prusaslicer"})

    def test_our_app_schemes_reads_managed_schemes_from_plist(self) -> None:
        with temporary_directory() as temp_dir:
            manager = make_manager(temp_dir / "project", temp_dir / "Applications")
            create_fake_app(manager, ["bambu", "prusa"])

            self.assertEqual(manager.our_app_schemes(), {"bambustudioopen", "prusaslicer"})

    def test_command_current_detects_current_and_stale_applescript(self) -> None:
        with temporary_directory() as temp_dir:
            manager = make_manager(temp_dir / "project", temp_dir / "Applications")
            definitions = resolve_protocols(["bambu"])
            create_fake_app(manager, ["bambu"])

            self.assertTrue(manager.command_current(definitions))

            manager.applescript_source_file.write_text("stale", encoding="utf-8")
            self.assertFalse(manager.command_current(definitions))

    def test_status_displays_app_bundle_not_inner_python_details(self) -> None:
        with temporary_directory() as temp_dir:
            class FakeMacOSManager(MacOSLaunchServicesManager):
                def get_default_bundle_id(self, protocol: str) -> str | None:
                    return MACOS_BUNDLE_ID if protocol == "bambustudioopen" else None

            with patch.dict(os.environ, {"URI_BRIDGE_MACOS_APP_DIR": str(temp_dir / "Applications")}):
                manager = FakeMacOSManager(temp_dir / "project", sys.executable, False)

            definitions = resolve_protocols(["bambu"])
            create_fake_app(manager, ["bambu"])
            state = manager.get_state(definitions[0])
            text = manager.status_text(state)

        self.assertIn(str(manager.app_bundle), text)
        self.assertNotIn("Contents/Resources", text)
        self.assertNotIn("slicer_uri_bridge.handler", text)

    def test_expected_applescript_is_thin_launcher(self) -> None:
        with temporary_directory() as temp_dir:
            manager = make_manager(temp_dir / "project", temp_dir / "Applications")

            source = manager.expected_applescript_source()

        self.assertIn("on open location thisUrl", source)
        self.assertIn("do shell script", source)
        self.assertIn("quoted form of thisUrl", source)
        self.assertIn("-m", source)
        self.assertIn("launcher.log", source)
        self.assertNotIn("__BRIDGE_PYTHON__", source)
        self.assertNotIn("__BRIDGE_MODULE__", source)
        self.assertNotIn("__BRIDGE_CONFIG__", source)
        self.assertNotIn("__LAUNCHER_LOG__", source)

    def test_macos_launcher_template_is_loaded_from_package(self) -> None:
        with temporary_directory() as temp_dir:
            manager = make_manager(temp_dir / "project", temp_dir / "Applications")

            source = manager.macos_launcher_template()

        self.assertIn("__BRIDGE_PYTHON__", source)
        self.assertIn("__LAUNCHER_LOG__", source)

    def test_expected_python_preserves_venv_interpreter_path(self) -> None:
        with temporary_directory() as temp_dir:
            venv_python = temp_dir / "pipx-venv" / "bin" / "python"
            manager = make_manager(temp_dir / "project", temp_dir / "Applications")
            manager.python_command = str(venv_python)

            command = manager.expected_python()

        self.assertEqual(command, os.path.normpath(str(venv_python)))

    def test_check_expected_runtime_requires_user_config_for_real_run(self) -> None:
        with temporary_directory() as temp_dir:
            manager = make_manager(temp_dir / "project", temp_dir / "Applications")
            missing_config = temp_dir / "missing" / "config.toml"

            with patch("slicer_uri_bridge.manager.user_config_path", return_value=missing_config):
                with self.assertRaisesRegex(FileNotFoundError, "User config not found"):
                    manager.check_expected_runtime()


class MacOSDefaultRestoreTests(unittest.TestCase):
    def test_repair_unmanaged_defaults_restores_vendor_handlers(self) -> None:
        with temporary_directory() as temp_dir:
            services = FakeLaunchServices(
                defaults={
                    "bambustudioopen": MACOS_BUNDLE_ID,
                    "cura": MACOS_BUNDLE_ID,
                },
                handlers={
                    "cura": [MACOS_BUNDLE_ID, "nl.ultimaker.cura_UltiMaker_Cura"],
                },
            )

            class FakeMacOSManager(MacOSLaunchServicesManager):
                def launch_services(self) -> FakeLaunchServices:
                    return services

            with patch.dict(os.environ, {"URI_BRIDGE_MACOS_APP_DIR": str(temp_dir / "Applications")}):
                manager = FakeMacOSManager(temp_dir / "project", sys.executable, False)

            previous_defaults = {
                "bambustudioopen": MACOS_BUNDLE_ID,
                "cura": "nl.ultimaker.cura_UltiMaker_Cura",
            }

            manager.repair_unmanaged_defaults(resolve_protocols(["bambu"]), previous_defaults)

        self.assertIn(("cura", "nl.ultimaker.cura_UltiMaker_Cura"), services.set_calls)
        self.assertEqual(services.defaults["cura"], "nl.ultimaker.cura_UltiMaker_Cura")


@unittest.skipUnless(sys.platform == "darwin", "requires macOS osacompile")
class MacOSOsacompileIntegrationTests(unittest.TestCase):
    def test_write_bridge_app_builds_real_applescript_bundle(self) -> None:
        with temporary_directory() as temp_dir:
            class NoLaunchServicesRegistration(MacOSLaunchServicesManager):
                def require_user_config(self) -> None:
                    return None

                def register_app_bundle(self) -> None:
                    self._registered_for_test = True

                def unregister_app_bundle(self) -> None:
                    self._unregistered_for_test = True

            with patch.dict(os.environ, {"URI_BRIDGE_MACOS_APP_DIR": str(temp_dir / "Applications")}):
                manager = NoLaunchServicesRegistration(temp_dir / "project", sys.executable, False)

            definitions = resolve_protocols(["bambu", "prusa"])
            manager.write_bridge_app(definitions)
            info = read_plist(manager.info_plist)

            self.assertTrue(manager.app_bundle.is_dir())
            self.assertTrue(manager.info_plist.is_file())
            self.assertTrue(manager.applescript_source_file.is_file())
            self.assertEqual(info["CFBundleIdentifier"], MACOS_BUNDLE_ID)
            self.assertEqual(set(manager.scheme_list_from_info(info)), {"bambustudioopen", "prusaslicer"})
            self.assertTrue(manager.command_current(definitions))


if __name__ == "__main__":
    unittest.main()
