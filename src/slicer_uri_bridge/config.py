from __future__ import annotations

import contextlib
import copy
import os
import shutil
import stat
import sys
import tomllib
from collections.abc import Mapping
from functools import cache
from importlib import resources
from pathlib import Path
from typing import TypeGuard

from tomlkit import parse as parse_toml
from tomlkit.exceptions import TOMLKitError

CONFIG_DIR_NAME = "slicer-uri-bridge"
CONFIG_FILE_NAME = "config.toml"
LOG_FILE_NAME = "slicer-uri-bridge.log"


def user_config_dir(
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-user config directory for this package."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = Path.home() if home is None else home

    if platform == "win32":
        appdata = env.get("APPDATA")
        base = Path(appdata).expanduser() if appdata else home / "AppData" / "Roaming"
        return base / CONFIG_DIR_NAME

    xdg_config_home = env.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home).expanduser() if xdg_config_home else home / ".config"
    return base / CONFIG_DIR_NAME


def user_config_path(
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    return user_config_dir(platform=platform, env=env, home=home) / CONFIG_FILE_NAME


def user_log_path(
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    return user_config_dir(platform=platform, env=env, home=home) / LOG_FILE_NAME


def default_config_text() -> str:
    return resources.files("slicer_uri_bridge").joinpath("resources", "default_config.toml").read_text(encoding="utf-8")


def package_config_text() -> str:
    return resources.files("slicer_uri_bridge").joinpath("resources", "package_config.toml").read_text(encoding="utf-8")


@cache
def package_config() -> dict[str, object]:
    return tomllib.loads(package_config_text())


def init_user_config(*, force: bool = False) -> tuple[Path, bool, list[str]]:
    """Create the user config or add missing default options.

    Returns ``(path, created, added_keys)``. ``created`` is True when the file was
    written from the bundled template. ``added_keys`` lists dotted paths inserted
    into an existing config.
    """
    path = user_config_path()
    if path.exists() and not force:
        return path, False, upgrade_user_config(path)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_config_text(), encoding="utf-8")
    return path, True, []


def upgrade_user_config(path: Path | None = None) -> list[str]:
    """Insert missing default options into an existing config file.

    Existing values, comments, and unknown keys are left unchanged. Returns the
    dotted paths that were added. The file is not rewritten when nothing is missing.
    When the file is rewritten, the previous contents are saved as ``config.toml.bak``
    with the original permissions.
    """
    path = user_config_path() if path is None else path
    try:
        original = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Cannot read config file: {path}") from exc

    try:
        updated, added = upgrade_config_text(original)
    except TOMLKitError as exc:
        raise RuntimeError(f"Cannot upgrade invalid config file: {path}") from exc

    if not added or updated == original:
        return []

    tmp_path = path.with_name(f".{path.name}.tmp")
    backup_path = path.with_name(f"{path.name}.bak")
    try:
        shutil.copy2(path, backup_path)
        tmp_path.write_text(updated, encoding="utf-8")
        _copy_file_metadata(path, tmp_path)
        tmp_path.replace(path)
    except OSError as exc:
        raise RuntimeError(f"Cannot write config file: {path}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return added


def upgrade_config_text(user_text: str, default_text: str | None = None) -> tuple[str, list[str]]:
    """Return ``(updated_text, added_dotted_paths)`` for a config string."""
    default_text = default_config_text() if default_text is None else default_text
    user_doc = parse_toml(user_text)
    default_doc = parse_toml(default_text)
    if not user_text.strip():
        text = default_text if default_text.endswith("\n") else f"{default_text}\n"
        return text, _leaf_paths(default_doc, ())

    added = _add_missing(user_doc, default_doc)
    if not added:
        return user_text, []

    updated = user_doc.as_string()
    if not updated.endswith("\n"):
        updated += "\n"

    merged = tomllib.loads(updated)
    for path in added:
        node: object = merged
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise RuntimeError(f"Config upgrade failed to add {path}")
            node = node[part]
    return updated, added


def config_matches_default(path: Path | None = None) -> bool:
    path = user_config_path() if path is None else path
    if not path.is_file():
        return False
    try:
        return path.read_text(encoding="utf-8") == default_config_text()
    except OSError:
        return False


def _copy_file_metadata(source: Path, dest: Path) -> None:
    """Copy mode and, where supported, ownership and other file metadata."""
    try:
        shutil.copystat(source, dest, follow_symlinks=True)
    except OSError:
        os.chmod(dest, stat.S_IMODE(source.stat().st_mode))

    if hasattr(os, "chown"):
        source_stat = source.stat()
        with contextlib.suppress(OSError):
            os.chown(dest, source_stat.st_uid, source_stat.st_gid)


def missing_config_message(path: Path | None = None) -> str:
    path = user_config_path() if path is None else path
    return f"User config not found: {path}. Run `slicer-uri-bridge init-config` first."


def _add_missing(user: Mapping[str, object], default: Mapping[str, object], prefix: tuple[str, ...] = ()) -> list[str]:
    added: list[str] = []
    for key in default:
        path = prefix + (str(key),)
        if key not in user:
            _copy_key(user, default, key)
            added.extend(_leaf_paths(default[key], path))
            continue
        user_value = user[key]
        default_value = default[key]
        if _is_table(user_value) and _is_table(default_value):
            added.extend(_add_missing(user_value, default_value, path))
    return added


def _copy_key(dest: object, source: object, key: object) -> None:
    body = _item_body(source)
    append = getattr(dest, "append", None)
    if body is not None and callable(append):
        pending: list[object] = []
        for item_key, item in body:
            if item_key is None:
                pending.append(item)
                continue
            if _key_name(item_key) != str(key):
                pending = []
                continue
            for extra in pending:
                append(None, copy.deepcopy(extra))
            append(copy.deepcopy(item_key), copy.deepcopy(item))
            return

    dest[key] = copy.deepcopy(_source_item(source, key))  # type: ignore[index]


def _source_item(source: object, key: object) -> object:
    item = getattr(source, "item", None)
    if callable(item):
        return item(key)
    return source[key]  # type: ignore[index]


def _item_body(container: object) -> list[tuple[object, object]] | None:
    body = getattr(container, "body", None)
    if isinstance(body, list):
        return body
    value = getattr(container, "value", None)
    nested = getattr(value, "body", None)
    if isinstance(nested, list):
        return nested
    return None


def _key_name(item_key: object) -> str:
    key = getattr(item_key, "key", item_key)
    return key if isinstance(key, str) else str(key)


def _is_table(value: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(value, Mapping)


def _leaf_paths(node: object, prefix: tuple[str, ...]) -> list[str]:
    if not _is_table(node):
        return [".".join(prefix)] if prefix else []

    leaves: list[str] = []
    for key, value in node.items():
        leaves.extend(_leaf_paths(value, prefix + (str(key),)))
    return leaves
