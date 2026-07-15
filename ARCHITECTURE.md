# Final architecture

```text
Public browser / WeCom
        │
        ▼
Alibaba ECS — FastAPI
├── public QA and health
├── login/session/role checks
├── authenticated upload and source manager
├── authenticated Claude documentation authoring on `/upload`
├── SQLite users, sessions, uploads and audit log
└── one persistent Worker WebSocket
        │
        ▼
Private Worker
├── 3 QA consumers → Claude subprocesses
├── 2 async download consumers
├── 1 serialized file-operation consumer
│   ├── list raw/sources
│   └── soft-delete into .agent1-trash
├── ZIP extraction via asyncio.to_thread
└── LLM Wiki queue/cache monitoring
        │ filesystem
        ▼
Existing LLM Wiki GUI
├── Source Watch
├── Auto Ingest
├── serial ingestion queue
└── wiki writer
```

## Security boundaries

- Browser users never receive a Worker filesystem path outside `raw/sources`.
- The ECS authenticates users and authorizes roles.
- The Worker independently validates every relative path.
- Only the Worker accesses local knowledge-base files.
- The Worker WebSocket and download endpoint use the shared Worker secret.
- Authoring sessions are persisted under the Worker runtime directory; Claude receives only read-only tools.
- Generated articles remain drafts until an editor/admin explicitly publishes reviewed Markdown into `raw/sources/`.
- A bounded authoring queue runs separate sessions concurrently and serializes commands for the same session.
- File removal is soft deletion and is serialized through one queue.
- Wiki writing remains owned by LLM Wiki.
