#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import logging
import posixpath
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .acnext import (
    ACNEXT_SCHEME,
    acnext_endpoint_override,
    packaged_acnext_endpoint,
    packaged_acnext_endpoints,
    parse_acnext_uri,
    redact_protocol_uri,
    resolve_acnext_download,
)
from .config import missing_config_message, package_config, user_config_path, user_log_path
from .exceptions import BridgeError
from .files import BUFFER_SIZE, MAX_MODEL_BYTES, STL_SUFFIX, ZIP_SUFFIX, available_destination, safe_filename
from .network import USER_AGENT, NoRedirectHandler, has_control_chars
from .stl_archive import extract_stl_archive
from .ui import show_bundle_hint, show_error, show_warning

CONFIG_FILE = user_config_path()
LOG_FILE = user_log_path()
SUPPORTED_QUERY_SCHEMES = {"cura", "crealityprintlink", "prusaslicer", "orcaslicer"}
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
MAX_REDIRECTS = 5
REDIRECT_CODES = {301, 302, 303, 307, 308}
EXECUTABLE_MODE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
PROJECT_SETTINGS_PATH = "Metadata/project_settings.config"
POST_PROCESS_ACTION_DEFAULT = "warn"
POST_PROCESS_ACTIONS = {"ignore", "warn", "block"}
WINDOWS_COMMAND_LINE_LIMIT = 32767

logger = logging.getLogger("slicer_uri_bridge")


@dataclass(frozen=True)
class DownloadTarget:
    url: str
    suggested_name: str | None
    trusted_resolver: bool = False


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open supported slicer-style URIs in local Bambu Studio.")
    parser.add_argument("uri", nargs="?")
    parser.add_argument("--uri-file", "-UriFile", dest="uri_file")
    return parser.parse_args(argv)


def setup_logging() -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=LOG_FILE,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )
    except OSError:
        logging.basicConfig(level=logging.CRITICAL + 1)


def is_host(value: str) -> bool:
    try:
        p = urlsplit(f"//{value}")
        return bool(p.hostname) and not any((p.scheme, p.path, p.query, p.fragment, p.username, p.password))
    except ValueError:
        return False


def normalize_post_process_action(value: object) -> str:
    if isinstance(value, str):
        action = value.strip().lower()
        if action in POST_PROCESS_ACTIONS:
            return action

    logger.warning("Invalid security.post_process_action; using warn")
    return POST_PROCESS_ACTION_DEFAULT


def load_config() -> dict:
    if not CONFIG_FILE.is_file():
        raise BridgeError(missing_config_message(CONFIG_FILE))

    try:
        with CONFIG_FILE.open("rb") as config_file:
            config = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as exc:
        message = f"Invalid config file: {CONFIG_FILE}"
        raise BridgeError(message) from exc

    security = config.get("security")
    if not isinstance(security, dict):
        message = "Missing [security] in config"
        logger.error(message)
        raise BridgeError(message)

    if not isinstance(security.get("allow_plain_http", False), bool):
        logger.warning("Invalid security.allow_plain_http; using false")
        security["allow_plain_http"] = False

    if not isinstance(security.get("allow_any_original_host", False), bool):
        logger.warning("Invalid security.allow_any_original_host; using false")
        security["allow_any_original_host"] = False

    if not isinstance(security.get("allow_local_resolved_hosts", False), bool):
        logger.warning("Invalid security.allow_local_resolved_hosts; using false")
        security["allow_local_resolved_hosts"] = False

    if not isinstance(security.get("allow_printables_bundle", True), bool):
        logger.warning("Invalid security.allow_printables_bundle; using false")
        security["allow_printables_bundle"] = False

    security["post_process_action"] = normalize_post_process_action(security.get("post_process_action"))

    packaged = package_config()
    packaged_security = packaged.get("security")
    if not isinstance(packaged_security, dict):
        raise BridgeError("Invalid packaged config: missing [security].")
    packaged_acnext_endpoints()

    security["allowed_hosts"] = _union_config_strings(
        _parse_allowed_hosts(packaged_security.get("allowed_hosts"), source="packaged security.allowed_hosts"),
        _parse_allowed_hosts(security.get("extra_allowed_hosts"), source="security.extra_allowed_hosts"),
        _parse_allowed_hosts(security.get("allowed_hosts"), source="security.allowed_hosts"),
    )
    if not security["allowed_hosts"] and not security["allow_any_original_host"]:
        message = "No allowed download hosts are configured."
        logger.error(message)
        raise BridgeError(message)

    security["allowed_extensions"] = _union_config_strings(
        _parse_allowed_extensions(
            packaged_security.get("allowed_extensions"),
            source="packaged security.allowed_extensions",
        ),
        _parse_allowed_extensions(
            security.get("extra_allowed_extensions"),
            source="security.extra_allowed_extensions",
        ),
        _parse_allowed_extensions(
            security.get("allowed_extensions"),
            source="security.allowed_extensions",
        ),
    )
    if not security["allowed_extensions"]:
        message = "No allowed model extensions are configured."
        logger.error(message)
        raise BridgeError(message)

    if not isinstance(config.get("bambu_studio"), dict):
        message = "Missing [bambu_studio] in config"
        logger.error(message)
        raise BridgeError(message)

    logger.info(f"Read config file: {CONFIG_FILE}")
    return config


