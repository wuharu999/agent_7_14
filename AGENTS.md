# AGENTS.md

## Purpose

This repository is a two-machine knowledge-base upload and QA system.

Codex should continue development, testing, and deployment from the current project state without replacing the architecture or deleting live data.

The production topology is:

```text
Browser
    -> existing Alibaba ECS FastAPI server
    -> one authenticated persistent WebSocket
    -> Worker Manager on a new cloud computer
    -> local LLM Wiki project
    -> index-constrained Wiki retrieval
    -> Cerebras chat-completion API with DeepSeek V4 Flash failover for QA
```

The ECS is the public-facing gateway. The Worker computer owns the real knowledge-base files, runs LLM Wiki, performs bounded retrieval, and calls Cerebras with DeepSeek V4 Flash failover for public QA.

---

## Deployment targets

### Existing ECS server

```text
Public IP: 47.239.12.206
Project root: /root/agent_7_14
HTTP test port: 8000
ECS environment: /root/agent_7_14/ecs/.env
ECS data: /root/agent_7_14/ecs-data
SQLite database: /root/agent_7_14/ecs-data/agent_jobs.db
Expected tmux session: agent-7-14-ecs
```

### New Worker computer

Do not hard-code the username. Resolve it from `$HOME`.

```text
Project root: $HOME/Documents/agent_7_14
Worker environment: $HOME/Documents/agent_7_14/worker/.env
LLM Wiki projects: $HOME/Documents/agent_7_14/agent1/<team>
Expected tmux session: agent-7-14-worker
```

### Old Worker computer

The old Worker must remain stopped after cutover. The ECS currently supports one active Worker connection. Never run the old and new Workers simultaneously.

---

## Current product requirements

The latest intended product includes all of the following.

### Public QA page

- Public route: `/`
- Accept a user question.
- Maintain a browser conversation ID in `localStorage`.
- Route the same conversation consistently to the same QA lane.
- Preserve recent conversation history in Worker memory.
- Provide a `New conversation` action.
- Provide these answer-language choices:
  - Simplified Chinese
  - Traditional Chinese
  - Korean
  - Japanese
  - English
  - Portuguese
  - Russian
  - Spanish
- Send the selected language with each question.
- The UI language syncs automatically to the selected answer language.
- The active Cerebras or DeepSeek provider must answer in the selected language.
- Public QA must not expose internal retrieval steps, use Claude Code, read `CLAUDE.md`, or search original source files.
- Rate limiting applies on the QA page (10 req/min, 50 req/hour per IP).
- Internal provider errors are logged internally and a generic translated message is shown to users.

### Authentication

- `/login` and `/logout` exist.
- QA remains public.
- Upload and source modification require authentication.
- Roles (only two):
  - `editor`: list, upload, remove, and export
  - `admin`: editor permissions plus user account administration via `/admin/users`
- A hardcoded default admin account is seeded on first database initialization:
  - Username: `admin`
  - Password: `Admin#2026!Secured89` (override via `DEFAULT_ADMIN_PASSWORD` env var)
  - Teams: all teams from `ALLOWED_TEAMS` env var
  - This account is only created when no admin user exists in the database.
- Passwords must be stored as salted slow hashes, never plaintext.
- Sessions must use HttpOnly cookies.
- State-changing browser requests must be protected by CSRF validation.
- The `/teams` and `/dashboard` routes have been removed. Admin manages users at `/admin/users`.

### Upload and ingestion

- Authenticated route: `/upload`
- Browser uploads to ECS first.
- ECS stores the upload and sends a download command to the Worker over WebSocket.
- Worker downloads into staging, safely extracts ZIP files when necessary, and atomically publishes completed sources under:

```text
agent1/<team>/raw/sources/<upload_id>/
```

- Allowed teams currently are:

```text
tian_gong
walker_s2
walker_c1
```

- Never publish a partially downloaded or partially extracted source directory.
- Enforce ZIP traversal, symlink, file-count, per-file-size, and total-extracted-size limits.

### Source manager

- Authenticated route: `/manage`
- List folders and files under `raw/sources/`.
- Only use relative paths from `RAW_SOURCES_DIR`.
- Reject absolute paths and `..` traversal.
- Never permit operations outside `raw/sources/`.
- File/folder removal is a soft delete into `.agent1-trash/`.
- Do not permanently erase sources in the first production version.
- Block deletion when the relevant LLM Wiki source is actively `processing`.
- Serialize file-management operations with one file-operation consumer.
- Record login, upload, and remove operations in the audit log.

