# Validation

## Local validation

From the repository root:

```bash
python3 -m compileall -q ecs worker shared scripts
./scripts/uv_sync.sh dev
.venv-dev/bin/python -m pytest -q
```

On the deployed pair, verify in this order:

1. `/health` reports `worker_online: true`.
2. Confirm `/` contains only the public QA workflow and no retired assessment UI.
3. Ask an indexed question and confirm the response streams in the selected language.
4. Confirm **New conversation** resets the bounded Worker-side context.


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

- real DeepSeek retrieval/answer calls from the Worker;
- real LLM Wiki Source Watch deletion cleanup;
- Alibaba security group and future HTTPS proxy.

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

Live validation on the Worker machine must configure `DEEPSEEK_API_KEY`, then
ask an indexed question and a Walker C1 question whose
page is absent from `index.md`. Confirm answers stream in the selected language
without slugs or file paths. Simulate a DeepSeek request or stream failure and
confirm the localized generic error does not expose provider details or partial
output.
The test must not start, modify, or import from `$HOME/Documents/agent_tests`.
