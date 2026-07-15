# Worker setup

See [FINAL_SETUP.md](FINAL_SETUP.md), sections **Worker upgrade** and **Removal behavior**.

The key new settings are:

```env
TRASH_DIR=/home/eason/Documents/agent_7_14/agent1/agent/.agent1-trash
AUTHORING_DIR=/home/eason/Documents/agent_7_14/agent1/agent/.agent1-worker/authoring
FILE_OPERATION_WORKERS=1
FILE_MANAGER_MAX_ENTRIES=10000
AUTHORING_WORKERS=2
AUTHORING_QUEUE_MAX=8
AUTHORING_LOCK_STRIPES=64
AUTHORING_MAX_CONTEXT_BYTES=768000
PROMPT_GUARD_ENABLED=true
PROMPT_GUARD_TIMEOUT=20
PROMPT_GUARD_CONCURRENCY=2
PROMPT_SCAN_MAX_FILE_BYTES=2097152
PROMPT_SCAN_MAX_TOTAL_BYTES=10485760
PROMPT_SCAN_MAX_WARNINGS=1000
LLM_WIKI_RESCAN_AFTER_PUBLISH=false
```

Keep LLM Wiki open with Source Watch and Auto Ingest enabled.

Different authoring sessions can run concurrently, while commands for the same
session are serialized to preserve message order. Each authoring worker starts at
most one Claude subprocess, so increase `AUTHORING_WORKERS` only after checking
Worker memory, Claude account limits, and observed latency.

Public QA, WeCom QA, and authoring share one hardened Claude launcher. Messages
are sent through stdin, only `--model` is accepted from `CLAUDE_EXTRA_ARGS`, and
the effective tools are hard-limited to `Read`, `Glob`, and `Grep`. Ambiguous
security-sensitive requests use at most `PROMPT_GUARD_CONCURRENCY` isolated,
zero-tool classifier subprocesses. Text uploads are scanned within the byte
limits above; warnings are informational and do not block atomic publication.
