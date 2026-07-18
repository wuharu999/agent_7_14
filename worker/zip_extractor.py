from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from worker.config import MAX_ZIP_EXTRACTED_BYTES, MAX_ZIP_FILES, MAX_ZIP_SINGLE_FILE_BYTES


def _safe_destination(root: Path, archive_name: str) -> Path:
    normalized = archive_name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"Unsafe ZIP path: {archive_name}")
    destination = (root / Path(*pure.parts)).resolve()
    root_resolved = root.resolve()
    if destination != root_resolved and root_resolved not in destination.parents:
        raise ValueError(f"ZIP path escapes destination: {archive_name}")
    return destination


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def extract_zip_safely(zip_path: Path, destination: Path) -> list[Path]:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    extracted_files: list[Path] = []

    with zipfile.ZipFile(zip_path, "r") as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_FILES:
            raise ValueError(f"ZIP contains too many entries ({len(entries)} > {MAX_ZIP_FILES})")
        total_size = sum(info.file_size for info in entries)
        if total_size > MAX_ZIP_EXTRACTED_BYTES:
            raise ValueError("ZIP expanded size exceeds configured limit")

        for info in entries:
            if _is_symlink(info):
                raise ValueError(f"ZIP symbolic link rejected: {info.filename}")
            if info.file_size > MAX_ZIP_SINGLE_FILE_BYTES:
                raise ValueError(f"ZIP member too large: {info.filename}")
            target = _safe_destination(destination, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            extracted_files.append(target)

    return extracted_files
