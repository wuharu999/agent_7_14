# Worker setup

See [FINAL_SETUP.md](FINAL_SETUP.md), sections **Worker upgrade** and **Removal behavior**.

The key new settings are:

```env
TRASH_DIR=/home/eason/Documents/agent_7_14/agent1/agent/.agent1-trash
FILE_OPERATION_WORKERS=1
FILE_MANAGER_MAX_ENTRIES=10000
LLM_WIKI_RESCAN_AFTER_PUBLISH=false
```

Keep LLM Wiki open with Source Watch and Auto Ingest enabled.
