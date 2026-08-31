# Worker setup

See [FINAL_SETUP.md](FINAL_SETUP.md), sections **Worker upgrade** and **Removal behavior**.

Install `uv`, then run `./scripts/bootstrap_worker.sh`. Dependencies come from
the root `pyproject.toml` and committed `uv.lock`; `.venv-worker` remains the
runtime environment and its existing Python version is preserved on upgrades.

The key new settings are:

```env
TRASH_DIR=/home/eason/Documents/agent_7_14/agent1/agent/.agent1-trash
FILE_OPERATION_WORKERS=1
FILE_MANAGER_MAX_ENTRIES=10000
PROMPT_GUARD_ENABLED=true
PROMPT_GUARD_TIMEOUT=20
PROMPT_GUARD_CONCURRENCY=2
PROMPT_SCAN_MAX_FILE_BYTES=2097152
PROMPT_SCAN_MAX_TOTAL_BYTES=10485760
PROMPT_SCAN_MAX_WARNINGS=1000
LLM_WIKI_RESCAN_AFTER_PUBLISH=false
DEEPSEEK_API_KEY=replace-with-a-worker-only-key
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT=240
WIKI_QA_MAX_PAGES=5
WIKI_QA_MAX_PAGE_CHARS=24000
```

The Worker reads `BASE_DIR/wiki/index.md`, supplements a stale index with at
most 20 filename/path matches for the selected robot/topic (including Walker C1),
validates router-selected slugs, and opens only those permitted Markdown pages.
DeepSeek V4 Flash is the sole public QA provider; no provider circuit or
egress-country gate is configured. The browser continues
to receive streamed chunks through the authenticated Worker WebSocket and never
receives a Worker file path. `$HOME/Documents/agent_tests` is reference material
only and is not part of deployment.

Public QA is API-only: it does not launch local coding agents, read local agent instruction files, or open
original documents under `raw/sources/`. Product-name normalization is applied
to the in-memory Wiki projection and streamed answer; source and Wiki files on
disk are not rewritten.

The Worker launcher automatically removes `ALL_PROXY`/`all_proxy` only when a
desktop VPN supplied an unsupported SOCKS URL. Valid `HTTP_PROXY`,
`HTTPS_PROXY`, `NO_PROXY`, and HTTP-valued `ALL_PROXY` settings are preserved.
This prevents provider client initialization failures after scripted restarts
without disabling a working HTTP VPN proxy.

Keep LLM Wiki open with Source Watch and Auto Ingest enabled.
The Worker never calls `/sources/rescan`; Source Watch is the only ingestion trigger.
In Source Watch settings, include every format accepted by the website that you plan to use (especially `json`, `xml`, `yaml`, and `yml`, which are ingestable but are not selected in LLM Wiki's current defaults). Also set the Source Watch file-size limit high enough for the individual source files you upload.

Public QA uses bounded Python retrieval plus two DeepSeek calls.
Ambiguous security-sensitive requests still use at most `PROMPT_GUARD_CONCURRENCY`
concurrent schema-validated classifier calls. Text uploads are scanned within the
byte limits above; warnings are informational and do not block atomic publication.
