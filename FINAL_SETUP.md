# Deployment and upgrade guide

This is the single operational guide for the two-machine deployment. The
public ECS gateway owns HTTP, authentication, uploads, and SQLite data. The
private Worker owns the LLM Wiki project, source files, and DeepSeek access.

## Before starting

- Keep the existing ECS `.env`, `ecs-data/`, and virtual environment.
- Keep the Worker `.env`, `agent1/agent/raw/`, `agent1/agent/wiki/`, and
  `agent1/agent/.llm-wiki/`.
- Run exactly one Worker. The ECS accepts one active Worker connection.
- Do not put Worker or DeepSeek credentials in the ECS configuration or a
  release archive.
- Plain HTTP on port 8000 is for controlled testing only. Restrict access and
  keep `COOKIE_SECURE=false` until HTTPS is configured; set it to `true` when
  HTTPS is enabled.

## First ECS setup

On the ECS at `/root/agent_7_14`:

```bash
./scripts/bootstrap_ecs.sh
cp ecs/.env.example ecs/.env  # only if bootstrap did not create it
chmod 600 ecs/.env
```

Set `PUBLIC_BASE_URL`, `DATA_ROOT`, `DATABASE_PATH`, `ALLOWED_TEAMS`, and a
strong `WORKER_SHARED_SECRET` in `ecs/.env`. Generate the secret with:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

The same value must be configured on the Worker. On first startup, ECS creates
the configured default administrator when no administrator exists. Change that
password immediately at `/admin/users`.

Start the gateway and verify it:

```bash
tmux new -s agent-7-14-ecs
./scripts/run_ecs.sh
curl -fsS http://127.0.0.1:8000/health
```

## First Worker setup

On the Worker at `$HOME/Documents/agent_7_14`:

```bash
./scripts/bootstrap_worker.sh
cp worker/.env.example worker/.env  # only if bootstrap did not create it
chmod 600 worker/.env
```

Set `SERVER_URL` to the ECS WebSocket URL and set the identical
`WORKER_SHARED_SECRET`. Replace every `/home/<username>/...` path in the
example with the Worker computer's actual absolute path; dotenv does not
expand a literal `$HOME`. Keep `DEEPSEEK_API_KEY` only in `worker/.env`, and
leave `LLM_WIKI_RESCAN_AFTER_PUBLISH=false`.

Open LLM Wiki on the configured `BASE_DIR` and enable **Source Watch** and
**Auto Ingest**. Then validate and start the Worker:

```bash
./scripts/check_worker_machine.sh
tmux new -s agent-7-14-worker
./scripts/run_worker.sh
```

Confirm the ECS health response reports `worker_online: true`.

## Upgrade an existing deployment

Run the guarded upgrade script on each respective machine. Each script backs
up live configuration/data, fast-forwards the requested branch, synchronizes
the locked dependencies, compiles the relevant code, and restarts its tmux
session.

```bash
# ECS
./scripts/pull_and_restart_ecs.sh

# Worker
./scripts/pull_and_restart_worker.sh
```

The scripts intentionally preserve the ECS database and the Worker's live LLM
Wiki project. Review the Git worktree and choose the deployment branch before
running either script.

## Acceptance checks

After ECS, LLM Wiki, and exactly one Worker are running:

```bash
./scripts/check_ecs.sh http://127.0.0.1:8000
```

1. Sign in at `/login` and verify `/manage` shows `raw/sources/`.
2. Upload a small supported file and confirm its detail page separates the
   current upload from the global LLM Wiki status.
3. Ask an indexed question in a non-default language, then send a follow-up;
   verify the answer streams and keeps the selected language/context.
4. Start a new conversation and verify the following question has no earlier
   conversation context.
5. Soft-delete a non-processing source and verify it moved to
   `.agent1-trash/` on the Worker.

Local regression validation before packaging or deployment is:

```bash
python3 -m compileall -q ecs worker shared scripts
./scripts/uv_sync.sh dev
.venv-dev/bin/python -m pytest -q
```

Passing local tests does not prove live DeepSeek, LLM Wiki Source Watch, or
browser behavior; perform the acceptance checks on the deployment machines.
