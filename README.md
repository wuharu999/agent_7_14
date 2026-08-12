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

### Indexed QA with provider failover

Public answers now use an Agent1-owned implementation based on the read-only
pattern demonstrated in `$HOME/Documents/agent_tests`: a router sees the Wiki
index plus a bounded set of filename-matched pages for the selected robot/topic,
Python validates the selected slugs and reads those pages, and a second model call
streams the answer. Cerebras is primary only when the Worker's freshly verified outbound
country is permitted; CN/TW/HK/SG or an unverifiable country uses DeepSeek
directly. Any Cerebras API failure opens a five-minute circuit and retries through
DeepSeek V4 Flash. If DeepSeek returns unusable router identifiers, Python safely
normalizes path/case variants and otherwise selects bounded Wiki pages
deterministically. Internal slugs and paths are removed before display. Files in `agent_tests` are not imported,
modified, packaged, or required at runtime. Keep both provider keys only in
`worker/.env`.

The public QA path does not start local coding agents, read local agent instruction files, or search
`raw/sources/`. Python opens only permitted Markdown under the selected
project's `wiki/` directory. Legacy TianGong product names are canonicalized in
memory before routing and generation and again at the streaming boundary; the
original uploads and generated Wiki files are never rewritten by this feature.

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
should contain the internal Cerebras retrieval or generation failure.

To validate the feature branch before merge, run either update script with:

```bash
DEPLOY_BRANCH=agent/upload-token-notice ./scripts/pull_and_restart_worker.sh
```

Use the equivalent ECS script on the ECS machine. Normal production updates
should return to the default `main` branch after the pull request is merged.

### Knowledge-base images in QA answers

The answer model may include up to three relevant images referenced by retrieved
`wiki/media/`. The Worker accepts only project-relative Markdown references,
rejects traversal and symlinks, permits PNG, JPEG, GIF, and WebP, and limits each
image to 8 MiB and the total answer images to 12 MiB. Validated images are sent
through the existing authenticated Worker connection and rendered by the QA
page; the private Worker filesystem is never exposed as a public path.

QA answers must preserve product, project, platform, SDK, API, company, and brand
names exactly as written in the knowledge base. The Worker also corrects known
generated translations of `Thinkerstudio` and `Thinkercosmos` before streaming
them to users.

### Robot scenario feasibility compiler

Open `/capability-match` to create a persistent scenario for one robot. The
server saves each clarification answer as a version, asks one conclusion-changing
customer question at a time, starts analysis when the scenario is stable, and
keeps the latest report visible during later refinement. The QA page's **Scenario
Feasibility** button opens this dedicated workflow and can carry the current draft,
language, and selected robot. The main chat does not run scenario analysis itself.
When no robot is selected or named, the scenario service selects one from the
scenario and available robot metadata and shows the selected model to the customer.

The canonical catalog has two capability types: `building_block` for reusable
interface-level primitives and `operational_behavior` for end-to-end behavior
with an observable operating envelope. Old L0/L1/L2 records are migrated at the
read boundary for compatibility; L3 scenario modules are solution artifacts,
not atomic capabilities. A building block cannot by itself prove an operational
behavior, even if the LLM proposes a passing match.

The scenario page explicitly distinguishes working, waiting, reconnecting, paused
(Worker offline), and completed states. It also shows versioned reports, conditions,
evidence, unresolved boundaries, next actions, and Markdown/PDF exports. Assessments are
stored additively in `agent_jobs.db`; anonymous public assessments have no user
owner. At `/admin/capabilities`, administrators can review aggregate requested
gaps, create idempotent evidence-acquisition stubs, inspect added/modified/deleted
Wiki paths since the last successful organization, and start the repository-wide atomic
capability organizer. Organization runs are persisted in SQLite so every admin
sees the same progress after a refresh. **Scan whole Wiki and replace catalog**
first backs up the last successful catalog, performs fresh parallel extraction
over every generated Wiki file, and replaces the live catalog only after complete
validation. **Scan new Wiki files and append** processes added or modified Wiki
files since the successful baseline and may only add new capability IDs and
semantic keys; it never modifies existing entries. The organizer excludes its own generated
`wiki/capabilities/` tree and does not send raw images directly through the
text-only DeepSeek evidence pass. Incomplete coverage is reported as partial and
does not publish or advance the successful baseline. DeepSeek receives only bounded generated Wiki evidence; only
schema-validated draft entries are atomically published by Python.
The persistent Worker WebSocket sends its shared secret in the
`X-Worker-Secret` handshake header, not in the URL, so access logs do not record
the credential. ECS temporarily accepts the older query parameter to support a
rolling Worker upgrade.

