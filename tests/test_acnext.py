from __future__ import annotations

import base64
import json
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

from slicer_uri_bridge.acnext import (
    MAX_RESPONSE_BYTES,
    AcnextPayload,
    configured_acnext_endpoint,
    parse_acnext_uri,
    redact_protocol_uri,
    resolve_acnext_download,
)
from slicer_uri_bridge.config import package_config
from slicer_uri_bridge.exceptions import BridgeError
from slicer_uri_bridge.handler import DownloadTarget, main, resolve_download_target


def make_acnext_uri(**overrides: object) -> str:
    payload: dict[str, object] = {
        "accessToken": "test-access-token",
        "hash": "0123456789abcdef0123456789abcdef",
        "fileName": "MakerOnline model.3mf",
        "userId": "11111111-2222-3333-4444-555555555555",
        "fileId": 52056,
        "fileType": 1,
        "regionCn": "0",
        "prod": "1",
    }
    payload.update(overrides)
    encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    query = urllib.parse.urlencode({"jsonvalue": encoded, "timestamp": "1786886230648"})
    return f"acnext://open?{query}"


class AcnextUriTests(unittest.TestCase):
    def test_parses_payload_and_selects_production_endpoint_key(self) -> None:
        payload = parse_acnext_uri(make_acnext_uri())

        self.assertEqual(payload.access_token, "test-access-token")
        self.assertEqual(payload.file_name, "MakerOnline model.3mf")
        self.assertEqual(payload.file_hash, "0123456789abcdef0123456789abcdef")
        self.assertEqual(payload.user_id, "11111111-2222-3333-4444-555555555555")
        self.assertEqual(payload.endpoint_key, "global_production_endpoint")

    def test_selects_packaged_endpoint_for_each_region_and_environment(self) -> None:
        expected = {
            ("0", "0"): "global_development_endpoint",
            ("0", "1"): "global_production_endpoint",
            ("1", "0"): "china_development_endpoint",
            ("1", "1"): "china_production_endpoint",
        }
        packaged = package_config()["acnext"]
        assert isinstance(packaged, dict)

        for flags, key in expected.items():
            with self.subTest(flags=flags):
                payload = parse_acnext_uri(make_acnext_uri(regionCn=flags[0], prod=flags[1]))
                self.assertEqual(payload.endpoint_key, key)
                self.assertEqual(configured_acnext_endpoint(payload, {}), packaged[key])

    def test_user_override_replaces_packaged_endpoint(self) -> None:
        payload = parse_acnext_uri(make_acnext_uri())
        override = "https://maker-proxy.example/v2/download"
        packaged = package_config()["acnext"]
        assert isinstance(packaged, dict)

        self.assertEqual(
            configured_acnext_endpoint(payload, {"acnext": {payload.endpoint_key: override}}),
            override,
        )
        self.assertEqual(configured_acnext_endpoint(payload, {"acnext": {}}), packaged[payload.endpoint_key])

    def test_blank_override_keeps_packaged_endpoint(self) -> None:
        payload = parse_acnext_uri(make_acnext_uri())
        packaged = package_config()["acnext"]
        assert isinstance(packaged, dict)

        self.assertEqual(
            configured_acnext_endpoint(payload, {"acnext": {payload.endpoint_key: "   "}}),
            packaged[payload.endpoint_key],
        )

    def test_rejects_wrong_host_duplicate_value_and_invalid_base64(self) -> None:
        valid_query = urllib.parse.urlsplit(make_acnext_uri()).query
        cases = (
            "acnext://download?" + valid_query,
            "acnext://open?jsonvalue=first&jsonvalue=second",
            "acnext://open?jsonvalue=%25%25%25",
        )

        for uri in cases:
            with self.subTest(uri=uri), self.assertRaisesRegex(BridgeError, "Invalid acnext URI"):
                parse_acnext_uri(uri)

    def test_rejects_missing_required_fields_and_unknown_flags(self) -> None:
        for uri in (make_acnext_uri(accessToken=""), make_acnext_uri(prod="production")):
            with self.subTest(uri=redact_protocol_uri(uri)), self.assertRaisesRegex(BridgeError, "Invalid acnext URI"):
                parse_acnext_uri(uri)

    def test_redacts_embedded_token_from_diagnostics(self) -> None:
        uri = make_acnext_uri(accessToken="sensitive-token")

        redacted = redact_protocol_uri(uri)

        self.assertEqual(redacted, "acnext://open?jsonvalue=<redacted>")
        self.assertNotIn("sensitive-token", redacted)
        self.assertEqual(redact_protocol_uri("ACNEXT:malformed"), redacted)
        self.assertEqual(redact_protocol_uri("prusaslicer:unchanged"), "prusaslicer:unchanged")

    def test_rejects_invalid_endpoint_override(self) -> None:
        payload = parse_acnext_uri(make_acnext_uri())

        with self.assertRaisesRegex(BridgeError, f"acnext.{payload.endpoint_key}"):
            configured_acnext_endpoint(payload, {"acnext": {payload.endpoint_key: "bad\nendpoint"}})

    def test_invalid_endpoint_url_error_names_config_key(self) -> None:
        config = {
            "acnext": {
                "global_production_endpoint": "http://api.makeronline.example/download",
            }
        }

        with self.assertRaisesRegex(
            BridgeError,
            r"acnext\.global_production_endpoint: Only https://",
        ):
            resolve_download_target(make_acnext_uri(), {".3mf"}, config)


