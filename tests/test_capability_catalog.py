from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from pathlib import Path

import pytest

from ecs.app import config as ecs_config
from ecs.app import database
from worker import capability_batch
from worker import capability_catalog
from worker import config as worker_config


def _draft_entry(model: str = "walker_s2") -> dict:
    return {
        "schema_version": "2.0",
        "capability_id": "CAP-WALK-FORWARD",
        "semantic_key": "walk_forward",
        "name": "walk_forward",
        "capability_type": "building_block",
        "verification_profiles": [],
        "migration_warnings": [],
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
    (wiki / "sources" / "image.png").write_bytes(b"not sent to the text model")
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


def test_capability_organization_enforces_long_running_timeout_floors() -> None:
    assert worker_config.CAPABILITY_CATALOG_BATCH_TIMEOUT >= 1800
    assert worker_config.CAPABILITY_CATALOG_REDUCE_TIMEOUT >= 3600
    assert ecs_config.CAPABILITY_CATALOG_TIMEOUT >= 86400


def test_worker_websocket_url_never_contains_shared_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker_config,
        "SERVER_URL",
        "wss://example.test/ws/client?project=agent&secret=legacy-value",
    )

    url = worker_config.websocket_url()

    assert url == "wss://example.test/ws/client?project=agent"
    assert "secret" not in url


def test_evidence_units_and_batches_are_deterministic_and_byte_bounded(
    tmp_path: Path,
) -> None:
    assert capability_batch.PIPELINE_VERSION == "capability-batch-v2-full-skill"
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "a.md").write_text("机器人向前移动。" * 20, encoding="utf-8")
    (wiki / "b.md").write_text("walk() moves the base", encoding="utf-8")

    units = capability_batch.load_evidence_units(
        wiki,
        ["b.md", "a.md"],
        max_unit_bytes=48,
    )
    batches = capability_batch.partition_evidence_units(units, max_batch_bytes=700)

    assert units[0].source_id == "wiki/a.md"
    assert units[0].unit_id.startswith("wiki/a.md#part=1/")
    assert all(unit.size_bytes <= 48 for unit in units)
    assert [capability_batch.batch_id("fangan", batch) for batch in batches] == [
        capability_batch.batch_id("fangan", batch) for batch in batches
    ]


def test_batch_parser_requires_every_unit_and_checkpoint_round_trip(
    tmp_path: Path,
) -> None:
    unit = capability_batch.EvidenceUnit(
        unit_id="wiki/manual.md",
        source_id="wiki/manual.md",
        part_index=1,
        part_count=1,
        content="walk() moves the robot base",
        content_sha256="a" * 64,
        size_bytes=27,
    )
    identifier = capability_batch.batch_id("walker_s2", [unit])
    payload = {
        "batch_id": identifier,
        "sources": [
            {
                "unit_id": unit.unit_id,
                "source_id": unit.source_id,
                "status": "processed",
                "reason": "Documented SDK trigger and observable effect",
                "extracted_claims": 1,
            }
        ],
        "candidates": [
            {
                "candidate_id": "temporary",
                "name": "walk_forward",
                "semantic_key": "walk_forward",
                "effect": {
                    "action": "move",
                    "object": "robot base",
                    "observable_result": "Robot base moves forward",
                },
                "trigger": "walk()",
                "interface_reference": "walk()",
                "body_parts": ["legs"],
                "environment": "level ground",
                "evidence": [
                    {
                        "unit_id": unit.unit_id,
                        "source_id": unit.source_id,
                        "locator": "manual.md#walk",
                        "claim": "walk() moves the robot base",
                        "excerpt": "walk() moves the robot base",
                    }
                ],
                "unknowns": [],
            }
        ],
        "non_capability_candidates": 0,
    }
    parsed = capability_batch.parse_batch_extraction(
        json.dumps(payload),
        expected_batch_id=identifier,
        units=[unit],
    )
    capability_batch.normalize_candidate_ids(identifier, parsed)
    capability_batch.save_checkpoint(
        tmp_path,
        "walker_s2",
        identifier,
        parsed,
    )

    restored = capability_batch.load_checkpoint(
        tmp_path,
        "walker_s2",
        identifier,
        [unit],
    )

    assert restored is not None
    assert restored["candidates"][0]["candidate_id"] == f"{identifier}-C0001"
    checkpoint = capability_batch.checkpoint_path(
        tmp_path,
        "walker_s2",
        identifier,
    )
    stale = json.loads(checkpoint.read_text(encoding="utf-8"))
    stale["pipeline_version"] = "capability-batch-v1"
    checkpoint.write_text(json.dumps(stale), encoding="utf-8")
    assert (
        capability_batch.load_checkpoint(
            tmp_path,
            "walker_s2",
            identifier,
            [unit],
        )
        is None
    )
    with pytest.raises(ValueError, match="every evidence unit"):
        capability_batch.parse_batch_extraction(
            json.dumps({**payload, "sources": []}),
            expected_batch_id=identifier,
            units=[unit],
        )
    invalid_count = json.loads(json.dumps(payload))
    invalid_count["sources"][0]["extracted_claims"] = True
    with pytest.raises(ValueError, match="claim count"):
        capability_batch.parse_batch_extraction(
            json.dumps(invalid_count),
            expected_batch_id=identifier,
            units=[unit],
        )
    incomplete_candidate = json.loads(json.dumps(payload))
    del incomplete_candidate["candidates"][0]["effect"]
    with pytest.raises(ValueError, match="candidate is incomplete"):
        capability_batch.parse_batch_extraction(
            json.dumps(incomplete_candidate),
            expected_batch_id=identifier,
            units=[unit],
        )


