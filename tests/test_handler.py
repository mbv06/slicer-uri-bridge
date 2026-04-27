from __future__ import annotations

import shutil
import subprocess
import unittest
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from slicer_uri_bridge.handler import (
    BridgeError,
    build_destination,
    choose_filename,
    extract_download,
    filename_from_url,
    normalize_host,
    read_protocol_uri,
    has_executable_bits,
    launch_bambu,
    validate_downloaded_file,
    validate_remote_url,
)


TEMP_ROOT = Path(__file__).resolve().parent / ".tmp"


@contextmanager
def temporary_directory() -> Iterator[str]:
    TEMP_ROOT.mkdir(exist_ok=True)
    path = TEMP_ROOT / f"case-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class DownloadUriTests(unittest.TestCase):
    def test_bambu_uri_decodes_payload_and_strips_model_slash(self) -> None:
        url, suggested_name = extract_download(
            "bambustudioopen://https%3A%2F%2Ffiles.example%2Fmodels%2Fbenchy.3mf%2F",
            {".3mf", ".stl"},
        )

        self.assertEqual(url, "https://files.example/models/benchy.3mf")
        self.assertIsNone(suggested_name)

    def test_query_style_uri_extracts_file_and_name(self) -> None:
        url, suggested_name = extract_download(
            "prusaslicer://open?file=https%3A%2F%2Ffiles.example%2Fpart.stl&name=Display%20Name.3mf",
            {".3mf", ".stl"},
        )

        self.assertEqual(url, "https://files.example/part.stl")
        self.assertEqual(suggested_name, "Display Name.3mf")

    def test_query_style_uri_requires_open_host(self) -> None:
        with self.assertRaisesRegex(BridgeError, "Invalid cura URI"):
            extract_download("cura://download?file=https%3A%2F%2Ffiles.example%2Fpart.stl", {".stl"})

    def test_download_url_rejects_control_characters(self) -> None:
        with self.assertRaisesRegex(BridgeError, "control characters"):
            extract_download(
                "bambustudioopen://https%3A%2F%2Ffiles.example%2Fbad%0Aname.3mf",
                {".3mf"},
            )


class FilenameTests(unittest.TestCase):
    def test_filename_from_url_prefers_query_name_basename(self) -> None:
        self.assertEqual(
            filename_from_url("https://files.example/download?name=folder%2Fmodel%20v2.3mf"),
            "model v2.3mf",
        )

    def test_choose_filename_adds_suffix_from_download_url(self) -> None:
        self.assertEqual(
            choose_filename(
                "https://cdn.example/models/part.stl?token=abc",
                "https://files.example/download",
                "friendly-name",
                {".3mf", ".stl"},
            ),
            "friendly-name.stl",
        )

    def test_build_destination_uses_download_folder_directly(self) -> None:
        with temporary_directory() as temp_dir:
            destination = build_destination("bad name $$..3mf", {".3mf"}, Path(temp_dir))
            destination.write_bytes(b"solid model\n")

            self.assertEqual(destination, Path(temp_dir) / "bad name $$..3mf")
            self.assertEqual(destination.read_bytes(), b"solid model\n")

    def test_build_destination_adds_chrome_style_suffix_for_existing_file(self) -> None:
        with temporary_directory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "model.step").write_bytes(b"first")
            (folder / "model (1).step").write_bytes(b"second")

            destination = build_destination("model.step", {".step"}, folder)

            self.assertEqual(destination, folder / "model (2).step")

    def test_build_destination_uses_python_temp_folder_when_download_folder_is_missing(self) -> None:
        with temporary_directory() as temp_dir:
            with patch("slicer_uri_bridge.handler.tempfile.mkdtemp", return_value=temp_dir) as mkdtemp:
                destination = build_destination("original name.obj", {".obj"}, None)

            mkdtemp.assert_called_once_with(prefix="bambu-studio-")
            self.assertEqual(destination, Path(temp_dir) / "original name.obj")

    def test_build_destination_keeps_path_components_out_of_filename(self) -> None:
        with temporary_directory() as temp_dir:
            destination = build_destination("../nested\\model.3mf", {".3mf"}, Path(temp_dir))

            self.assertEqual(destination, Path(temp_dir) / "model.3mf")

    def test_build_destination_rejects_disallowed_suffix(self) -> None:
        with self.assertRaisesRegex(BridgeError, "Unsupported file extension"):
            build_destination("model.exe", {".3mf"}, None)

    def test_build_destination_rejects_missing_suffix(self) -> None:
        with self.assertRaisesRegex(BridgeError, "Could not determine file extension"):
            build_destination("model", {".3mf", ".step"}, None)


