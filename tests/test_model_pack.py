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
from xml.etree import ElementTree

from slicer_uri_bridge.bambu_project import match_object_ids, replace_plates
from slicer_uri_bridge.files import safe_filename
from slicer_uri_bridge.stl_archive import extract_stl_archive

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

            with self.assertRaisesRegex(ValueError, "found 0"):
                extract_stl_archive(archive_path, folder / "stl")

    def test_rejects_more_than_36_stl_files(self) -> None:
        with temporary_directory() as folder:
            archive_path = folder / "models.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for index in range(37):
                    archive.writestr(f"part-{index}.stl", b"solid model\n")

            with self.assertRaisesRegex(ValueError, "between 1 and 36 STL files"):
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
                self.assertRaisesRegex(ValueError, "exceed the size limit"),
            ):
                extract_stl_archive(folder / "models.zip", folder / "stl")


class BambuProjectTests(unittest.TestCase):
    def test_object_name_mismatch_reports_expected_and_found_names(self) -> None:
        settings = ElementTree.fromstring(
            '<config><object id="2"><metadata key="name" value="first"/></object></config>'
        )

        with self.assertRaisesRegex(RuntimeError, r"Expected names: \['first\.stl'\]; found names: \['first'\]"):
            match_object_ids(settings, [Path("first.stl")])

    def test_creates_named_plates(self) -> None:
        settings = ElementTree.fromstring("<config><plate/><assemble/></config>")
        stl_paths = [Path("first.stl"), Path("second.stl")]

        replace_plates(settings, ["2", "4"], stl_paths)

        plate_names = [
            next(metadata.get("value") for metadata in plate if metadata.get("key") == "plater_name")
            for plate in settings.findall("plate")
        ]
        self.assertEqual(plate_names, ["first.stl", "second.stl"])