def test_aggregate_reports_accounts_for_every_file_and_split_unit() -> None:
    units = [
        capability_batch.EvidenceUnit(
            unit_id=f"wiki/manual.md#part={index}/2",
            source_id="wiki/manual.md",
            part_index=index,
            part_count=2,
            content="content",
            content_sha256=str(index) * 64,
            size_bytes=7,
        )
        for index in (1, 2)
    ]
    result = {
        "sources": [
            {
                "unit_id": units[0].unit_id,
                "source_id": units[0].source_id,
                "status": "processed",
                "reason": "Capability evidence",
                "extracted_claims": 2,
            },
            {
                "unit_id": units[1].unit_id,
                "source_id": units[1].source_id,
                "status": "excluded",
                "reason": "No additional claims",
                "extracted_claims": 0,
            },
        ],
        "candidates": [],
        "non_capability_candidates": 1,
    }

    sources, totals = capability_batch.aggregate_source_reports(
        ["manual.md"], units, [result]
    )

    assert sources[0]["status"] == "processed"
    assert totals["processed"] == 1
    assert totals["extracted_claims"] == 2
    assert totals["non_capability_candidates"] == 1


def test_evidence_diagnostics_groups_reasons_and_keeps_blocked_files() -> None:
    diagnostics = capability_catalog._evidence_diagnostics(
        [
            {
                "source_id": "wiki/a.md",
                "status": "blocked",
                "reason": "Unreadable section",
            },
            {
                "source_id": "wiki/b.md",
                "status": "excluded",
                "reason": "No target-model relationship",
            },
            {
                "source_id": "wiki/c.md",
                "status": "excluded",
                "reason": "No target-model relationship",
            },
        ]
    )

    assert diagnostics["blocked_sources"] == [
        {"source_id": "wiki/a.md", "reason": "Unreadable section"}
    ]
    assert diagnostics["excluded_reasons"] == [
        {"reason": "No target-model relationship", "count": 2}
    ]


