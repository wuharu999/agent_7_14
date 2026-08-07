# Agent1 7.14 Final

Agent1 is a public ECS gateway plus a private Worker for a robot-documentation knowledge base.

## Git-based cloud updates

After the one-time private GitHub clone and machine configuration, deploy code
updates without copying ZIP files:

```bash
# ECS
cd /root/agent_7_14
./scripts/pull_and_restart_ecs.sh

# Worker
cd "$HOME/Documents/agent_7_14"
./scripts/pull_and_restart_worker.sh
```

Both scripts pull `origin/main` with a fast-forward-only merge, preserve local
environment files and ignored runtime data, create deployment backups, update
Python dependencies, restart the expected tmux session, and run machine-level
checks. Override the branch with `DEPLOY_BRANCH` when required.

### Updating the cloud computers for the chunking-error fix

The recommended path is to merge the GitHub pull request into `main`, then
update the ECS and Worker separately. Do not start the old Worker during this
process; the ECS supports only one active Worker connection.

On the existing ECS computer:

```bash
cd /root/agent_7_14
git status --short
./scripts/pull_and_restart_ecs.sh
curl -fsS http://127.0.0.1:8000/health | python3 -m json.tool
```

On the new Worker computer, using its real login user:

```bash
cd "$HOME/Documents/agent_7_14"
git status --short
./scripts/pull_and_restart_worker.sh

# Install the updated query template into the preserved live Wiki project.
mkdir -p agent1/agent/wiki/queries
cp agent1/wiki/queries/knowledge-base-query-template.md \
  agent1/agent/wiki/queries/knowledge-base-query-template.md

curl -fsS http://47.239.12.206:8000/health | python3 -m json.tool
tmux capture-pane -pt agent-7-14-worker -S -80
```

The final health response must show `worker_online: true`. Then start a new
browser conversation and test a normal `tian_gong` question in Simplified
Chinese. The old raw error must not appear; if the upstream failure recurs, the
page should show the localized temporary-unavailable message and the Worker log
should contain `Suppressed internal document-chunking error in Claude output`.

To validate the feature branch before merge, run either update script with:

```bash
DEPLOY_BRANCH=agent/upload-token-notice ./scripts/pull_and_restart_worker.sh
```

Use the equivalent ECS script on the ECS machine. Normal production updates
should return to the default `main` branch after the pull request is merged.

### Why `Separator is found, but chunk is longer than limit` appeared

This message comes from Python's asynchronous subprocess stream reader, not
from LLM Wiki splitting a Markdown document. The Worker reads Claude CLI's
newline-delimited `stream-json` output with `readline()`. One JSON event could
exceed Python's default 64 KiB stream limit even when every individual Markdown
file was small. Tool results, image data, JSON escaping, and accumulated answer
content can all make a single transport line much larger than its source file.
It appeared only sometimes because the failure depended on what Claude read for
that request and the size of the resulting JSON event.

The Worker now gives Claude's stream a bounded 32 MiB buffer, converts any larger
event into a controlled `ClaudeProcessError`, and keeps a final manager-level
boundary that never exposes raw exceptions. Normal answer text streams through
a short rolling safety buffer, while thinking-token progress remains live and
raw chain-of-thought stays private. Old raw failures are omitted from later
conversation history. The technical event remains in the Worker log for
diagnosis. Override `CLAUDE_STREAM_BUFFER_LIMIT` only if a known deployment
requires a different bounded value.

### Knowledge-base images in QA answers

Claude may include up to three relevant images that it has actually read from
`wiki/media/`. The Worker accepts only project-relative Markdown references,
rejects traversal and symlinks, permits PNG, JPEG, GIF, and WebP, and limits each
image to 8 MiB and the total answer images to 12 MiB. Validated images are sent
through the existing authenticated Worker connection and rendered by the two QA
pages; the private Worker filesystem is never exposed as a public path. Text-only
consumers such as WeCom receive the cleaned answer without image-path markers.

QA answers must preserve product, project, platform, SDK, API, company, and brand
names exactly as written in the knowledge base. The Worker also corrects known
generated translations of `Thinkerstudio` and `Thinkercosmos` before streaming
them to users.

### Robot scenario feasibility compiler

Open `/capability-match` to create a persistent scenario for one robot. The
server saves each clarification answer as a version, asks one conclusion-changing
customer question at a time, starts analysis when the scenario is stable, and
keeps the latest report visible during later refinement. The QA page's **Scenario
Feasibility** shortcut can prefill this workflow from recent customer-side turns.

The deterministic matcher enforces four explicit capability layers:
`L0_primitive_driver`, `L1_atomic_skill`, `L2_composite_skill`, and
`L3_scenario_module`. L0 drivers cannot satisfy L1-L3 scenario requirements. A
match backed only by L0 primitives is always rewritten to
`R&D Gap (Composite Skill Missing)`, even if the LLM proposed a passing match.

