from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .stl_archive import extract_stl_archive

BAMBU_TIMEOUT_SECONDS = 300
MODEL_SETTINGS_PATH = "Metadata/model_settings.config"

logger = logging.getLogger("slicer_uri_bridge")


def run_bambu_conversion(command: list[str], stl_paths: list[Path], work_folder: Path) -> Path:
    output_path = work_folder / "project.3mf"
    process = subprocess.run(
        [
            *command,
            *(str(path.resolve()) for path in stl_paths),
            "--arrange",
            "0",
            "--export-3mf",
            output_path.name,
            "--outputdir",
            str(output_path.parent.resolve()),
        ],
        cwd=work_folder,
        capture_output=True,
        text=True,
        timeout=BAMBU_TIMEOUT_SECONDS,
        check=False,
    )
    if process.returncode != 0 or not output_path.is_file() or not zipfile.is_zipfile(output_path):
        detail = (process.stderr or process.stdout or "no 3MF project was created").strip()
        raise RuntimeError(f"Bambu Studio could not build a project from the STL files: {detail[-1000:]}")
    return output_path


def match_object_ids(settings: ElementTree.Element, stl_paths: list[Path]) -> list[str]:
    objects = {
        name: object_id
        for element in settings.findall("object")
        if (object_id := element.get("id"))
        for metadata in element.findall("metadata")
        if metadata.get("key") == "name" and (name := metadata.get("value"))
    }
    expected_names = [path.name for path in stl_paths]
    if any(name not in objects for name in expected_names):
        raise RuntimeError(
            "Could not match Bambu Studio project objects to the extracted STL files. "
            f"Expected names: {expected_names}; found names: {sorted(objects)}"
        )
    return [objects[name] for name in expected_names]


def _make_plate(index: int, object_id: str, name: str) -> ElementTree.Element:
    plate = ElementTree.Element("plate")
    for key, value in (("plater_id", str(index)), ("plater_name", name)):
        ElementTree.SubElement(plate, "metadata", {"key": key, "value": value})
    instance = ElementTree.SubElement(plate, "model_instance")
    ElementTree.SubElement(instance, "metadata", {"key": "object_id", "value": object_id})
    ElementTree.SubElement(instance, "metadata", {"key": "instance_id", "value": "0"})
    return plate


def replace_plates(settings: ElementTree.Element, object_ids: list[str], stl_paths: list[Path]) -> None:
    for plate in settings.findall("plate"):
        settings.remove(plate)

    for index, (object_id, stl_path) in enumerate(zip(object_ids, stl_paths, strict=True), start=1):
        settings.append(_make_plate(index, object_id, stl_path.name))


def patch_plates(project_path: Path, stl_paths: list[Path]) -> None:
    replacement_path = project_path.with_name(f".{project_path.name}.new")
    try:
        with zipfile.ZipFile(project_path) as source:
            settings = ElementTree.fromstring(source.read(MODEL_SETTINGS_PATH))
            object_ids = match_object_ids(settings, stl_paths)
            replace_plates(settings, object_ids, stl_paths)
            replacement = ElementTree.tostring(settings, encoding="utf-8", xml_declaration=True)
            with zipfile.ZipFile(replacement_path, "w", allowZip64=True) as destination:
                for info in source.infolist():
                    destination.writestr(
                        info, replacement if info.filename == MODEL_SETTINGS_PATH else source.read(info)
                    )
        replacement_path.replace(project_path)
    except Exception:
        with contextlib.suppress(OSError):
            replacement_path.unlink()
        raise


def build_bambu_project(
    archive_path: Path,
    output_path: Path,
    command: list[str],
) -> Path:
    work_folder = Path(tempfile.mkdtemp(prefix=".slicer-uri-bridge-", dir=archive_path.parent))
    try:
        stl_paths = extract_stl_archive(archive_path, work_folder / "stl")
        project_path = run_bambu_conversion(command, stl_paths, work_folder)
        patch_plates(project_path, stl_paths)
        shutil.move(project_path, output_path)
        logger.info("Prepared %d STL file(s), one named plate per file", len(stl_paths))
        return output_path
    finally:
        shutil.rmtree(work_folder, ignore_errors=True)
