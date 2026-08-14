from __future__ import annotations

import shutil
import unittest
import uuid
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from slicer_uri_bridge.exceptions import BridgeError
from slicer_uri_bridge.files import safe_filename
from slicer_uri_bridge.stl_archive import MAX_NAME_COLLISIONS, MAX_STL_FILES, extract_stl_archive

TEMP_ROOT = Path(__file__).resolve().parent / ".tmp"


@contextmanager
def temporary_directory() -> Iterator[Path]:
    TEMP_ROOT.mkdir(exist_ok=True)
    path = TEMP_ROOT / f"case-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class StlArchiveTests(unittest.TestCase):
    def test_empty_safe_filename_falls_back_to_model(self) -> None:
        self.assertEqual(safe_filename("folder/..."), "model")

    def test_extracts_only_stl_files(self) -> None:
        with temporary_directory() as folder:
            archive_path = folder / "models.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("models/part.STL", b"solid first\n")
                archive.writestr("other/part.STL", b"solid second\n")
                archive.writestr("__MACOSX/models/._part.STL", b"not a model")
                archive.writestr("._hidden.stl", b"not a model")
                archive.writestr("model.obj", b"ignored")
                archive.writestr("model.step", b"ignored")
                archive.writestr("project.3mf", b"ignored")
                archive.writestr("manual.pdf", b"ignored")

            extracted = extract_stl_archive(archive_path, folder / "stl")

            self.assertEqual([path.name for path in extracted], ["part.STL", "part (1).STL"])

    def test_rejects_archive_without_stl_files(self) -> None:
        with temporary_directory() as folder:
            archive_path = folder / "models.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("model.obj", b"ignored")
                archive.writestr("project.3mf", b"ignored")

            with self.assertRaisesRegex(BridgeError, "at least one STL file"):
                extract_stl_archive(archive_path, folder / "stl")

    def test_ignores_macos_appledouble_stl_stubs(self) -> None:
        with temporary_directory() as folder:
            archive_path = folder / "models.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("__MACOSX/models/._part.stl", b"not a model")
                archive.writestr("._part.stl", b"not a model")

            with self.assertRaisesRegex(BridgeError, "at least one STL file"):
                extract_stl_archive(archive_path, folder / "stl")

    def test_skips_symlinks_and_keeps_stl_inside_destination(self) -> None:
        with temporary_directory() as folder:
            archive_path = folder / "models.zip"
            link = zipfile.ZipInfo("link.stl")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(link, b"/tmp/evil.stl")
                archive.writestr("../escape.stl", b"solid escaped\n")
                archive.writestr("models/../../also.stl", b"solid also\n")

            destination = folder / "stl"
            extracted = extract_stl_archive(archive_path, destination)

            self.assertEqual(sorted(path.name for path in extracted), ["also.stl", "escape.stl"])
            self.assertTrue(all(path.parent == destination.resolve() for path in extracted))
            self.assertFalse((folder / "escape.stl").exists())
            self.assertFalse((destination / "link.stl").exists())

    def test_extracts_more_than_36_stl_files(self) -> None:
        with temporary_directory() as folder:
            archive_path = folder / "models.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for index in range(37):
                    archive.writestr(f"part-{index}.stl", b"solid model\n")

            extracted = extract_stl_archive(archive_path, folder / "stl")

            self.assertEqual(len(extracted), 37)

    def test_rejects_more_than_max_stl_files(self) -> None:
        with temporary_directory() as folder:
            archive_path = folder / "models.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for index in range(MAX_STL_FILES + 1):
                    archive.writestr(f"part-{index}.stl", b"solid model\n")

            with self.assertRaisesRegex(BridgeError, f"at most {MAX_STL_FILES} STL files"):
                extract_stl_archive(archive_path, folder / "stl")

    def test_rejects_excessive_duplicate_stl_names(self) -> None:
        with temporary_directory() as folder:
            archive_path = folder / "models.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for index in range(MAX_NAME_COLLISIONS + 1):
                    archive.writestr(f"folder-{index}/part.stl", b"solid model\n")

            with self.assertRaisesRegex(BridgeError, "too many STL files with the same name"):
                extract_stl_archive(archive_path, folder / "stl")

    def test_enforces_size_limit_while_extracting(self) -> None:
        entry = zipfile.ZipInfo("model.stl")
        entry.file_size = 1
        archive = MagicMock()
        archive.__enter__.return_value = archive
        archive.open.return_value = BytesIO(b"x" * 11)
        archive.infolist.return_value = [entry]

        with temporary_directory() as folder:
            with (
                patch("slicer_uri_bridge.stl_archive.zipfile.ZipFile", return_value=archive),
                patch("slicer_uri_bridge.stl_archive.MAX_MODEL_BYTES", 10),
                self.assertRaisesRegex(BridgeError, "exceed the size limit"),
            ):
                extract_stl_archive(folder / "models.zip", folder / "stl")