### Upload status synchronization

The web status must distinguish:

1. `This upload`
2. `All current LLM Wiki work`

The status page must accurately display:

```text
queued
processing
retrying
completed
failed
deleted
```

For each source, expose when available:

- relative source path
- retry count and maximum retry count
- latest LLM Wiki error
- number of matching active queue entries
- generated wiki files from the completion cache

Do not classify every `pending` item as simply queued. A task with an error and `retryCount > 0` is retrying unless it has exhausted its allowed retries.

When multiple queue entries match the same source, inspect all of them. Do not use only the first match. Use a precedence similar to:

```text
completed cache receipt
-> processing
-> retrying
-> queued
-> permanently failed
-> waiting
```

The global queue view must include work from older uploads, manually added sources, and automatic retries.

### LLM Wiki integration

The live LLM Wiki project path is:

```text
$HOME/Documents/agent_7_14/agent1/agent
```

Required operating mode:

```text
Source Watch: ON
Auto Ingest: ON
```

The Worker must keep this disabled:

```env
LLM_WIKI_RESCAN_AFTER_PUBLISH=false
```

Reason: Source Watch plus Worker-triggered `/sources/rescan` can enqueue duplicate ingestion work.

The Worker may monitor:

```text
.llm-wiki/ingest-queue.json
.llm-wiki/ingest-cache.json
```

LLM Wiki ingestion is serial. Several visible Activity items usually represent one processing item plus pending/retrying/failed items, not several parallel ingestion workers.

The web must surface LLM Wiki errors such as DeepSeek network failures without rewriting or hiding them.

### Public QA retrieval and API generation

The normal Worker configuration is:

```env
CEREBRAS_API_KEY=<Worker-only secret>
CEREBRAS_MODEL=gpt-oss-120b
CEREBRAS_TIMEOUT=240
DEEPSEEK_API_KEY=<Worker-only fallback secret>
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT=240
QA_PROVIDER_COOLDOWN_SECONDS=300
WIKI_QA_MAX_PAGES=5
WIKI_QA_MAX_PAGE_CHARS=24000
CONVERSATION_MAX_TURNS=6
CONVERSATION_MAX_SESSIONS=1000
```

The Worker implementation follows the read-only retrieval pattern demonstrated in `$HOME/Documents/agent_tests`: the router sees `wiki/index.md` plus at most 20 filename/path matches for the selected robot/topic when the index is stale, Python validates returned slugs and reads only those permitted pages, and a second provider call streams the answer. Cerebras is primary; any Cerebras API failure opens a five-minute circuit and retries the complete request through DeepSeek V4 Flash. Agent1 must not modify or depend on runtime files inside `agent_tests`. The Worker injects bounded recent history, answer language, and the selected robot/topic into its own prompts.

Public QA is API-only. It must not launch Claude Code, import a Claude Q&A
runner, read `CLAUDE.md`, or fall back to `raw/sources/`. Terminology rewriting
is an in-memory prompt/output boundary and must not mutate original uploads or
generated Wiki files.

Public QA output requirements:

- Retrieve relevant indexed Wiki pages silently inside the Worker.
- Return only the user-facing answer.
- Do not mention tool permissions.
- Do not show chain of thought.
- Do not explain internal retrieval.
- Do not expose Wiki slugs, local file paths, citations, or source lists.
- Mark insufficient knowledge according to the repository's existing knowledge-gap convention.

## Architecture boundaries

### ECS responsibilities

- Public HTTP pages and APIs
- Login, sessions, roles, and CSRF
- SQLite persistence
- Temporary upload storage
- Upload/status pages
- Worker command dispatch over WebSocket
- Audit logging

### Worker responsibilities

- Maintain the outbound WebSocket connection to ECS
- QA queues and QA lane routing
- Perform bounded Wiki retrieval and call Cerebras with DeepSeek fallback
- Download uploaded files
- Safe ZIP extraction
- Atomic source publication
- File-tree listing and soft deletion
- LLM Wiki queue/cache monitoring
- Send detailed progress updates to ECS

### LLM Wiki responsibilities

- Watch `raw/sources/`
- Queue ingestion
- Retry failed ingestion work
- Generate `wiki/`
- Persist queue/cache state

Do not move the live source files to ECS. Do not expose the Worker or LLM Wiki API publicly.

