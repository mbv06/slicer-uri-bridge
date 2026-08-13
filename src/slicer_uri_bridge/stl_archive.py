from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

from .files import BUFFER_SIZE, MAX_MODEL_BYTES, STL_SUFFIX, available_destination, safe_filename

MAX_STL_FILES = 36


def extract_stl_archive(archive_path: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        entries = [
            entry
            for entry in archive.infolist()
            if not entry.is_dir() and PurePosixPath(entry.filename.replace("\\", "/")).suffix.lower() == STL_SUFFIX
        ]
        if not 1 <= len(entries) <= MAX_STL_FILES:
            raise ValueError(
                f"A model-pack ZIP must contain between 1 and {MAX_STL_FILES} STL files (found {len(entries)})."
            )
        paths = []
        extracted_bytes = 0
        for entry in entries:
            path = available_destination(destination, safe_filename(entry.filename))
            with archive.open(entry) as source, path.open("xb") as output:
                while chunk := source.read(BUFFER_SIZE):
                    extracted_bytes += len(chunk)
                    if extracted_bytes > MAX_MODEL_BYTES:
                        raise ValueError(f"Extracted STL files exceed the size limit: {MAX_MODEL_BYTES} bytes")
                    output.write(chunk)
            paths.append(path)
        return paths
