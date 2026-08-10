# Startup checklist

## ECS

```bash
cd /root/agent_7_14
uv --version
./scripts/bootstrap_ecs.sh
nvim ecs/.env
tmux new -s agent-7-14-ecs
./scripts/run_ecs.sh
```

On a fresh database, sign in as `admin` with `DEFAULT_ADMIN_PASSWORD` (or the
documented default) and change the password immediately at `/admin/users`.

## LLM Wiki computer

1. Open LLM Wiki.
2. Select `/home/eason/Documents/agent_7_14/agent1/agent`.
3. Enable Source Watch and Auto Ingest.
4. Keep the application open/minimized.

## Worker

```bash
cd ~/Documents/agent_7_14
uv --version
./scripts/bootstrap_worker.sh
nvim worker/.env
tmux new -s agent-7-14-worker
./scripts/run_worker.sh
```

## Browser

```text
/login  → sign in
/manage → inspect/remove sources
/upload → upload sources
/       → public questions
```