---

## Required environment configuration

### ECS: `ecs/.env`

Expected keys include:

```env
APP_NAME=Agent1 Knowledge Base
PUBLIC_BASE_URL=http://47.239.12.206:8000
DATA_ROOT=/root/agent_7_14/ecs-data
DATABASE_PATH=/root/agent_7_14/ecs-data/agent_jobs.db
ALLOWED_TEAMS=tian_gong,walker_s2,walker_c1
WORKER_SHARED_SECRET=<same random value as Worker>
WORKER_TIMEOUT=240
FILE_COMMAND_TIMEOUT=60
SESSION_COOKIE_NAME=agent1_session
SESSION_HOURS=8
COOKIE_SECURE=false
COOKIE_SAMESITE=lax
```

Use `COOKIE_SECURE=false` only during plain-HTTP testing. Change it to `true` after HTTPS deployment.

### Worker: `worker/.env`

Resolve paths from the real `$HOME` on the new Worker computer.

```env
SERVER_URL=ws://47.239.12.206:8000/ws/client
WORKER_SHARED_SECRET=<exact same value as ECS>
BASE_DIR=$HOME/Documents/agent_7_14/agent1/agent
STAGING_DIR=$HOME/Documents/agent_7_14/agent1/agent/.agent1-worker/staging
TRASH_DIR=$HOME/Documents/agent_7_14/agent1/agent/.agent1-trash
QA_WORKERS=3
DOWNLOAD_WORKERS=2
FILE_OPERATION_WORKERS=1
FILE_MANAGER_MAX_ENTRIES=10000
CLAUDE_TIMEOUT=240
CEREBRAS_API_KEY=<Worker-only secret>
CEREBRAS_MODEL=gpt-oss-120b
CEREBRAS_TIMEOUT=240
DEEPSEEK_API_KEY=<Worker-only fallback secret>
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT=240
QA_PROVIDER_COOLDOWN_SECONDS=300
WIKI_QA_MAX_PAGES=5
WIKI_QA_MAX_PAGE_CHARS=24000
DOWNLOAD_TIMEOUT=1800
LLM_WIKI_QUEUE_FILE=$HOME/Documents/agent_7_14/agent1/agent/.llm-wiki/ingest-queue.json
LLM_WIKI_CACHE_FILE=$HOME/Documents/agent_7_14/agent1/agent/.llm-wiki/ingest-cache.json
LLM_WIKI_POLL_SECONDS=2
LLM_WIKI_MONITOR_TIMEOUT=7200
LLM_WIKI_RESCAN_AFTER_PUBLISH=false
LLM_WIKI_API_URL=http://127.0.0.1:19828/api/v1
LLM_WIKI_API_TOKEN=
LLM_WIKI_PROJECT_ID=
CLAUDE_ALLOWED_TOOLS=Read,Glob,Grep
CLAUDE_EXTRA_ARGS=--model haiku
CONVERSATION_MAX_TURNS=6
CONVERSATION_MAX_SESSIONS=1000
```

Do not literally save `$HOME` in the file unless the current configuration loader expands environment variables. Prefer writing the fully resolved absolute path.

---

## Secret handling

Generate the shared Worker secret with:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Store the exact same value in ECS and Worker `.env` files.

Never print secrets during normal diagnostics. When verifying configuration, print only whether a value exists and whether the ECS and Worker values match through a safe out-of-band process.

Apply:

```bash
chmod 600 ecs/.env
chmod 600 worker/.env
```

Never commit `.env`, SQLite data, uploaded source files, LLM provider keys, session tokens, or Claude credentials.

---

## Cloud deployment quick-start

When uploading `release.zip` to a new cloud computer:

### ECS server

```bash
# 1. Unzip release into the project root
mkdir -p /root/agent_7_14 && cd /root/agent_7_14
unzip release.zip -d .

# 2. Install locked ECS dependencies with uv
# Install uv first: https://docs.astral.sh/uv/getting-started/installation/
./scripts/uv_sync.sh ecs

# 3. Create ecs/.env (copy from ecs/.env.example and fill in values)
cp ecs/.env.example ecs/.env
# Edit ecs/.env:
#   - Set PUBLIC_BASE_URL to the cloud server's public URL
#   - Set DATA_ROOT and DATABASE_PATH
#   - Set WORKER_SHARED_SECRET (generate with: python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
#   - Optionally set DEFAULT_ADMIN_PASSWORD (default: Admin#2026!Secured89)
chmod 600 ecs/.env

# 4. Start ECS (the database and default admin account are auto-created on startup)
./scripts/run_ecs.sh
# Or with a custom port:
PORT=8000 ./scripts/run_ecs.sh

# 5. Verify
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
# Login at http://<server-ip>:8000/login with admin / Admin#2026!Secured89
```