def read_protocol_uri(uri_file: str) -> str:
    path = Path(uri_file).expanduser()
    try:
        data = path.read_bytes()
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16").strip()
        return data.decode("utf-8-sig").strip()
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def resolve_protocol_uri(args: argparse.Namespace) -> str:
    if args.uri:
        return str(args.uri).strip()

    if args.uri_file:
        return read_protocol_uri(str(args.uri_file)).strip()

    raise BridgeError("Missing URI argument.")


def load_allowed_hosts(config: dict) -> tuple[set[str], bool]:
    security = config["security"]
    allow_any = security.get("allow_any_original_host", False)
    return set(security["allowed_hosts"]), allow_any


def normalize_host(host: str) -> str:
    return host.rstrip(".").lower()


def _union_config_strings(*groups: list[str]) -> list[str]:
    return list(dict.fromkeys(value for group in groups for value in group))


def _config_list(raw: object, *, source: str) -> list[object]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("Ignoring invalid list in %s", source)
        return []
    return raw


def _parse_allowed_hosts(raw: object, *, source: str) -> list[str]:
    hosts: list[str] = []
    for host in _config_list(raw, source=source):
        if isinstance(host, str) and is_host(host):
            hosts.append(normalize_host(host))
        else:
            logger.warning("Read invalid host in %s: %s Skipping...", source, host)
    return hosts


def _parse_allowed_extensions(raw: object, *, source: str) -> list[str]:
    extensions: list[str] = []
    for extension in _config_list(raw, source=source):
        if not isinstance(extension, str) or not extension.strip():
            logger.warning("Ignoring invalid extension in %s: %r", source, extension)
            continue

        extension = extension.strip().lower()
        if not extension.startswith("."):
            extension = f".{extension}"
        extensions.append(extension)
    return extensions


def strip_trailing_model_slash(url: str, allowed_extensions: set[str]) -> str:
    without_slash = url.rstrip("/")
    extensions = allowed_extensions | {ZIP_SUFFIX}
    if without_slash != url and any(without_slash.lower().endswith(ext) for ext in extensions):
        return without_slash
    return url


def extract_download(protocol_uri: str, allowed_extensions: set[str]) -> tuple[str, str | None]:
    parsed = urllib.parse.urlsplit(protocol_uri)
    scheme = parsed.scheme.lower()

    if not scheme:
        raise BridgeError("Unsupported URI protocol.")

    if scheme == "bambustudioopen":
        payload = protocol_uri.split(":", 1)[1].lstrip("/")
        download_url = urllib.parse.unquote(payload).strip()
        suggested_name = None
    elif scheme in SUPPORTED_QUERY_SCHEMES:
        if parsed.netloc.lower() != "open":
            raise BridgeError(f"Invalid {scheme} URI.")
        query = urllib.parse.parse_qs(parsed.query)
        download_url = query.get("file", [""])[0].strip()
        suggested_name = query.get("name", [""])[0].strip() or None
    else:
        raise BridgeError(f"Unsupported URI protocol: {scheme}")

    if not download_url:
        raise BridgeError(f"Invalid {scheme} URI.")

    if has_control_chars(download_url):
        raise BridgeError("Download URL contains unsupported control characters.")

    return strip_trailing_model_slash(download_url, allowed_extensions), suggested_name


