from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ecs.app import database
from worker import capability_catalog


def _draft_entry(model: str = "walker_s2") -> dict:
    return {
        "schema_version": "1.0",
        "capability_id": "CAP-WALK-FORWARD",
        "semantic_key": "walk_forward",
        "name": "walk_forward",
        "effect": {
            "action": "move",
            "object": "robot base",
            "observable_result": "Robot base moves forward",
        },
        "scope": {
            "vendor": "UBTECH",
            "model_id": model,
            "source_model_names": [model],
            "body_parts": ["legs"],
            "environment": "level ground",
            "selector": model,
            "resolution_status": "resolved",
        },
        "trigger": "Call the documented walking interface",
        "inputs": [],
        "outputs": [],
        "preconditions": [],
        "hold_conditions": [],
        "postconditions": ["Robot base has moved forward"],
        "constraints": {"time": [], "space": [], "information": [], "energy": []},
        "quality_metrics": [],
        "failure_modes": [],
        "interfaces": [{"type": "sdk", "reference": "walk()", "version": None}],
        "dependencies": [],
        "incompatible_resources": [],
        "evidence": [
            {
                "evidence_id": "EV-WALK-1",
                "source_type": "file",
                "source_id": "manual.md",
                "source_version": None,
                "source_hash": None,
                "locator": "manual.md#walk",
                "claim": "The SDK exposes forward walking",
                "evidence_level": "E2",
                "excerpt": None,
            }
        ],
        "confidence": {"extraction_score": 0.8, "basis": "Documented SDK interface"},
        "unknowns": [],
        "lifecycle": {
            "status": "draft",
            "supersedes": [],
            "replaced_by": [],
            "deprecation_reason": None,
        },
    }


def _changeset(action: str = "create") -> dict:
    return {
        "schema_version": "1.0",
        "changeset_id": "CHG-TEST-WALK",
        "model_id": "walker_s2",
        "source_snapshot": {
            "snapshot_id": "SRC-1",
            "sources": [
                {
                    "source_id": "manual.md",
                    "version": None,
                    "hash_or_revision": None,
                    "status": "processed",
                }
            ],
        },
        "target": {"wiki_id": "wiki", "section_id": "capabilities", "base_revision": "empty"},
        "operations": [
            {
                "operation_id": "OP-1",
                "action": action,
                "target_entry_id": "CAP-WALK-FORWARD",
                "reason": "Documented atomic behavior",
                "source_evidence_ids": ["EV-WALK-1"],
                "approval_required": True,
                "after_entry": _draft_entry() if action in {"create", "update"} else None,
            }
        ],
        "coverage_report": {
            "total_sources": 1,
            "processed_sources": 1,
            "unchanged_sources": 0,
            "excluded_sources": 0,
            "blocked_sources": 0,
            "unprocessed_sources": 0,
            "extracted_claims": 1,
            "atomic_entries": 1 if action in {"create", "update"} else 0,
            "non_capability_candidates": 0,
            "operation_counts": {action: 1},
            "is_complete": True,
        },
    }


def test_source_manifest_reports_added_modified_and_deleted(tmp_path: Path) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    (source / "added.md").write_text("new", encoding="utf-8")
    (source / "modified.md").write_text("changed", encoding="utf-8")
    current = capability_catalog._collect_source_manifest(source)
    previous = {
        "deleted.md": {"size_bytes": 8, "mtime_ns": 1},
        "modified.md": {"size_bytes": 3, "mtime_ns": 1},
    }

    changes = capability_catalog._source_changes(previous, current)

    assert changes["added"] == ["added.md"]
    assert changes["modified"] == ["modified.md"]
    assert changes["deleted"] == ["deleted.md"]
    assert changes["counts"] == {"added": 1, "modified": 1, "deleted": 1, "total": 3}