### Worker computer

```bash
# 1. Unzip release into the project root
mkdir -p $HOME/Documents/agent_7_14 && cd $HOME/Documents/agent_7_14
unzip release.zip -d .

# 2. Install locked Worker dependencies with uv
# Install uv first: https://docs.astral.sh/uv/getting-started/installation/
./scripts/uv_sync.sh worker

# 3. Create worker/.env (copy from worker/.env.example and fill in values)
cp worker/.env.example worker/.env
# Edit worker/.env:
#   - Set SERVER_URL to ws://<ecs-public-ip>:8000/ws/client
#   - Set WORKER_SHARED_SECRET (exact same value as ECS)
#   - Set all paths using the fully resolved $HOME (e.g. /home/username/Documents/...)
#   - Do NOT use literal $HOME — write the absolute path
chmod 600 worker/.env

# 4. Start Worker
./scripts/run_worker.sh

# 5. Verify connection
curl -s http://<ecs-ip>:8000/health | python3 -m json.tool
# Should show worker_online: true
```

### Environment variables that change between machines

The following values in `.env` files must be adjusted for each deployment:

| Variable | ECS | Worker | Notes |
|---|---|---|---|
| `PUBLIC_BASE_URL` | ✅ | — | Set to the ECS public IP/domain |
| `DATA_ROOT` | ✅ | — | Absolute path to ECS data directory |
| `DATABASE_PATH` | ✅ | — | Absolute path to SQLite database |
| `SERVER_URL` | — | ✅ | WebSocket URL pointing to ECS |
| `WORKER_SHARED_SECRET` | ✅ | ✅ | Must be identical on both |
| `BASE_DIR` | — | ✅ | Absolute path to LLM Wiki project |
| `STAGING_DIR` | — | ✅ | Absolute path under BASE_DIR |
| `TRASH_DIR` | — | ✅ | Absolute path under BASE_DIR |
| `LLM_WIKI_QUEUE_FILE` | — | ✅ | Absolute path to ingest-queue.json |
| `LLM_WIKI_CACHE_FILE` | — | ✅ | Absolute path to ingest-cache.json |
| `DEFAULT_ADMIN_PASSWORD` | ✅ | — | Optional; default `Admin#2026!Secured89` |

### Default admin account

On first startup with a fresh database, the ECS automatically creates:

- Username: `admin`
- Password: value of `DEFAULT_ADMIN_PASSWORD` env var, or `Admin#2026!Secured89`
- Role: `admin`
- Teams: all teams from `ALLOWED_TEAMS`

Change the password immediately after first login via `/admin/users`.

---

## Repository hygiene

Before editing, inspect the actual repository. The user's earlier archives may not contain every latest feature even when the intended product requirements above do.

First run:

```bash
find . -maxdepth 4 -type f | sort
rg -n "conversation_id|language|retry_count|global.*wiki|LLM_WIKI_RESCAN_AFTER_PUBLISH|source_tree|delete_source|create_user|CLAUDE_EXTRA_ARGS" .
```

Confirm which features are implemented before changing code.

Do not include these in release ZIP files:

```text
.venv-ecs/
.venv-worker/
__pycache__/
*.pyc
ecs/.env
worker/.env
ecs-data/
agent1/agent/raw/
agent1/agent/wiki/
agent1/agent/.llm-wiki/
agent1/agent/.agent1-trash/
```

A release may include an empty LLM Wiki skeleton and safe `.env.example` files, but never live data or secrets.

---

## Coding rules

- Use Python type hints for new public functions.
- Prefer `pathlib.Path` for filesystem operations.
- Use structured logging; do not use `print` in server/Worker runtime code.
- Do not block the asyncio event loop with network or filesystem-heavy operations.
- Use `asyncio.to_thread` for blocking ZIP/file work when appropriate.
- Use `asyncio.create_subprocess_exec` for Claude.
- Bound all queues.
- Add timeouts for external network calls and subprocesses.
- Preserve cancellation behavior.
- Do not swallow errors. Return useful user-facing status and log technical detail.
- Avoid broad `except Exception` unless the boundary logs the full exception and converts it into an explicit result.
- Keep WebSocket message schemas backward-compatible where practical.
- Include a correlation ID in every request/response pair.
- Never trust paths, team names, filenames, upload IDs, or WebSocket payloads.
- Do not follow symlinks in file-management operations.
- Do not use shell commands constructed from user data.
- Keep database migrations additive and idempotent.
- Preserve existing user accounts and status data during upgrades.