The scenario page shows reconnecting progress, versioned reports, conditions,
evidence, unresolved boundaries, next actions, and Markdown/PDF exports. Assessments are
stored additively in `agent_jobs.db`; anonymous public assessments have no user
owner. At `/admin/capabilities`, administrators can review aggregate requested
gaps, create idempotent evidence-acquisition stubs, inspect added/modified/deleted
source paths since the last successful organization, and start the atomic
capability organizer. Organization runs are persisted in SQLite so every admin
sees the same progress after a refresh. The first run automatically scans all
generated Wiki evidence; later runs are incremental. **Resume full scan** reuses
matching content checkpoints, while **Force full re-extraction** ignores them and
calls the LLM for every batch. The organizer excludes its own generated
`wiki/capabilities/` tree and does not send raw images directly through the
text-only Claude evidence pass. Incomplete coverage is reported as partial and
does not advance the successful baseline. Claude has read-only source access; only
schema-validated draft entries are atomically published by Python, with a backup.
Deletion, deprecation, and overwriting reviewed/verified entries are rejected.
The persistent Worker WebSocket sends its shared secret in the
`X-Worker-Secret` handshake header, not in the URL, so access logs do not record
the credential. ECS temporarily accepts the older query parameter to support a
rolling Worker upgrade.

Capability organization is a deterministic batched map/reduce pipeline. Python
enumerates and reads every eligible generated Wiki text file, splits oversized
files into UTF-8-safe evidence units, and creates stable content-hashed batches.
Claude runs without tools for each batch and receives the complete bundled
`maintain-model-atomic-capability-wiki` skill contract, so it cannot choose or
silently skip files and applies the same atomicity/evidence rules during extraction
and reduction. Successful batch results are checkpointed under
`.agent1-worker/capability-batch-cache/`; interrupted reruns reuse checkpoints.
A final no-tools reduction merges and deduplicates candidates, after which Python
constructs the coverage report and runs the existing hard-gate validator before
atomic publication. Shared progress includes cumulative candidate, blocked, and
excluded counts; completed results include blocked filenames and grouped exclusion
reasons.

## Included

- Public question page and WeCom callback.
- Authenticated multi-file upload page with per-file pipeline status.
- Authenticated source-tree manager.
- Editor and admin roles.
- Soft deletion into `.agent1-trash/` rather than permanent erasure.
- CSRF-protected file-changing requests.
- SQLite users, sessions, uploads, source status and audit logs.
- Strict isolation of files and QA sessions across multiple knowledge base teams.
- Three concurrent Claude QA subprocess slots by default.
- Two concurrent downloads by default.
- QA rate limiting (10 requests/min, 50 requests/hour per IP).
- Complete UI language synchronization and translation for public QA.
- Safe ZIP extraction.
- Existing LLM Wiki GUI used for Source Watch, Auto Ingest and wiki writing.
- Browser-visible ingestion status through LLM Wiki queue/cache monitoring.
- Scenario feasibility workbench with deterministic L0 hard-gate enforcement.
- SQLite-backed capability-gap analytics and admin draft-stub generation.
- Shared capability-organization progress and source-manifest change tracking.
- Prompt/command-injection hardening for browser QA, WeCom, authoring, and
  retrieved source content.
- Non-blocking text-source security warnings on upload status pages.

## Pages

- `/` — public question page
- `/login` — sign in
- `/manage` — authenticated source tree and removal controls
- `/upload` — editor/admin upload page
- `/uploads/<upload_id>` — authenticated upload progress
- `/health` — public service health
- `/capability-match` — public scenario feasibility workbench
- `/admin/capabilities` — admin-only atomic capability organizer, shared progress,
  source changes, R&D gap analytics, and draft stubs
- `/wecom/callback` — WeCom callback

## Roles

- `editor`: inspect, upload and remove sources
- `admin`: editor permissions plus account administration at `/admin/users`

## Upload batches

Editors and admins can select up to 20 supported files for one team on
`/upload`. The browser sends at most two files concurrently, and each file is
still an independent upload with its own ID, atomic source folder, security
scan, error handling, and LLM Wiki status link. A failed file does not stop the
rest of the selection.

Supported documents are PDF, DOCX, PPTX, and XLSX. Supported text/data files
are Markdown, TXT, CSV, JSON, HTML, XML, and YAML. Visual assets are PNG,
JPG/JPEG, WebP, and GIF; they are published as original source assets for LLM
Wiki's multimodal ingestion. ZIP archives may contain supported sources and
should be used when related files need to remain one source bundle.

On the first startup of a fresh database, the ECS seeds an `admin` account. Its
password comes from `DEFAULT_ADMIN_PASSWORD`, or defaults to
`Admin#2026!Secured89`. Change it immediately at `/admin/users`.

Create additional accounts from the ECS command line when needed:

```bash
source .venv-ecs/bin/activate
python3 scripts/create_user.py --username uploader --role editor
```

## Important safety behavior

Removal is a move, not a permanent delete:

```text
raw/sources/team/upload/file.pdf
→ .agent1-trash/<timestamp>/team/upload/file.pdf
```

The Worker rejects absolute paths, `..` traversal, symlinks, removal of the `raw/sources` root, and removal of a source while LLM Wiki marks it as `processing`.

