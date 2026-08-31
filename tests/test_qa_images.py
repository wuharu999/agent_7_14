from __future__ import annotations

import base64
import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

from worker import qa_images


@dataclass(frozen=True)
class RetrievedDocument:
    path: Path
    text: str


def _image_bytes(name: str, width: int = 320, height: int = 240) -> bytes:
    suffix = Path(name).suffix.casefold()
    if suffix == ".png":
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", width, height)
    if suffix in {".jpg", ".jpeg"}:
        return b"\xff\xd8\xff\xc0" + struct.pack(">H", 11) + bytes(
            [8]
        ) + struct.pack(">HH", height, width) + b"\x01\x01\x00\x00"
    if suffix == ".gif":
        return b"GIF89a" + struct.pack("<HH", width, height)
    if suffix == ".webp":
        return (
            b"RIFF"
            + struct.pack("<I", 22)
            + b"WEBPVP8X"
            + struct.pack("<I", 10)
            + b"\x00\x00\x00\x00"
            + (width - 1).to_bytes(3, "little")
            + (height - 1).to_bytes(3, "little")
        )
    return b"image-data"


def _media_file(
    root: Path,
    name: str,
    content: bytes | None = None,
    *,
    width: int = 320,
    height: int = 240,
) -> Path:
    path = root / "wiki" / "media" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if content is not None else _image_bytes(name, width, height))
    return path


def test_extracts_existing_bounded_wiki_image(tmp_path: Path) -> None:
    image_path = _media_file(tmp_path, "manual/robot.png")
    answer = "机器人外观如下。\n\n![机器人正面](wiki/media/manual/robot.png)"

    cleaned, images = qa_images.extract_qa_images(answer, tmp_path)

    assert cleaned == "机器人外观如下。"
    assert len(images) == 1
    assert images[0]["alt"] == "机器人正面"
    assert images[0]["mime_type"] == "image/png"
    assert images[0]["size"] == image_path.stat().st_size
    assert base64.b64decode(str(images[0]["data"])) == image_path.read_bytes()
    assert images[0]["fingerprint"] == hashlib.sha256(image_path.read_bytes()).hexdigest()


def test_rejects_original_source_asset_image(tmp_path: Path) -> None:
    image_path = tmp_path / "raw" / "sources" / "tian_gong" / "upload-1" / "robot.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"image-data")

    cleaned, images = qa_images.extract_qa_images(
        "![机器人正面](raw/sources/tian_gong/upload-1/robot.png)",
        tmp_path,
    )

    assert cleaned == ""
    assert images == []


def test_rejects_traversal_symlink_and_unsupported_type(tmp_path: Path) -> None:
    media = tmp_path / "wiki" / "media"
    media.mkdir(parents=True)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    (media / "escape.png").symlink_to(outside)
    _media_file(tmp_path, "unsafe.svg", b"<svg></svg>")
    answer = "\n".join(
        (
            "![traversal](wiki/media/../../outside.png)",
            "![symlink](wiki/media/escape.png)",
            "![svg](wiki/media/unsafe.svg)",
        )
    )

    cleaned, images = qa_images.extract_qa_images(answer, tmp_path)

    assert cleaned == ""
    assert images == []


def test_enforces_image_count_and_total_size(tmp_path: Path, monkeypatch) -> None:
    for index in range(4):
        _media_file(tmp_path, f"image-{index}.jpg")
    monkeypatch.setattr(qa_images, "MAX_QA_IMAGES", 2)
    monkeypatch.setattr(qa_images, "MAX_QA_IMAGE_BYTES", 40)
    monkeypatch.setattr(qa_images, "MAX_QA_IMAGE_TOTAL_BYTES", 70)
    answer = "\n".join(
        f"![image {index}](wiki/media/image-{index}.jpg)" for index in range(4)
    )

    _cleaned, images = qa_images.extract_qa_images(answer, tmp_path)

    assert [image["alt"] for image in images] == ["image 0", "image 1"]


