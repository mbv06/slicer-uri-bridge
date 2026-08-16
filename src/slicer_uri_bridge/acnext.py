from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass

from .exceptions import BridgeError
from .network import USER_AGENT, NoRedirectHandler, has_control_chars

ACNEXT_SCHEME = "acnext"
ACNEXT_ENDPOINT_KEYS = {
    ("1", "1"): "china_production_endpoint",
    ("1", "0"): "china_development_endpoint",
    ("0", "1"): "global_production_endpoint",
    ("0", "0"): "global_development_endpoint",
}
MAX_QUERY_LENGTH = 48 * 1024
MAX_PAYLOAD_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class AcnextPayload:
    access_token: str
    file_hash: str
    file_name: str
    user_id: str
    region_cn: str
    production: str

    @property
    def endpoint_key(self) -> str:
        return ACNEXT_ENDPOINT_KEYS[(self.region_cn, self.production)]


def redact_protocol_uri(protocol_uri: str) -> str:
    if protocol_uri.lower().startswith(f"{ACNEXT_SCHEME}:"):
        return "acnext://open?jsonvalue=<redacted>"
    return protocol_uri


def _require_string(payload: dict, key: str, *, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(f"Invalid acnext URI: missing or invalid {key}.")

    value = value.strip()
    if len(value) > max_length or has_control_chars(value):
        raise BridgeError(f"Invalid acnext URI: invalid {key}.")
    return value


def _require_flag(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or value.strip() not in {"0", "1"}:
        raise BridgeError(f"Invalid acnext URI: {key} must be 0 or 1.")
    return value.strip()


def parse_acnext_uri(protocol_uri: str) -> AcnextPayload:
    try:
        parsed = urllib.parse.urlsplit(protocol_uri)
    except ValueError as exc:
        raise BridgeError("Invalid acnext URI.") from exc
    if parsed.scheme.lower() != ACNEXT_SCHEME or parsed.netloc.lower() != "open" or parsed.path not in {"", "/"}:
        raise BridgeError("Invalid acnext URI.")
    if not parsed.query or len(parsed.query) > MAX_QUERY_LENGTH:
        raise BridgeError("Invalid acnext URI: missing or oversized query.")

    try:
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=16)
    except ValueError as exc:
        raise BridgeError("Invalid acnext URI query.") from exc

    encoded_values = query.get("jsonvalue", [])
    if len(encoded_values) != 1 or not encoded_values[0].strip():
        raise BridgeError("Invalid acnext URI: expected one jsonvalue parameter.")

    try:
        encoded = encoded_values[0].strip().replace(" ", "+").encode("ascii")
        decoded = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise BridgeError("Invalid acnext URI: jsonvalue is not valid base64.") from exc

    if not decoded or len(decoded) > MAX_PAYLOAD_BYTES:
        raise BridgeError("Invalid acnext URI: decoded jsonvalue is empty or too large.")

    try:
        payload = json.loads(decoded.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("Invalid acnext URI: jsonvalue is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise BridgeError("Invalid acnext URI: jsonvalue must contain a JSON object.")

    return AcnextPayload(
        access_token=_require_string(payload, "accessToken", max_length=16 * 1024),
        file_hash=_require_string(payload, "hash", max_length=512),
        file_name=_require_string(payload, "fileName", max_length=1024),
        user_id=_require_string(payload, "userId", max_length=512),
        region_cn=_require_flag(payload, "regionCn"),
        production=_require_flag(payload, "prod"),
    )


def configured_acnext_endpoint(payload: AcnextPayload, config: Mapping[str, object]) -> str:
    section = config.get("acnext")
    endpoint = section.get(payload.endpoint_key) if isinstance(section, Mapping) else None
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise BridgeError(f"Missing or invalid configuration value: acnext.{payload.endpoint_key}.")

    endpoint = endpoint.strip()
    if len(endpoint) > 4096 or has_control_chars(endpoint):
        raise BridgeError(f"Invalid configuration value: acnext.{payload.endpoint_key}.")
    return endpoint


def resolve_acnext_download(payload: AcnextPayload, *, endpoint: str) -> str:
    request_body = json.dumps(
        {
            "sourceType": "9",
            "ip": "127.0.0.1",
            "userId": payload.user_id,
            "batchDownLoadFlg": "0",
            "hash": payload.file_hash,
            "fileName": payload.file_name,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=request_body,
        headers={
            "Accept": "application/json",
            "Authorization": payload.access_token,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    opener = urllib.request.build_opener(NoRedirectHandler())

    try:
        response = opener.open(request, timeout=TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        raise BridgeError(f"MakerOnline download-link request failed: {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise BridgeError(f"MakerOnline download-link request failed: {exc.reason}") from exc

    with response:
        status = response.status
        if status != 200:
            raise BridgeError(f"MakerOnline download-link request failed with HTTP status {status}.")
        response_body = response.read(MAX_RESPONSE_BYTES + 1)

    if len(response_body) > MAX_RESPONSE_BYTES:
        raise BridgeError("MakerOnline download-link response is too large.")

    try:
        response_json = json.loads(response_body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("MakerOnline returned an invalid download-link response.") from exc

    download_url = response_json.get("data") if isinstance(response_json, dict) else None
    if not isinstance(download_url, str) or not download_url.strip():
        raise BridgeError("MakerOnline did not return a download URL.")

    download_url = download_url.strip()
    if has_control_chars(download_url):
        raise BridgeError("MakerOnline returned a download URL with unsupported control characters.")
    return download_url
