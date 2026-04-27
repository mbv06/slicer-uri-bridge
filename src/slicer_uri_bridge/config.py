from __future__ import annotations

import os
import sys
from importlib import resources
from pathlib import Path
from typing import Mapping

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
    return (
        resources.files("slicer_uri_bridge")
        .joinpath("resources", "default_config.toml")
        .read_text(encoding="utf-8")
    )


def init_user_config(*, force: bool = False) -> tuple[Path, bool]:
    path = user_config_path()
    if path.exists() and not force:
        return path, False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_config_text(), encoding="utf-8")
    return path, True


def config_matches_default(path: Path | None = None) -> bool:
    path = user_config_path() if path is None else path
    if not path.is_file():
        return False
    try:
        return path.read_text(encoding="utf-8") == default_config_text()
    except OSError:
        return False


def missing_config_message(path: Path | None = None) -> str:
    path = user_config_path() if path is None else path
    return f"User config not found: {path}. Run `slicer-uri-bridge init-config` first."