def test_wiki_manifest_scans_text_evidence_and_excludes_generated_catalog(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "wiki"
    (wiki / "sources").mkdir(parents=True)
    (wiki / "sources" / "manual.md").write_text("walk()", encoding="utf-8")
    (wiki / "sources" / "image.png").write_bytes(b"not sent to Claude")
    (wiki / "capabilities" / "walker_s2").mkdir(parents=True)
    (wiki / "capabilities" / "walker_s2" / "CAP-OLD.md").write_text(
        "generated output", encoding="utf-8"
    )

    manifest = capability_catalog._collect_wiki_manifest(wiki)

    assert list(manifest) == ["sources/manual.md"]


def test_first_run_is_full_then_normal_runs_are_incremental() -> None:
    assert capability_catalog._effective_scan_mode("incremental", {}) == "full"
    baseline = {"wiki_files": {"sources/manual.md": {}}}
    assert capability_catalog._effective_scan_mode("incremental", baseline) == "incremental"
    assert capability_catalog._effective_scan_mode("full", baseline) == "full"


def test_generated_changeset_passes_the_bundled_hard_gate(tmp_path: Path) -> None:
    path = tmp_path / "changeset.json"
    path.write_text(json.dumps(_changeset()), encoding="utf-8")

    asyncio.run(capability_catalog._validate_changeset(path))


def test_claude_generation_schema_is_draft7_compatible_and_focused() -> None:
    schema = capability_catalog.CATALOG_CHANGESET_SCHEMA
    after_entry = schema["properties"]["operations"]["items"]["properties"][
        "after_entry"
    ]

    assert "$schema" not in schema
    assert "$id" not in schema
    assert after_entry["oneOf"] == [{"type": "object"}, {"type": "null"}]
    assert len(json.dumps(schema)) < 15_000


def test_changeset_parser_accepts_fenced_fallback_and_reports_retry_exhaustion() -> None:
    fenced = f"```json\n{json.dumps(_changeset())}\n```"
    assert capability_catalog._parse_changeset(fenced)["changeset_id"] == "CHG-TEST-WALK"

    with pytest.raises(ValueError, match="could not satisfy.*after its retries"):
        capability_catalog._parse_changeset(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error_max_structured_output_retries",
                    "is_error": True,
                    "errors": ["output did not match schema"],
                }
            )
        )


