# Validation

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
