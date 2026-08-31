from __future__ import annotations

import base64
import hashlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence
from urllib.parse import quote, unquote, urlsplit

MAX_QA_IMAGES = 3
MAX_QA_IMPLICIT_IMAGES = 1
MAX_QA_IMAGE_BYTES = 8 * 1024**2
MAX_QA_IMAGE_TOTAL_BYTES = 12 * 1024**2
MIN_QA_IMAGE_DIMENSION = 64
MIN_QA_IMAGE_PIXELS = 4_096

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
_DECORATIVE_IMAGE_NAME = re.compile(
    r"(?:^|[-_.\s])(?:avatar|bullet|favicon|icon|loading|pixel|placeholder|"
    r"spacer|sprite|thumb|thumbnail)(?:$|[-_.\s])|"
    r"(?:图标|圖標|アイコン|아이콘|ícone|icono|иконк)",
    re.IGNORECASE,
)
_GENERIC_IMAGE_LABEL = re.compile(
    r"^(?:img|image|picture|photo|figure|fig|scan|page|"
    r"图|图片|圖|圖片|画像|写真|이미지|사진|imagen|foto|"
    r"изображение|фото)[\s._-]*\d*$",
    re.IGNORECASE,
)

_MARKDOWN_IMAGE = re.compile(
    r"!\[(?P<alt>[^\]\n]{0,200})\]\(\s*<?(?P<target>[^)>\n]+)>?\s*\)"
)
_OBSIDIAN_IMAGE = re.compile(r"!\[\[(?P<target>[^\]\n]{1,500})\]\]")
_HTML_IMAGE = re.compile(r"<img\b(?P<attrs>[^>]{0,2000})>", re.IGNORECASE)
_HTML_ATTRIBUTE = re.compile(
    r"(?P<name>src|alt)\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
_HEADING = re.compile(r"(?m)^(?P<level>#{1,6})[ \t]+(?P<title>[^\n]+)$")
_ASCII_TERM = re.compile(r"[a-z0-9][a-z0-9._+-]*", re.IGNORECASE)
_CJK_RUN = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "do",
    "does",
    "for",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "please",
    "show",
    "the",
    "this",
    "to",
    "what",
    "where",
    "with",
}
_IMAGE_INTENT = re.compile(
    r"(?:图片|图像|照片|截图|示意图|外观|长什么样|看(?:一?下|看)|展示.{0,4}图|"
    r"圖片|圖像|照片|截圖|示意圖|外觀|長什麼樣|"
    r"\b(?:image|picture|photo|diagram|screenshot|show me|look like)\b|"
    r"\b(?:imagem|foto|diagrama|captura de tela|mostrar)\b|"
    r"\b(?:imagen|foto|diagrama|captura de pantalla|mu[eé]strame)\b|"
    r"\b(?:изображени[ея]|фото|схем[ау]|скриншот|покажи)\b|"
    r"(?:画像|写真|図|スクリーンショット|見た目|見せて)|"
    r"(?:이미지|사진|그림|스크린샷|보여))",
    re.IGNORECASE,
)


class RetrievedImageDocument(Protocol):
    path: Path
    text: str


@dataclass(frozen=True)
class _Candidate:
    path: Path
    alt: str
    score: int
    lexical_score: int
    answer_score: int
    document_order: int
    image_order: int


@dataclass(frozen=True)
class _Section:
    heading: str
    text: str
    start: int
    end: int


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


