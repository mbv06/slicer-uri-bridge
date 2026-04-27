#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.resources
import importlib.util
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import missing_config_message, user_config_path, user_log_path

APP_NAME = "Slicer URI Bridge"
DESKTOP_ID = "slicer-uri-bridge.desktop"
MACOS_BUNDLE_ID = "mbv06.slicer-uri-bridge"
MACOS_APP_BUNDLE_NAME = "SlicerURIBridge.app"
HANDLER_MODULE = "slicer_uri_bridge.handler"
BRIDGE_DISPLAY_TARGET = "slicer-uri-bridge"
CONFIG_FILE_NAME = "config.toml"
RESOURCE_DIR_NAME = "resources"
MACOS_LAUNCHER_TEMPLATE_NAME = "macos-launcher.applescript"


@dataclass(frozen=True)
class ProtocolDef:
    key: str
    name: str
    protocol: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class HandlerState:
    definition: ProtocolDef
    effective_target: str | None
    display_target: str
    managed_by_us: bool
    effective_managed_by_us: bool
    command_current: bool


@dataclass(frozen=True)
class ActionResult:
    action: str
    definition: ProtocolDef
    target: str
    note: str = ""


PROTOCOLS: tuple[ProtocolDef, ...] = (
    ProtocolDef("bambu", "Bambu", "bambustudioopen", ("bambu", "bambuopen", "bambustudio", "bambustudioopen")),
    ProtocolDef("cura", "Cura", "cura", ("cura", "ultimaker-cura", "ultimakercura")),
    ProtocolDef("creality", "Creality", "crealityprintlink", ("creality", "crealityprint", "creality-print", "crealityprintlink", "creality-print-link")),
    ProtocolDef("prusa", "Prusa", "prusaslicer", ("prusa", "prusa-slicer", "prusaslicer")),
    ProtocolDef("orca", "Orca", "orcaslicer", ("orca", "orca-slicer", "orcaslicer")),
)


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def normalize_token(value: str) -> str:
    return value.strip().lower().replace("_", "-").replace(" ", "")


def resolve_protocols(tokens: Iterable[str]) -> list[ProtocolDef]:
    lookup: dict[str, ProtocolDef] = {}
    for item in PROTOCOLS:
        lookup[item.protocol] = item
        for alias in item.aliases:
            lookup[normalize_token(alias)] = item

    selected: list[ProtocolDef] = []
    seen: set[str] = set()
    for token in tokens:
        key = normalize_token(token)
        if not key:
            continue
        if key not in lookup:
            allowed = ", ".join(item.key for item in PROTOCOLS)
            raise ValueError(f"Unknown protocol or alias: {token}. Allowed values: {allowed}")
        item = lookup[key]
        if item.key not in seen:
            selected.append(item)
            seen.add(item.key)
    return selected


