from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from . import __version__
from .config import config_matches_default, init_user_config, user_config_path
from .manager import main as manager_main


YES_VALUES = {"y", "yes"}
NO_VALUES = {"n", "no"}
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
TEST_URI = (
    "bambustudioopen://https%3A%2F%2Ffiles.printables.com%2Fmedia%2Fprints%2F3161%2Fstls%2F"
    "123914_1f1d8ca1-252a-4770-846f-52f1208d193d%2F3dbenchy.stl"
)


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slicer-uri-bridge",
        description="Register slicer URI handlers and bridge slicer links to Bambu Studio.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_config = subparsers.add_parser("init-config", help="Create the user config.toml from the default template.")
    init_config.add_argument("--force", action="store_true", help="Overwrite an existing user config.toml.")

    subparsers.add_parser("config-path", help="Print the active user config.toml path.")

    subparsers.add_parser("status", help="Show supported URI handler status.")

    subparsers.add_parser("manager", help="Open the interactive URI handler manager.")

    subparsers.add_parser("test", help="Open a known Benchy URI through the registered system handler.")

    for command in ("register", "unregister"):
        action = subparsers.add_parser(command, help=f"{command.capitalize()} URI handlers.")
        action.add_argument("protocols", nargs="*", help="Protocol names or aliases.")
        action.add_argument("--auto", action="store_true", help="Use conservative automatic protocol selection.")
        action.add_argument("--dry-run", action="store_true", help="Print changes without writing them.")

    return parser


def ask_yes_no(prompt: str, *, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = input(f"{prompt} {suffix} ").strip().lower()
        except EOFError:
            return default

        if not answer:
            return default
        if answer in YES_VALUES:
            return True
        if answer in NO_VALUES:
            return False
        print("Please answer yes or no.")


def print_help_hint() -> None:
    print("")
    print("Use `slicer-uri-bridge -h` to see all manual commands.")
    print("")


def configured_bambu_target_exists(value: str) -> bool:
    target = value.strip()
    if not target:
        return False

    path = Path(target).expanduser()
    if path.is_absolute() or path.parent != Path("."):
        return path.exists()
    return shutil.which(target) is not None


def warn_if_bambu_target_missing(config_path: Path) -> None:
    if not config_path.is_file():
        return

    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return

    bambu_studio = config.get("bambu_studio")
    if not isinstance(bambu_studio, dict):
        return

    key = "windows" if IS_WINDOWS else "macos" if IS_MACOS else "linux"
    configured = bambu_studio.get(key)
    if not isinstance(configured, str) or configured_bambu_target_exists(configured):
        return

    eprint(f"Warning: Bambu Studio path from config was not found: {configured}")
    eprint(f"Edit {config_path} and update [bambu_studio].{key}.")
    if key != "windows":
        eprint("Fallback: if this path stays invalid, the bridge will try to open models with your default application.")


def interactive_onboarding() -> int:
    if not sys.stdin.isatty():
        eprint("Interactive setup requires a terminal.")
        eprint("Run `slicer-uri-bridge -h` to see manual commands.")
        return 2

    config_path = user_config_path()
    print("Slicer URI Bridge interactive setup")
    print(f"Config path: {config_path}")
    print_help_hint()

    if not config_path.exists():
        print("No user config was found.")
        if not ask_yes_no("Create it from the bundled default config and continue?", default=True):
            print("Setup cancelled. No changes were made.")
            return 0
        path, _ = init_user_config(force=False)
        print(f"Created config: {path}")
    elif not config_matches_default(config_path):
        print("Your config differs from the bundled default template.")
        if ask_yes_no("Replace your config with the bundled default?", default=False):
            path, _ = init_user_config(force=True)
            print(f"Replaced config: {path}")
        else:
            print("Keeping your existing config.")
    else:
        print("Config already exists and matches the bundled default.")

    warn_if_bambu_target_missing(config_path)
    result = manager_main([])
    if result == 0:
        print("")
        print("You can inspect the result any time with `slicer-uri-bridge status`.")
    return result


def detached_process_kwargs() -> dict:
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True
    return kwargs


def open_system_uri(uri: str) -> None:
    if IS_WINDOWS:
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise RuntimeError("os.startfile is not available on this Python build.")
        startfile(uri)
        return

    if IS_MACOS:
        subprocess.Popen(["open", uri], **detached_process_kwargs())
        return

    xdg_open = shutil.which("xdg-open")
    if xdg_open:
        subprocess.Popen([xdg_open, uri], **detached_process_kwargs())
        return

    gio = shutil.which("gio")
    if gio:
        subprocess.Popen([gio, "open", uri], **detached_process_kwargs())
        return

    raise RuntimeError("No system URI opener found. Install xdg-open or gio.")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        try:
            return interactive_onboarding()
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            eprint(f"Error: {exc}")
            return 1

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init-config":
            path, created = init_user_config(force=args.force)
            print(f"{'Created' if created else 'Config already exists'}: {path}")
            warn_if_bambu_target_missing(path)
            return 0

        if args.command == "config-path":
            print(user_config_path())
            return 0

        if args.command == "status":
            return manager_main(["status"])

        if args.command == "manager":
            return manager_main([])

        if args.command == "test":
            print(f"Opening test URI: {TEST_URI}")
            open_system_uri(TEST_URI)
            return 0

        if args.command in {"register", "unregister"}:
            manager_args = [args.command, *args.protocols]
            if args.auto:
                manager_args.append("--auto")
            if args.dry_run:
                manager_args.append("--dry-run")
            return manager_main(manager_args)

        parser.error(f"Unknown command: {args.command}")
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        eprint(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
