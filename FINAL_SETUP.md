# Final setup and upgrade guide

## 1. Security warning before deployment

The login system protects application permissions, but passwords sent over plain `http://` can still be observed in transit. For temporary testing on port 8000:

- restrict the Alibaba security group to trusted IP addresses;
- keep `COOKIE_SECURE=false`;
- do not treat the plain-HTTP deployment as production-ready.

For production, place Uvicorn behind HTTPS and set:

```env
COOKIE_SECURE=true
```

## 2. ECS upgrade

Back up the current configuration and database before replacing code:

```bash
cd /root/agent_7_14
cp ecs/.env /root/agent_7_14-ecs.env.backup
cp -a ecs-data /root/agent_7_14-ecs-data.backup
```

Extract the new package. Restore your `.env` because release ZIPs intentionally do not contain secrets:

```bash
cp /root/agent_7_14-ecs.env.backup /root/agent_7_14/ecs/.env
```

Add these settings if they are not already present:

```env
FILE_COMMAND_TIMEOUT=60
AUTHORING_COMMAND_TIMEOUT=270
SESSION_COOKIE_NAME=agent1_session
SESSION_HOURS=8
COOKIE_SECURE=false
COOKIE_SAMESITE=lax
```

Install/update dependencies:

```bash
cd /root/agent_7_14
chmod +x scripts/*.sh scripts/create_user.py
# Install uv first if `uv --version` is not available:
# https://docs.astral.sh/uv/getting-started/installation/
./scripts/bootstrap_ecs.sh
```

The bootstrap uses the committed `uv.lock` and keeps the ECS environment at
`.venv-ecs`. It does not use `pip install` or a requirements file.

The database migration runs automatically at ECS startup and adds the new authentication/audit tables without deleting old upload records. On a fresh database, startup creates the `admin` account using `DEFAULT_ADMIN_PASSWORD` or the default `Admin#2026!Secured89`. Change that password immediately at `/admin/users`.

Create more accounts when needed:

```bash
python3 scripts/create_user.py --username uploader --role editor
```

The command asks for the password without displaying it. Passwords must be at least 10 characters and are stored using salted `scrypt`, not plaintext. Running the command again for an existing username updates its password and role.

Start ECS:

```bash
tmux new -s agent-7-14-ecs
cd /root/agent_7_14
./scripts/run_ecs.sh
```

Detach with `Ctrl+B`, then `D`.

Verify:

```bash
curl http://127.0.0.1:8000/health
```

## 3. Worker upgrade

From the development checkout, the repeatable remote deployment is:

```bash
WORKER_SSH_TARGET=user@worker-host ./scripts/deploy_worker.sh
```

The script builds `release.zip`, uploads it, backs up `worker/.env` and the
live project metadata, stops the existing Worker tmux session, replaces code
while preserving virtualenvs and `raw/`, `wiki/`, `.llm-wiki/`, trash, and
runtime state, synchronizes the locked Worker dependencies with `uv`, then
starts the Worker again. Set `WORKER_REMOTE_ROOT` when the
remote project is not `$HOME/Documents/agent_7_14`; set `START_WORKER=false` to
deploy without starting it.

Back up the existing Worker environment before replacing code:

```bash
cd ~/Documents/agent_7_14
cp worker/.env ~/agent_7_14-worker.env.backup
```

Restore it after extracting the new release:

```bash
cp ~/agent_7_14-worker.env.backup ~/Documents/agent_7_14/worker/.env
```

Add or verify:

```env
BASE_DIR=/home/eason/Documents/agent_7_14/agent1/agent
STAGING_DIR=/home/eason/Documents/agent_7_14/agent1/agent/.agent1-worker/staging
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
AUTHORING_MAX_TURNS=100
AUTHORING_MAX_CONTEXT_BYTES=768000
PROMPT_GUARD_ENABLED=true
PROMPT_GUARD_TIMEOUT=20
PROMPT_GUARD_CONCURRENCY=2
PROMPT_SCAN_MAX_FILE_BYTES=2097152
PROMPT_SCAN_MAX_TOTAL_BYTES=10485760
PROMPT_SCAN_MAX_WARNINGS=1000

CEREBRAS_API_KEY=<worker-only-key>
CEREBRAS_MODEL=gpt-oss-120b
CEREBRAS_TIMEOUT=240
DEEPSEEK_API_KEY=<worker-only-fallback-key>
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT=240
QA_PROVIDER_COOLDOWN_SECONDS=300

LLM_WIKI_QUEUE_FILE=/home/eason/Documents/agent_7_14/agent1/agent/.llm-wiki/ingest-queue.json
LLM_WIKI_CACHE_FILE=/home/eason/Documents/agent_7_14/agent1/agent/.llm-wiki/ingest-cache.json
LLM_WIKI_RESCAN_AFTER_PUBLISH=false
```