class UriHandlerManager(ABC):
    def __init__(self, script_dir: Path, python_command: str | None, dry_run: bool) -> None:
        self.script_dir = script_dir.resolve()
        self.python_command = python_command
        self.dry_run = dry_run


    @property
    @abstractmethod
    def our_target(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_state(self, definition: ProtocolDef) -> HandlerState:
        raise NotImplementedError

    @abstractmethod
    def set_handler(self, definition: ProtocolDef) -> None:
        raise NotImplementedError

    @abstractmethod
    def remove_handler(self, definition: ProtocolDef) -> None:
        raise NotImplementedError

    def require_bridge_script(self) -> None:
        if importlib.util.find_spec(HANDLER_MODULE) is None:
            raise FileNotFoundError(f"Python handler module not found: {HANDLER_MODULE}")

    def require_user_config(self) -> None:
        path = user_config_path()
        if not path.is_file():
            raise FileNotFoundError(missing_config_message(path))

    def status_text(self, state: HandlerState) -> str:
        if not state.effective_target:
            return "not registered"
        if state.effective_managed_by_us:
            return f"({'current' if state.command_current else 'stale'}) -> {state.display_target}"
        return f"(default) -> {state.display_target}"


class LinuxXdgManager(UriHandlerManager):
    """Linux/XDG backend.

    Creates one user desktop entry in ~/.local/share/applications and updates
    user MIME defaults for x-scheme-handler/<scheme>. The desktop entry claims
    only the schemes currently managed by this bridge and runs the installed
    package as `python -m slicer_uri_bridge.handler %u`.
    """

    def __init__(self, script_dir: Path, python_command: str | None, dry_run: bool) -> None:
        super().__init__(script_dir, python_command or sys.executable, dry_run)
        home = Path.home()
        self.xdg_data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
        self.xdg_config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        self.applications_dir = self.xdg_data_home / "applications"
        self.desktop_file = self.applications_dir / DESKTOP_ID
        self.mimeapps_file = self.xdg_config_home / "mimeapps.list"
        self._expected_python: str | None = None

    def data_dirs(self) -> list[Path]:
        home = Path.home()
        paths: list[Path] = [
            self.xdg_data_home,
            home / ".local" / "share" / "flatpak" / "exports" / "share",
            Path("/var/lib/flatpak/exports/share"),
        ]
        paths.extend(
            Path(base)
            for base in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(os.pathsep)
            if base
        )

        unique_paths: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key not in seen:
                unique_paths.append(path)
                seen.add(key)
        return unique_paths

    @property
    def our_target(self) -> str:
        return DESKTOP_ID

    @staticmethod
    def mime_for(protocol: str) -> str:
        return f"x-scheme-handler/{protocol}"

    @staticmethod
    def desktop_quote(value: str | Path) -> str:
        text = str(value)
        text = text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        return f'"{text}"'

    def expected_python(self) -> str:
        if self._expected_python:
            return self._expected_python

        command = self.python_command or shutil.which("python3") or "python3"
        if os.path.sep not in command:
            command = shutil.which(command) or command
        self._expected_python = command
        return command

    def expected_desktop_exec(self) -> str:
        return f"{self.desktop_quote(self.expected_python())} -m {HANDLER_MODULE} %u"

    def check_expected_runtime(self) -> None:
        self.require_bridge_script()
        if not self.dry_run:
            self.require_user_config()
        command = self.expected_python()
        if os.path.sep in command:
            path = Path(command)
            if not path.is_file() or not os.access(path, os.X_OK):
                raise FileNotFoundError(f"Python interpreter is not executable: {command}")
        elif not shutil.which(command):
            raise FileNotFoundError(f"Python interpreter was not found: {command}")

    def mimeapps_search_files(self) -> list[Path]:
        files = [self.xdg_config_home / "mimeapps.list", self.applications_dir / "mimeapps.list"]
        for base in self.data_dirs():
            files.append(base / "applications" / "mimeapps.list")
            files.append(base / "applications" / "defaults.list")
        return files

    def user_default_search_files(self) -> list[Path]:
        return [self.mimeapps_file, self.applications_dir / "mimeapps.list"]

    def find_desktop_file(self, desktop_id: str) -> Path | None:
        for base in self.data_dirs():
            candidate = base / "applications" / desktop_id
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def first_default_from_file(path: Path, mime: str) -> str | None:
        if not path.is_file():
            return None
        section = ""
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if section != "Default Applications" or "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            if key.strip() != mime:
                continue
            for item in value.strip().strip(";").split(";"):
                if item:
                    return item
        return None

    def get_default_handler(self, protocol: str) -> str | None:
        mime = self.mime_for(protocol)
        for path in self.user_default_search_files():
            result = self.first_default_from_file(path, mime)
            if result:
                return result
        for path in self.mimeapps_search_files():
            result = self.first_default_from_file(path, mime)
            if result:
                return result
        return self.query_xdg_default_handler(mime)

    def get_effective_default_handler(self, protocol: str) -> str | None:
        mime = self.mime_for(protocol)
        result = self.query_gio_default_handler(mime)
        if result:
            return result
        result = self.query_xdg_default_handler(mime)
        if result:
            return result
        return self.get_default_handler(protocol)

    @staticmethod
    def query_xdg_default_handler(mime: str) -> str | None:
        try:
            completed = subprocess.run(
                ["xdg-mime", "query", "default", mime],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None

        if completed.returncode != 0:
            return None

        value = completed.stdout.strip()
        return value or None

    @staticmethod
    def query_gio_default_handler(mime: str) -> str | None:
        try:
            completed = subprocess.run(
                ["gio", "mime", mime],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None

        if completed.returncode != 0:
            return None

        for raw_line in completed.stdout.splitlines():
            line = raw_line.strip()
            if not line.startswith("Default application for"):
                continue
            _, _, value = line.partition(":")
            value = value.strip()
            if not value or value == "(None)":
                return None
            return value

        return None

    def get_user_default_handler(self, protocol: str) -> str | None:
        mime = self.mime_for(protocol)
        for path in self.user_default_search_files():
            result = self.first_default_from_file(path, mime)
            if result:
                return result
        return None

    def current_bridge_definitions(self) -> list[ProtocolDef]:
        return [item for item in PROTOCOLS if self.get_user_default_handler(item.protocol) == DESKTOP_ID]

    def get_desktop_field(self, desktop_id: str, field_name: str) -> str | None:
        path = self.find_desktop_file(desktop_id)
        if not path:
            return None
        prefix = f"{field_name}="
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw_line.startswith(prefix):
                return raw_line[len(prefix):]
        return None

    def get_desktop_exec(self, desktop_id: str) -> str | None:
        return self.get_desktop_field(desktop_id, "Exec")

    def get_desktop_mime_types(self, desktop_id: str) -> set[str]:
        value = self.get_desktop_field(desktop_id, "MimeType")
        if not value:
            return set()
        return {item for item in value.strip().strip(";").split(";") if item}

    def command_current(self, definitions: Iterable[ProtocolDef]) -> bool:
        expected_mime_types = {self.mime_for(item.protocol) for item in definitions}
        if self.get_desktop_exec(DESKTOP_ID) != self.expected_desktop_exec():
            return False
        if self.get_desktop_mime_types(DESKTOP_ID) != expected_mime_types:
            return False
        return True

    def get_state(self, definition: ProtocolDef) -> HandlerState:
        effective = self.get_effective_default_handler(definition.protocol)
        current_user = self.get_user_default_handler(definition.protocol)
        if not effective:
            return HandlerState(definition, None, "<not registered>", False, False, False)

        exec_line = self.get_desktop_exec(effective)
        managed = current_user == DESKTOP_ID
        effective_managed = effective == DESKTOP_ID
        if managed or effective_managed:
            display = BRIDGE_DISPLAY_TARGET
        else:
            display = exec_line or effective
        current_command = self.command_current(self.current_bridge_definitions()) if managed else False
        return HandlerState(definition, effective, display, managed, effective_managed, current_command)

    def refresh_desktop_database(self) -> None:
        command = shutil.which("update-desktop-database")
        if not command:
            return
        try:
            subprocess.run([command, str(self.applications_dir)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def apply_xdg_default(self, mime: str) -> None:
        command = shutil.which("xdg-mime")
        if not command:
            return
        try:
            subprocess.run([command, "default", DESKTOP_ID, mime], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def write_bridge_files(self, definitions: Iterable[ProtocolDef]) -> None:
        self.check_expected_runtime()
        definitions = tuple(definitions)

        if self.dry_run:
            if definitions:
                print(f"[dry-run] Would write desktop entry: {self.desktop_file}")
            else:
                print(f"[dry-run] Would remove desktop entry: {self.desktop_file}")
            return

        self.applications_dir.mkdir(parents=True, exist_ok=True)

        if definitions:
            mime_types = "".join(f"{self.mime_for(item.protocol)};" for item in definitions)
            self.desktop_file.write_text(
                "\n".join((
                    "[Desktop Entry]",
                    "Type=Application",
                    f"Name={APP_NAME}",
                    "Comment=Route slicer URI schemes to a Python bridge",
                    "NoDisplay=true",
                    "Terminal=false",
                    f"Exec={self.expected_desktop_exec()}",
                    f"MimeType={mime_types}",
                    "Categories=Utility;",
                    "StartupNotify=false",
                    "",
                )),
                encoding="utf-8",
            )
        else:
            self.desktop_file.unlink(missing_ok=True)

        self.refresh_desktop_database()

    @staticmethod
    def update_mimeapps_default(path: Path, mime: str, desktop_id: str) -> None:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if path.exists() else []
        out: list[str] = []
        section = ""
        in_default = False
        done = False

        def emit_missing() -> None:
            nonlocal done
            if in_default and not done:
                out.append(f"{mime}={desktop_id};\n")
                done = True

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                emit_missing()
                section = stripped[1:-1]
                in_default = section == "Default Applications"
                out.append(line)
                continue

            if in_default and "=" in line:
                key, _ = line.split("=", 1)
                if key.strip() == mime:
                    if not done:
                        out.append(f"{mime}={desktop_id};\n")
                        done = True
                    continue
            out.append(line)

        if not done:
            if out and out[-1].strip():
                out.append("\n")
            if not in_default:
                out.append("[Default Applications]\n")
            out.append(f"{mime}={desktop_id};\n")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(out), encoding="utf-8")

    @staticmethod
    def remove_from_mimeapps(path: Path, mime: str, desktop_id: str) -> None:
        if not path.is_file():
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        out: list[str] = []
        section = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1]
                out.append(line)
                continue

            if section in {"Default Applications", "Added Associations"} and "=" in line:
                key, value = line.split("=", 1)
                if key.strip() == mime:
                    values = [item for item in value.strip().strip(";").split(";") if item and item != desktop_id]
                    if values:
                        out.append(f"{key}={';'.join(values)};\n")
                    continue
            out.append(line)

        path.write_text("".join(out), encoding="utf-8")

    def set_handler(self, definition: ProtocolDef) -> None:
        definitions = self.current_bridge_definitions()
        if all(item.protocol != definition.protocol for item in definitions):
            definitions.append(definition)
        self.write_bridge_files(definitions)
        if self.dry_run:
            print(f"[dry-run] Would set {self.mime_for(definition.protocol)}={DESKTOP_ID} in {self.mimeapps_file}")
            return
        mime = self.mime_for(definition.protocol)
        self.update_mimeapps_default(self.mimeapps_file, mime, DESKTOP_ID)
        self.apply_xdg_default(mime)

    def remove_handler(self, definition: ProtocolDef) -> None:
        mime = self.mime_for(definition.protocol)
        targets = [self.mimeapps_file, self.applications_dir / "mimeapps.list"]
        for target in targets:
            if self.dry_run:
                print(f"[dry-run] Would remove {DESKTOP_ID} from {mime} in {target}")
            else:
                self.remove_from_mimeapps(target, mime, DESKTOP_ID)

        remaining = [item for item in self.current_bridge_definitions() if item.protocol != definition.protocol]
        self.write_bridge_files(remaining)

        if os.environ.get("URI_BRIDGE_PURGE_UNUSED_FILES") == "1":
            self.cleanup_bridge_files_if_unused()

    def cleanup_bridge_files_if_unused(self) -> None:
        for item in PROTOCOLS:
            if self.get_default_handler(item.protocol) == DESKTOP_ID:
                return
        if self.dry_run:
            print(f"[dry-run] Would remove unused bridge file: {self.desktop_file}")
            return
        self.desktop_file.unlink(missing_ok=True)


class WindowsRegistryManager(UriHandlerManager):
    """Windows registry backend.

    Writes user-local protocol handlers under HKCU\\Software\\Classes\\<scheme>.
    Each handler command runs the installed package with Python and passes the
    incoming URI as "%1". Unregister removes only this package's HKCU
    registration for the selected scheme.
    """

    @property
    def our_target(self) -> str:
        return BRIDGE_DISPLAY_TARGET

    def __init__(self, script_dir: Path, python_command: str | None, dry_run: bool) -> None:
        super().__init__(
            script_dir,
            python_command or self.default_python_command(),
            dry_run,
        )
        self._resolved_python_command: str | None = None
        import winreg
        self.winreg = winreg

    @staticmethod
    def default_python_command() -> str:
        if sys.executable:
            sibling_pythonw = Path(sys.executable).with_name("pythonw.exe")
            if sibling_pythonw.is_file():
                return str(sibling_pythonw)
            return sys.executable
        return "pythonw.exe"

    @staticmethod
    def win_quote(value: str) -> str:
        return '"' + value.replace('"', '\\"') + '"'

    def expected_command(self) -> str:
        return f"{self.win_quote(self.resolved_python_command())} -m {HANDLER_MODULE} \"%1\""

    def resolved_python_command(self) -> str:
        if self._resolved_python_command:
            return self._resolved_python_command

        command = self.python_command or "pythonw.exe"
        if os.path.sep not in command:
            resolved = shutil.which(command)
            if not resolved:
                raise FileNotFoundError(f"{command} was not found on PATH.")
            command = resolved
        else:
            path = Path(command)
            if not path.is_file():
                raise FileNotFoundError(f"Python interpreter not found: {command}")
            command = str(path.resolve())

        self._resolved_python_command = command
        return command

    def check_expected_runtime(self) -> None:
        self.require_bridge_script()
        if not self.dry_run:
            self.require_user_config()
        self.resolved_python_command()

    def command_value(self, root, path: str) -> str | None:  # noqa: ANN001 - winreg root type is platform-only
        try:
            with self.winreg.OpenKey(root, path + r"\shell\open\command") as key:
                value, _ = self.winreg.QueryValueEx(key, "")
                return str(value) if value is not None else None
        except OSError:
            return None

    def current_user_command(self, protocol: str) -> str | None:
        return self.command_value(self.winreg.HKEY_CURRENT_USER, rf"Software\Classes\{protocol}")

    def effective_command(self, protocol: str) -> str | None:
        return self.command_value(self.winreg.HKEY_CLASSES_ROOT, protocol)

    @staticmethod
    def is_our_command(command: str | None) -> bool:
        if not command:
            return False
        return HANDLER_MODULE.lower() in command.lower()

    @staticmethod
    def primary_path(command: str | None) -> str | None:
        if not command:
            return None
        text = command.strip()
        if text.startswith('"'):
            match = re.match(r'^"([^"]+)"', text)
            if match:
                return match.group(1)
        return text.split(None, 1)[0] if text else None

    def get_state(self, definition: ProtocolDef) -> HandlerState:
        current_user = self.current_user_command(definition.protocol)
        effective = self.effective_command(definition.protocol)
        managed = self.is_our_command(current_user)
        effective_managed = self.is_our_command(effective)
        try:
            expected_command = self.expected_command().strip()
        except FileNotFoundError:
            expected_command = ""
        current = bool(current_user and current_user.strip() == expected_command)
        if managed or effective_managed:
            display = BRIDGE_DISPLAY_TARGET
        else:
            display = self.primary_path(effective) or "<not registered>"
        return HandlerState(definition, effective, display, managed, effective_managed, current)

    def set_handler(self, definition: ProtocolDef) -> None:
        self.check_expected_runtime()
        root_path = rf"Software\Classes\{definition.protocol}"
        command_path = root_path + r"\shell\open\command"

        if self.dry_run:
            print(f"[dry-run] Would set HKCU\\{command_path} = {self.expected_command()}")
            return

        key = self.winreg.CreateKeyEx(self.winreg.HKEY_CURRENT_USER, root_path, 0, self.winreg.KEY_WRITE)
        with key:
            self.winreg.SetValueEx(key, "", 0, self.winreg.REG_SZ, f"URL:{definition.protocol}")
            self.winreg.SetValueEx(key, "URL Protocol", 0, self.winreg.REG_SZ, "")
        command_key = self.winreg.CreateKeyEx(self.winreg.HKEY_CURRENT_USER, command_path, 0, self.winreg.KEY_WRITE)
        with command_key:
            self.winreg.SetValueEx(command_key, "", 0, self.winreg.REG_SZ, self.expected_command())

    def remove_handler(self, definition: ProtocolDef) -> None:
        root_path = rf"Software\Classes\{definition.protocol}"
        if self.dry_run:
            print(f"[dry-run] Would remove HKCU\\{root_path}")
            return

        current_user = self.current_user_command(definition.protocol)
        if not self.is_our_command(current_user):
            return

        self.delete_handler_registration(definition)

    def delete_handler_registration(self, definition: ProtocolDef) -> None:
        root_path = rf"Software\Classes\{definition.protocol}"
        paths = (
            root_path + r"\shell\open\command",
            root_path + r"\shell\open",
            root_path + r"\shell",
            root_path,
        )
        for path in paths:
            self.delete_expected_key(self.winreg.HKEY_CURRENT_USER, path, root_path)

    def delete_expected_key(self, root, path: str, root_path: str) -> None:  # noqa: ANN001 - winreg root type is platform-only
        if not self.safe_delete_path(root, path, root_path):
            return
        try:
            self.winreg.DeleteKey(root, path)
        except OSError:
            pass

    def safe_delete_path(self, root, path: str, root_path: str) -> bool:  # noqa: ANN001 - winreg root type is platform-only
        if root != self.winreg.HKEY_CURRENT_USER:
            print(f"Warning: refusing to delete registry key outside HKCU: {path}", file=sys.stderr)
            return False

        allowed_paths = {
            root_path,
            root_path + r"\shell",
            root_path + r"\shell\open",
            root_path + r"\shell\open\command",
        }
        if path not in allowed_paths:
            raise RuntimeError(f"Refusing to delete unexpected registry key: HKCU\\{path}")
        return True


class MacOSLaunchServicesManager(UriHandlerManager):
    """macOS LaunchServices backend.

    Builds ~/Applications/SlicerURIBridge.app as a thin AppleScript launcher.
    The app declares selected schemes in CFBundleURLTypes, keeps the fixed bundle
    id and runs the Python executable used at registration
    time as `python -m slicer_uri_bridge.handler <incoming-uri>`.

    The bundle intentionally does not copy package code or config. Status marks
    it stale when the saved Python executable is missing/different, Info.plist or
    AppleScript source differs from the expected launcher, or LaunchServices
    defaults do not point to this bundle id. Rebuilds preserve unmanaged scheme
    defaults so Cura/Orca/Prusa/etc. are not accidentally moved to this bridge.
    """

    @property
    def our_target(self) -> str:
        return str(self.app_bundle)

    def __init__(self, script_dir: Path, python_command: str | None, dry_run: bool) -> None:
        super().__init__(script_dir, python_command or sys.executable, dry_run)
        default_app_dir = Path.home() / "Applications"
        self.applications_dir = Path(os.environ.get("URI_BRIDGE_MACOS_APP_DIR", default_app_dir)).expanduser()
        self.app_bundle = self.applications_dir / MACOS_APP_BUNDLE_NAME
        self.contents_dir = self.app_bundle / "Contents"
        self.info_plist = self.contents_dir / "Info.plist"
        self.resources_dir = self.contents_dir / "Resources"
        self.applescript_source_file = self.resources_dir / "slicer-uri-bridge.applescript"
        self._expected_python: str | None = None

    def expected_python(self) -> str:
        if self._expected_python:
            return self._expected_python

        command = self.python_command or sys.executable or shutil.which("python3") or "python3"
        if os.path.sep not in command:
            command = shutil.which(command) or command
        else:
            path = Path(command).expanduser()
            if path.is_absolute():
                command = os.path.normpath(os.fspath(path))
            else:
                command = os.path.abspath(os.fspath(path))
        self._expected_python = command
        return command

    def display_bridge_target(self) -> str:
        return str(self.app_bundle)

    @staticmethod
    def applescript_quote(value: str | Path) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def macos_launcher_template() -> str:
        package_name = __package__ or "slicer_uri_bridge"
        try:
            return (
                importlib.resources.files(package_name)
                .joinpath(RESOURCE_DIR_NAME, MACOS_LAUNCHER_TEMPLATE_NAME)
                .read_text(encoding="utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError, AttributeError):
            fallback = Path(__file__).with_name(RESOURCE_DIR_NAME) / MACOS_LAUNCHER_TEMPLATE_NAME
            if fallback.is_file():
                return fallback.read_text(encoding="utf-8")
            raise FileNotFoundError(f"macOS launcher template not found: {MACOS_LAUNCHER_TEMPLATE_NAME}")

    def expected_applescript_source(self) -> str:
        replacements = {
            "__BRIDGE_PYTHON__": self.applescript_quote(self.expected_python()),
            "__BRIDGE_MODULE__": self.applescript_quote(HANDLER_MODULE),
            "__BRIDGE_CONFIG__": self.applescript_quote(user_config_path()),
            "__LAUNCHER_LOG__": self.applescript_quote(user_log_path().with_name("launcher.log")),
        }
        source = self.macos_launcher_template()
        for placeholder, value in replacements.items():
            source = source.replace(placeholder, value)
        return source

    def check_expected_runtime(self) -> None:
        self.require_bridge_script()
        if not self.dry_run:
            self.require_user_config()
        command = self.expected_python()
        if os.path.sep in command:
            path = Path(command)
            if not path.is_file() or not os.access(path, os.X_OK):
                raise FileNotFoundError(f"Python interpreter is not executable: {command}")
        elif not shutil.which(command):
            raise FileNotFoundError(f"Python interpreter was not found: {command}")

    @staticmethod
    def scheme_list_from_info(info: dict) -> list[str]:
        schemes: list[str] = []
        for item in info.get("CFBundleURLTypes", []) or []:
            for scheme in item.get("CFBundleURLSchemes", []) or []:
                if isinstance(scheme, str) and scheme not in schemes:
                    schemes.append(scheme)
        return schemes

    def read_info_plist(self) -> dict | None:
        if not self.info_plist.is_file():
            return None
        try:
            with self.info_plist.open("rb") as handle:
                value = plistlib.load(handle)
            return value if isinstance(value, dict) else None
        except (OSError, plistlib.InvalidFileException, ValueError):
            return None

    def our_app_schemes(self) -> set[str]:
        info = self.read_info_plist()
        if not info:
            return set()
        if info.get("CFBundleIdentifier") != MACOS_BUNDLE_ID:
            return set()
        return set(self.scheme_list_from_info(info))

    def current_bridge_definitions(self) -> list[ProtocolDef]:
        schemes = self.our_app_schemes()
        return [item for item in PROTOCOLS if item.protocol in schemes]

    def command_current(self, definitions: Iterable[ProtocolDef]) -> bool:
        info = self.read_info_plist()
        if not info:
            return False

        expected_schemes = {item.protocol for item in definitions}
        actual_schemes = set(self.scheme_list_from_info(info))
        saved_python = info.get("SlicerURIBridgePython")
        if info.get("CFBundleIdentifier") != MACOS_BUNDLE_ID:
            return False
        if saved_python != self.expected_python():
            return False
        if not isinstance(saved_python, str):
            return False
        saved_python_path = Path(saved_python)
        if not saved_python_path.is_file() or not os.access(saved_python_path, os.X_OK):
            return False
        if info.get("SlicerURIBridgeModule") != HANDLER_MODULE:
            return False
        if actual_schemes != expected_schemes:
            return False
        if set(info.get("SlicerURIBridgeManagedSchemes", []) or []) != expected_schemes:
            return False
        if not self.applescript_source_file.is_file():
            return False
        source = self.applescript_source_file.read_text(encoding="utf-8", errors="replace")
        return source == self.expected_applescript_source()

    def write_info_plist(self, definitions: Iterable[ProtocolDef]) -> None:
        definitions = tuple(definitions)
        if not self.info_plist.is_file():
            raise FileNotFoundError(f"Generated macOS app is missing Info.plist: {self.info_plist}")

        with self.info_plist.open("rb") as handle:
            info = plistlib.load(handle)

        schemes = [item.protocol for item in definitions]
        info.update({
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": MACOS_BUNDLE_ID,
            "CFBundleURLTypes": [
                {
                    "CFBundleURLName": APP_NAME,
                    "CFBundleTypeRole": "Viewer",
                    "CFBundleURLSchemes": schemes,
                }
            ],
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "SlicerURIBridgePython": self.expected_python(),
            "SlicerURIBridgeModule": HANDLER_MODULE,
            "SlicerURIBridgeConfig": str(user_config_path()),
            "SlicerURIBridgeManagedSchemes": schemes,
        })

        with self.info_plist.open("wb") as handle:
            plistlib.dump(info, handle, sort_keys=False)

    def write_bridge_app(self, definitions: Iterable[ProtocolDef]) -> None:
        definitions = tuple(definitions)
        self.check_expected_runtime()

        if self.dry_run:
            if definitions:
                print(f"[dry-run] Would write thin macOS app bundle: {self.app_bundle}")
                print(f"[dry-run] Would advertise schemes: {', '.join(item.protocol for item in definitions)}")
                print(f"[dry-run] Would launch: {self.expected_python()} -m {HANDLER_MODULE} <incoming-uri>")
                print(f"[dry-run] Would write launcher log: {user_log_path().with_name('launcher.log')}")
            else:
                print(f"[dry-run] Would remove macOS app bundle: {self.app_bundle}")
            return

        if not definitions:
            self.unregister_app_bundle()
            shutil.rmtree(self.app_bundle, ignore_errors=True)
            return

        osacompile = shutil.which("osacompile")
        if not osacompile:
            raise FileNotFoundError("osacompile was not found. This macOS backend must be run on macOS.")

        self.applications_dir.mkdir(parents=True, exist_ok=True)
        self.unregister_app_bundle()
        shutil.rmtree(self.app_bundle, ignore_errors=True)

        with tempfile.TemporaryDirectory(prefix="slicer-uri-bridge-") as temp_dir:
            source_path = Path(temp_dir) / "slicer-uri-bridge.applescript"
            source_path.write_text(self.expected_applescript_source(), encoding="utf-8")
            completed = subprocess.run(
                [osacompile, "-o", str(self.app_bundle), str(source_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()
                raise RuntimeError(f"osacompile failed: {detail or 'unknown error'}")

        self.resources_dir.mkdir(parents=True, exist_ok=True)
        self.applescript_source_file.write_text(self.expected_applescript_source(), encoding="utf-8")
        self.write_info_plist(definitions)
        self.register_app_bundle()

    @staticmethod
    def lsregister_command() -> str | None:
        candidates = [
            "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister",
            shutil.which("lsregister"),
        ]
        for item in candidates:
            if item and Path(item).is_file():
                return item
        return None

    def register_app_bundle(self) -> None:
        command = self.lsregister_command()
        if not command or not self.app_bundle.exists():
            return
        try:
            subprocess.run([command, "-f", str(self.app_bundle)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def unregister_app_bundle(self) -> None:
        command = self.lsregister_command()
        if not command or not self.app_bundle.exists():
            return
        try:
            subprocess.run([command, "-u", str(self.app_bundle)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    class _LaunchServices:
        ENCODING_UTF8 = 0x08000100

        def __init__(self) -> None:
            import ctypes

            self.ctypes = ctypes
            self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            self.core = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")

            self.cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
            self.cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            self.cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
            self.cf.CFStringGetLength.restype = ctypes.c_long
            self.cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
            self.cf.CFStringGetCString.restype = ctypes.c_bool
            self.cf.CFRelease.argtypes = [ctypes.c_void_p]
            self.cf.CFRelease.restype = None

            self.cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
            self.cf.CFArrayGetCount.restype = ctypes.c_long
            self.cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
            self.cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p

            self.core.LSCopyDefaultHandlerForURLScheme.argtypes = [ctypes.c_void_p]
            self.core.LSCopyDefaultHandlerForURLScheme.restype = ctypes.c_void_p
            self.core.LSCopyAllHandlersForURLScheme.argtypes = [ctypes.c_void_p]
            self.core.LSCopyAllHandlersForURLScheme.restype = ctypes.c_void_p
            self.core.LSSetDefaultHandlerForURLScheme.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            self.core.LSSetDefaultHandlerForURLScheme.restype = ctypes.c_int32

        def cf_string(self, text: str) -> int:
            ref = self.cf.CFStringCreateWithCString(None, text.encode("utf-8"), self.ENCODING_UTF8)
            if not ref:
                raise RuntimeError(f"Could not create CFString for: {text}")
            return int(ref)

        def cf_to_str(self, ref: int | None) -> str | None:
            if not ref:
                return None
            length = int(self.cf.CFStringGetLength(ref))
            buffer = self.ctypes.create_string_buffer(length * 4 + 1)
            ok = self.cf.CFStringGetCString(ref, buffer, len(buffer), self.ENCODING_UTF8)
            return buffer.value.decode("utf-8") if ok else None

        def default_handler(self, scheme: str) -> str | None:
            scheme_ref = self.cf_string(scheme)
            result_ref: int | None = None
            try:
                result_ref = self.core.LSCopyDefaultHandlerForURLScheme(scheme_ref)
                return self.cf_to_str(result_ref)
            finally:
                if result_ref:
                    self.cf.CFRelease(result_ref)
                self.cf.CFRelease(scheme_ref)

        def all_handlers(self, scheme: str) -> list[str]:
            scheme_ref = self.cf_string(scheme)
            array_ref: int | None = None
            try:
                array_ref = self.core.LSCopyAllHandlersForURLScheme(scheme_ref)
                if not array_ref:
                    return []
                count = int(self.cf.CFArrayGetCount(array_ref))
                handlers: list[str] = []
                for index in range(count):
                    item_ref = self.cf.CFArrayGetValueAtIndex(array_ref, index)
                    value = self.cf_to_str(item_ref)
                    if value and value not in handlers:
                        handlers.append(value)
                return handlers
            finally:
                if array_ref:
                    self.cf.CFRelease(array_ref)
                self.cf.CFRelease(scheme_ref)

        def set_default_handler(self, scheme: str, bundle_id: str) -> None:
            scheme_ref = self.cf_string(scheme)
            bundle_ref = self.cf_string(bundle_id)
            try:
                status = int(self.core.LSSetDefaultHandlerForURLScheme(scheme_ref, bundle_ref))
                if status != 0:
                    raise RuntimeError(f"LSSetDefaultHandlerForURLScheme failed for {scheme}: OSStatus {status}")
            finally:
                self.cf.CFRelease(bundle_ref)
                self.cf.CFRelease(scheme_ref)

    def launch_services(self) -> _LaunchServices | None:
        if sys.platform != "darwin":
            return None
        return self._LaunchServices()

    def get_default_bundle_id(self, protocol: str) -> str | None:
        services = self.launch_services()
        if not services:
            return None
        try:
            return services.default_handler(protocol)
        except OSError:
            return None

    def set_default_handler(self, protocol: str) -> None:
        if self.dry_run:
            print(f"[dry-run] Would set default handler for {protocol} to {MACOS_BUNDLE_ID}")
            return
        services = self.launch_services()
        if not services:
            raise RuntimeError("LaunchServices is available only on macOS.")
        services.set_default_handler(protocol, MACOS_BUNDLE_ID)

    def default_handlers_snapshot(self) -> dict[str, str | None]:
        return {item.protocol: self.get_default_bundle_id(item.protocol) for item in PROTOCOLS}

    def best_alternative_handler(self, protocol: str, previous_default: str | None) -> str | None:
        if previous_default and previous_default != MACOS_BUNDLE_ID:
            return previous_default

        services = self.launch_services()
        if not services:
            return None

        try:
            handlers = services.all_handlers(protocol)
        except OSError:
            return None

        for handler in handlers:
            if handler != MACOS_BUNDLE_ID:
                return handler
        return None

    def repair_unmanaged_defaults(self, managed_definitions: Iterable[ProtocolDef], previous_defaults: dict[str, str | None]) -> None:
        managed_schemes = {item.protocol for item in managed_definitions}
        for item in PROTOCOLS:
            protocol = item.protocol
            if protocol in managed_schemes:
                continue

            current_default = self.get_default_bundle_id(protocol)
            if current_default != MACOS_BUNDLE_ID:
                continue

            fallback = self.best_alternative_handler(protocol, previous_defaults.get(protocol))
            if not fallback:
                continue

            if self.dry_run:
                print(f"[dry-run] Would restore default handler for {protocol} to {fallback}")
                continue

            services = self.launch_services()
            if not services:
                continue
            services.set_default_handler(protocol, fallback)

    def get_state(self, definition: ProtocolDef) -> HandlerState:
        effective = self.get_default_bundle_id(definition.protocol)
        our_schemes = self.our_app_schemes()
        app_claims_scheme = definition.protocol in our_schemes
        effective_managed = effective == MACOS_BUNDLE_ID
        managed = app_claims_scheme or effective_managed

        if app_claims_scheme or effective_managed:
            display = self.display_bridge_target()
        else:
            display = effective or "<not registered>"

        current = bool(app_claims_scheme and effective_managed and self.command_current(self.current_bridge_definitions()))
        return HandlerState(definition, effective, display, managed, effective_managed, current)

    def status_text(self, state: HandlerState) -> str:
        if state.effective_managed_by_us:
            return f"({'current' if state.command_current else 'stale'}) -> {state.display_target}"
        if state.managed_by_us and state.effective_target:
            return f"(stale: default elsewhere) -> {state.effective_target}"
        if state.managed_by_us:
            return f"(stale: no default) -> {state.display_target}"
        if state.effective_target:
            return f"(default) -> {state.display_target}"
        return "not registered"

    def set_handler(self, definition: ProtocolDef) -> None:
        previous_defaults = self.default_handlers_snapshot()
        definitions = self.current_bridge_definitions()
        if all(item.protocol != definition.protocol for item in definitions):
            definitions.append(definition)
        self.write_bridge_app(definitions)
        self.repair_unmanaged_defaults(definitions, previous_defaults)
        self.set_default_handler(definition.protocol)

    def remove_handler(self, definition: ProtocolDef) -> None:
        definitions_before = self.current_bridge_definitions()
        previous_defaults = self.default_handlers_snapshot()
        defaults_that_were_ours = {
            item.protocol: self.get_default_bundle_id(item.protocol) == MACOS_BUNDLE_ID
            for item in definitions_before
        }
        remaining = [item for item in definitions_before if item.protocol != definition.protocol]

        if self.dry_run:
            print(f"[dry-run] Would unregister app bundle from LaunchServices: {self.app_bundle}")
            self.write_bridge_app(remaining)
            self.repair_unmanaged_defaults(remaining, previous_defaults)
            return

        self.unregister_app_bundle()
        self.write_bridge_app(remaining)
        self.repair_unmanaged_defaults(remaining, previous_defaults)
        if remaining:
            for item in remaining:
                if defaults_that_were_ours.get(item.protocol):
                    self.set_default_handler(item.protocol)

def make_manager(script_dir: Path, python_command: str | None, dry_run: bool) -> UriHandlerManager:
    if sys.platform == "win32":
        return WindowsRegistryManager(script_dir, python_command, dry_run)
    if sys.platform == "darwin":
        return MacOSLaunchServicesManager(script_dir, python_command, dry_run)
    return LinuxXdgManager(script_dir, python_command, dry_run)


def select_auto(manager: UriHandlerManager, action: str) -> list[ProtocolDef]:
    states = [manager.get_state(item) for item in PROTOCOLS]
    if action == "register":
        return [
            state.definition
            for state in states
            if not state.effective_target
            or state.definition.protocol == "bambustudioopen"
        ]
    return [state.definition for state in states if state.managed_by_us]


def print_statuses(manager: UriHandlerManager, numbered: bool = False) -> None:
    print("Supported URI handlers:")
    for index, state in enumerate((manager.get_state(item) for item in PROTOCOLS), start=1):
        if numbered:
            print(f"  {index}) {state.definition.protocol:<18} ({state.definition.name:<9}) {manager.status_text(state)}")
        else:
            print(f"  {state.definition.protocol:<18} ({state.definition.name:<9}) {manager.status_text(state)}")


def interactive_select(manager: UriHandlerManager) -> list[ProtocolDef] | None:
    print(f"URI handler manager for {APP_NAME}")
    print_statuses(manager, numbered=True)
    print("")
    choice = input("Select numbers separated by comma, or Enter for auto mode: ").strip().lower().replace(" ", "")
    if not choice:
        return None

    selected: list[ProtocolDef] = []
    seen: set[str] = set()
    for token in choice.split(","):
        if not token.isdigit():
            raise ValueError(f"Invalid selection: {token}")
        index = int(token)
        if index < 1 or index > len(PROTOCOLS):
            raise ValueError(f"Selection out of range: {token}")
        item = PROTOCOLS[index - 1]
        if item.key not in seen:
            selected.append(item)
            seen.add(item.key)
    return selected


def interactive_action() -> str | None:
    choice = input("Action [R]egister / [u]nregister (Enter = register): ").strip().lower()
    if not choice:
        return "register"
    if choice in {"r", "reg", "register"}:
        return "register"
    if choice in {"u", "unreg", "unregister"}:
        return "unregister"
    raise ValueError(f"Unknown action: {choice}")


def apply_action(manager: UriHandlerManager, action: str, selected: list[ProtocolDef]) -> list[ActionResult]:
    states = {item.protocol: manager.get_state(item) for item in selected}
    results: list[ActionResult] = []

    for item in selected:
        state = states[item.protocol]

        if action == "register":
            manager.set_handler(item)
            result_action = "Updated" if state.managed_by_us else "Registered"
            if state.managed_by_us and state.command_current:
                note = "rewrote current bridge registration"
            elif state.managed_by_us:
                note = "refreshed stale bridge command"
            else:
                note = ""
            results.append(ActionResult(result_action, item, manager.our_target, note))
            continue

        if state.managed_by_us:
            manager.remove_handler(item)
            results.append(ActionResult("Unregistered", item, manager.our_target))
        elif state.effective_target:
            results.append(ActionResult("Skipped", item, state.display_target, "handled by someone else"))
        else:
            results.append(ActionResult("Skipped", item, "<not registered>", "not registered"))

    return results


def print_results(action: str, results: list[ActionResult]) -> None:
    print(f"{action.capitalize()} summary:")
    for result in results:
        note = f" ({result.note})" if result.note else ""
        print(f"- {result.action:<12} {result.definition.name} ({result.definition.protocol}) -> {result.target}{note}")


def normalize_argv(argv: list[str]) -> list[str]:
    # Small compatibility layer for PowerShell-style flags: -Register -Unregister -Auto.
    mapping = {
        "-register": "--register",
        "-unregister": "--unregister",
        "-auto": "--auto",
    }
    return [mapping.get(arg.lower(), arg) for arg in argv]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register or unregister slicer URI handlers on Windows/Linux/macOS.")
    parser.add_argument("items", nargs="*", help="Optional action followed by protocol names or aliases.")
    parser.add_argument("--register", dest="flag_action", action="store_const", const="register", help="Register selected protocols.")
    parser.add_argument("--unregister", dest="flag_action", action="store_const", const="unregister", help="Unregister selected protocols.")
    parser.add_argument("--auto", action="store_true", help="register: protocols without a handler; unregister: protocols handled by us.")
    parser.add_argument("--python", dest="python_command", help="Python executable used by the registered bridge command.")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing them.")
    args = parser.parse_args(normalize_argv(argv))

    action: str | None = None
    protocols: list[str] = list(args.items)
    if protocols and protocols[0] in {"register", "unregister", "status"}:
        action = protocols[0]
        protocols = protocols[1:]

    if action and args.flag_action and action != args.flag_action:
        parser.error("Use only one action: register or unregister.")

    args.action = action or args.flag_action
    args.protocols = protocols
    delattr(args, "items")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    manager = make_manager(Path(__file__).parent, args.python_command, args.dry_run)

    if args.action == "status":
        if args.auto or args.protocols:
            raise ValueError("status does not accept --auto or protocol names")
        print_statuses(manager)
        return 0

    action = args.action
    if args.auto and not action:
        raise ValueError("--auto requires explicit action: register or unregister")
    if args.auto and args.protocols:
        raise ValueError("Use either explicit protocol names or --auto, not both")

    interactive_auto = False

    if args.protocols:
        selected = resolve_protocols(args.protocols)
    elif args.auto:
        selected = []
    else:
        if not sys.stdin.isatty():
            raise ValueError("No protocols provided and stdin is not interactive. Use --auto or pass protocol names.")
        temp = interactive_select(manager)
        selected = temp or []
        if temp is None:
            interactive_auto = True

    if not action:
        if not sys.stdin.isatty():
            raise ValueError("No action provided. Use register or unregister.")
        action = interactive_action()

    if args.auto or interactive_auto:
        selected = select_auto(manager, action)

    if not selected:
        print("Nothing to do.")
        return 0

    results = apply_action(manager, action, selected)
    print_results(action, results)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as exc:
        eprint(f"Error: {exc}")
        raise SystemExit(1)
