# Validation

## Robot scenario feasibility compiler (chat-page grill)

From the repository root:

```bash
python3 -m compileall -q ecs worker shared scripts
python3 -m json.tool shared/schemas/atomic-capability.schema.json >/dev/null
python3 -m json.tool shared/schemas/feasibility-assessment.schema.json >/dev/null
pytest -q tests/test_scenario_sessions.py tests/test_account_menu.py tests/test_security_migration.py
```

The scenario-sessions tests cover schema installation, the building-block versus
operational-behavior evidence gate, additive/idempotent SQLite migration,
persistent scenario sessions, CSRF enforcement, and the shared conversation_id
binding. Run the HTTP tests in a supported project test environment; some
sandboxed Python 3.14 TestClient builds cannot create the internal stream file
descriptor and may hang before sending an ASGI request.

The capability-match stack (standalone `/capability-match` route, admin
capabilities UI, worker catalog jobs, feasibility matching, PDF/Markdown
report export) has been removed. The scenario grill now runs inside the chat
page (`/`): the **Assess robot fit** button toggles the same conversation box
into grill mode, and the finished report is delivered as a chat response.

On the deployed pair, verify in this order:

1. `/health` reports `worker_online: true`.
2. On `/`, confirm **Assess robot fit** toggles the chat input into grill mode
   without navigating away, and that `/capability-match` redirects to `/`.
3. Choose a robot or leave **Auto-select from scenario** selected, create a
   persistent scenario, confirm the selected robot is shown, and answer the
   one-question clarification flow through the minimum gate.
4. Confirm the status pill distinguishes working, waiting for customer input,
   reconnecting, paused when the Worker is offline, and report completion. Confirm
   stable scenarios enter the countdown and the versioned report is returned as a
   chat response.
5. Use a scenario with only building-block SDK/driver evidence and confirm the
   operational-behavior evidence gate reports `Operational behavior evidence required`.
6. Confirm the report appears as a chat bubble, that returning to chat keeps the
   same conversation context, and that **New conversation** clears both the chat
   history and the grill session.


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

- real Cerebras retrieval/answer calls and DeepSeek failover from the Worker;
- real LLM Wiki Source Watch deletion cleanup;
- Alibaba security group and future HTTPS proxy.

## Scenario clarification V2 validation

```bash
./scripts/uv_sync.sh dev
PYTHONPATH=. .venv-dev/bin/python -m pytest -q \
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
recovery. Recovery coverage includes a crash before reanalysis job creation, a
crash between job creation and marker clearing, offline startup followed by
Worker reconnect, reopening an existing failed logical job, and coalescing
multiple pending state changes to the newest version. A deterministic live-retry
test pauses attempt 1 after marker restoration, starts attempt 2, then verifies
attempt 1 cannot suppress or remove attempt 2's task registration.

## QA conversation/language validation

Validated after the conversation update:

- all Python modules compile;
- the same conversation ID maps to the same QA worker lane;
- different conversation IDs can map across the configured QA lanes;
- conversation history is included in the next SSE API request and answer prompt;
- all eight requested language codes are accepted;
- unsupported languages return HTTP 400;
- the API receives the selected language and robot/topic and retrieves only index-selected Wiki pages;
- the Worker response boundary preserves localized notices, safe errors, and canonical product names;
- the question page includes all eight language options and a New conversation control.

Live validation on the Worker machine must configure `CEREBRAS_API_KEY` and
`DEEPSEEK_API_KEY`, then ask an indexed question and a Walker C1 question whose
page is absent from `index.md`. Confirm answers stream in the selected language
without slugs or file paths. Simulate a Cerebras provider failure and confirm the
same request succeeds through DeepSeek without exposing partial primary output.
The test must not start, modify, or import from `$HOME/Documents/agent_tests`.
