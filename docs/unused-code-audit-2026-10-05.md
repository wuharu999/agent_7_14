# Unused-code and historical-remains audit

**Follow-up:** the user subsequently approved cleanup. See the [implementation result](cleanup-result-2026-10-05.md) for actual removals, retained items, and completed tests. The audit below preserves the pre-removal findings and line references.

Date: 2026-10-05. Checkout: `0305af2`, including the existing uncommitted changes and the newly unignored `browser_parse/` source.

**Result:** there are credible cleanup candidates, principally the retired QA retrieval implementation. The quantified candidates total **1,090 source lines and one direct dependency**, subject to the decisions and test migrations below. This is a conservative count of selected definitions/files, not a proposed patch size or a claim that every candidate is safe to delete immediately. No application code, configuration, tests, or data was removed or changed by this audit.

## Scope and method

- Inventoried 275 Git-tracked or non-ignored files, including 146 Python files and the complete `browser_parse/` application. Excluded credentials, uploaded documents, generated knowledge, virtual environments, dependency installations, and generated builds.
- Parsed Python definitions/imports, traced service entry points, searched references across production code, tests, scripts, configuration, and documentation, and inspected dynamic imports and runtime staging.
- Traced all 23 TypeScript source modules, including the module Web Worker. A production Vite build with `write: false` confirmed that `brief.ts` is absent from the bundle.
- Cross-checked the retired QA implementation against Git history: `19f5c6a` introduced the LangGraph replacement. History supports the migration explanation; current caller analysis determines the findings.
- A missing import alone was not treated as evidence of dead code. HTTP handlers, shell entry points, operator utilities, runtime-copied skills, and dynamically loaded runners were reviewed separately.

This is a **repository-level audit**, not a live deployment audit. Cloud process arguments, cron/systemd configuration, external consumers, and traffic were not inspected. Public/internal compatibility interfaces therefore need extra care before deletion.

## Active paths to preserve

| Area | Current path |
| --- | --- |
| Portal API | `scripts/run_ecs.sh` → `cloud_app.py` → `ecs/app/main.py` → registered route modules |
| Knowledge QA Worker | `scripts/run_worker.sh` → `local_worker.py` → `worker.main` → `WorkerManager` → `run_qa_api_stream` → `_retrieve_and_stream` → `worker.langgraph_qa.stream_answer` |
| Tools API | `browser_parse/scripts/run_ecs.sh` → `backend.app` → log and Grill stores, intake, chat, and portal authentication |
| Tools Worker | `python -m backend.worker` → `DockerRuntime` → staged `sandbox/runtime/` and the image's `/opt/sandbox/run_codex.py` |
| Grill execution | runtime `run_codex.main` dispatches Grill jobs to `run_grill.run_grill` |
| Tools UI | `index.html` → `src/main.ts` → log UI, Grill routing/views, and preprocessing Web Worker |

All non-test application Python modules are reachable from these service/job entry points; the exceptions are explicit CLI tools, a compatibility wrapper, and a skill validator. Most cleanup is **inside still-used modules**, so deleting entire Python modules would be the wrong approach.

## Ranked removal candidates

### 1. Retired QA retrieval implementation — high confidence, coordinated cleanup

**Location:** [worker/qa_api.py](../worker/qa_api.py), especially `_run_provider` at line 1029 and the active `_retrieve_and_stream` at line 1151.

`_run_provider` has no caller anywhere in the repository. The active path directly invokes LangGraph. Its old router, page selection, context assembly, and streaming bridge form a disconnected implementation that several tests still exercise directly.

Candidate scopes:

- `_run_provider`, `_provider_client`, `_stream_in_thread`, `parse_router_response`, `_router_prompt`, `_answer_prompt`, and `_make_context`.
- Their private history/topic/slug helpers, unused `links_in`, and old `ProviderCallError`, `StreamCallbackError`, `ChatProvider`, and `Document` definitions.
- `Wiki.candidate_slugs`, robot-group/scope helpers, `fallback_slugs`, `expand_slugs`, and `load`.

