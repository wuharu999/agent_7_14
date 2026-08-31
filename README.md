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

### URL-prefix deployments

The ECS supports an optional URL prefix through `ROOT_PATH`. Leave it empty for
normal root deployments such as the existing 47 ECS. When a WAF forwards the
prefix unchanged, configure only that ECS machine, for example:

```env
ROOT_PATH=/v1/faq-platform
```

The FastAPI application receives this value through its constructor, so both
prefixed routes such as `/v1/faq-platform/health` and the existing unprefixed
routes continue to work at the origin. Browser links, forms, redirects, API
requests, uploads, and static assets automatically remain beneath the configured
prefix. Do not replace this setting with only Uvicorn's `--root-path` flag.

### Indexed QA with DeepSeek

Public answers now use an Agent1-owned implementation based on the read-only
pattern demonstrated in `$HOME/Documents/agent_tests`: a router sees the Wiki
index plus a bounded set of filename-matched pages for the selected robot/topic,
Python validates the selected slugs and reads those pages, and a second DeepSeek
V4 Flash call streams the answer. DeepSeek is the sole public QA provider; there is
no provider circuit, egress-country gate, or maintained failover path. If DeepSeek
returns unusable router identifiers, Python safely normalizes path/case variants
and otherwise selects bounded Wiki pages deterministically. Internal slugs and
paths are removed before display. Files in `agent_tests` are not imported,
modified, packaged, or required at runtime. Keep the DeepSeek key only in
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
should contain the internal DeepSeek retrieval or generation failure.

To validate the feature branch before merge, run either update script with:

```bash
DEPLOY_BRANCH=agent/upload-token-notice ./scripts/pull_and_restart_worker.sh
```

Use the equivalent ECS script on the ECS machine. Normal production updates
should return to the default `main` branch after the pull request is merged.

### Knowledge-base images in QA answers

The Worker deterministically associates images with the Wiki pages already
selected for an answer. It prefers Markdown, Obsidian, and HTML image references
inside the best-matching section. PDF extraction may leave separate files in
`wiki/media/<page-stem>/` without Markdown references; those files remain on disk
for auditability but are never attached because a generic filename has no
semantic evidence. A linked image must have meaningful alt text or section
context, match the question, and have matching evidence in the final answer.
The answer model never selects paths and the normal QA path makes no extra image
classification call.

At most three images are attached. The Worker rejects traversal and symlinks,
permits PNG, JPEG, GIF, and WebP, and limits each image to 8 MiB and the total
answer images to 12 MiB. Validated images are sent through the existing
authenticated Worker connection and rendered by the QA page; the private Worker
filesystem is never exposed as a public path.

The Worker treats `LLM_WIKI_MONITOR_TIMEOUT` as a status-heartbeat interval,
not an ingestion deadline. A source remains queued, retrying, or processing as
long as LLM Wiki's persistent queue says it is active; only a current cache
receipt or terminal queue failure completes the per-source monitor.

QA retrieval uses the router's selected pages, then adds a bounded one-hop set
of their generated-Wiki links and lexically related generated pages. The total
is limited by `WIKI_QA_MAX_PAGES` (default 8); it never reads `raw/sources`.

QA answers must preserve product, project, platform, SDK, API, company, and brand
names exactly as written in the knowledge base. The Worker also corrects known
generated translations of `Thinkerstudio` and `Thinkercosmos` before streaming
them to users.

## Included

- Public question page.
- Authenticated multi-file upload page with per-file pipeline status.
- Authenticated source-tree manager.
- Editor and admin roles.
- Admin-managed robot display order shared by QA, upload, and management selectors.
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

Deploy ECS first, then deploy the one active Worker. The normal preserving
upgrade is:

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

Keep the old Worker stopped. After both restarts, confirm `worker_online: true`,
run one indexed QA request in the selected language, and verify that no internal
Wiki path or provider error is exposed. Existing `.env` files, SQLite data, virtual environments,
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

The public question page keeps a random conversation ID in the browser. Requests from that conversation are routed to the same QA lane and include a bounded recent history, so follow-up questions retain the most recently established robot/topic even when its name is omitted. The browser also sends a bounded copy of its visible completed turns so a restarted Worker can recover an otherwise unknown conversation; existing Worker memory remains authoritative and all history is treated as untrusted reference context. Selectable answer languages are Simplified Chinese, Traditional Chinese, Korean, Japanese, English, Portuguese, Russian, and Spanish.

When a specific robot is selected, Worker-side retrieval prioritizes that robot's Wiki evidence. Because one generated page may discuss several robots, the answer prompt enforces scope at the individual claim, table-row, bullet, and procedure level rather than trusting the page as a whole. Directly relevant information from another robot may be included only as clearly named secondary context; it must not be presented as proof that the selected robot has the same capability. With `All Robots`, the most recent applicable conversation turn remains the subject anchor.

Public QA uses the DeepSeek-only API pipeline in `worker/qa_api.py`. Prompt
classification also uses the shared tool-free DeepSeek API client. Python alone
controls Wiki retrieval, validation, and response filtering.

Direct policy-override, secret-extraction, and tool-escalation attempts receive
a generic localized refusal and are not retained in conversation storage or
security logs. Text-based uploads are scanned before atomic publication. The
status page shows relative filenames and warning categories only; suspicious
documents continue into the normal LLM Wiki pipeline.