Capability organization is a deterministic batched map/reduce pipeline. Python
enumerates and reads every eligible generated Wiki text file, splits oversized
files into UTF-8-safe evidence units, and creates stable content-hashed batches.
Up to four DeepSeek calls run concurrently without tools and receive only the relevant
allowlisted catalog-policy sections, so it cannot choose or
silently skip files and applies the same atomicity/evidence rules during extraction
and reduction. Successful batch results are checkpointed under
`.agent1-worker/capability-batch-cache/`; interrupted reruns reuse checkpoints.
A set of small parallel reduction calls handles at most two candidates each;
Python then merges semantic duplicates and resolves ID collisions deterministically,
avoiding one oversized final response. Python constructs the coverage report and
runs the existing hard-gate validator before atomic publication. Shared progress includes cumulative candidate, blocked, and
excluded counts; completed results include blocked filenames and grouped exclusion
reasons.

DeepSeek may propose a capability's robot scope only from the registered robot
IDs supplied by ECS. Python normalizes known display aliases, rejects cross-robot
catalog matches, and stores unknown or conflicting scopes as `unassigned` for
review. Catalog organization never creates robot records; robot management or
the deterministic source-onboarding reconciliation owns the registry. A later
scan can assign capabilities only after the new robot appears in that registry.

## Included

- Public question page.
- Authenticated multi-file upload page with per-file pipeline status.
- Authenticated source-tree manager.
- Editor and admin roles.
- Soft deletion into `.agent1-trash/` rather than permanent erasure.
- CSRF-protected file-changing requests.
- SQLite users, sessions, uploads, source status and audit logs.
- Strict isolation of files and QA sessions across multiple knowledge base teams.
- Three concurrent local SSE-backed QA slots by default.
- Two concurrent downloads by default.
- QA rate limiting (10 requests/min, 50 requests/hour per IP).
- Complete UI language synchronization and translation for public QA.
- Safe ZIP extraction.
- Existing LLM Wiki GUI used for Source Watch, Auto Ingest and wiki writing.
- Browser-visible ingestion status through LLM Wiki queue/cache monitoring.
- Scenario feasibility workbench with deterministic capability-type and evidence gates.
- SQLite-backed capability-gap analytics and admin draft-stub generation.
- Shared capability-organization progress and source-manifest change tracking.
- Prompt/command-injection hardening for browser QA and
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
  repository Wiki changes, R&D gap analytics, and draft stubs

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
.venv-ecs/bin/python scripts/create_user.py --username uploader --role editor
```

Python dependencies are declared in `pyproject.toml`, resolved in the committed
`uv.lock`, and installed with `uv`. Use `./scripts/uv_sync.sh ecs`,
`./scripts/uv_sync.sh worker`, or `./scripts/uv_sync.sh dev`; do not install the
runtime from ad-hoc requirements files.

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
CAPABILITY_CATALOG_WORKERS=2
CAPABILITY_CATALOG_QUEUE_MAX=8
CAPABILITY_CATALOG_BATCH_CONCURRENCY=4
CAPABILITY_CATALOG_BATCH_BYTES=98304
CAPABILITY_CATALOG_UNIT_BYTES=65536
CAPABILITY_CATALOG_BATCH_TIMEOUT=1800
CAPABILITY_CATALOG_REDUCE_TIMEOUT=3600
```

The ECS permits one repository catalog transaction at a time. Inside that job,
Python runs up to four DeepSeek extraction batches concurrently, checkpoints
each result, and reduces candidates in groups of up to 16 with the same bounded
concurrency. Schema-valid but semantically incomplete reduction output is split
into smaller groups automatically; an irreparable single candidate is retried
once and recorded as blocked for manual review. Adaptive recovery is capped at
eight provider calls per original group, so persistent invalid output cannot
expand into an unbounded retry tree. Any candidate still blocked after recovery
makes the scan partial, preserving the last successful catalog and baseline
instead of publishing incomplete replacement data. If a non-recoverable request
fails, outstanding sibling requests are cancelled before the job reports its
failure. Final consolidation and publication remain deterministic Python operations.
The second Worker consumer keeps the bounded command queue responsive; it does
not create a second live catalog writer. Raise these bounds only after checking
provider rate and token limits.

These are enforced minimums during rolling upgrades: 30 minutes for each
extraction batch, 60 minutes for the final reduction, and 24 hours for the
complete ECS-to-Worker job. A retry restores completed content-hashed batches,
so only the unfinished batch must run again after a provider timeout.

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

The public question page keeps a random conversation ID in the browser. Requests from that conversation are routed to the same QA lane and include a bounded recent history, so follow-up questions retain context without dedicating a provider session to one user. Selectable answer languages are Simplified Chinese, Traditional Chinese, Korean, Japanese, English, Portuguese, Russian, and Spanish.

Public QA uses the provider-neutral API pipeline in `worker/qa_api.py`. Scenario
clarification, feasibility analysis, capability organization, prompt
classification, and contradiction review use the shared tool-free DeepSeek API
client. Python alone controls Wiki retrieval, schemas, state, and publication.

Direct policy-override, secret-extraction, and tool-escalation attempts receive
a generic localized refusal and are not retained in conversation storage or
security logs. Text-based uploads are scanned before atomic publication. The
status page shows relative filenames and warning categories only; suspicious
documents continue into the normal LLM Wiki pipeline.
