from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from slicer_uri_bridge.config import user_config_dir, user_config_path
from slicer_uri_bridge.manager import BRIDGE_DISPLAY_TARGET, HandlerState, PROTOCOLS, WindowsRegistryManager, main as manager_main, resolve_protocols, select_auto


class FakeManager:
    def __init__(self, states: dict[str, tuple[str | None, bool]]) -> None:
        self.states = states

    def get_state(self, definition):  # noqa: ANN001 - test double keeps the contract small
        effective_target, managed_by_us = self.states.get(definition.protocol, (None, False))
        return HandlerState(
            definition=definition,
            effective_target=effective_target,
            display_target=effective_target or "<not registered>",
            managed_by_us=managed_by_us,
            effective_managed_by_us=managed_by_us,
            command_current=managed_by_us,
        )


class ProtocolResolutionTests(unittest.TestCase):
    def test_resolve_aliases_and_dedupe(self) -> None:
        selected = resolve_protocols(["bambu", "bambustudioopen", "orca-slicer"])
        self.assertEqual([item.protocol for item in selected], ["bambustudioopen", "orcaslicer"])

    def test_unknown_alias_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_protocols(["not-a-slicer"])


class AutoSelectionTests(unittest.TestCase):
    def test_register_auto_selects_empty_handlers_and_always_bambu(self) -> None:
        manager = FakeManager({
            "bambustudioopen": ("vendor.bambu", False),
            "cura": (None, False),
            "prusaslicer": ("vendor.prusa", False),
            "orcaslicer": ("vendor.orca", False),
            "crealityprintlink": ("vendor.creality", False),
        })
        selected = select_auto(manager, "register")
        self.assertEqual([item.protocol for item in selected], ["bambustudioopen", "cura"])

    def test_unregister_auto_selects_only_our_handlers(self) -> None:
        manager = FakeManager({
            "bambustudioopen": ("slicer-uri-bridge", True),
            "cura": ("vendor.cura", False),
            "orcaslicer": ("slicer-uri-bridge", True),
        })
        selected = select_auto(manager, "unregister")
        self.assertEqual([item.protocol for item in selected], ["bambustudioopen", "orcaslicer"])


class ConfigPathTests(unittest.TestCase):
    def test_xdg_config_home_is_used_on_linux_and_macos(self) -> None:
        expected = Path("/tmp/xdg") / "slicer-uri-bridge" / "config.toml"
        self.assertEqual(
            user_config_path(platform="darwin", env={"XDG_CONFIG_HOME": "/tmp/xdg"}, home=Path("/Users/alice")),
            expected,
        )
        self.assertEqual(
            user_config_path(platform="linux", env={"XDG_CONFIG_HOME": "/tmp/xdg"}, home=Path("/home/alice")),
            expected,
        )

    def test_default_unix_config_dir(self) -> None:
        self.assertEqual(
            user_config_dir(platform="linux", env={}, home=Path("/home/alice")),
            Path("/home/alice") / ".config" / "slicer-uri-bridge",
        )

    def test_windows_appdata_config_dir(self) -> None:
        self.assertEqual(
            user_config_path(
                platform="win32",
                env={"APPDATA": r"C:\Users\Alice\AppData\Roaming"},
                home=Path(r"C:\Users\Alice"),
            ),
            Path(r"C:\Users\Alice\AppData\Roaming") / "slicer-uri-bridge" / "config.toml",
        )


class StatusDisplayTests(unittest.TestCase):
    def test_windows_managed_package_handler_hides_python_path(self) -> None:
        command = r'"C:\Users\Alice\AppData\Local\Programs\Python\Python313\pythonw.exe" -m slicer_uri_bridge.handler "%1"'

        class FakeWindowsManager(WindowsRegistryManager):
            def current_user_command(self, protocol: str) -> str | None:
                return command

            def effective_command(self, protocol: str) -> str | None:
                return command

            def expected_command(self) -> str:
                return command

        manager = object.__new__(FakeWindowsManager)
        state = manager.get_state(PROTOCOLS[0])

        self.assertEqual(state.display_target, BRIDGE_DISPLAY_TARGET)
        self.assertNotIn("python", manager.status_text(state).lower())


class WindowsRegistryDeleteSafetyTests(unittest.TestCase):
    class FakeWinReg:
        HKEY_CURRENT_USER = object()
        HKEY_CLASSES_ROOT = object()
        KEY_READ = 1
        KEY_WRITE = 2

        def __init__(self) -> None:
            self.deleted: list[tuple[object, str]] = []

        def DeleteKey(self, root: object, path: str) -> None:
            self.deleted.append((root, path))

    def test_init_imports_winreg_immediately(self) -> None:
        fake_winreg = self.FakeWinReg()

        with patch.dict(sys.modules, {"winreg": fake_winreg}):
            manager = WindowsRegistryManager(Path(__file__).resolve().parent, r"C:\Python\pythonw.exe", True)

        self.assertIs(manager.winreg, fake_winreg)

    def make_manager(self) -> WindowsRegistryManager:
        manager = object.__new__(WindowsRegistryManager)
        manager.winreg = self.FakeWinReg()
        manager.dry_run = False
        return manager

    def test_delete_handler_registration_deletes_only_known_scheme_paths(self) -> None:
        manager = self.make_manager()

        manager.delete_handler_registration(PROTOCOLS[0])

        root = r"Software\Classes\bambustudioopen"
        self.assertEqual(
            [path for _, path in manager.winreg.deleted],
            [
                root + r"\shell\open\command",
                root + r"\shell\open",
                root + r"\shell",
                root,
            ],
        )

    def test_remove_handler_skips_registry_delete_for_unmanaged_command(self) -> None:
        manager = self.make_manager()
        manager.current_user_command = Mock(return_value=r"C:\Vendor\App.exe")
        manager.delete_handler_registration = Mock()

        manager.remove_handler(PROTOCOLS[0])

        manager.delete_handler_registration.assert_not_called()

    def test_registry_delete_guard_rejects_unexpected_path_and_warns_for_wrong_root(self) -> None:
        manager = self.make_manager()
        root = r"Software\Classes\bambustudioopen"

        with self.assertRaisesRegex(RuntimeError, "unexpected registry key"):
            manager.safe_delete_path(manager.winreg.HKEY_CURRENT_USER, r"Software\Classes\cura", root)

        with patch("sys.stderr"):
            self.assertFalse(manager.safe_delete_path(manager.winreg.HKEY_CLASSES_ROOT, root, root))


class ManagerArgvTests(unittest.TestCase):
    def test_empty_argv_stays_empty_instead_of_falling_back_to_sys_argv(self) -> None:
        with self.assertRaisesRegex(ValueError, "stdin is not interactive"):
            manager_main([])


if __name__ == "__main__":
    unittest.main()
