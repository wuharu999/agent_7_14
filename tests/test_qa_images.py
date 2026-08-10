from __future__ import annotations

import base64
from pathlib import Path

from worker import qa_images


def _media_file(root: Path, name: str, content: bytes = b"image-data") -> Path:
    path = root / "wiki" / "media" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
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
    assert base64.b64decode(str(images[0]["data"])) == b"image-data"


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
        _media_file(tmp_path, f"image-{index}.jpg", bytes([index]) * 4)
    monkeypatch.setattr(qa_images, "MAX_QA_IMAGES", 2)
    monkeypatch.setattr(qa_images, "MAX_QA_IMAGE_BYTES", 5)
    monkeypatch.setattr(qa_images, "MAX_QA_IMAGE_TOTAL_BYTES", 8)
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