The ECS and Worker `WORKER_SHARED_SECRET` values must still match exactly.

Install/update dependencies:

```bash
cd ~/Documents/agent_7_14
chmod +x scripts/*.sh
# Install uv first if `uv --version` is not available:
# https://docs.astral.sh/uv/getting-started/installation/
./scripts/bootstrap_worker.sh
```

The existing Python interpreter in `.venv-worker` is retained during upgrades,
including Python 3.10 installations. `uv` synchronizes that environment from
`uv.lock` without downloading or switching Python.

Open LLM Wiki and select exactly:

```text
/home/eason/Documents/agent_7_14/agent1/agent
```

Enable:

```text
Source Watch: ON
Auto Ingest: ON
```

The LLM Wiki local API may remain enabled, but the Worker does not call `/sources/rescan` and does not need an API token for monitoring.

Start the Worker:

```bash
tmux new -s agent-7-14-worker
cd ~/Documents/agent_7_14
./scripts/run_worker.sh
```

Detach with `Ctrl+B`, then `D`.

## 4. Browser test

From any computer allowed by the ECS security group:

```text
http://47.239.12.206:8000/login
```

Sign in with the seeded `admin` account or an account created by `create_user.py`.

Test in this order:

1. Open `/manage`; confirm the current `raw/sources` directory tree appears.
2. Open `/upload`; select two small supported files for one team and confirm
   both appear in the inline queue with separate detail links.
3. Confirm both detail pages reach the expected LLM Wiki completion or show
   the original processing error.
4. Return to `/manage`; confirm the new team/upload folder and file appear.
5. Remove the file. Confirm it disappears from `raw/sources` and appears under `.agent1-trash` on the Worker.

Worker verification:

```bash
find /home/eason/Documents/agent_7_14/agent1/agent/raw/sources -type f -print
find /home/eason/Documents/agent_7_14/agent1/agent/.agent1-trash -type f -print
```

## 5. Permissions

| Route/action | Public | Viewer | Editor | Admin |
|---|---:|---:|---:|---:|
| Ask questions | Yes | Yes | Yes | Yes |
| Health | Yes | Yes | Yes | Yes |
| List sources | No | Yes | Yes | Yes |
| View upload status | No | Yes | Yes | Yes |
| Upload | No | No | Yes | Yes |
| Remove file/folder | No | No | Yes | Yes |

## 6. Removal behavior

The browser sends a relative path such as:

```text
tian_gong/7f3c.../manual.pdf
```

The Worker validates it against `BASE_DIR/raw/sources`, blocks path traversal and active ingestion, then moves it to:

```text
BASE_DIR/.agent1-trash/<timestamp>/tian_gong/7f3c.../manual.pdf
```

LLM Wiki Source Watch observes that the source disappeared from `raw/sources` and applies its source-removal lifecycle.

## 7. Troubleshooting

### `/manage` says Worker offline

```bash
curl http://47.239.12.206:8000/health
```

Confirm `worker_online` is true and inspect the Worker tmux session.

### Removal returns 409

The source or a file inside the selected folder is currently `processing` in LLM Wiki. Wait until ingestion finishes, refresh, and remove again.

### Login loops back to `/login`

Check the ECS clock and `SESSION_HOURS`. If using plain HTTP, `COOKIE_SECURE` must be false. After HTTPS is installed, change it to true.

### The same source ingests twice

Confirm:

```env
LLM_WIKI_RESCAN_AFTER_PUBLISH=false
```

and do not manually call `/sources/rescan` while Source Watch + Auto Ingest are active.

### Source manager exceeds the entry limit

Increase carefully:

```env
FILE_MANAGER_MAX_ENTRIES=20000
```

then restart the Worker.

## QA conversation/language upgrade

No new Python package is required. The new Worker settings are optional because defaults are built in:

```env
CEREBRAS_MODEL=gpt-oss-120b
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_TIMEOUT=240
DEEPSEEK_STRUCTURED_RETRIES=1
DEEPSEEK_TRANSPORT_RETRIES=1
CONVERSATION_MAX_TURNS=6
CONVERSATION_MAX_SESSIONS=1000
```

Public QA uses Cerebras with DeepSeek failover. Scenario clarification,
feasibility analysis, authoring, catalog organization, prompt classification,
and contradiction review call DeepSeek directly with thinking disabled and no
tools. Existing obsolete command-line-agent variables are ignored and may be
removed from deployed environment files.

After copying this version over the existing code, restart both ECS and Worker so the new `/ask` protocol is loaded. Existing browsers automatically receive a conversation ID on their next question. Use **New conversation** on the question page to intentionally clear context.

The same browser conversation is routed to the same QA worker lane. Context continuity is supplied by the Worker's bounded recent-turn history. That history is in memory and resets when the Worker process restarts.
