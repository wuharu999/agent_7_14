from __future__ import annotations

from pathlib import Path

from worker.scenario_retrieval import ScenarioEvidenceIndex, anonymous_context


def test_wiki_only_bilingual_search_and_link_expansion(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "Walker C1 产品介绍.md").write_text(
        "# Walker C1 产品介绍\nWalker C1 是面向城市公共服务的全尺寸商用服务人形机器人。\n"
        "相关能力：[[thinker-wam]]\n",
        encoding="utf-8",
    )
    (wiki / "thinker-wam.md").write_text(
        "# Thinker-WAM\n支持视觉语言导航与多模态交互。\n",
        encoding="utf-8",
    )
    capabilities = wiki / "capabilities" / "walker_c1"
    capabilities.mkdir(parents=True)
    (capabilities / "CAP-CITY.json").write_text(
        '{"capability_id":"CAP-CITY","name":"City service interaction","effect":"Guide visitors"}',
        encoding="utf-8",
    )
    raw = tmp_path / "raw" / "sources"
    raw.mkdir(parents=True)
    (raw / "forbidden.md").write_text("Walker C1 secret source", encoding="utf-8")

    index = ScenarioEvidenceIndex(wiki, tmp_path / "cache.sqlite3")
    snapshot = index.search(
        "我需要 Walker C1 产品介绍和交互能力 City service interaction",
        model_id="walker_c1",
        max_documents=10,
    )

    text = "\n".join(document.text for document in snapshot.documents)
    assert "城市公共服务" in text
    assert "视觉语言导航" in text
    assert "secret source" not in text
    assert any(document.kind == "capability" for document in snapshot.documents)
    rendered = anonymous_context(snapshot.documents)
    assert str(tmp_path) not in rendered
    assert ".md" not in rendered
    assert "thinker-wam" not in rendered


def test_content_change_rebuilds_snapshot_revision(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    page = wiki / "robot.md"
    page.write_text("# Robot\nOriginal navigation behavior.", encoding="utf-8")
    index = ScenarioEvidenceIndex(wiki, tmp_path / "cache.sqlite3")
    first = index.search("navigation", model_id="walker_s2")
    page.write_text("# Robot\nUpdated navigation and recovery behavior.", encoding="utf-8")
    second = index.search("recovery", model_id="walker_s2")
    assert first.revision != second.revision
    assert "Updated navigation" in second.documents[0].text


def test_symlink_root_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "wiki-link"
    linked.symlink_to(outside, target_is_directory=True)
    try:
        ScenarioEvidenceIndex(linked, tmp_path / "cache.sqlite3")
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("Symlink Wiki root was accepted")
