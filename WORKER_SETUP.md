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
CAPABILITY_MATCH_WORKERS=1
CAPABILITY_MATCH_QUEUE_MAX=8
CLARIFICATION_WORKERS=1
CLARIFICATION_QUEUE_MAX=16
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
The Worker never calls `/sources/rescan`; Source Watch is the only ingestion trigger.
In Source Watch settings, include every format accepted by the website that you plan to use (especially `json`, `xml`, `yaml`, and `yml`, which are ingestable but are not selected in LLM Wiki's current defaults). Also set the Source Watch file-size limit high enough for the individual source files you upload.

Different authoring sessions can run concurrently, while commands for the same
session are serialized to preserve message order. Each authoring worker starts at
most one Claude subprocess, so increase `AUTHORING_WORKERS` only after checking
Worker memory, Claude account limits, and observed latency.

Scenario feasibility analysis has its own bounded queue. Keep one analysis
worker initially because each item starts a read-only Claude subprocess and may
read many Wiki records. Increase `CAPABILITY_MATCH_WORKERS` only after checking
memory, account concurrency, and latency; do not make the queue unbounded.

Clarification has a separate short-work queue so one-question turns cannot
starve immutable report analysis. Keep `CLARIFICATION_WORKERS=1` until the
Worker host has been tested with additional concurrent Claude subprocesses.

Public QA, WeCom QA, and authoring share one hardened Claude launcher. Messages
are sent through stdin, only `--model` is accepted from `CLAUDE_EXTRA_ARGS`, and
the effective tools are hard-limited to `Read`, `Glob`, and `Grep`. Ambiguous
security-sensitive requests use at most `PROMPT_GUARD_CONCURRENCY` isolated,
zero-tool classifier subprocesses. Text uploads are scanned within the byte
limits above; warnings are informational and do not block atomic publication.
