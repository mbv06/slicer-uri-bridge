from __future__ import annotations

import stat
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from .files import BUFFER_SIZE, MAX_MODEL_BYTES, STL_SUFFIX, available_destination, safe_filename

MAX_STL_FILES = 128
MAX_NAME_COLLISIONS = 16


def is_zip_symlink(entry: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(entry.external_attr >> 16)


def is_pack_stl(member_name: str) -> bool:
    posix = PurePosixPath(member_name.replace("\\", "/"))
    if posix.name.startswith("._") or "__MACOSX" in posix.parts:
        return False
    return posix.suffix.lower() == STL_SUFFIX


def extract_stl_archive(archive_path: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        entries = [
            entry
            for entry in archive.infolist()
            if not entry.is_dir() and not is_zip_symlink(entry) and is_pack_stl(entry.filename)
        ]
        if not entries:
            raise ValueError("A model-pack ZIP must contain at least one STL file")
        if len(entries) > MAX_STL_FILES:
            raise ValueError(f"A model-pack ZIP must contain at most {MAX_STL_FILES} STL files (found {len(entries)}).")
        name_counts = Counter(safe_filename(entry.filename) for entry in entries)
        if any(count > MAX_NAME_COLLISIONS for count in name_counts.values()):
            raise ValueError(
                f"A model-pack ZIP has too many STL files with the same name "
                f"(limit {MAX_NAME_COLLISIONS} per name)."
            )
        paths = []
        extracted_bytes = 0
        for entry in entries:
            path = available_destination(destination, safe_filename(entry.filename))
            if not path.resolve().is_relative_to(destination):
                continue
            with archive.open(entry) as source, path.open("xb") as output:
                while chunk := source.read(BUFFER_SIZE):
                    extracted_bytes += len(chunk)
                    if extracted_bytes > MAX_MODEL_BYTES:
                        raise ValueError(f"Extracted STL files exceed the size limit: {MAX_MODEL_BYTES} bytes")
                    output.write(chunk)
            paths.append(path)
        if not paths:
            raise ValueError("A model-pack ZIP must contain at least one STL file")
        return paths
