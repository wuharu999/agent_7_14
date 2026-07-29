from __future__ import annotations

from pathlib import PurePath

DOCUMENT_SOURCE_SUFFIXES = (".pdf", ".docx", ".pptx", ".xlsx")
TEXT_SOURCE_SUFFIXES = (
    ".md",
    ".mdx",
    ".txt",
    ".csv",
    ".json",
    ".html",
    ".htm",
    ".xml",
    ".yaml",
    ".yml",
)
# Published unchanged as source assets for the multimodal LLM Wiki ingester.
VISUAL_ASSET_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
)
ARCHIVE_UPLOAD_SUFFIXES = (".zip",)

SUPPORTED_SOURCE_SUFFIXES = frozenset(
    DOCUMENT_SOURCE_SUFFIXES + TEXT_SOURCE_SUFFIXES + VISUAL_ASSET_SUFFIXES
)
SUPPORTED_UPLOAD_SUFFIXES = frozenset(
    (*SUPPORTED_SOURCE_SUFFIXES, *ARCHIVE_UPLOAD_SUFFIXES)
)
UPLOAD_ACCEPT = ",".join(sorted(SUPPORTED_UPLOAD_SUFFIXES))


def upload_suffix(filename: str) -> str:
    return PurePath((filename or "").replace("\\", "/")).suffix.casefold()


def is_supported_upload(filename: str) -> bool:
    return upload_suffix(filename) in SUPPORTED_UPLOAD_SUFFIXES
