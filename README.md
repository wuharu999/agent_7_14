# Agent1 7.14 Final

Agent1 is a public ECS gateway plus a private Worker for a robot-documentation knowledge base.

## Included

- Public question page and WeCom callback.
- Authenticated multi-file upload page with per-file pipeline status.
- Authenticated source-tree manager.
- Viewer, editor and admin roles.
- Soft deletion into `.agent1-trash/` rather than permanent erasure.
- CSRF-protected file-changing requests.
- SQLite users, sessions, uploads, source status and audit logs.
- Three concurrent Claude QA subprocess slots by default.
- Two concurrent downloads by default.
- Safe ZIP extraction.
- Existing LLM Wiki GUI used for Source Watch, Auto Ingest and wiki writing.
- Browser-visible ingestion status through LLM Wiki queue/cache monitoring.
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
- `/wecom/callback` — WeCom callback

## Roles

- `viewer`: inspect source folders and files
- `editor`: inspect, upload and remove sources
- `admin`: same file permissions as editor; intended for system administrators

## Upload batches

Editors and admins can select up to 20 supported files for one team on
`/upload`. The browser sends at most two files concurrently, and each file is
still an independent upload with its own ID, atomic source folder, security
scan, error handling, and LLM Wiki status link. A failed file does not stop the
rest of the selection.

Supported documents are PDF, DOCX, PPTX, and XLSX. Supported text/data files
are Markdown, TXT, CSV, JSON, HTML, XML, and YAML. ZIP archives may contain
supported sources and should be used when related files need to remain one
source bundle.

Users are created from the ECS command line:

```bash
source .venv-ecs/bin/activate
python3 scripts/create_user.py --username admin --role admin
```

## Important safety behavior

Removal is a move, not a permanent delete:

```text
raw/sources/team/upload/file.pdf
→ .agent1-trash/<timestamp>/team/upload/file.pdf
```

The Worker rejects absolute paths, `..` traversal, symlinks, removal of the `raw/sources` root, and removal of a source while LLM Wiki marks it as `processing`.

## LLM Wiki

Keep LLM Wiki open on the exact project configured as `BASE_DIR`, with Source Watch and Auto Ingest enabled.

`LLM_WIKI_RESCAN_AFTER_PUBLISH=false` is the default. This avoids triggering ingestion twice through both Source Watch and `/sources/rescan`.

The local LLM Wiki API token is optional for this version. Queue/cache monitoring and Auto Ingest do not require it.

## Deployment

Read [FINAL_SETUP.md](FINAL_SETUP.md) for full first-run and upgrade instructions.

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