def test_duplicate_image_is_sent_once(tmp_path: Path) -> None:
    _media_file(tmp_path, "same.webp")
    answer = (
        "![first](wiki/media/same.webp)\n"
        "![second](wiki/media/same.webp)"
    )

    _cleaned, images = qa_images.extract_qa_images(answer, tmp_path)

    assert len(images) == 1


def test_selects_image_from_best_matching_markdown_section(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "manuals" / "walker.md"
    charging = _media_file(tmp_path, "walker/charging.png")
    emergency = _media_file(tmp_path, "walker/emergency-stop.png")
    document = RetrievedDocument(
        path=page,
        text=(
            "# Walker manual\n\n"
            "## Charging\nThe charging indicator is blue.\n"
            "![Charging indicator](../media/walker/charging.png)\n\n"
            "## Emergency stop\nPress the red emergency stop button.\n"
            "![Emergency stop button](../media/walker/emergency-stop.png)\n"
        ),
    )

    selected = qa_images.select_relevant_qa_images(
        "Where is the emergency stop button?",
        wiki,
        [document],
    )

    assert selected == [("Emergency stop button", emergency)]
    assert charging not in [path for _alt, path in selected]


def test_supports_obsidian_and_html_image_references(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "manuals" / "network.md"
    port = _media_file(tmp_path, "network/port.png")
    cable = _media_file(tmp_path, "network/cable.jpg")
    document = RetrievedDocument(
        path=page,
        text=(
            "## Ethernet port\nConnect the Ethernet cable here.\n"
            "![[../media/network/port.png|Ethernet port]]\n"
            '<img src="../media/network/cable.jpg" alt="Ethernet cable">\n'
        ),
    )

    selected = qa_images.select_relevant_qa_images(
        "Show me the Ethernet port and cable",
        wiki,
        [document],
    )

    assert selected == [("Ethernet port", port), ("Ethernet cable", cable)]


def test_unreferenced_extracted_media_is_never_sent_even_for_image_request(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "sources" / "walker-guide.md"
    _media_file(tmp_path, "walker-guide/img-1.png")
    _media_file(tmp_path, "walker-guide/img-2.png")
    document = RetrievedDocument(
        path=page,
        text="# Walker guide\n\nThe manual describes the robot exterior.",
    )

    assert qa_images.select_relevant_qa_images(
        "Show me pictures of the robot exterior",
        wiki,
        [document],
        language="en",
    ) == []


def test_unreferenced_media_is_not_sent_for_unrelated_ordinary_question(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "sources" / "walker-guide.md"
    _media_file(tmp_path, "walker-guide/img-1.png")
    document = RetrievedDocument(
        path=page,
        text="# Walker guide\n\nThe manual describes the robot exterior.",
    )

    selected = qa_images.select_relevant_qa_images(
        "How does account authentication work?",
        wiki,
        [document],
        language="en",
    )

    assert selected == []


def test_linked_image_without_question_relevance_is_not_sent(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "sources" / "walker-guide.md"
    first = _media_file(tmp_path, "walker-guide/front.png")
    second = _media_file(tmp_path, "walker-guide/rear.png")
    document = RetrievedDocument(
        path=page,
        text=(
            "# Walker guide\n\n"
            "![Front](../media/walker-guide/front.png)\n"
            "![Rear](../media/walker-guide/rear.png)"
        ),
    )

    selected = qa_images.select_relevant_qa_images(
        "What capabilities are available?",
        wiki,
        [document],
    )

    assert selected == []
    assert second not in [path for _alt, path in selected]


def test_attached_image_markers_round_trip_without_exposing_paths(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "sources" / "机器人 手册.md"
    image = _media_file(tmp_path, "机器人 手册/正面 图.png")
    document = RetrievedDocument(
        path=page,
        text="# 机器人手册\n\n## 外观\n机器人正面。\n![机器人正面](../media/机器人 手册/正面 图.png)",
    )

    answer = qa_images.attach_relevant_qa_images(
        "机器人正面如下。",
        "请展示机器人外观图片",
        wiki,
        [document],
        language="zh-CN",
    )
    cleaned, images = qa_images.extract_qa_images(answer, tmp_path)

    assert cleaned == "机器人正面如下。"
    assert len(images) == 1
    assert images[0]["alt"] == "机器人正面"
    assert base64.b64decode(str(images[0]["data"])) == image.read_bytes()


def test_does_not_attach_images_to_knowledge_gap_answer(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "sources" / "guide.md"
    _media_file(tmp_path, "guide/img-1.png")
    document = RetrievedDocument(path=page, text="# Guide")

    answer = qa_images.attach_relevant_qa_images(
        "[KNOWLEDGE_GAP]\nMissing evidence.",
        "Show me an image",
        wiki,
        [document],
    )

    assert answer == "[KNOWLEDGE_GAP]\nMissing evidence."


def test_duplicate_markdown_reference_is_selected_once(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "sources" / "guide.md"
    image = _media_file(tmp_path, "guide/front.png")
    document = RetrievedDocument(
        path=page,
        text=(
            "# Guide\n\n## Front view\n"
            "![Robot front](../media/guide/front.png)\n"
            "![Robot front](../media/guide/front.png)"
        ),
    )

    selected = qa_images.select_relevant_qa_images(
        "Show me the robot front image",
        wiki,
        [document],
    )

    assert selected == [("Robot front", image)]


def test_filters_tiny_and_decorative_assets_before_selection(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "sources" / "guide.md"
    _media_file(tmp_path, "guide/status-icon.png", width=320, height=240)
    _media_file(tmp_path, "guide/one-pixel.png", width=32, height=32)
    document = RetrievedDocument(
        path=page,
        text=(
            "# Guide\n\n"
            "![Status icon](../media/guide/status-icon.png)\n"
            "![Tiny diagram](../media/guide/one-pixel.png)\n"
        ),
    )

    assert qa_images.select_relevant_qa_images(
        "Show me the guide images",
        wiki,
        [document],
    ) == []


def test_generic_linked_alt_can_use_meaningful_section_context(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "sources" / "guide.md"
    image = _media_file(tmp_path, "guide/img-1.png")
    document = RetrievedDocument(
        path=page,
        text=(
            "# Guide\n\n## Emergency stop\n"
            "The emergency stop is the red button.\n"
            "![img-1](../media/guide/img-1.png)"
        ),
    )

    selected = qa_images.select_relevant_qa_images(
        "Where is the emergency stop?",
        wiki,
        [document],
        answer="The emergency stop is the red button.",
    )

    assert selected == [("Emergency stop", image)]


def test_linked_image_requires_final_answer_evidence(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "sources" / "guide.md"
    image = _media_file(tmp_path, "guide/front.png")
    document = RetrievedDocument(
        path=page,
        text=(
            "# Guide\n\n## Robot front\n"
            "The robot front is shown here.\n"
            "![Robot front](../media/guide/front.png)"
        ),
    )

    assert qa_images.select_relevant_qa_images(
        "Show me the robot front",
        wiki,
        [document],
        answer="The manual does not contain the requested operating procedure.",
    ) == []
    assert image.exists()


def test_extracts_supported_formats_with_realistic_dimensions(tmp_path: Path) -> None:
    for name in ("diagram.gif", "diagram.jpg", "diagram.webp"):
        _media_file(tmp_path, f"manual/{name}")
    answer = "\n".join(
        f"![{name}](wiki/media/manual/{name})"
        for name in ("diagram.gif", "diagram.jpg", "diagram.webp")
    )

    _cleaned, images = qa_images.extract_qa_images(answer, tmp_path)

    assert [image["mime_type"] for image in images] == [
        "image/gif",
        "image/jpeg",
        "image/webp",
    ]


def test_rejects_supported_extension_with_unreadable_header(tmp_path: Path) -> None:
    _media_file(tmp_path, "manual/not-an-image.png", b"not-an-image")

    _cleaned, images = qa_images.extract_qa_images(
        "![not an image](wiki/media/manual/not-an-image.png)",
        tmp_path,
    )

    assert images == []