These selected callable/class scopes cover **630 lines**. The old `ROUTER_SYSTEM`/`ANSWER_SYSTEM`, constants, imports, and now-unused reset plumbing can be considered afterward; they are not included in the count.

**Preserve:** `Wiki` construction and the page/slug data needed by the active output filters; `DeepSeekClient`; `_run_blocking`; retrieval-reference and unsupported-synthesis filters; and the public QA wrappers. Other modules have unrelated classes/helpers with some of the same names.

**Before removal:** migrate the relevant safeguards to tests of the active LangGraph path. In particular, `tests/test_qa_api.py:543` and `tests/test_terminology.py:68` assert text in the old `ANSWER_SYSTEM`; passing those assertions does not establish the active prompt's behavior. Do not delete all of either test file: both also cover live code. Complete the asynchronous QA checks after resolving the bounded-run limitation noted below.

### 2. Alternative Grill hibernation/resume API — no production caller; hold for integration review

**Location:** [browser_parse/backend/grill_store.py](../browser_parse/backend/grill_store.py), `check_and_hibernate_idle_sessions` at line 1005 and `resume_session` at line 1078: **114 lines**.

Both methods are referenced only by tests. The service's Worker loop runs through `run_once` → `check_idle_containers` (`backend/worker.py:790,829`). It either calls an injected store's `hibernate_session` or sends a hibernation request to ECS. Snapshot resumption code exists in `submit_answers` (`grill_store.py:690`) and the Worker's restore branch (`worker.py:633`).

**Integration gap:** the standard `main()` constructs `DockerWorker(config)` without an injected store (`worker.py:862`). `WorkerApi.hibernate_grill` posts to `/api/worker/grill/{session_id}/hibernate` and suppresses exceptions (`worker.py:292–296`), but no matching route exists in `backend/app.py` or elsewhere in the server source. Thus the presence of lifecycle code and passing unit tests does not establish working distributed hibernation.

**Recommendation:** hold deletion until the intended lifecycle is resolved in a separate correctness review. Then consolidate and migrate direct tests to the integrated path. Preserve snapshot columns, `hibernate_session`, task payloads, workspace snapshot/restore, and the UI's hibernated state. These 114 lines are conditional candidates, not immediate safe removals.

### 3. Deprecated hard-coded Wiki guide — high confidence

**Location:** [worker/langgraph_qa/wiki/indexer.py](../worker/langgraph_qa/wiki/indexer.py), `_legacy_static_wiki_guide`, lines 125–232: **108 lines**.

The function labels itself deprecated and has no production or test caller. The index build calls `generate_wiki_guide(catalog_records)` at line 579, which derives the guide from parsed content.

**Recommendation:** remove the unused static function. Preserve the catalog-based guide generator and its indexing tests.

### 4. Standalone brief generator — unused by the product, but a documented API decision

**Location:** [browser_parse/src/brief.ts](../browser_parse/src/brief.ts): **106 lines**.

Only `tests/brief.test.ts` imports `buildBrief`; no production source imports it. Both the import graph and the production bundle check confirm this. However, `browser_parse/README.md:13,67` explicitly describes `robot-log-brief/v1` as a developer API, and `PROJECT_STRUCTURE.md` presents it as an internal handoff.

**Recommendation:** remove it and its dedicated tests only if this developer API is retired; otherwise clearly document it as an opt-in source utility rather than current internal runtime behavior. Keep the active evidence preprocessing, context replay, and archive libraries.

### 5. Old knowledge-content gate — high confidence, partial-file removal

**Location:** [worker/knowledge.py](../worker/knowledge.py), `STOP_WORDS`, `_keywords`, and `has_wiki_content`: **30 lines**.

`has_wiki_content` has no production caller; the only external reference is a test monkeypatch. Its keyword helper and stop words serve only that function. The module remains active because `WorkerManager` imports `log_unanswered`.

**Recommendation:** remove the old gate and update its obsolete test reference. Preserve `log_unanswered` and the module.

### 6. Small unused helpers and compatibility definitions — high confidence within this checkout

