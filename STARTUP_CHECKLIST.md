# Startup checklist

## ECS

```bash
cd /root/agent_7_14
./scripts/bootstrap_ecs.sh
nvim ecs/.env
source .venv-ecs/bin/activate
python3 scripts/create_user.py --username admin --role admin
tmux new -s agent-7-14-ecs
./scripts/run_ecs.sh
```

## LLM Wiki computer

1. Open LLM Wiki.
2. Select `/home/eason/Documents/agent_7_14/agent1/agent`.
3. Enable Source Watch and Auto Ingest.
4. Keep the application open/minimized.

## Worker

```bash
cd ~/Documents/agent_7_14
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