def resolve_download_target(
    protocol_uri: str,
    allowed_extensions: set[str],
    config: dict,
) -> DownloadTarget:
    scheme = protocol_uri.partition(":")[0].lower()
    if scheme == ACNEXT_SCHEME:
        payload = parse_acnext_uri(protocol_uri)
        override = acnext_endpoint_override(payload, config)
        endpoint = override or packaged_acnext_endpoint(payload)
        try:
            validate_remote_url(
                endpoint,
                allowed_hosts=set(),
                allow_any_original_host=True,
                allow_plain_http=False,
                check_allowlist=False,
                allow_local_resolved_hosts=False,
            )
        except BridgeError as exc:
            if override:
                raise BridgeError(f"Invalid configuration value: acnext.{payload.endpoint_key}: {exc}") from exc
            raise
        download_url = resolve_acnext_download(payload, endpoint=endpoint)
        logger.info("Resolved acnext URI to a signed download URL")
        return DownloadTarget(download_url, payload.file_name, trusted_resolver=True)

    download_url, suggested_name = extract_download(protocol_uri, allowed_extensions)
    logger.info("Resolved input URI with download URL: %s", download_url)
    return DownloadTarget(download_url, suggested_name)


def is_empty_bambustudioopen_uri(protocol_uri: str) -> bool:
    parsed = urllib.parse.urlsplit(protocol_uri)
    if parsed.scheme.lower() != "bambustudioopen":
        return False

    payload = protocol_uri.split(":", 1)[1].lstrip("/")
    return not urllib.parse.unquote(payload).strip()


def filename_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    query_name = urllib.parse.parse_qs(parsed.query).get("name", [""])[0].strip()
    if query_name:
        return posixpath.basename(urllib.parse.unquote(query_name))

    path_name = posixpath.basename(urllib.parse.unquote(parsed.path))
    if path_name:
        return path_name

    return None


def choose_filename(
    final_url: str,
    initial_url: str,
    suggested_name: str | None,
    allowed_extensions: set[str],
) -> str:
    file_name = suggested_name or filename_from_url(final_url) or filename_from_url(initial_url)
    if not file_name:
        raise BridgeError("Could not determine a safe filename from the response or URL.")

    if not Path(file_name).suffix:
        source_name = filename_from_url(final_url) or filename_from_url(initial_url)
        source_suffix = Path(source_name or "").suffix.lower()
        if source_suffix in allowed_extensions:
            file_name = f"{file_name}{source_suffix}"

    return file_name


def is_supported_extension(file_name: str, allowed_extensions: set[str]) -> bool:
    return Path(file_name).suffix.lower() in allowed_extensions


def is_zip_filename(file_name: str | None) -> bool:
    return Path(file_name or "").suffix.lower() == ZIP_SUFFIX


def assert_printables_bundle_allowed(allowed_extensions: set[str], *, allow_bundle: bool) -> None:
    if not allow_bundle:
        raise BridgeError("Printables model-pack downloads are disabled by security.allow_printables_bundle.")
    if STL_SUFFIX not in allowed_extensions:
        raise BridgeError("STL is not enabled in security.allowed_extensions.")


def assert_public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BridgeError(f"Host did not resolve: {host}") from exc

    addresses = {info[4][0] for info in infos if info[4]}
    if not addresses:
        raise BridgeError(f"Host did not resolve: {host}")

    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise BridgeError(
                f"Host resolves to a local/private/reserved address and is not allowed: {host} -> {address}"
            )


def validate_remote_url(
    url: str,
    *,
    allowed_hosts: set[str],
    allow_any_original_host: bool,
    allow_plain_http: bool,
    check_allowlist: bool,
    allow_local_resolved_hosts: bool,
) -> None:
    parsed = urllib.parse.urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        raise BridgeError("The download URL must be absolute.")

    if parsed.scheme.lower() not in ({"https", "http"} if allow_plain_http else {"https"}):
        raise BridgeError("Only https:// download URLs are allowed.")

    if parsed.username or parsed.password:
        raise BridgeError("URLs with embedded credentials are not allowed.")

    host = parsed.hostname
    if not host:
        raise BridgeError("The download URL host is missing.")

    if check_allowlist and not allow_any_original_host:
        if normalize_host(host) not in allowed_hosts:
            raise BridgeError(f"Download host is not allow-listed: {host}")

    if not allow_local_resolved_hosts or not check_allowlist:
        assert_public_host(host)


