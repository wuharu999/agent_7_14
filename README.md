# Agent1 7.14 Final

Agent1 is a public ECS gateway plus a private Worker for a robot-documentation knowledge base.

## Included

- Public question page and WeCom callback.
- Authenticated upload page.
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

The Worker invokes Claude with the read-only tools `Read`, `Glob`, and `Grep` pre-approved. The bundled service prompt and `CLAUDE.md` explicitly prohibit asking the website user for file-read permission.