---

## WebSocket protocol expectations

Typical ECS-to-Worker messages:

```json
{"type": "question", "id": "q-...", "conversation_id": "...", "language": "en", "text": "..."}
```

```json
{"type": "download_file", "id": "dl-...", "upload_id": "...", "team": "walker_s2", "filename": "...", "url": "..."}
```

```json
{"type": "list_sources", "id": "files-..."}
```

```json
{"type": "delete_source", "id": "delete-...", "path": "walker_s2/upload-id/file.md"}
```

Typical Worker-to-ECS messages:

```json
{"type": "answer", "id": "q-...", "text": "..."}
```

```json
{"type": "upload_progress", "upload_id": "...", "status": "processing", "source_path": "..."}
```

```json
{"type": "llm_wiki_snapshot", "generated_at": "...", "counts": {}, "tasks": []}
```

```json
{"type": "source_tree_result", "id": "files-...", "status": "ok", "tree": []}
```

```json
{"type": "delete_source_result", "id": "delete-...", "status": "ok", "path": "...", "trash_path": "..."}
```

When extending schemas, ignore unknown fields for compatibility.

---

## Known operational problems

### DeepSeek ingestion failures

LLM Wiki has shown errors such as:

```text
Generation failed: error sending request for url (https://api.deepseek.com/chat/completions)
Analysis failed: error sending request for url ...
```

These are LLM Wiki/provider/network problems, not upload-transfer failures. The website must surface the real error and retry state. Do not mark such a source as completed.

Check on the Worker computer:

```bash
curl -I https://api.deepseek.com
getent hosts api.deepseek.com
env | grep -i proxy
```

Do not automatically assume the API key is wrong; distinguish DNS, proxy, TLS, connection reset, rate limit, authentication, and provider response errors.

### Duplicate ingestion

Do not combine automatic LLM Wiki Source Watch with Worker-triggered automatic rescan. Keep:

```env
LLM_WIKI_RESCAN_AFTER_PUBLISH=false
```

### Stale status

The Worker must send a status update when any of these change, even if the broad state string does not:

- retry count
- error text
- matching task count
- generated files
- completion receipt
- queue timestamp

### Claude latency

The intended fast default is:

```env
CLAUDE_EXTRA_ARGS=--model haiku
```

Do not invent a `Claude Flash` model. Haiku is the intended low-latency choice.

---

## Development workflow for Codex

For every task:

1. Inspect the relevant files and current implementation.
2. State the exact bug or required behavior in code terms.
3. Make the smallest coherent change.
4. Add or update tests.
5. Run compilation and targeted tests.
6. Run a compatibility check against an existing SQLite database.
7. Update documentation when configuration or deployment changes.
8. Produce a release ZIP without secrets or runtime data.
9. Provide an exact upgrade path preserving `.env`, databases, virtual environments, and the live LLM Wiki project.

Do not rewrite the entire project unless explicitly directed.

---

## Local validation commands

From repository root:

```bash
python3 -m compileall -q ecs worker scripts
```

Install the locked test/runtime dependencies with uv:

```bash
./scripts/uv_sync.sh dev
.venv-dev/bin/python -m pytest -q
```

Start ECS:

```bash
./scripts/run_ecs.sh
```

Check:

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

Start Worker in a separate terminal after safe test configuration:

```bash
./scripts/run_worker.sh
```

Do not point development tests at the live ECS or live Worker unless the user explicitly approves.

---

## Required test coverage

### Authentication

- valid login
- invalid login
- inactive user
- session expiration
- logout
- role enforcement
- CSRF failure
- upload protected
- delete protected

### Filesystem security

- absolute path rejected
- `../` traversal rejected
- encoded traversal rejected
- symlink escape rejected
- deletion of root rejected
- processing source deletion rejected
- soft-delete preserves file under trash
- duplicate delete handled safely

### Upload pipeline

