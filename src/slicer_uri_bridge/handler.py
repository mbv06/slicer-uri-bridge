#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import logging
import posixpath
import re
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
import urllib.response
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

from .config import missing_config_message, user_config_path, user_log_path

CONFIG_FILE = user_config_path()
LOG_FILE = user_log_path()
SUPPORTED_QUERY_SCHEMES = {"cura", "crealityprintlink", "prusaslicer", "orcaslicer"}
USER_AGENT = "OrcaSlicer/2.4.0-dev"
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MACOS
MAX_REDIRECTS = 5
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
BUFFER_SIZE = 81920
REDIRECT_CODES = {301, 302, 303, 307, 308}
EXECUTABLE_MODE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


class BridgeError(RuntimeError):
    pass


logger = logging.getLogger("slicer_uri_bridge")


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def return_response(self, req, fp, code, msg, headers):
        return urllib.response.addinfourl(fp, headers, req.full_url, code=code)

    http_error_301 = http_error_302 = http_error_303 = http_error_307 = http_error_308 = return_response


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open supported slicer-style URIs in local Bambu Studio."
    )
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

    allowed_hosts = security.get("allowed_hosts", [])
    valid_hosts = []
    if isinstance(allowed_hosts, list):
        for host in allowed_hosts:
            if isinstance(host, str) and is_host(host):
                valid_hosts.append(normalize_host(host))
            else:
                logger.warning(f"Read invalid host: {host} Skipping...")

    security["allowed_hosts"] = valid_hosts
    if not security["allowed_hosts"] and not security["allow_any_original_host"]:
        message = "Config value must be a list: security.allowed_hosts"
        logger.error(message)
        raise BridgeError(message)

    allowed_extensions = security.get("allowed_extensions", [])
    valid_extensions = []
    if isinstance(allowed_extensions, list):
        for extension in allowed_extensions:
            if not isinstance(extension, str) or not extension.strip():
                logger.warning(f"Ignoring invalid extension in security.allowed_extensions: {extension!r}")
                continue
            
            extension = extension.strip().lower()
            if not extension.startswith("."):
                extension = f".{extension}"
            valid_extensions.append(extension)

    security["allowed_extensions"] = valid_extensions
    if  not security["allowed_extensions"]:
        message = "Config value must be a list: security.allowed_extensions"
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
        try:
            path.unlink()
        except OSError:
            pass


def resolve_protocol_uri(args: argparse.Namespace) -> str:
    if args.uri:
        return args.uri.strip()

    if args.uri_file:
        return read_protocol_uri(args.uri_file).strip()

    raise BridgeError("Missing URI argument.")


def load_allowed_hosts(config: dict) -> tuple[set[str], bool]:
    security = config["security"]
    allow_any = security.get("allow_any_original_host", False)
    return set(security["allowed_hosts"]), allow_any


def normalize_host(host: str) -> str:
    return host.rstrip(".").lower()


def has_control_chars(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def strip_trailing_model_slash(url: str, allowed_extensions: set[str]) -> str:
    without_slash = url.rstrip("/")
    if without_slash != url and any(without_slash.lower().endswith(ext) for ext in allowed_extensions):
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
                "Host resolves to a local/private/reserved address and is not allowed: "
                f"{host} -> {address}"
            )


def validate_remote_url(
    url: str,
    *,
    allowed_hosts: set[str],
    allow_any_original_host: bool,
    allow_plain_http: bool,
    check_allowlist: bool,
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
    safe_name = safe_download_filename(file_name)
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


def safe_download_filename(file_name: str) -> str:
    name = PureWindowsPath(PurePosixPath(file_name).name).name.strip()
    name = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]+', "_", name).strip(" .")
    return name or "model"