def _image_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    """Read dimensions from bounded image headers without decoding pixels."""
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(image_bytes) >= 24:
        return struct.unpack(">II", image_bytes[16:24])
    if image_bytes[:6] in (b"GIF87a", b"GIF89a") and len(image_bytes) >= 10:
        return struct.unpack("<HH", image_bytes[6:10])
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        if len(image_bytes) < 30:
            return None
        chunk = image_bytes[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(image_bytes[24:27], "little")
            height = 1 + int.from_bytes(image_bytes[27:30], "little")
            return width, height
        if chunk == b"VP8L" and len(image_bytes) >= 25 and image_bytes[20] == 0x2F:
            bits = int.from_bytes(image_bytes[21:25], "little")
            return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
        if chunk == b"VP8 ":
            start = image_bytes.find(b"\x9d\x01\x2a", 20)
            if start >= 0 and len(image_bytes) >= start + 7:
                width, height = struct.unpack("<HH", image_bytes[start + 3 : start + 7])
                return width & 0x3FFF, height & 0x3FFF
        return None
    if not image_bytes.startswith(b"\xff\xd8"):
        return None
    offset = 2
    while offset + 3 < len(image_bytes):
        if image_bytes[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(image_bytes) and image_bytes[offset] == 0xFF:
            offset += 1
        if offset >= len(image_bytes):
            break
        marker = image_bytes[offset]
        offset += 1
        if marker in (0xD8, 0xD9):
            continue
        if marker == 0xDA:
            break
        if offset + 2 > len(image_bytes):
            break
        segment_length = struct.unpack(">H", image_bytes[offset : offset + 2])[0]
        if segment_length < 2 or offset + segment_length > len(image_bytes):
            break
        is_size_marker = (
            marker in range(0xC0, 0xC4)
            or marker in range(0xC5, 0xC8)
            or marker in range(0xC9, 0xCC)
            or marker in range(0xCD, 0xD0)
        )
        if is_size_marker:
            if segment_length >= 7:
                height, width = struct.unpack(">HH", image_bytes[offset + 3 : offset + 7])
                return width, height
        offset += segment_length
    return None


def _is_useful_image(path: Path, label: str = "") -> bool:
    """Reject malformed, tiny, and clearly decorative assets before attachment."""
    if _DECORATIVE_IMAGE_NAME.search(f"{path.stem} {label}"):
        return False
    try:
        size = path.stat().st_size
        if size <= 0 or size > MAX_QA_IMAGE_BYTES:
            return False
        # Every supported format has a small, bounded header containing dimensions.
        with path.open("rb") as image_file:
            dimensions = _image_dimensions(image_file.read(64 * 1024))
    except OSError:
        return False
    if dimensions is None:
        return False
    width, height = dimensions
    return (
        width >= MIN_QA_IMAGE_DIMENSION
        and height >= MIN_QA_IMAGE_DIMENSION
        and width * height >= MIN_QA_IMAGE_PIXELS
    )


def _lexical_terms(value: str) -> set[str]:
    normalized = unquote(value).casefold()
    terms = {
        term
        for term in _ASCII_TERM.findall(normalized)
        if len(term) >= 2 and term not in _QUERY_STOPWORDS
    }
    for run in _CJK_RUN.findall(normalized):
        terms.add(run)
        if len(run) > 1:
            terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def _sections(markdown: str) -> list[_Section]:
    headings = list(_HEADING.finditer(markdown))
    if not headings:
        return [_Section("", markdown, 0, len(markdown))]
    result: list[_Section] = []
    if headings[0].start() > 0:
        result.append(_Section("", markdown[: headings[0].start()], 0, headings[0].start()))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        result.append(
            _Section(
                heading.group("title").strip(),
                markdown[heading.end() : end],
                heading.start(),
                end,
            )
        )
    return result


def _section_for_offset(sections: Sequence[_Section], offset: int) -> _Section:
    for section in sections:
        if section.start <= offset < section.end:
            return section
    return sections[-1]


def _strip_markdown_title(target: str) -> str:
    target = target.strip()
    # Markdown permits an optional quoted title after the destination.
    return re.sub(r"\s+['\"][^'\"]*['\"]\s*$", "", target).strip(" <>")


def _resolve_media_path(raw_target: str, document_path: Path, wiki_root: Path) -> Path | None:
    target = _strip_markdown_title(raw_target.split("|", 1)[0].split("#", 1)[0])
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    decoded = unquote(parsed.path).replace("\\", "/")
    relative = Path(decoded)
    if not decoded or relative.is_absolute():
        return None

    media_root = (wiki_root / "media").resolve()
    candidates: list[Path] = []
    if decoded.startswith("wiki/"):
        candidates.append(wiki_root.parent / relative)
    elif decoded.startswith("media/"):
        candidates.append(wiki_root / relative)
    else:
        candidates.extend(
            (
                document_path.parent / relative,
                media_root / document_path.stem / relative.name,
            )
        )
        if len(relative.parts) == 1:
            matches = sorted(media_root.rglob(relative.name))
            if len(matches) == 1:
                candidates.append(matches[0])

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            relative_to_wiki = resolved.relative_to(wiki_root)
            if not resolved.is_relative_to(media_root):
                continue
            if _contains_symlink(wiki_root, relative_to_wiki):
                continue
            if (
                resolved.is_file()
                and resolved.suffix.casefold() in _IMAGE_MIME_TYPES
                and 0 < resolved.stat().st_size <= MAX_QA_IMAGE_BYTES
            ):
                return resolved
        except (FileNotFoundError, OSError, ValueError):
            continue
    return None


def _linked_images(document: RetrievedImageDocument, wiki_root: Path) -> list[tuple[int, str, Path]]:
    found: list[tuple[int, str, Path]] = []
    for match in _MARKDOWN_IMAGE.finditer(document.text):
        path = _resolve_media_path(match.group("target"), document.path, wiki_root)
        alt = match.group("alt").strip()
        if path is not None and _is_useful_image(path, alt):
            found.append((match.start(), alt, path))
    for match in _OBSIDIAN_IMAGE.finditer(document.text):
        raw_target = match.group("target")
        path = _resolve_media_path(raw_target, document.path, wiki_root)
        alias = raw_target.split("|", 1)[1].strip() if "|" in raw_target else ""
        if path is not None and _is_useful_image(path, alias):
            found.append((match.start(), alias, path))
    for match in _HTML_IMAGE.finditer(document.text):
        attributes = {
            attribute.group("name").casefold(): attribute.group("value")
            for attribute in _HTML_ATTRIBUTE.finditer(match.group("attrs"))
        }
        path = _resolve_media_path(attributes.get("src", ""), document.path, wiki_root)
        alt = attributes.get("alt", "").strip()
        if path is not None and _is_useful_image(path, alt):
            found.append((match.start(), alt, path))
    return sorted(found, key=lambda item: item[0])


def _meaningful_image_label(value: str) -> bool:
    """Require semantic text before a linked image can cross the QA boundary."""
    label = value.strip()
    if not label or _GENERIC_IMAGE_LABEL.fullmatch(label):
        return False
    return bool(_lexical_terms(label))


def select_relevant_qa_images(
    question: str,
    wiki_root: Path,
    documents: Sequence[RetrievedImageDocument],
    *,
    language: str = "en",
    answer: str = "",
) -> list[tuple[str, Path]]:
    """Select linked, semantically labelled images for the final answer.

    PDF extraction can leave many generic ``img-N.png`` files in ``wiki/media``
    without a Markdown reference or description. Those files remain on disk for
    auditability, but they are never eligible for QA output. A linked image must
    have meaningful alt text or section context and match the question. Once the
    final answer is available, it must also contain matching evidence; this keeps
    image selection deterministic and avoids an extra provider call per image.
    """
    wiki_root = wiki_root.resolve()
    query_terms = _lexical_terms(question)
    answer_terms = _lexical_terms(answer)
    explicit_image_request = bool(_IMAGE_INTENT.search(question))
    candidates: list[_Candidate] = []
    seen: set[Path] = set()

    for document_order, document in enumerate(documents):
        sections = _sections(document.text)
        for image_order, (offset, alt, path) in enumerate(
            _linked_images(document, wiki_root)
        ):
            if path in seen:
                continue
            section = _section_for_offset(sections, offset)
            linked_label = alt if _meaningful_image_label(alt) else ""
            section_label = (
                section.heading if _meaningful_image_label(section.heading) else ""
            )
            if not linked_label and not section_label:
                continue
            alt = linked_label or section_label
            context = f"{alt} {section.heading} {section.text[:4000]} {path.stem}"
            context_terms = _lexical_terms(context)
            heading_overlap = len(query_terms & _lexical_terms(section.heading))
            alt_overlap = len(query_terms & _lexical_terms(alt))
            filename_overlap = len(query_terms & _lexical_terms(path.stem))
            text_overlap = len(query_terms & _lexical_terms(section.text[:4000]))
            lexical_score = (
                + heading_overlap * 20
                + alt_overlap * 16
                + filename_overlap * 8
                + text_overlap * 3
            )
            answer_score = len(answer_terms & context_terms)
            # If called before answer generation, question relevance is the
            # available gate. The production path supplies the final answer.
            if not lexical_score or (answer_terms and not answer_score):
                continue
            score = 100 - document_order * 5 + lexical_score + answer_score * 4
            candidates.append(
                _Candidate(
                    path,
                    alt,
                    score,
                    lexical_score,
                    answer_score,
                    document_order,
                    image_order,
                )
            )
            seen.add(path)

    candidates.sort(
        key=lambda candidate: (
            -candidate.score,
            candidate.document_order,
            candidate.image_order,
            candidate.path.as_posix(),
        )
    )
    image_limit = (
        MAX_QA_IMAGES
        if explicit_image_request
        else min(MAX_QA_IMAGES, MAX_QA_IMPLICIT_IMAGES)
    )
    return [(candidate.alt, candidate.path) for candidate in candidates[:image_limit]]


def attach_relevant_qa_images(
    answer: str,
    question: str,
    wiki_root: Path,
    documents: Sequence[RetrievedImageDocument],
    *,
    language: str = "en",
) -> str:
    """Append private transport markers for deterministic images to an answer."""
    if answer.lstrip().startswith("[KNOWLEDGE_GAP]"):
        return answer
    selected = select_relevant_qa_images(
        question,
        wiki_root,
        documents,
        language=language,
        answer=answer,
    )
    if not selected:
        return answer
    project_root = wiki_root.resolve().parent
    markers: list[str] = []
    for alt, path in selected:
        relative_path = path.resolve().relative_to(project_root).as_posix()
        encoded_path = quote(relative_path, safe="/-._~")
        safe_alt = re.sub(r"[\]\r\n]", " ", alt).strip()[:200] or path.stem
        markers.append(f"![{safe_alt}]({encoded_path})")
    return f"{answer.rstrip()}\n\n" + "\n".join(markers)


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
        if not _is_useful_image(image_path, match.group("alt")):
            continue
        if total_bytes + size > MAX_QA_IMAGE_TOTAL_BYTES:
            continue

        encoded = base64.b64encode(image_bytes).decode("ascii")
        images.append(
            {
                "alt": match.group("alt").strip() or image_path.stem,
                "data": encoded,
                # A content digest lets the browser suppress a repeated image
                # in one conversation without receiving its local Wiki path.
                "fingerprint": hashlib.sha256(image_bytes).hexdigest(),
                "mime_type": mime_type,
                "size": size,
            }
        )
        seen.add(image_path)
        total_bytes += size

    return strip_qa_image_markdown(answer), images
