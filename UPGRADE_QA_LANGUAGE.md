# QA conversation and language upgrade

This version fixes two QA behaviors:

1. The same browser conversation is always routed to the same QA worker lane and receives a bounded recent conversation history.
2. Claude is started with the read-only tools `Read`, `Glob`, and `Grep` pre-approved, and is instructed to read the wiki silently rather than asking the web user for permission.

It also adds these answer languages to the public question page:

- Simplified Chinese
- Traditional Chinese
- Korean
- Japanese
- English
- Portuguese
- Russian
- Spanish

## Upgrade an existing installation

Back up the live environment files and knowledge-base directory first. Do not replace `.env` with the example files.

### ECS

Copy the new ECS code and templates, preserve `ecs/.env`, then restart Uvicorn.

```bash
cd /root/agent_7_14
tmux attach -t agent-7-14-ecs
# Ctrl+C
./scripts/run_ecs.sh
```

### Worker

Copy the new Worker code, preserve `worker/.env`, and copy the updated rules into the actual LLM Wiki project:

```bash
cp agent1/agent/CLAUDE.md \
  /home/eason/Documents/agent_7_14/agent1/agent/CLAUDE.md
```

The following settings are optional because the code has defaults:

```env
CLAUDE_ALLOWED_TOOLS=Read,Glob,Grep
CLAUDE_EXTRA_ARGS=
CONVERSATION_MAX_TURNS=6
CONVERSATION_MAX_SESSIONS=1000
```

Restart the Worker:

```bash
cd /home/eason/Documents/agent_7_14
tmux attach -t agent-7-14-worker
# Ctrl+C
./scripts/run_worker.sh
```

## Test

1. Open the public question page.
2. Select English and ask a knowledge-base question; verify the answer is English.
3. Ask a follow-up such as “What about the higher-end model?”; verify it uses the previous turn as context.
4. Check Worker logs: both requests from that browser conversation should show the same QA worker number.
5. Click **New conversation** and ask a context-dependent follow-up; it should no longer use the earlier turns.
6. Ask a question requiring wiki lookup; the response must not say it needs permission to read `wiki/index.md`.
