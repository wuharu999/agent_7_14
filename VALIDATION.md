# Validation

## Robot scenario feasibility compiler

From the repository root:

```bash
python3 /home/eason/Downloads/vscode-copilot-robot-scenario-compiler-agent-v1.0.0-20260730/scripts/validate_agent_package.py
python3 -m compileall -q ecs worker shared scripts
python3 -m json.tool shared/schemas/atomic-capability.schema.json >/dev/null
python3 -m json.tool shared/schemas/feasibility-assessment.schema.json >/dev/null
pytest -q tests/test_capability_catalog.py tests/test_capability_match.py tests/test_account_menu.py tests/test_security_migration.py
```

The capability-match tests cover schema installation, the building-block versus
operational-behavior evidence gate, additive/idempotent SQLite migration, public persistence,
Markdown/PDF exports, admin analytics, CSRF enforcement, and idempotent draft
stub creation. Run the HTTP tests in a supported project test environment; some
sandboxed Python 3.14 TestClient builds cannot create the internal stream file
descriptor and may hang before sending an ASGI request.

On the deployed pair, verify in this order:

1. `/health` reports `worker_online: true`.
2. Select a specific robot on `/`, enable Analyze Demand Mode, and submit a
   bounded customer scenario.
3. Open the CTA and confirm `SCN-*`/`REQ-*` records and the three workbench
   columns.
4. Use a scenario with only building-block SDK/driver evidence and confirm the
   operational-behavior evidence gate reports `Operational behavior evidence required`.
5. Download `feasibility_report.md` and `feasibility_report.pdf`.
6. Sign in as admin and open `/admin/capabilities`. Confirm the file-change panel
   shows the selected robot's added/modified/deleted paths.
7. Start organization with no prior organization manifest. Confirm it reports a
   full Wiki scan, and a second admin session plus a refreshed page see the same state.
8. Add or modify a generated Wiki page and confirm **Organize changes** uses an
   incremental scan. Then use **Resume full scan** and confirm matching checkpoints
   are restored. Use **Force full re-extraction** and confirm all generated Wiki
   evidence is inventoried without checkpoint reads while `wiki/capabilities/` and
   raw images are excluded.
   Confirm the progress advances through deterministic extraction batches and
   reduction, every eligible evidence file has a final status, and Claude is
   invoked with no tools. Interrupt a test run after one batch, rerun it, and
   confirm completed batches are restored from content-hashed checkpoints.
   Confirm every extraction call contains the complete atomic-capability skill
   contract, and the shared UI displays cumulative candidates, blocked files, and
   grouped exclusion reasons.
   Confirm deployed timeout floors are 1800 seconds per extraction batch, 3600
   seconds for reduction, and 86400 seconds for the complete ECS command.
9. Confirm validated drafts appear in both the UI and
   `wiki/capabilities/<model>/`, the source change list resets only after complete
   coverage, and a backup is retained for an existing catalog. A partial run must
   keep the previous successful baseline.
10. Create one draft stub. Confirm it appears once after repeating the same request and the audit
   log contains `create_capability_draft_stub`.

The final package was checked with:

- Python compilation of ECS, Worker and user-management code.
- SQLite schema creation and migration.
- User creation, password verification and session lookup.
- Login redirect and authenticated page tests with FastAPI TestClient.
- Source-tree listing with cache-derived status.
- Soft deletion into `.agent1-trash`.
- Blocking folder removal while a child source is being ingested.
- Exact-prefix database marking for removed source folders.
- Existing safe ZIP and upload/download design retained.

External integrations still require live validation on the deployment machines:

- real Claude CLI calls;
- real LLM Wiki Source Watch deletion cleanup;
- real WeCom callback credentials;
- Alibaba security group and future HTTPS proxy.

## Scenario clarification V2 validation

```bash
PYTHONPATH=. .venv-ecs/bin/python -m pytest -q \
  tests/test_scenario_sessions.py \
  tests/test_capability_match.py \
  tests/test_capability_catalog.py \
  tests/test_security_migration.py
```

This covers one-question selection, semantic deduplication, the goal/workflow
minimum gate, anonymous and account resume, optimistic state versions, separate
Worker queues, immutable/superseded report revisions, the locker reference
flow, revision-bound Markdown/PDF export, share-link redaction, and safe DOM
rendering. It also covers stability precedence, canonical status synchronization,
allowlisted model state patches, evidence-context requests, structured follow-up
classification, conversational change confirmation, changes during analysis,
coalesced reanalysis, retained superseded reports, unknown-owner resolution,
long-lived/reconnecting SSE, safe fact-derived progress summaries, and restart
recovery.

## QA conversation/language validation

Validated after the conversation update:

- all Python modules compile;
- the same conversation ID maps to the same QA worker lane;
- different conversation IDs can map across the configured QA lanes;
- conversation history is included in the next Claude prompt;
- all eight requested language codes are accepted;
- unsupported languages return HTTP 400;
- the Claude command includes read-only `--allowedTools Read Glob Grep` arguments;
- both bundled `CLAUDE.md` files prohibit permission requests and retrieval narration;
- the question page includes all eight language options and a New conversation control.

Live validation still required on the Worker machine: run `claude --help` once to confirm the installed Claude CLI supports `--allowedTools` (current Claude Code versions do). Then ask a question that requires reading `wiki/index.md` and confirm no permission-request text is returned.