## LLM Wiki

All robots use the single live LLM Wiki project configured by `BASE_DIR` (normally `agent1/agent`). Their source files remain separated under `raw/sources/<robot>/`.
Keep LLM Wiki open on that exact project directory with Source Watch and Auto Ingest enabled.

The Worker does not call `/sources/rescan`; Source Watch is the only ingestion trigger. Keep the legacy `LLM_WIKI_RESCAN_AFTER_PUBLISH=false` setting during upgrades for configuration consistency.

The local LLM Wiki API token is not required for queue/cache monitoring or Auto Ingest.

## Deployment

Read [FINAL_SETUP.md](FINAL_SETUP.md) for full first-run and upgrade instructions.

For this feature, deploy ECS first so the additive assessment tables and routes
exist, then deploy the one active Worker so it understands the new correlated
`analyze_scenario` command. The normal preserving upgrade is:

```bash
# Existing ECS: backs up ecs/.env and ecs-data, then migrates additively.
cd /root/agent_7_14
./scripts/pull_and_restart_ecs.sh
curl -fsS http://127.0.0.1:8000/health | python3 -m json.tool

# New Worker computer: preserves worker/.env and the complete live Wiki project.
cd "$HOME/Documents/agent_7_14"
./scripts/pull_and_restart_worker.sh
curl -fsS http://47.239.12.206:8000/health | python3 -m json.tool
```

Keep the old Worker stopped. The two optional Worker settings default safely to
one bounded analysis worker and eight queued analyses:

```env
CAPABILITY_MATCH_WORKERS=1
CAPABILITY_MATCH_QUEUE_MAX=8
CLARIFICATION_WORKERS=1
CLARIFICATION_QUEUE_MAX=16
```

Deterministic capability extraction uses these bounded defaults. The ECS command
timeout is the end-to-end guardrail; the Worker uses separate per-batch and
reduction limits:

```env
# ECS
CAPABILITY_CATALOG_TIMEOUT=86400

# Worker
CAPABILITY_CATALOG_BATCH_BYTES=98304
CAPABILITY_CATALOG_UNIT_BYTES=65536
CAPABILITY_CATALOG_BATCH_TIMEOUT=1800
CAPABILITY_CATALOG_REDUCE_TIMEOUT=3600
```

These are enforced minimums during rolling upgrades: 30 minutes for each
extraction batch, 60 minutes for the final reduction, and 24 hours for the
complete ECS-to-Worker job. A retry restores completed content-hashed batches,
so only the unfinished batch must run again after a Claude timeout.

After both restarts, confirm `worker_online: true`, run one small persistent
scenario for a specific model, verify Markdown and PDF exports, and create one
draft stub as an admin. Existing `.env` files, SQLite data, virtual environments,
`raw/`, `wiki/`, `.llm-wiki/`, `.agent1-trash/`, and `.agent1-worker/` remain
preserved by the deployment scripts.

For a Worker computer that receives the release manually, download the newest
`release.zip` into `~/Downloads`, then run:

```bash
cd "$HOME/Documents/agent_7_14"
./scripts/deploy_worker_from_downloads.sh
```

The script backs up `worker/.env` and the live LLM Wiki data, preserves the
virtual environment and complete `agent1/agent` project, installs any Worker
requirements, restarts the single Worker tmux session, and verifies the ECS
connection. An explicit ZIP path may be passed as its only argument.

## Conversation-aware multilingual QA

The public question page keeps a random conversation ID in the browser. Requests from that conversation are routed to the same QA lane and include a bounded recent history, so follow-up questions retain context without dedicating a permanent Claude process to one user. Selectable answer languages are Simplified Chinese, Traditional Chinese, Korean, Japanese, English, Portuguese, Russian, and Spanish.

The Worker invokes every Claude path through the same safe-mode subprocess
launcher. User content is sent through stdin; slash commands, session
persistence, plugins, and MCP servers are disabled; and only `Read`, `Glob`, and
`Grep` are exposed. The bundled service prompt and `CLAUDE.md` treat retrieved
documents as untrusted evidence and prohibit asking website users for file-read
permission.

Direct policy-override, secret-extraction, and tool-escalation attempts receive
a generic localized refusal and are not retained in conversation storage or
security logs. Text-based uploads are scanned before atomic publication. The
status page shows relative filenames and warning categories only; suspicious
documents continue into the normal LLM Wiki pipeline.

Editors and admins can also use the Claude documentation author on `/upload`. The authoring conversation is persisted by the Worker, can generate a Markdown article for review, and publishes only after an explicit confirmation. Claude remains read-only; the Worker performs the final atomic publication into `raw/sources/` for LLM Wiki Source Watch.

Authoring uses a bounded, configurable Worker pool. Separate sessions may run concurrently, while each session is serialized so turns cannot overwrite one another. Claude prompts are streamed through stdin with a bounded recent-history context; Python multiprocessing is intentionally unnecessary because Claude already runs as an external subprocess. The upload page polls the stored article status and displays LLM Wiki completion or the original ingestion error.
