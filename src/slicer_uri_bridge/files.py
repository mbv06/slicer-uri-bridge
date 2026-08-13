from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath

BUFFER_SIZE = 81920
MAX_MODEL_BYTES = 200 * 1024 * 1024
STL_SUFFIX = ".stl"
ZIP_SUFFIX = ".zip"


def safe_filename(file_name: str) -> str:
    name = PureWindowsPath(PurePosixPath(file_name).name).name.strip()
    name = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]+', "_", name).strip(" .")
    return name or "model"


def available_destination(folder: Path, file_name: str) -> Path:
    destination = folder / file_name
    if not destination.exists():
        return destination

    path = Path(file_name)
    index = 1
    while True:
        destination = folder / f"{path.stem or 'model'} ({index}){path.suffix}"
        if not destination.exists():
            return destination
        index += 1