| Definition | Location | Counted lines | Evidence / action |
| --- | --- | ---: | --- |
| `extract_clean_summary` | [final_answer.py](../worker/langgraph_qa/qa/nodes/final_answer.py), line 46 | 24 | No caller, including tests; active final-answer generation does not use it. |
| `get_user_robots` | [database.py](../ecs/app/database.py), line 973 | 11 | No caller; preserve other robot lookup and permission functions. |
| `save_summary_context`, `add_followup_question` | [grill_store.py](../browser_parse/backend/grill_store.py), lines 489,497 | 9 | First has no caller; second has only a test caller. Live completion writes summary context directly, and the API uses `begin_followup_question`. |
| `create_merge_client` | [deepseek_client.py](../worker/deepseek_client.py), line 243 | 8 | No caller. Treat the intended merge-model behavior as a separate decision; do not infer that the configuration contract can also be removed. |
| `WorkerGateway.send_command` | [gateway.py](../ecs/app/gateway.py), line 208 | 8 | Unused wrapper; routes use `command`. |
| `SearchQuery`, `AnswerPlan`, `SelectedImage` alias | [schemas.py](../worker/langgraph_qa/qa/schemas.py), lines 40,93,90 | 8 | No callers; live output schemas use other fields/types. Preserve the live schemas. |
| `allocation` | [resources.py](../browser_parse/backend/resources.py), line 53 | 4 | Unused fixed-template migration helper. Preserve `estimate_resources` and `PROFILES`. |

Total: **72 lines**. The unused `PlanOutput` import/alias and command-progress callback plumbing are additional candidates, but are excluded from the quantified total and should be checked together with their interface contracts.

### 7. Unused legacy final-message extractor — high confidence; update direct tests

**Location:** [browser_parse/sandbox/runtime/run_codex.py](../browser_parse/sandbox/runtime/run_codex.py), `PUBLIC_AGENT_TYPES`, `_item_text`, and `_agent_message` at lines 32,45,76: **22 lines**.

`_agent_message` is called only by tests. Its helper and constant serve only that function. The production runner uses Codex's `--output-last-message` file for the final report.

**Recommendation:** remove the unused extractor and replace its legacy-only test expectations with checks of the active output-file handling where needed. Preserve event/usage parsing, redaction, and subagent lifecycle handling: these remain active.

### 8. Cube/QEMU development image — high confidence within this checkout

**Location:** [browser_parse/sandbox/HostVM.Dockerfile](../browser_parse/sandbox/HostVM.Dockerfile): **8 lines**.

This file installs QEMU for the old Cube development VM. No current script, Compose configuration, or application code references it. Current jobs use `sandbox/Dockerfile` and `DockerRuntime`; the progress log explicitly records the Cube-to-Docker migration.

**Recommendation:** remove the old development Dockerfile if no operator still invokes it manually. Historical Cube entries in `PROGRESS.md` are a changelog, not evidence of a current dependency; retain or archive those entries as history.

### 9. Unused direct PDF-generation dependency — high confidence

**Location:** [pyproject.toml](../pyproject.toml), ECS extra, line 15: `reportlab>=4.2,<5`.

There is no `reportlab` import or invocation in the application, scripts, or tests. The current question-report endpoint produces Markdown (`ecs/app/routes/manage.py:575`).

**Recommendation:** remove this direct dependency and regenerate the lockfile when cleanup is approved. No replacement is needed. Do not remove the image's PDF/OCR tools or its analysis libraries: uploaded-document processing and agent-generated scripts can use them without ordinary application imports.

## Keep or investigate separately