def test_publish_is_atomic_saves_manifest_and_protects_verified_entries(tmp_path: Path) -> None:
    manifest = {"upload/manual.md": {"size_bytes": 12, "mtime_ns": 34}}
    result = capability_catalog._publish_drafts(
        _changeset(),
        model="walker_s2",
        job_id="CAT-FIRST",
        worker_root=tmp_path,
        snapshot_id="SRC-1",
        source_manifest=manifest,
        source_changes={"counts": {"total": 1}},
        wiki_manifest={"sources/manual.md": {"size_bytes": 6, "mtime_ns": 1}},
        scan_mode="full",
    )
    target = tmp_path / "wiki" / "capabilities" / "walker_s2"
    assert result["entries_written"] == ["CAP-WALK-FORWARD"]
    assert json.loads((target / "_source-manifest.json").read_text())["files"] == manifest
    organization_manifest = json.loads(
        (target / "_organization-manifest.json").read_text()
    )
    assert organization_manifest["scan_mode"] == "full"
    assert organization_manifest["wiki_files"] == {
        "sources/manual.md": {"size_bytes": 6, "mtime_ns": 1}
    }
    assert result["catalog_entries"][0]["capability_id"] == "CAP-WALK-FORWARD"
    assert (target / "CAP-WALK-FORWARD.md").is_file()

    verified = json.loads((target / "CAP-WALK-FORWARD.json").read_text())
    verified["lifecycle"]["status"] = "verified"
    (target / "CAP-WALK-FORWARD.json").write_text(json.dumps(verified), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot overwrite non-draft"):
        capability_catalog._publish_drafts(
            _changeset("update"),
            model="walker_s2",
            job_id="CAT-SECOND",
            worker_root=tmp_path,
            snapshot_id="SRC-2",
            source_manifest=manifest,
            source_changes={"counts": {"total": 0}},
        )
    assert json.loads((target / "CAP-WALK-FORWARD.json").read_text())["lifecycle"]["status"] == "verified"


def test_incomplete_scan_publishes_drafts_without_advancing_baseline(tmp_path: Path) -> None:
    changeset = _changeset()
    changeset["coverage_report"]["blocked_sources"] = 1
    changeset["coverage_report"]["processed_sources"] = 0
    changeset["coverage_report"]["is_complete"] = False

    result = capability_catalog._publish_drafts(
        changeset,
        model="walker_s2",
        job_id="CAT-PARTIAL",
        worker_root=tmp_path,
        snapshot_id="SRC-PARTIAL",
        source_manifest={"upload/image.png": {"size_bytes": 12, "mtime_ns": 1}},
        source_changes={"counts": {"total": 1}},
        wiki_manifest={"sources/image.md": {"size_bytes": 20, "mtime_ns": 2}},
        scan_mode="full",
    )
    target = tmp_path / "wiki" / "capabilities" / "walker_s2"

    assert result["completion_status"] == "partial"
    assert result["baseline_advanced"] is False
    assert not (target / "_source-manifest.json").exists()
    assert not (target / "_organization-manifest.json").exists()
    assert (target / "CAP-WALK-FORWARD.json").is_file()


def test_publish_rejects_browser_triggered_deletion(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Deletion and deprecation"):
        capability_catalog._publish_drafts(
            _changeset("delete-proposal"),
            model="walker_s2",
            job_id="CAT-DELETE",
            worker_root=tmp_path,
            snapshot_id="SRC-1",
            source_manifest={},
            source_changes={"counts": {"total": 0}},
        )


def test_catalog_jobs_and_source_changes_are_persisted_globally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "catalog.db")
    database.initialize_database()
    user_id = database.create_user_record(
        username="catalog-admin",
        email="catalog-admin@example.com",
        password_hash="hash",
        password_salt="salt",
        role="admin",
        teams="",
    )
    created = database.create_capability_catalog_job(
        job_id="CAT-PERSISTED",
        created_by=user_id,
        model_id="walker_s2",
        snapshot_id="SRC-PERSISTED",
        scan_mode="full",
    )
    database.update_capability_catalog_job(
        created["job_id"],
        status="processing",
        stage="inventorying",
        message="Reading files",
    )
    database.upsert_capability_catalog_source_state(
        model_id="walker_s2",
        changes={"added": ["upload/manual.md"], "modified": [], "deleted": []},
        current_source_files=1,
        last_organized_manifest_files=0,
    )

    database.initialize_database()

    restarted = database.get_capability_catalog_job("CAT-PERSISTED")
    assert restarted is not None
    assert restarted["status"] == "failed"
    assert restarted["stage"] == "interrupted"
    assert restarted["scan_mode"] == "full"
    state = database.get_capability_catalog_source_state("walker_s2")
    assert state is not None
    assert state["changes"]["added"] == ["upload/manual.md"]


def test_admin_page_has_shared_progress_change_window_and_chinese_translation() -> None:
    page = (
        Path(__file__).resolve().parents[1]
        / "ecs"
        / "app"
        / "templates"
        / "admin_capabilities.html"
    ).read_text(encoding="utf-8")
    assert "Shared organization progress" in page
    assert "Raw file changes since the last successful organization" in page
    assert "自上次成功整理以来的原始文件变更" in page
    assert "整理新增变更" in page
    assert "Full Wiki rescan" in page
    assert "全量重新扫描 Wiki" in page
    assert "Organized atomic capabilities" in page
    assert "已整理的原子能力" in page
    assert "localStorage.getItem('catalog" not in page
