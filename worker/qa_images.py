from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import unquote

MAX_QA_IMAGES = 3
MAX_QA_IMAGE_BYTES = 8 * 1024**2
MAX_QA_IMAGE_TOTAL_BYTES = 12 * 1024**2

_WIKI_IMAGE_MARKDOWN = re.compile(
    r"!\[(?P<alt>[^\]\n]{0,200})\]\(\s*<?(?P<path>wiki/media/[^)>\n]+)>?\s*\)"
)
_LOCAL_IMAGE_MARKDOWN = re.compile(
    r"!\[[^\]\n]{0,200}\]\(\s*<?(?:wiki/media|raw/sources)/[^)>\n]+>?\s*\)"
)
_IMAGE_MIME_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def strip_qa_image_markdown(answer: str) -> str:
    cleaned_answer = _LOCAL_IMAGE_MARKDOWN.sub("", answer)
    return re.sub(r"\n{3,}", "\n\n", cleaned_answer).strip()


def _contains_symlink(project_root: Path, relative_path: Path) -> bool:
    current = project_root
    for part in relative_path.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def extract_qa_images(
    answer: str,
    project_root: Path,
) -> tuple[str, list[dict[str, str | int]]]:
    """Extract bounded, local Wiki images referenced by a QA provider answer."""
    project_root = project_root.resolve()
    image_root = (project_root / "wiki/media").resolve()
    images: list[dict[str, str | int]] = []
    seen: set[Path] = set()
    total_bytes = 0

    for match in _WIKI_IMAGE_MARKDOWN.finditer(answer):
        if len(images) >= MAX_QA_IMAGES:
            break
        raw_path = unquote(match.group("path").strip())
        relative_path = Path(raw_path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            continue
        if _contains_symlink(project_root, relative_path):
            continue
        try:
            image_path = (project_root / relative_path).resolve(strict=True)
            if not image_path.is_relative_to(image_root):
                continue
        except (FileNotFoundError, OSError, ValueError):
            continue
        if image_path in seen or not image_path.is_file():
            continue
        mime_type = _IMAGE_MIME_TYPES.get(image_path.suffix.lower())
        if mime_type is None:
            continue
        image_bytes = image_path.read_bytes()
        size = len(image_bytes)
        if size <= 0 or size > MAX_QA_IMAGE_BYTES:
            continue
        if total_bytes + size > MAX_QA_IMAGE_TOTAL_BYTES:
            continue

        encoded = base64.b64encode(image_bytes).decode("ascii")
        images.append(
            {
                "alt": match.group("alt").strip() or image_path.stem,
                "data": encoded,
                "mime_type": mime_type,
                "size": size,
            }
        )
        seen.add(image_path)
        total_bytes += size

    return strip_qa_image_markdown(answer), images