def test_batch_extractor_disables_tools_and_reducer_accounts_for_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = capability_batch.EvidenceUnit(
        unit_id="wiki/manual.md",
        source_id="wiki/manual.md",
        part_index=1,
        part_count=1,
        content="walk() moves the robot base",
        content_sha256="a" * 64,
        size_bytes=27,
    )
    identifier = capability_batch.batch_id("walker_s2", [unit])
    calls: list[dict] = []

    async def fake_run(prompt: str, **kwargs):
        calls.append(kwargs)
        if kwargs["json_schema"] is capability_batch.BATCH_EXTRACTION_SCHEMA:
            return json.dumps(
                {
                    "batch_id": identifier,
                    "sources": [
                        {
                            "unit_id": unit.unit_id,
                            "source_id": unit.source_id,
                            "status": "processed",
                            "reason": "Documented capability",
                            "extracted_claims": 1,
                        }
                    ],
                    "candidates": [
                        {
                            "candidate_id": "temporary",
                            "name": "walk_forward",
                            "semantic_key": "walk_forward",
                            "effect": {
                                "action": "move",
                                "object": "robot base",
                                "observable_result": "Robot base moves forward",
                            },
                            "trigger": "walk()",
                            "interface_reference": "walk()",
                            "body_parts": ["legs"],
                            "environment": "level ground",
                            "evidence": [
                                {
                                    "unit_id": unit.unit_id,
                                    "source_id": unit.source_id,
                                    "locator": "manual.md#walk",
                                    "claim": "walk() moves the robot base",
                                    "excerpt": "walk() moves the robot base",
                                }
                            ],
                            "unknowns": [],
                        }
                    ],
                    "non_capability_candidates": 0,
                }
            )
        return json.dumps(
            {
                "reducer_id": "CR-TEST-REDUCER",
                "decisions": [
                    {
                        "candidate_ids": [f"{identifier}-C0001"],
                        "action": "create",
                        "target_entry_id": "CAP-WALK-FORWARD",
                        "reason": "Documented atomic capability",
                        "after_entry": _draft_entry(),
                    }
                ],
            }
        )

    class FakeClient:
        async def complete_json(self, system: str, prompt: str, **kwargs):
            output = await fake_run(
                prompt,
                system_prompt=system,
                json_schema=kwargs.get("schema"),
                tools=(),
            )
            return json.loads(output)

    monkeypatch.setattr(capability_catalog, "create_deepseek_client", lambda **_kwargs: FakeClient())
    extraction = asyncio.run(
        capability_catalog._extract_batch(
            model="walker_s2",
            identifier=identifier,
            units=[unit],
        )
    )
    reduction = asyncio.run(
        capability_catalog._reduce_candidates(
            model="walker_s2",
            reducer_id="CR-TEST-REDUCER",
            candidates=extraction["candidates"],
            existing_entries=[],
        )
    )

    assert reduction["decisions"][0]["action"] == "create"
    assert all(call["tools"] == () for call in calls)
    invalid_reduction = json.dumps(
        {
            "reducer_id": "CR-TEST-REDUCER",
            "decisions": [
                {
                    "candidate_ids": [f"{identifier}-C0001"],
                    "action": "delete",
                    "target_entry_id": None,
                    "reason": "Invalid destructive action",
                    "after_entry": None,
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="action is invalid"):
        capability_batch.parse_reduction(
            invalid_reduction,
            expected_reducer_id="CR-TEST-REDUCER",
            candidate_ids={f"{identifier}-C0001"},
        )


def test_python_builds_final_coverage_and_valid_changeset(tmp_path: Path) -> None:
    reduction = {
        "decisions": [
            {
                "candidate_ids": ["CB-ONE-C0001"],
                "action": "create",
                "target_entry_id": "CAP-WALK-FORWARD",
                "reason": "Documented atomic capability",
                "after_entry": _draft_entry(),
            }
        ]
    }
    changeset = capability_catalog._build_changeset_from_reduction(
        job_id="CAT-ABC123",
        model="walker_s2",
        snapshot_id="SRC-BATCH",
        base_revision="empty",
        source_snapshot=[
            {
                "source_id": "wiki/manual.md",
                "version": None,
                "hash_or_revision": "a" * 64,
                "status": "processed",
            }
        ],
        source_totals={"extracted_claims": 1, "non_capability_candidates": 0},
        reduction=reduction,
    )
    path = tmp_path / "changeset.json"
    path.write_text(json.dumps(changeset), encoding="utf-8")

    asyncio.run(capability_catalog._validate_changeset(path))
    assert changeset["coverage_report"]["processed_sources"] == 1
    assert changeset["coverage_report"]["atomic_entries"] == 1


def test_python_orders_reduction_decisions_by_candidate_id() -> None:
    reduction = {
        "decisions": [
            {
                "candidate_ids": ["CB-Z-C0001"],
                "action": "skip",
                "target_entry_id": None,
                "reason": "Z candidate is not independently triggerable",
                "after_entry": None,
            },
            {
                "candidate_ids": ["CB-A-C0001"],
                "action": "skip",
                "target_entry_id": None,
                "reason": "A candidate is not independently triggerable",
                "after_entry": None,
            },
        ]
    }

    changeset = capability_catalog._build_changeset_from_reduction(
        job_id="CAT-ORDERED",
        model="fangan",
        snapshot_id="SRC-ORDERED",
        base_revision="empty",
        source_snapshot=[
            {
                "source_id": "wiki/manual.md",
                "version": None,
                "hash_or_revision": "a" * 64,
                "status": "processed",
            }
        ],
        source_totals={"extracted_claims": 2, "non_capability_candidates": 0},
        reduction=reduction,
    )

    assert [operation["reason"][0] for operation in changeset["operations"]] == [
        "A",
        "Z",
    ]


def test_full_organization_batches_every_file_without_agent_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_sources = tmp_path / "raw" / "sources" / "fangan"
    wiki = tmp_path / "wiki"
    raw_sources.mkdir(parents=True)
    wiki.mkdir()
    (raw_sources / "source.txt").write_text("source", encoding="utf-8")
    (wiki / "a.md").write_text("A" * 180, encoding="utf-8")
    (wiki / "b.md").write_text("B" * 180, encoding="utf-8")
    config = SimpleNamespace(
        base_dir=tmp_path,
        raw_sources_dir=raw_sources,
        wiki_dir=wiki,
    )
    calls: list[dict] = []
    progress: list[tuple[str, str]] = []

    async def fake_run(prompt: str, **kwargs):
        calls.append(kwargs)
        identifier = re.search(r"Batch ID: (CB-[A-Z0-9]+)", prompt).group(1)
        encoded = prompt.split("<untrusted_wiki_evidence>", 1)[1].split(
            "</untrusted_wiki_evidence>", 1
        )[0]
        units = json.loads(encoded)
        return json.dumps(
            {
                "batch_id": identifier,
                "sources": [
                    {
                        "unit_id": unit["unit_id"],
                        "source_id": unit["source_id"],
                        "status": "processed",
                        "reason": "Content was examined and has no atomic capability",
                        "extracted_claims": 0,
                    }
                    for unit in units
                ],
                "candidates": [],
                "non_capability_candidates": 0,
            }
        )

    async def capture_progress(
        stage: str,
        message: str,
        details: dict | None,
    ) -> None:
        progress.append((stage, message))

    monkeypatch.setattr(capability_catalog, "get_team_config", lambda _model: config)
    class FakeClient:
        async def complete_json(self, system: str, prompt: str, **kwargs):
            output = await fake_run(
                prompt,
                system_prompt=system,
                json_schema=kwargs.get("schema"),
                tools=(),
            )
            return json.loads(output)

    monkeypatch.setattr(capability_catalog, "create_deepseek_client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(capability_catalog, "CAPABILITY_CATALOG_BATCH_BYTES", 500)
    monkeypatch.setattr(capability_catalog, "CAPABILITY_CATALOG_UNIT_BYTES", 400)
    monkeypatch.setattr(
        capability_catalog,
        "load_checkpoint",
        lambda *_args, **_kwargs: pytest.fail(
            "Forced re-extraction must not read checkpoints"
        ),
    )

    result = asyncio.run(
        capability_catalog.organize_capability_catalog(
            job_id="CAT-BATCHED",
            model_id="fangan",
            snapshot_id="SRC-BATCHED",
            scan_mode="full",
            reuse_checkpoints=False,
            on_progress=capture_progress,
        )
    )

    assert result["coverage_report"]["total_sources"] == 2
    assert result["coverage_report"]["processed_sources"] == 2
    assert result["batch_metrics"]["batch_count"] == 2
    assert result["batch_metrics"]["checkpoint_mode"] == "fresh"
    assert result["evidence_diagnostics"] == {
        "blocked_sources": [],
        "excluded_reasons": [],
    }
    assert len(calls) == 2
    assert all(call["tools"] == () for call in calls)
    assert all(
        "Extract triggerable actions and observable effects" in call["system_prompt"]
        for call in calls
    )
    assert sum(stage == "batch_extracting" for stage, _message in progress) == 4


def test_generated_changeset_passes_the_bundled_hard_gate(tmp_path: Path) -> None:
    path = tmp_path / "changeset.json"
    path.write_text(json.dumps(_changeset()), encoding="utf-8")

    asyncio.run(capability_catalog._validate_changeset(path))


def test_provider_generation_schema_is_draft7_compatible_and_focused() -> None:
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
        result={
            "progress_snapshot": {
                "completed_batches": 3,
                "batch_count": 10,
                "candidate_count": 7,
            }
        },
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
    assert restarted["result"]["progress_snapshot"] == {
        "completed_batches": 3,
        "batch_count": 10,
        "candidate_count": 7,
    }
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
    assert "Resume full scan" in page
    assert "恢复全量扫描" in page
    assert "Force full re-extraction" in page
    assert "强制全量重新提取" in page
    assert "progress_snapshot" in page
    assert "Blocked evidence files" in page
    assert "被阻塞的证据文件" in page
    assert "Organized atomic capabilities" in page
    assert "已整理的原子能力" in page
    assert "localStorage.getItem('catalog" not in page


def test_save_and_delete_capability_entry(tmp_path: Path) -> None:
    from worker.capability_catalog import save_capability_entry, delete_capability_entry
    entry = {
        "capability_id": "CAP-TG-UNITTEST-001",
        "name": "Test Capability",
        "capability_type": "building_block",
        "verification_profiles": [],
        "migration_warnings": [],
        "effect": {"action": "Test action", "object": "Arm", "observable_result": "Done"},
    }
    saved = save_capability_entry(model_id="tian_gong", entry=entry, base_dir=tmp_path)
    assert saved["status"] == "ok"
    assert saved["capability_id"] == "CAP-TG-UNITTEST-001"

    json_file = tmp_path / "wiki" / "capabilities" / "tian_gong" / "CAP-TG-UNITTEST-001.json"
    md_file = tmp_path / "wiki" / "capabilities" / "tian_gong" / "CAP-TG-UNITTEST-001.md"
    assert json_file.exists()
    assert md_file.exists()

    deleted = delete_capability_entry(model_id="tian_gong", capability_id="CAP-TG-UNITTEST-001", base_dir=tmp_path)
    assert deleted["status"] == "ok"
    assert not json_file.exists()
    assert not md_file.exists()