- **Compatibility entry points:** `cloud_app.py` and `local_worker.py` are invoked by the startup scripts. Both `browser_parse/sandbox/run_*.py` wrappers have test callers; `run_grill.py` is also a runtime fallback import. Keep unless all callers are migrated together.
- **Runtime skill and agent files:** `backend/worker.py:377` stages `sandbox/runtime/` into each job. Its `.agents/skills/` and `.codex/agents/` files are runtime inputs, despite having no Python import. Keep the validator referenced by the Grill skill as well.
- **Template fragments:** `bg_graph.html` is dynamically included by `ecs/app/web_paths.py:52`. It is not orphaned.
- **Database migrations and old protocol formats:** existing-database upgrades, legacy chat-history import, and Worker authentication compatibility remain executable paths. Confirm deployed versions and data before retiring compatibility; age alone is insufficient.
- **Operator commands:** deployment scripts, `create_user.py`, demo seeding, cost estimation, resource benchmarking, and the runtime checker are explicit commands, not service imports. Static reachability does not establish that operators no longer use them.
- **Grill fallback generation:** it is explicitly selected by `ROBOT_GRILL_USE_FALLBACK` and used in tests. It is a supported alternate mode until intentionally retired.
- **Two DeepSeek adapters:** the synchronous provider in `qa_api.py` is passed into the LangGraph interface; the asynchronous client in `deepseek_client.py` serves other Worker tasks. Neither entire adapter is dead.
- **Old manual query template:** `agent1/wiki/queries/knowledge-base-query-template.md` is outside the default live `agent1/agent/wiki/` path and has no code references. It may be a human template, so classify it as a documentation decision, not removable runtime code.

## Documentation that should be corrected alongside cleanup

- `browser_parse/docs/open-source-options.md:35` says there is no backend service or remote worker transport. That paragraph describes the earlier browser-only implementation and contradicts the current application. Update the paragraph; retain its still-relevant archive-library discussion.
- `browser_parse/README.md` and `PROJECT_STRUCTURE.md` should accurately describe the brief generator's status after the decision above.
- `browser_parse/PROGRESS.md` mixes historical Cube verification with newer Docker work. Keep it explicitly chronological rather than using old entries as current setup instructions.

## Checks and limits

| Check | Result |
| --- | --- |
| Python syntax/AST parsing | 146 files passed |
| Shell syntax (`bash -n`) | 13 files passed; scripts were not executed |
| TypeScript (`tsc --project browser_parse/tsconfig.json --noEmit`) | Passed |
| Vite production build with writes disabled | Passed; `brief.ts` absent |
| Tools frontend (`npm test`) | 155 tests passed |
| Focused tools backend | 88 tests passed across Docker runtime, resource sizing, Grill store, runner, lifecycle, and regression modules |
| Focused synchronous QA checks | 46 passed, 14 asynchronous tests deselected |
| Broader QA test attempt | Hit a 55-second timeout after 15 progress dots; incomplete, not a full-suite pass |
| Real Docker jobs, model calls, browser sessions, and cloud processes | Not validated in this audit |

Focused tools command, run from `browser_parse/` using the working parent Python environment:

```sh
PYTHONPATH=. ../.venv-dev/bin/python -m pytest \
  tests_backend/test_docker_runtime.py tests_backend/test_resources.py \
  tests_backend/test_grill_store.py tests_backend/test_runner.py \
  tests_backend/test_grill_lifecycle.py tests_backend/test_grill_regressions.py -q
```

Focused synchronous QA command, run from the repository root:

```sh
.venv-dev/bin/python -m pytest tests/test_qa_api.py tests/test_langgraph_qa.py \
  tests/test_topic_aware_qa.py tests/test_deepseek_client.py -m 'not anyio' -q
```

## Suggested cleanup sequence — not executed

1. Remove the isolated unused guide/helpers and the unused direct dependency in a small change, updating only directly affected tests and the dependency lockfile.
2. Retire the old QA branch while preserving the active output boundaries; move applicable tests to the LangGraph path and complete streaming regression validation.
3. Resolve the missing Grill hibernation API integration in a separate change, then determine whether to consolidate the alternative lifecycle methods and migrate their tests.
4. Decide whether to retain the developer brief API and the old manually invoked Cube development image; align documentation with those decisions.

Count basis: QA scopes 630 + alternate Grill lifecycle 114 + static guide 108 + brief file 106 + knowledge gate 30 + small helpers 72 + legacy message parser 22 + old Dockerfile 8 = **1,090 lines**. Counts exclude surrounding whitespace cleanup, old prompts/constants, test changes, documentation changes, and lockfile churn. No database tables, live records, source documents, or persistent snapshots are proposed for deletion.

net: -1,090 source lines, -1 direct dependency possible, subject to the conditions above.