class RemoteUrlValidationTests(unittest.TestCase):
    def test_validate_remote_url_checks_allowlist_and_public_host(self) -> None:
        with patch("slicer_uri_bridge.handler.assert_public_host") as assert_public_host:
            validate_remote_url(
                "https://Files.Example/model.3mf",
                allowed_hosts={"files.example"},
                allow_any_original_host=False,
                allow_plain_http=False,
                check_allowlist=True,
            )

        assert_public_host.assert_called_once_with("files.example")

    def test_validate_remote_url_rejects_plain_http_by_default(self) -> None:
        with self.assertRaisesRegex(BridgeError, "Only https"):
            validate_remote_url(
                "http://files.example/model.3mf",
                allowed_hosts={"files.example"},
                allow_any_original_host=False,
                allow_plain_http=False,
                check_allowlist=True,
            )

    def test_validate_remote_url_rejects_embedded_credentials(self) -> None:
        with self.assertRaisesRegex(BridgeError, "embedded credentials"):
            validate_remote_url(
                "https://user:secret@files.example/model.3mf",
                allowed_hosts={"files.example"},
                allow_any_original_host=False,
                allow_plain_http=False,
                check_allowlist=True,
            )

    def test_validate_remote_url_skips_allowlist_for_redirect_targets(self) -> None:
        with patch("slicer_uri_bridge.handler.assert_public_host") as assert_public_host:
            validate_remote_url(
                "https://cdn.example/model.3mf",
                allowed_hosts={"files.example"},
                allow_any_original_host=False,
                allow_plain_http=False,
                check_allowlist=False,
            )

        assert_public_host.assert_called_once_with("cdn.example")


class FileValidationTests(unittest.TestCase):
    def test_validate_downloaded_file_accepts_non_empty_model_payload(self) -> None:
        with temporary_directory() as temp_dir:
            path = Path(temp_dir) / "model.stl"
            path.write_bytes(b"solid model\n")

            validate_downloaded_file(path)

    def test_validate_downloaded_file_rejects_empty_file(self) -> None:
        with temporary_directory() as temp_dir:
            path = Path(temp_dir) / "model.stl"
            path.touch()

            with self.assertRaisesRegex(BridgeError, "empty"):
                validate_downloaded_file(path)

    def test_validate_downloaded_file_rejects_windows_executable_header(self) -> None:
        with temporary_directory() as temp_dir:
            path = Path(temp_dir) / "model.stl"
            path.write_bytes(b"MZ\x90\x00")

            with self.assertRaisesRegex(BridgeError, "Windows executable"):
                validate_downloaded_file(path)

    def test_validate_downloaded_file_rejects_executable_permission_bits(self) -> None:
        with temporary_directory() as temp_dir:
            path = Path(temp_dir) / "model.stl"
            path.write_bytes(b"solid model\n")

            with patch("slicer_uri_bridge.handler.has_executable_bits", return_value=True):
                with self.assertRaisesRegex(BridgeError, "executable permission"):
                    validate_downloaded_file(path)

    def test_has_executable_bits_detects_posix_execute_bits(self) -> None:
        with patch("slicer_uri_bridge.handler.IS_WINDOWS", False):
            self.assertTrue(has_executable_bits(0o100755))
            self.assertFalse(has_executable_bits(0o100644))

    def test_has_executable_bits_is_disabled_on_windows(self) -> None:
        with patch("slicer_uri_bridge.handler.IS_WINDOWS", True):
            self.assertFalse(has_executable_bits(0o100755))


class ProtocolFileTests(unittest.TestCase):
    def test_read_protocol_uri_decodes_bom_and_removes_temp_file(self) -> None:
        with temporary_directory() as temp_dir:
            path = Path(temp_dir) / "uri.txt"
            path.write_text("  prusaslicer://open?file=x  ", encoding="utf-16")

            with patch.object(Path, "unlink") as unlink:
                self.assertEqual(read_protocol_uri(str(path)), "prusaslicer://open?file=x")

        unlink.assert_called_once_with()


class LaunchTests(unittest.TestCase):
    def test_launch_bambu_detaches_output_streams(self) -> None:
        with (
            patch("slicer_uri_bridge.handler.IS_WINDOWS", False),
            patch("slicer_uri_bridge.handler.subprocess.Popen") as popen,
        ):
            launch_bambu(["bambu-studio"], Path("/tmp/model.stl"))

        _, kwargs = popen.call_args
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertIs(kwargs["start_new_session"], True)


class HostNormalizationTests(unittest.TestCase):
    def test_normalize_host_lowercases_and_strips_trailing_dots(self) -> None:
        self.assertEqual(normalize_host("Files.Example..."), "files.example")


if __name__ == "__main__":
    unittest.main()