- normal file
- Chinese filename
- zero-byte file policy
- large-file limit
- ZIP extraction
- ZIP traversal
- ZIP symlink
- ZIP bomb limits
- partial download cleanup
- atomic publish
- Worker reconnect during upload

### Status monitor

- queued
- processing
- retrying
- permanently failed
- completed and removed from active queue
- generated files from cache
- duplicate queue entries
- error changes without state changes
- global snapshot versus current upload
- malformed/missing queue files
- Worker restart

### QA

- same conversation retains context
- new conversation resets context
- same conversation maps to one QA lane
- separate conversations can run concurrently
- eight languages transmitted and honored
- router call receives the Wiki index, selected robot/topic, bounded history, and at most 20 topic-matched stale-index slugs
- router-selected slugs are validated before Python reads local pages
- answer call includes only selected pages and uses Cerebras or the configured DeepSeek fallback
- answers contain no Wiki slugs, local paths, retrieval citations, or source lists
- one Cerebras failure opens a five-minute circuit with one half-open recovery probe
- no permission-request text in normal answer
- timeout and nonzero exit handling

### Migration

- old database opens
- additive columns/tables created once
- existing users remain
- existing uploads remain
- rerunning initialization is safe

---

## Deployment rules

### Before every ECS deployment

Back up:

```text
ecs/.env
ecs-data/
current code
```

When copying new code, preserve:

```text
ecs/.env
.venv-ecs/
ecs-data/
```

### Before every Worker deployment

Back up:

```text
worker/.env
agent1/agent/CLAUDE.md
live LLM Wiki project or at least raw/, wiki/, and .llm-wiki/
```

When copying new code, preserve:

```text
worker/.env
.venv-worker/
agent1/agent/
```

Only copy the updated `CLAUDE.md` into the live LLM Wiki project when the QA instructions changed.

### Startup order

1. Start ECS.
2. Open LLM Wiki on the correct project.
3. Confirm Source Watch and Auto Ingest are on.
4. Start exactly one Worker.
5. Confirm `/health` reports `worker_online: true`.
6. Run a small upload test.
7. Run a QA language/context test.
8. Run a soft-delete test.

---

## Production hardening still required

The current public-IP port-8000 setup is suitable for controlled testing, not final production.

Before broad production use:

- configure a domain
- add Nginx or another reverse proxy
- enable HTTPS
- set `COOKIE_SECURE=true`
- change Worker URL from `ws://` to `wss://`
- restrict or close public port 8000
- permit only ports 80/443 externally
- add login rate limiting
- add upload rate/size controls at the reverse proxy
- add database backup rotation
- add log rotation
- add service supervision or systemd after tmux-based testing is stable
- define trash retention and restore operations
- define user administration UI or documented CLI workflow

Do not perform this hardening in the same change as functional bug fixes unless explicitly requested.

---

## Definition of done for the current deployment

The deployment is complete only when all are true:

```text
Existing ECS runs the latest code.
Existing ecs/.env and SQLite data are preserved.
A new Worker secret is identical on ECS and Worker.
The old Worker is stopped.
The new Worker is connected.
The Worker has valid Cerebras and DeepSeek keys; live QA retrieves permitted Wiki pages, streams without internal references, and fails over automatically.
LLM Wiki opens the migrated project on the new computer.
Source Watch and Auto Ingest are enabled.
Automatic Worker rescan is disabled.
/health reports worker_online=true.
/login works with an existing or newly created admin account.
/manage reflects raw/sources accurately.
/upload sends files to the new Worker.
The upload page shows current-upload and global LLM Wiki status separately.
Retry count and actual DeepSeek errors are visible.
Soft deletion moves sources to .agent1-trash.
QA supports all eight languages.
Same-browser follow-up questions retain recent context.
The answer does not narrate retrieval or ask the website user for file permission.
```

---

## First action for the next Codex session

Do not immediately modify code.

Run this first:

```bash
pwd
find . -maxdepth 4 -type f | sort
rg -n "conversation_id|language|retry_count|max_retries|llm_wiki_snapshot|source_tree|delete_source|create_user|CLAUDE_EXTRA_ARGS|LLM_WIKI_RESCAN_AFTER_PUBLISH" .
```

Then compare the repository's actual implementation against this AGENTS.md and report:

1. which required features are present,
2. which are missing,
3. which appear partially implemented,
4. which database migrations exist,
5. which deployment files need updating,
6. the smallest safe plan to finish and deploy on the existing ECS plus the new Worker computer.