class AcnextDownloadResolutionTests(unittest.TestCase):
    def test_rejects_non_https_endpoint_before_opening(self) -> None:
        payload = parse_acnext_uri(make_acnext_uri())

        with patch("slicer_uri_bridge.acnext.urllib.request.build_opener") as build_opener:
            for endpoint in ("http://api.makeronline.test/download", "file:///tmp/download"):
                with self.subTest(endpoint=endpoint), self.assertRaisesRegex(BridgeError, "must use HTTPS"):
                    resolve_acnext_download(payload, endpoint=endpoint)

        build_opener.assert_not_called()

    def test_posts_official_request_shape_and_returns_signed_url(self) -> None:
        payload = AcnextPayload(
            access_token="test-access-token",
            file_hash="0123456789abcdef0123456789abcdef",
            file_name="MakerOnline model.3mf",
            user_id="11111111-2222-3333-4444-555555555555",
            region_cn="0",
            production="1",
        )
        signed_url = (
            "https://acop-prod-private.s3.us-east-2.amazonaws.com/" "MakerOnline%20model.3mf?signature=temporary"
        )
        response_body = json.dumps({"code": 0, "data": signed_url}).encode()
        response = MagicMock()
        response.status = 200
        response.read.return_value = response_body
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response

        with patch("slicer_uri_bridge.acnext.urllib.request.build_opener", return_value=opener):
            result = resolve_acnext_download(
                payload,
                endpoint="https://api.makeronline.com/file/fileService/download",
            )

        self.assertEqual(result, signed_url)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.makeronline.com/file/fileService/download")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "test-access-token")
        self.assertEqual(
            json.loads(request.data),
            {
                "sourceType": "9",
                "ip": "127.0.0.1",
                "userId": "11111111-2222-3333-4444-555555555555",
                "batchDownLoadFlg": "0",
                "hash": "0123456789abcdef0123456789abcdef",
                "fileName": "MakerOnline model.3mf",
            },
        )

    def test_rejects_invalid_download_link_responses(self) -> None:
        payload = parse_acnext_uri(make_acnext_uri())
        cases = (
            (503, b"", "HTTP status 503"),
            (200, b"x" * (MAX_RESPONSE_BYTES + 1), "response is too large"),
            (200, b"not JSON", "invalid download-link response"),
            (200, b"{}", "did not return a download URL"),
            (
                200,
                json.dumps({"data": "https://example.test/model.3mf\nignored"}).encode(),
                "unsupported control characters",
            ),
        )

        for status, response_body, expected_error in cases:
            response = MagicMock()
            response.status = status
            response.read.return_value = response_body
            response.__enter__.return_value = response
            opener = MagicMock()
            opener.open.return_value = response

            with (
                self.subTest(expected_error=expected_error),
                patch("slicer_uri_bridge.acnext.urllib.request.build_opener", return_value=opener),
                self.assertRaisesRegex(BridgeError, expected_error),
            ):
                resolve_acnext_download(
                    payload,
                    endpoint="https://api.makeronline.com/file/fileService/download",
                )

    def test_main_validates_configured_api_and_trusts_its_signed_url(self) -> None:
        config = {
            "security": {
                "allowed_extensions": [".3mf"],
                "allow_plain_http": False,
                "allow_any_original_host": False,
                "allow_local_resolved_hosts": False,
                "allow_printables_bundle": True,
                "post_process_action": "warn",
                "allowed_hosts": ["unrelated.example"],
            },
            "acnext": {
                "global_development_endpoint": "https://custom-maker-api.example/v2/download",
            },
            "bambu_studio": {},
        }
        signed_url = (
            "https://acop-prod-private.s3.us-east-2.amazonaws.com/" "MakerOnline%20model.3mf?signature=temporary"
        )
        model = Path("MakerOnline model.3mf")

        with (
            patch("slicer_uri_bridge.handler.load_config", return_value=config),
            patch("slicer_uri_bridge.handler.validate_remote_url") as validate_url,
            patch("slicer_uri_bridge.handler.resolve_acnext_download", return_value=signed_url) as resolve,
            patch("slicer_uri_bridge.handler.resolve_bambu_command", return_value=["bambu-studio"]),
            patch("slicer_uri_bridge.handler.download_model", return_value=model) as download,
            patch("slicer_uri_bridge.handler.validate_downloaded_file"),
            patch("slicer_uri_bridge.handler.check_3mf_post_process"),
            patch("slicer_uri_bridge.handler.launch_bambu") as launch,
            patch("slicer_uri_bridge.handler.show_error") as show_error,
        ):
            exit_code = main([make_acnext_uri(prod="0")])

        self.assertEqual(exit_code, 0)
        validate_url.assert_called_once_with(
            "https://custom-maker-api.example/v2/download",
            allowed_hosts=set(),
            allow_any_original_host=True,
            allow_plain_http=False,
            check_allowlist=False,
            allow_local_resolved_hosts=False,
        )
        self.assertEqual(resolve.call_args.args[0].file_name, "MakerOnline model.3mf")
        self.assertEqual(resolve.call_args.kwargs["endpoint"], "https://custom-maker-api.example/v2/download")
        self.assertEqual(
            download.call_args.args[0],
            DownloadTarget(signed_url, "MakerOnline model.3mf", trusted_resolver=True),
        )
        launch.assert_called_once()
        show_error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