def available_destination(folder: Path, file_name: str) -> Path:
    destination = folder / file_name
    if not destination.exists():
        return destination

    path = Path(file_name)
    stem = path.stem or "model"
    suffix = path.suffix
    index = 1
    while True:
        candidate = folder / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def request_headers(url: str, referrer: str | None) -> dict[str, str]:
    headers = {"Accept": "*/*", "User-Agent": USER_AGENT}
    if referrer:
        headers["Referer"] = referrer

    return headers


def download_model(
    initial_url: str,
    *,
    suggested_name: str | None,
    allowed_extensions: set[str],
    download_folder: Path | None,
    allowed_hosts: set[str],
    allow_any_original_host: bool,
    allow_plain_http: bool,
) -> Path:
    opener = urllib.request.build_opener(NoRedirectHandler())
    current_url = initial_url
    referrer = None

    for redirect_index in range(MAX_REDIRECTS + 1):
        validate_remote_url(
            current_url,
            allowed_hosts=allowed_hosts,
            allow_any_original_host=allow_any_original_host,
            allow_plain_http=allow_plain_http,
            check_allowlist=redirect_index == 0,
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
                if size is not None and size > MAX_DOWNLOAD_BYTES:
                    raise BridgeError(f"Download is too large: {size} bytes")

            file_name = choose_filename(
                current_url,
                initial_url,
                suggested_name,
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
                        if total > MAX_DOWNLOAD_BYTES:
                            raise BridgeError(
                                f"Download exceeded the size limit: {MAX_DOWNLOAD_BYTES} bytes"
                        )
                        output.write(chunk)
            except Exception:
                if output_created:
                    try:
                        destination.unlink()
                    except OSError:
                        pass
                if download_folder is None:
                    try:
                        destination.parent.rmdir()
                    except OSError:
                        pass
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
        if not path.exists():
            return warn_and_resolve_default_open_command(f"Bambu Studio app not found: {path}")
        return ["open", "-a", str(path), "--args"]

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
        return warn_and_resolve_default_open_command(
            f"Bambu Studio executable not found on PATH: {configured_path}"
        )
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


def detached_process_kwargs() -> dict:
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True
    return kwargs


def launch_bambu(command: list[str], model_path: Path) -> None:
    try:
        subprocess.Popen([*command, str(model_path)], **detached_process_kwargs())
        logger.info(f"Opened Bambu Studio with file: {model_path}")
    except OSError as exc:
        raise BridgeError(f"Failed to start Bambu Studio: {exc}") from exc


def show_error(message: str) -> None:
    print(message, file=sys.stderr)
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("Bambu Studio URI Bridge", message)
        root.destroy()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    local_path: Path | None = None
    download_folder: Path | None = None

    try:
        config = load_config()
        security = config["security"]
        allow_plain_http = security.get("allow_plain_http", False)
        allowed_extensions = security["allowed_extensions"]
        download_folder = download_folder_from_config(config)

        uri = resolve_protocol_uri(args)
        download_url, suggested_name = extract_download(uri, allowed_extensions)
        logger.info(f"Resolved input URI with download URL: {download_url}")
        allowed_hosts, allow_any_original_host = load_allowed_hosts(config)
        validate_remote_url(
            download_url,
            allowed_hosts=allowed_hosts,
            allow_any_original_host=allow_any_original_host,
            allow_plain_http=allow_plain_http,
            check_allowlist=True,
        )

        command = resolve_bambu_command(config)

        local_path = download_model(
            download_url,
            suggested_name=suggested_name,
            allowed_extensions=allowed_extensions,
            download_folder=download_folder,
            allowed_hosts=allowed_hosts,
            allow_any_original_host=allow_any_original_host,
            allow_plain_http=allow_plain_http,
        )
        validate_downloaded_file(local_path)

        launch_bambu(command, local_path)

        return 0
    except Exception as exc:
        logger.error(f"Failed: {exc}")
        if local_path:
            try:
                local_path.unlink()
            except OSError:
                pass
            if download_folder is None:
                try:
                    local_path.parent.rmdir()
                except OSError:
                    pass
        show_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