def download_folder_from_config(config: dict) -> Path | None:
    folder = config.get("download_folder")
    if folder is None or folder == "":
        return None

    if not isinstance(folder, str):
        logger.warning("Invalid download_folder; using system temp")
        return None

    path = Path(folder).expanduser()
    if not path.is_absolute():
        path = CONFIG_FILE.parent / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_destination(file_name: str, allowed_extensions: set[str], download_folder: Path | None) -> Path:
    safe_name = safe_filename(file_name)
    suffix = Path(safe_name).suffix.lower()
    if not suffix:
        raise BridgeError(f"Could not determine file extension: {file_name}")

    if suffix not in allowed_extensions:
        raise BridgeError(f"Unsupported file extension: {suffix}")

    if download_folder is None:
        return Path(tempfile.mkdtemp(prefix="bambu-studio-")) / safe_name

    folder = download_folder
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BridgeError(f"Could not create download folder: {folder}") from exc

    return available_destination(folder, safe_name)


def request_headers(url: str, referrer: str | None) -> dict[str, str]:
    headers = {"Accept": "*/*", "User-Agent": USER_AGENT}
    if referrer:
        headers["Referer"] = referrer

    return headers


def download_model(
    target: DownloadTarget,
    *,
    allowed_extensions: set[str],
    download_folder: Path | None,
    allowed_hosts: set[str],
    allow_any_original_host: bool,
    allow_plain_http: bool,
    allow_local_resolved_hosts: bool,
) -> Path:
    opener = urllib.request.build_opener(NoRedirectHandler())
    initial_url = target.url
    current_url = initial_url
    referrer = None
    # API-resolved URLs stay HTTPS-only even when plain HTTP is enabled for user-supplied URLs.
    allow_plain_http_for_target = allow_plain_http and not target.trusted_resolver

    for redirect_index in range(MAX_REDIRECTS + 1):
        validate_remote_url(
            current_url,
            allowed_hosts=allowed_hosts,
            allow_any_original_host=allow_any_original_host,
            allow_plain_http=allow_plain_http_for_target,
            check_allowlist=not target.trusted_resolver and redirect_index == 0,
            allow_local_resolved_hosts=allow_local_resolved_hosts,
        )

        request = urllib.request.Request(
            current_url,
            headers=request_headers(current_url, referrer),
            method="GET",
        )

        try:
            response = opener.open(request, timeout=60)
        except urllib.error.HTTPError as exc:
            raise BridgeError(f"HTTP download failed: {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise BridgeError(f"HTTP download failed: {exc.reason}") from exc

        with response:
            status = getattr(response, "status", response.getcode())

            if status in REDIRECT_CODES:
                location = response.headers.get("Location")
                if not location:
                    raise BridgeError(f"Redirect response without a Location header: {current_url}")
                referrer = current_url
                current_url = urllib.parse.urljoin(current_url, location)
                continue

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    size = int(content_length)
                except ValueError:
                    size = None
                if size is not None and size > MAX_MODEL_BYTES:
                    raise BridgeError(f"Download is too large: {size} bytes")

            file_name = choose_filename(
                current_url,
                initial_url,
                target.suggested_name,
                allowed_extensions,
            )
            if not is_supported_extension(file_name, allowed_extensions):
                raise BridgeError(f"Unsupported file type in response: {file_name}")

            destination = build_destination(file_name, allowed_extensions, download_folder)
            total = 0
            output_created = False

            try:
                with destination.open("xb") as output:
                    output_created = True
                    while chunk := response.read(BUFFER_SIZE):
                        total += len(chunk)
                        if total > MAX_MODEL_BYTES:
                            raise BridgeError(f"Download exceeded the size limit: {MAX_MODEL_BYTES} bytes")
                        output.write(chunk)
            except Exception:
                if output_created:
                    with contextlib.suppress(OSError):
                        destination.unlink()
                if download_folder is None:
                    with contextlib.suppress(OSError):
                        destination.parent.rmdir()
                raise

            logger.info(f"Downloaded {total} bytes to {destination}")
            return destination

    raise BridgeError(f"Too many redirects. Limit: {MAX_REDIRECTS}")


def validate_downloaded_file(path: Path) -> None:
    if not path.is_file():
        raise BridgeError(f"Model download finished, but the file was not found.\n\n{path}")

    file_stat = path.stat()
    if file_stat.st_size <= 0:
        raise BridgeError("Downloaded file is empty.")

    if has_executable_bits(file_stat.st_mode):
        raise BridgeError("Downloaded file has executable permission bits set, refusing to open it.")

    with path.open("rb") as stream:
        header = stream.read(8)

    if header.startswith(b"MZ"):
        raise BridgeError("Downloaded file is a Windows executable (MZ header), refusing to open it.")

    if header.startswith(b"\x7fELF"):
        raise BridgeError("Downloaded file is an ELF executable, refusing to open it.")

    macho_magics = {
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    }
    if header[:4] in macho_magics:
        raise BridgeError("Downloaded file is a Mach-O executable, refusing to open it.")


def prepare_model_paths(path: Path) -> list[Path]:
    if path.suffix.lower() != ZIP_SUFFIX:
        return [path]
    destination = Path(tempfile.mkdtemp(prefix=".slicer-uri-bridge-stl-", dir=path.parent))
    try:
        return extract_stl_archive(path, destination)
    except BridgeError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(destination, ignore_errors=True)
        detail = str(exc).strip() or type(exc).__name__
        raise BridgeError(f"Could not open the model pack.\n\n{detail}") from exc


def scan_3mf_post_process(path: Path) -> list[str] | None:
    if path.suffix.lower() != ".3mf":
        return None

    try:
        with zipfile.ZipFile(path) as archive:
            settings = json.loads(archive.read(PROJECT_SETTINGS_PATH).decode("utf-8-sig"))
            if isinstance(raw := settings.get("post_process"), list):
                return raw or None
    except Exception as exc:
        logger.warning("Could not inspect 3MF project settings in %s: %s", path, exc)

    return None


def post_process_message(path: Path, commands: list[str]) -> str:
    if len(commands) == 1:
        post_process = commands[0]
    else:
        post_process = "\n\n".join(f"[{index}]\n{command}" for index, command in enumerate(commands, start=1))

    return f"Downloaded 3MF file contains a post-processing script.\n\nFile: {path}\n\npost_process:\n{post_process}"


def check_3mf_post_process(path: Path, action: str) -> None:
    if action == "ignore":
        return

    commands = scan_3mf_post_process(path)
    # No real commands if every value becomes empty after trimming whitespace.
    if commands is None or not any(command.strip() for command in commands):
        return

    message = post_process_message(path, commands)
    logger.warning("%s", message)

    if action == "block":
        raise BridgeError(message)

    show_warning(message)


def has_executable_bits(mode: int) -> bool:
    if IS_WINDOWS:
        return False
    return bool(mode & EXECUTABLE_MODE_BITS)


def platform_config_key() -> str:
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    return "linux"


def resolve_bambu_command(config: dict) -> list[str]:
    platform_key = platform_config_key()
    bambu_studio = config["bambu_studio"]
    configured_path = bambu_studio.get(platform_key)

    if not isinstance(configured_path, str) or not configured_path.strip():
        return resolve_default_open_command()

    configured_path = configured_path.strip()
    path = Path(configured_path).expanduser()

    if IS_MACOS and path.suffix.lower() == ".app":
        if not path.is_dir():
            return warn_and_resolve_default_open_command(f"Bambu Studio app not found: {path}")
        return ["open", "-a", str(path)]

    if path.is_absolute() or path.parent != Path("."):
        if not path.exists():
            if IS_WINDOWS:
                raise BridgeError(f"Bambu Studio executable not found: {path}")
            return warn_and_resolve_default_open_command(f"Bambu Studio executable not found: {path}")
        return [str(path)]

    resolved = shutil.which(configured_path)
    if not resolved:
        if IS_WINDOWS:
            raise BridgeError(f"Bambu Studio executable not found on PATH: {configured_path}")
        return warn_and_resolve_default_open_command(f"Bambu Studio executable not found on PATH: {configured_path}")
    return [resolved]


def warn_and_resolve_default_open_command(message: str) -> list[str]:
    logger.warning(f"{message}. Using platform default file opener.")
    return resolve_default_open_command()


def resolve_default_open_command() -> list[str]:
    if IS_WINDOWS:
        raise BridgeError(f"Missing bambu_studio.windows in {CONFIG_FILE}")

    if IS_MACOS:
        return ["open"]

    for command in ("xdg-open", "gio"):
        resolved = shutil.which(command)
        if resolved and command == "gio":
            return [resolved, "open"]
        if resolved:
            return [resolved]

    raise BridgeError("No default file opener found. Configure bambu_studio.linux.")


def is_single_file_opener(command: list[str]) -> bool:
    return bool(command) and Path(command[0]).name.lower() == "xdg-open"


def detached_process_kwargs() -> dict:
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True
    return kwargs


def launch_bambu(command: list[str], model_paths: list[Path]) -> None:
    if len(model_paths) > 1 and is_single_file_opener(command):
        raise BridgeError(
            "Printables STL bundles require Bambu Studio. "
            f"Configure bambu_studio.{platform_config_key()} with the path to Bambu Studio."
        )
    arguments = [*command, *(str(path) for path in model_paths)]
    if IS_WINDOWS and len(subprocess.list2cmdline(arguments)) >= WINDOWS_COMMAND_LINE_LIMIT:
        raise BridgeError(
            "The Bambu Studio command exceeds the Windows command-line limit. "
            "Use a shorter download_folder or a model pack with fewer or shorter STL filenames."
        )
    try:
        subprocess.Popen(arguments, **detached_process_kwargs())
        logger.info("Opened Bambu Studio with %d file(s)", len(model_paths))
    except OSError as exc:
        raise BridgeError(f"Failed to start Bambu Studio: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    uri: str | None = None
    downloaded_path: Path | None = None
    model_paths: list[Path] = []
    extract_dir: Path | None = None
    download_folder: Path | None = None

    try:
        config = load_config()
        security = config["security"]
        allow_plain_http = security.get("allow_plain_http", False)
        allow_local_resolved_hosts = security.get("allow_local_resolved_hosts", False)
        allowed_extensions = set(security["allowed_extensions"])
        download_folder = download_folder_from_config(config)

        uri = resolve_protocol_uri(args)
        if is_empty_bambustudioopen_uri(uri):
            logger.info("Ignoring empty bambustudioopen URI: %r", uri)
            return 0

        try:
            target = resolve_download_target(uri, allowed_extensions, config)
        except BridgeError:
            logger.error("Input URI: %r", redact_protocol_uri(uri))
            raise

        allow_bundle = security.get("allow_printables_bundle", True)
        predicted_name = target.suggested_name or filename_from_url(target.url)
        if is_zip_filename(predicted_name):
            assert_printables_bundle_allowed(allowed_extensions, allow_bundle=allow_bundle)
        download_extensions = allowed_extensions | (
            {ZIP_SUFFIX} if allow_bundle and STL_SUFFIX in allowed_extensions else set()
        )

        allowed_hosts, allow_any_original_host = load_allowed_hosts(config)
        command = resolve_bambu_command(config)

        downloaded_path = download_model(
            target,
            allowed_extensions=download_extensions,
            download_folder=download_folder,
            allowed_hosts=allowed_hosts,
            allow_any_original_host=allow_any_original_host,
            allow_plain_http=allow_plain_http,
            allow_local_resolved_hosts=allow_local_resolved_hosts,
        )
        validate_downloaded_file(downloaded_path)
        is_bundle = is_zip_filename(downloaded_path.name)
        if is_bundle:
            assert_printables_bundle_allowed(allowed_extensions, allow_bundle=allow_bundle)
        model_paths = prepare_model_paths(downloaded_path)
        if model_paths[0] != downloaded_path:
            extract_dir = model_paths[0].parent
        if is_bundle:
            for model_path in model_paths:
                validate_downloaded_file(model_path)
        check_3mf_post_process(downloaded_path, security["post_process_action"])

        if is_bundle:
            show_bundle_hint()
        launch_bambu(command, model_paths)

        return 0
    except Exception as exc:
        logger.error(f"Failed: {exc}")
        if extract_dir is not None:
            shutil.rmtree(extract_dir, ignore_errors=True)
        if downloaded_path is not None:
            with contextlib.suppress(OSError):
                downloaded_path.unlink()
            if download_folder is None:
                with contextlib.suppress(OSError):
                    downloaded_path.parent.rmdir()
        show_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
