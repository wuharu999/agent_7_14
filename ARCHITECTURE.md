# Final architecture

```text
Public browser
        │
        ▼
Alibaba ECS — FastAPI
├── public QA and health
├── login/session/role checks
├── authenticated multi-file upload queue and source manager
├── authenticated DeepSeek documentation authoring on `/upload`
├── SQLite users, sessions, uploads and audit log
└── one persistent Worker WebSocket
        │
        ▼
Private Worker
├── 3 QA consumers → bounded Wiki retrieval → Cerebras / DeepSeek fallback
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
- Authoring sessions are persisted under the Worker runtime directory; DeepSeek receives bounded Wiki excerpts and no tools.
- Generated articles remain drafts until an editor/admin explicitly publishes reviewed Markdown into `raw/sources/`.
- A bounded authoring queue runs separate sessions concurrently and serializes commands for the same session.
- Public QA uses validated index retrieval plus bounded robot/topic filename
  discovery for stale indexes and sends only selected Wiki pages to the active
  provider. Cerebras is primary only when the outbound-country gate is
  permitted; CN/TW/HK/SG or an unverifiable region uses DeepSeek directly.
  DeepSeek V4 Flash is also the circuit-breaker fallback. Public QA never launches local coding agents, reads local agent instruction files, or searches
  original files under `raw/sources/`. Authoring treats editor messages and
  Wiki excerpts as untrusted data and uses stateless API calls with thinking disabled.
- High-confidence prompt attacks are refused locally. Only ambiguous suspicious
  messages enter the bounded zero-tool classifier; classifier failure closes
  that request without affecting ordinary traffic.
- Text sources are scanned before publication. The ECS stores only relative
  filenames and warning categories, never excerpts; findings do not block
  publication or change LLM Wiki status.
- The upload page accepts up to 20 files for one team and schedules no more
  than two browser transfers at once. Each transfer uses the existing single-file
  endpoint, upload record, Worker job, staging directory, and status lifecycle.
- ECS and Worker validation share one supported-source suffix definition. ZIP
  extraction keeps its traversal, symlink, count, and size protections.
- File removal is soft deletion and is serialized through one queue.
- Wiki writing remains owned by LLM Wiki.
