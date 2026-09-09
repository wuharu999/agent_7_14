# AGENTS.md

## 1. System Architecture & Topology

### 1.1 Overview & Purpose

This repository implements a two-machine knowledge-base upload and question-answering (QA) system designed for enterprise robotics documentation and technical customer support.

The system partitions responsibilities across two distinct physical/virtual hosts:
1. **ECS Gateway**: A public-facing web server running FastAPI on Alibaba Cloud. It handles client web traffic, Server-Sent Events (SSE) streaming, Enterprise WeChat (WeCom) integration, user authentication, session security, database persistence, upload staging, and audit logging.
2. **Worker Computer**: A private machine running `WorkerManager` that maintains an authenticated outbound persistent WebSocket connection to the ECS gateway. It hosts the live LLM Wiki project, manages the local filesystem, executes bounded LangGraph-based Wiki retrieval, runs prompt security evaluations, and orchestrates streaming inference using DeepSeek APIs (`deepseek-v4-flash` and `deepseek-v4-pro`).

The production topology is:

```text
Browser / WeCom Client
    -> ECS Gateway (FastAPI, SQLite WAL, Auth, Staging)
    -> One authenticated persistent WebSocket (/ws/client)
    -> Worker Computer (WorkerManager, Worker Queues)
    -> Local LLM Wiki Project (agent1/agent)
    -> LangGraph QA Pipeline (Plan -> FTS5 Search -> Evidence -> Scope -> Stream)
    -> DeepSeek API (deepseek-v4-flash / deepseek-v4-pro)
```

The ECS gateway never reads live source markdown or executes local coding agents. The Worker never exposes HTTP ports publicly and maintains an outbound connection to ECS.

---

### 1.2 Topology Architecture Diagram

```text
+-------------------------------------------------------------------------------+
|                             PUBLIC / CLIENT ZONE                              |
|   Web Browsers (SSE /ask)        Enterprise WeChat (WeCom /wecom/callback)    |
+-------------------------------------------------------------------------------+
                                     |
                                     | HTTPS / HTTP
                                     v
+-------------------------------------------------------------------------------+
|                      ECS GATEWAY SERVER (120.77.250.227)                      |
|                                                                               |
|  - FastAPI Web Application (ecs/app/)                                         |
|  - Routes:                                                                    |
|      /                    Public QA web interface                             |
|      /ask                 SSE streaming QA endpoint (metadata, chunk, done)   |
|      /login, /logout      Session authentication (PBKDF2, HttpOnly, CSRF)     |
|      /upload              Upload staging & quota validation                   |
|      /manage              Source tree, robots, contradiction alerts, reports  |
|      /admin/users         User administration (editor / admin roles)          |
|      /wecom/callback      WeCom bot signature verification & message dispatch |
|      /health              Gateway health & worker online status               |
|  - Persistence: SQLite with WAL mode (ecs-data/agent_jobs.db)                 |
|  - WebSocket Endpoint: /ws/client (Protected by X-Worker-Secret header)       |
|  - 120-second reconnection grace period preserving in-flight jobs             |
+-------------------------------------------------------------------------------+
                                     ^
                                     | Persistent Outbound WebSocket (/ws/client)
                                     | Authenticated via X-Worker-Secret
                                     v
+-------------------------------------------------------------------------------+
|                        WORKER COMPUTER ($HOME/Documents)                     |
|                                                                               |
|  - Worker Orchestrator: WorkerManager (worker/manager.py)                     |
|  - Worker Pools:                                                              |
|      QA Workers (QA_WORKERS=3, Blake2s session routing)                       |
|      Download Workers (DOWNLOAD_WORKERS=2, chunked transfer)                  |
|      File Operation Worker (FILE_OPERATION_WORKERS=1, serialized)             |
|  - LangGraph Multi-Node QA Pipeline (worker/langgraph_qa/):                   |
|      Plan -> Search (FTS5) -> Load Evidence -> Expand -> Reason -> Scope -> Stream
|  - Model Inference: DeepSeek V4 Flash (QA) & DeepSeek V4 Pro (Merge/Reasoning)|
|  - Prompt Guard: Pattern matching + DeepSeek attack classification            |
|  - Safe ZIP Extraction & Atomic Publishing (raw/sources/<team>/<upload_id>)   |
|  - Filesystem Operations: Soft delete to .agent1-trash/                       |
|  - LLM Wiki Monitor: Polls ingest-queue.json & ingest-cache.json              |
|  - Local LLM Wiki Service: Source Watch: ON, Auto Ingest: ON (agent1/agent)  |
+-------------------------------------------------------------------------------+
```

---

### 1.3 Machine Responsibilities

#### ECS Gateway Responsibilities
- Serve public web pages (`/`) and administrative interfaces (`/upload`, `/manage`, `/status`, `/admin/users`).
- Handle user authentication, PBKDF2 salted slow password hashing, session tokens, and CSRF token validation.
- Enforce role-based access control (`editor` vs `admin`).
- Manage SQLite database persistence (`agent_jobs.db`) with Write-Ahead Logging (WAL) and additive idempotent migrations.
- Receive uploaded files into staging (`DATA_ROOT/uploads/<upload_id>`) and validate size/extension quotas.
- Host the authenticated WebSocket server endpoint (`/ws/client`) with strict single-active-connection semantics.
- Stream Server-Sent Events (SSE) from the Worker to the browser client via `/ask`.
- Receive WeCom webhook events, verify signatures, decrypt XML payloads, dispatch questions to Worker, and send replies.
- Provide export endpoints for Wiki archives and 14-day QA question Markdown audit reports.

#### Worker Computer Responsibilities
- Establish and maintain the outbound WebSocket connection to ECS with exponential backoff and automatic reconnects.
- Hash browser conversation IDs (via Blake2s) to route turns consistently to dedicated QA lanes.
- Guard incoming user queries against jailbreaks, system prompt extractions, and malicious injection attacks.
- Execute bounded LangGraph QA retrieval over the local LLM Wiki using FTS5 BM25 search and strict robot topic scoping.
- Stream generated answers token-by-token, extracting and curating referenced Wiki images while suppressing decorative or unsafe media.
- Download staged files from ECS, verify ZIP safety limits, scan uploaded text files for injection markers, and publish atomically.
- Perform safe file management (source tree listing, soft-deletion to `.agent1-trash/`, robot directory creation).
- Monitor LLM Wiki `.llm-wiki/ingest-queue.json` and `.llm-wiki/ingest-cache.json` and emit real-time progress snapshots to ECS.
- Run automated contradiction reviews over generated Wiki entries and report detected conflicts.

#### LLM Wiki Responsibilities
- Monitor `raw/sources/` via filesystem watcher (`Source Watch: ON`).
- Automatically enqueue and ingest newly published sources (`Auto Ingest: ON`).
- Parse source documents, extract concepts, and generate structured Markdown pages in `wiki/`.
- Maintain queue state in `.llm-wiki/ingest-queue.json` and completed cache receipts in `.llm-wiki/ingest-cache.json`.
- Retry transient extraction and LLM ingestion failures up to maximum retry limits.

---

### 1.4 Production Deployment Targets & Endpoints

#### ECS Server
- **Public IP**: `120.77.250.227`
- **Project Root**: `/root/agent_7_14`
- **HTTP Port**: `8000` (Direct test port; behind reverse proxy in production)
- **Environment File**: `/root/agent_7_14/ecs/.env`
- **Data Root**: `/root/agent_7_14/ecs-data`
- **SQLite Database**: `/root/agent_7_14/ecs-data/agent_jobs.db`
- **Expected Tmux Session**: `agent-7-14-ecs`

#### Worker Computer
- **Project Root**: `$HOME/Documents/agent_7_14` (Resolve username dynamically from `$HOME`)
- **Environment File**: `$HOME/Documents/agent_7_14/worker/.env`
- **LLM Wiki Project Root (`BASE_DIR`)**: `$HOME/Documents/agent_7_14/agent1/agent`
- **Staging Directory**: `$HOME/Documents/agent_7_14/agent1/agent/.agent1-worker/staging`
- **Trash Directory**: `$HOME/Documents/agent_7_14/agent1/agent/.agent1-trash`
- **Expected Tmux Session**: `agent-7-14-worker`

*Note*: Only one active Worker connection is accepted by ECS at any time. Any prior or secondary Worker instances must remain stopped.

---

## 2. Core Functional Specifications

### 2.1 Public QA & Streaming Pipeline

The public QA interface is accessible at `/`. It allows anonymous or authenticated users to query the knowledge base with real-time token streaming.

#### Browser Client Behavior
- Maintains a persistent `conversation_id` in browser `localStorage`.
- Provides a "New Conversation" action that generates a new UUID v4 conversation ID, clearing recent client history.
- Sends the selected robot/team topic and one of 8 supported answer languages with each question.
- Syncs the web UI language automatically with the selected answer language.

#### Supported Languages
The system natively supports 8 answer languages defined in `ecs/app/languages.py`:
1. `zh-CN` (Simplified Chinese - Default)
2. `zh-TW` (Traditional Chinese)
3. `ko` (Korean)
4. `ja` (Japanese)
5. `en` (English)
6. `pt` (Portuguese)
7. `ru` (Russian)
8. `es` (Spanish)

#### Server-Sent Events (SSE) Streaming (`/ask`)
The `/ask` endpoint streams results using standard Server-Sent Events (`text/event-stream`):
- `metadata`: Emitted at stream start containing `conversation_id`, `language`, and `team`.
- `chunk`: Emitted for each incremental text token, thinking/reasoning token, text replacement, or curated image.
- `done`: Emitted upon clean completion of the answer.
- `error`: Emitted with a localized user-friendly error message if inference or retrieval fails.

#### Rate Limiting
The public QA endpoint enforces in-memory IP rate limiting:
- Maximum 10 requests per minute per IP.
- Maximum 50 requests per hour per IP.
- Payloads exceeding 20,000 characters or specifying invalid languages are rejected with HTTP 400.

#### LangGraph Multi-Node QA Pipeline
Worker executes queries through a compiled LangGraph pipeline (`worker/langgraph_qa/`):
1. **Plan Node (`plan`)**: Analyzes the question, conversation history, and robot scope; generates focused retrieval queries and keyword filters.
2. **Search Node (`search`)**: Executes high-speed FTS5 BM25 full-text queries and slug matching over indexed Wiki pages.
3. **Load Evidence Node (`load_evidence`)**: Safely reads candidate Wiki markdown documents, bounded by `QA_REASONING_MAX_CANDIDATES` (15) and `QA_REASONING_MAX_PAGES` (6).
4. **Expand Related Node (`expand_related`)**: Follows internal Wiki hyperlinks to retrieve closely coupled sub-pages (e.g., specific error codes or sub-assemblies).
5. **Reason Node (`reason`)**: Synthesizes evidence against the user query, resolving ambiguities or conflicting references across sections.
6. **Scope Stop Node (`scope_stop`)**: When `QA_STRICT_ROBOT_SCOPE=true`, strictly filters out evidence not belonging to the chosen robot topic.
7. **Final Answer Node (`final_answer`)**: Streams the final answer in the requested language using DeepSeek V4 Flash.

#### DeepSeek Models
- **Inference / QA Streaming**: `DEEPSEEK_MODEL=deepseek-v4-flash` via `https://api.deepseek.com`.
- **Merge / Complex Reasoning**: `DEEPSEEK_MERGE_MODEL=deepseek-v4-pro`.
- **Section Output Cap**: `DEEPSEEK_SECTION_MAX_TOKENS=8192` (prevents model truncation errors).

#### Wiki Image Curation (`worker/qa_images.py`)
- Wiki images are curated strictly at the QA presentation boundary; generated Wiki markdown files are never mutated.
- An image is attached only if:
  1. It is explicitly referenced via Markdown, Obsidian, or HTML tags within permitted Wiki pages.
  2. It has descriptive alt text or meaningful surrounding section context.
  3. It is semantically relevant to the question and confirmed in the final answer.
  4. The file exists, is within the allowed MIME types (`.png`, `.jpg`, `.jpeg`, `.webp`), and has valid dimensions (> 100x100px).
- Decorative icons, generic UI buttons, badges, duplicates, and unreferenced PDF artifacts (e.g. `img-N.png`) are suppressed.

#### AI Notice & Knowledge Gap Conventions
- Every customer-facing response is suffixed with an AI disclaimer notice (`with_ai_notice`) tailored to the selected language.
- If retrieval yields insufficient evidence, the pipeline emits `GAP_MARKER` (`[[INSUFFICIENT_KNOWLEDGE]]`), logs the query to `unanswered_questions.log`, and outputs a localized polite notice acknowledging the knowledge boundary.
- Public QA never outputs internal retrieval reasoning, file paths, Wiki slugs, or tool permission requests.

---

### 2.2 Enterprise WeChat (WeCom) Integration

Enterprise WeChat bot integration is handled in `ecs/app/routes/wecom.py`:
- **Verification Endpoint**: `GET /wecom/callback` verifies URL authenticity using `WXBizMsgCrypt` signature generation with `WXWORK_TOKEN`, `WXWORK_AESKEY`, and `WXWORK_CORPID`. Decrypts and echoes `echostr`.
- **Message Receiver**: `POST /wecom/callback` decrypts incoming XML payloads, verifies signatures, and de-duplicates message IDs (`MsgId`) using an in-memory 10-minute cache window.
- **Asynchronous Execution**: When a text message is received, ECS enqueues a background task that calls `gateway.ask(question, conversation_id=f"wecom:{from_user}", language="zh-CN")` and posts the reply back to the user via WeCom's message send API (`WX_SEND_MSG_URL`).

---

### 2.3 Authentication, Roles & Permissions

Authentication is restricted to knowledge base administrators and content editors:
- **Routes**: `/login`, `/logout`, `/admin/users`.
- **Session Security**:
  - Authenticated sessions use cryptographically secure 32-byte tokens hashed with SHA-256 before storage.
  - Session cookies are marked `HttpOnly` and configured with `COOKIE_SAMESITE=lax` (and `COOKIE_SECURE=true` in production HTTPS).
  - Configurable duration via `SESSION_HOURS` (default: 8 hours).
- **CSRF Protection**: All mutating state requests (POST, PUT, DELETE) require a valid CSRF token transmitted via header (`X-CSRF-Token`) or form body (`csrf_token`).
- **Password Security**: Passwords are saved as salted PBKDF2-HMAC-SHA256 slow hashes with 100,000 iterations. Plaintext passwords are never stored or logged.

#### Role Model
The system enforces exactly two roles:
1. **`editor`**:
   - View source file tree and upload status.
   - Upload new knowledge documents and archives.
   - Soft-delete sources to trash.
   - Trigger contradiction reviews and export Wiki archives.
2. **`admin`**:
   - All `editor` capabilities.
   - Manage user accounts at `/admin/users` (create users, toggle active status, reset passwords, update roles).
   - Configure dynamic robots and assign robot editing permissions.

#### Seeded Default Admin Account
Upon the very first database initialization, if no admin account exists, the system automatically creates:
- **Username**: `admin`
- **Password**: Value of `DEFAULT_ADMIN_PASSWORD` env variable, or `Admin#2026!Secured89`
- **Role**: `admin`
- **Assigned Teams**: All teams defined in `ALLOWED_TEAMS`

---

### 2.4 Robot & Knowledge Base Management

Robot knowledge bases are managed dynamically in `ecs/app/database.py` (`robots` and `robot_editors` tables) and synchronized with the Worker:
- **Dynamic Robots**: Administrators can create new robot topics (`POST /api/manage/create_robot`), delete robots (`POST /api/manage/delete_robot`), reorder display sequences (`POST /api/manage/reorder_robots`), and update bilingual display names in English and Chinese (`POST /api/manage/update_robot_names`).
- **Worker Directory Mapping**: Creating a robot triggers `create_robot_folder` to ensure `raw/sources/<robot>` exists on the Worker. Deleting a robot triggers `delete_robot_folder` to safely move the directory to `.agent1-trash/`.
- **Automatic Reconciliation**: When `list_sources` queries the Worker source tree, any newly discovered folders under `raw/sources/` are automatically registered in the `robots` table.
- **Soft Deletion**: Sources and robot directories are never permanently unlinked on deletion; they are atomically moved into `.agent1-trash/<timestamp>_<path>`. Deletion is blocked if the source is actively `processing` in LLM Wiki.

---

### 2.5 Upload Pipeline & Atomic Publishing

The upload pipeline guarantees data integrity and protects against malicious payloads:
1. **ECS Staging**:
   - Client uploads files to `/upload`.
   - Files are validated against maximum upload limits (`TEAM_MAX_UPLOAD_BYTES`, default 20 GB per team).
   - Filenames are sanitized, and files are written to `DATA_ROOT/uploads/<upload_id>/<filename>`.
2. **WebSocket Dispatch**:
   - ECS dispatches a `download_file` message to the Worker specifying the upload ID and temporary download URL.
3. **Worker Chunked Download**:
   - Worker streams the file from ECS `/download/{upload_id}/{filename}` into `STAGING_DIR/<upload_id>/`.
4. **Safe ZIP Extraction**:
   - If the uploaded file is a ZIP archive, `worker/zip_extractor.py` enforces:
     - Directory traversal protection (rejection of absolute paths or `..` components).
     - Symlink escape protection (all symbolic links are strictly rejected).
     - Zip bomb limits: maximum 20,000 files, max single file size 2 GB, max total extracted size 10 GB.
5. **Text Source Prompt Security Scanning**:
   - Text and Markdown sources are scanned by `worker/prompt_security.py:scan_text_sources` for prompt-injection markers before publication.
   - Any identified warnings are sent to ECS via `upload_security_warnings` and recorded in SQLite for administrator inspection.
6. **Atomic Publishing**:
   - Completed and validated files are atomically moved from staging into:
     ```text
     agent1/agent/raw/sources/<team>/<upload_id>/
     ```
   - Partial downloads or corrupt archives are cleaned from staging and never published.
   - Worker sends `sources_published` to register the individual source identities in ECS.

#### Supported File Suffixes (`shared/source_types.py`)
- **Document Sources**: `.pdf`, `.docx`, `.pptx`, `.xlsx`
- **Text Sources**: `.md`, `.mdx`, `.txt`, `.csv`, `.json`, `.html`, `.htm`, `.xml`, `.yaml`, `.yml`
- **Visual Assets**: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`
- **Archives**: `.zip`

---

### 2.6 Status Synchronization & LLM Wiki Monitoring

The web status dashboard (`/status`) separates concerns into two distinct views:
1. **This Upload**: Tracks the progress of files uploaded in the current batch (downloading, extracting, scanning, publishing, ingesting).
2. **All Current LLM Wiki Work**: A global view of all background ingestion activity across historical uploads, manual additions, and automatic retries.

#### Status Precedence
When evaluating LLM Wiki task entries in `.llm-wiki/ingest-queue.json` and `.llm-wiki/ingest-cache.json`, multiple entries may match a given source. The Worker evaluates all matches and determines the status using the following strict precedence:
```text
completed (cache receipt exists)
  -> processing (actively ingesting)
  -> retrying (failed with retryCount > 0 and retryCount < maxRetries)
  -> queued (pending in queue without active errors)
  -> failed (exhausted maxRetries or unrecoverable error)
  -> waiting
```

#### No Duplicate Ingestion Loop
`worker/.env` MUST enforce:
```env
LLM_WIKI_RESCAN_AFTER_PUBLISH=false
```
Because the LLM Wiki runs with `Source Watch: ON` and `Auto Ingest: ON`, the internal file watcher automatically detects newly published files under `raw/sources/`. Triggering an explicit `/sources/rescan` API call causes duplicate ingestion tasks and wasted API tokens.

---

### 2.7 Maintenance Workflows

#### Contradiction Review Workflow
- **Trigger**: An editor/admin initiates a review via `POST /api/manage/review` or the Worker triggers a scheduled review.
- **Execution**: Worker evaluates generated Wiki entries using DeepSeek (`DEEPSEEK_MERGE_MODEL=deepseek-v4-pro`) to identify conflicting factual claims across documentation.
- **Alert Dispatch**: Worker emits a `contradiction_alert` message over WebSocket.
- **Persistence & Notification**: ECS records the alert in the `wiki_contradictions` table, renders a warning banner in `/manage` (queried via `GET /api/manage/contradictions`), and logs email alerts to administrators via `MockEmailLogger.send_contradiction_alert`.

#### Wiki Archive Export
- **Trigger**: Authorized users call `GET /api/export/wiki`.
- **Packaging**: ECS dispatches `create_export` to the Worker. Worker compresses `agent1/agent/wiki/` into a temporary archive, uploads it to ECS at `/api/worker/upload-export/{export_id}`, and ECS delivers `wiki_export.zip` to the requesting client.

#### 14-Day QA Question Audit Report
- **Endpoint**: `GET /api/manage/export-qa-report`.
- **Output**: Generates and downloads a clean Markdown summary of all user questions asked over the prior 14 days, grouped by conversation ID and client IP, recording query counts, language choices, and timestamps.

---

## 3. Communication Protocols & Message Schemas

### 3.1 WebSocket Transport & Connection Management

- **Endpoint**: `ws://<ecs-host>:8000/ws/client`
- **Authentication**: Worker must send the `X-Worker-Secret` HTTP header matching `WORKER_SHARED_SECRET` in `ecs/.env`.
- **Single Connection Enforcement**: ECS permits only one connected Worker at a time. If an active Worker is connected, subsequent connection attempts receive WebSocket close code `1008` (Policy Violation).
- **120-Second Reconnection Grace Period**: If the Worker disconnects unexpectedly, ECS enters a 120-second grace period during which pending in-flight queries and streaming jobs are retained. If the Worker reconnects within 120 seconds, jobs resume seamlessly. Only upon expiration of the grace period are pending jobs failed.

---

### 3.2 ECS-to-Worker Messages

#### `question` (Initiate QA Query)
```json
{
  "type": "question",
  "id": "q-9f8a3c1e2b4d",
  "team": "walker_s2",
  "text": "What is the joint torque limit for arm actuator 3?",
  "conversation_id": "c-7b2e1f9a8d3c",
  "language": "en",
  "topic_label": "Walker S2",
  "history": [
    {"role": "user", "content": "Hello"},
    {"role": "bot", "content": "Hello! How can I assist you with Walker S2 today?"}
  ],
  "stream": true
}
```

#### `download_file` (Download Staged File)
```json
{
  "type": "download_file",
  "id": "dl-1a2b3c4d5e6f",
  "upload_id": "u-abcdef123456",
  "team": "walker_s2",
  "filename": "arm_actuator_spec.pdf",
  "download_url": "http://120.77.250.227:8000/download/u-abcdef123456/arm_actuator_spec.pdf",
  "published_at_ms": 1725883200000
}
```

#### `list_sources` (Query Source File Tree)
```json
{
  "type": "list_sources",
  "id": "cmd-112233445566"
}
```

#### `delete_source` (Soft Delete Source)
```json
{
  "type": "delete_source",
  "id": "cmd-998877665544",
  "path": "walker_s2/u-abcdef123456/arm_actuator_spec.pdf"
}
```

#### `create_robot_folder` (Create Robot Source Directory)
```json
{
  "type": "create_robot_folder",
  "id": "cmd-aabbccddeeff",
  "team": "walker_c1"
}
```

#### `delete_robot_folder` (Soft Delete Robot Directory)
```json
{
  "type": "delete_robot_folder",
  "id": "cmd-ffeeeeddccbb",
  "team": "walker_c1"
}
```

#### `trigger_review` (Trigger Contradiction Review)
```json
{
  "type": "trigger_review",
  "id": "rev-1234567890ab",
  "team": "walker_s2"
}
```

#### `create_export` (Request Wiki Export Package)
```json
{
  "type": "create_export",
  "id": "cmd-export123456",
  "export_id": "exp-abcdef789012"
}
```

---

### 3.3 Worker-to-ECS Messages

#### `qa_stream_chunk` (Token Streaming & Curation Events)
```json
{
  "type": "qa_stream_chunk",
  "id": "q-9f8a3c1e2b4d",
  "conversation_id": "c-7b2e1f9a8d3c",
  "status": "chunk",
  "text": "The maximum joint torque",
  "thinking": "",
  "thinking_tokens": 0,
  "replace_text": null,
  "image": null
}
```
*Note*: Emitted with `status: "done"` upon stream finish, or `status: "error"` with an error message on failure. Can deliver inline curated images with `"image": {"src": "...", "alt": "...", "caption": "..."}`.

#### `answer` (Non-Streaming Fallback Response)
```json
{
  "type": "answer",
  "id": "q-9f8a3c1e2b4d",
  "conversation_id": "c-7b2e1f9a8d3c",
  "text": "The maximum joint torque limit for arm actuator 3 is 48 Nm."
}
```

#### `job_progress` (Upload Download & Ingestion Progress)
```json
{
  "type": "job_progress",
  "upload_id": "u-abcdef123456",
  "stage": "extracting",
  "status": "running",
  "percent": 45,
  "message": "Extracting archive contents...",
  "error": null
}
```

#### `sources_published` (Register Published Sources)
```json
{
  "type": "sources_published",
  "upload_id": "u-abcdef123456",
  "source_identities": [
    "walker_s2/u-abcdef123456/spec.pdf",
    "walker_s2/u-abcdef123456/manual.md"
  ],
  "published_at_ms": 1725883200000
}
```

#### `upload_security_warnings` (Prompt Injection Findings)
```json
{
  "type": "upload_security_warnings",
  "upload_id": "u-abcdef123456",
  "warnings": [
    "manual.md: line 42 contains suspicious instruction override marker"
  ],
  "security_scan_complete": true
}
```

#### `source_tree_result` (Source Listing Response)
```json
{
  "type": "source_tree_result",
  "id": "cmd-112233445566",
  "status": "ok",
  "tree": {
    "walker_s2": {
      "u-abcdef123456": [
        {"name": "spec.pdf", "size": 1048576, "modified": 1725883200}
      ]
    }
  }
}
```

#### `delete_source_result` (Soft Delete Confirmation)
```json
{
  "type": "delete_source_result",
  "id": "cmd-998877665544",
  "status": "ok",
  "path": "walker_s2/u-abcdef123456/spec.pdf",
  "trash_path": ".agent1-trash/20260909_140000_spec.pdf"
}
```

#### `create_robot_folder_result` / `delete_robot_folder_result`
```json
{
  "type": "create_robot_folder_result",
  "id": "cmd-aabbccddeeff",
  "status": "ok",
  "team": "walker_c1"
}
```

#### `contradiction_alert` (Detected Knowledge Conflict)
```json
{
  "type": "contradiction_alert",
  "team": "walker_s2",
  "details": "Section 4.1 in 'arm_spec.md' specifies 48 Nm while Section 2 in 'actuator_limits.md' specifies 36 Nm for Actuator 3."
}
```

#### `llm_wiki_snapshot` (Global Activity Snapshot)
```json
{
  "type": "llm_wiki_snapshot",
  "generated_at": "2026-09-09T14:00:00Z",
  "counts": {
    "processing": 1,
    "retrying": 0,
    "queued": 2,
    "failed": 0
  },
  "tasks": [
    {
      "source_path": "walker_s2/u-abcdef123456/spec.pdf",
      "status": "processing",
      "retry_count": 0,
      "max_retries": 3,
      "error": null,
      "files_written": ["wiki/walker_s2/arm_spec.md"]
    }
  ]
}
```

#### `sync_existing_uploads` (Disk Reconciliation at Connect)
```json
{
  "type": "sync_existing_uploads",
  "uploads": [
    {"team": "walker_s2", "upload_id": "u-abcdef123456"}
  ]
}
```

#### `download_result` (Download Failure Notification)
```json
{
  "type": "download_result",
  "upload_id": "u-abcdef123456",
  "status": "failed",
  "error": "HTTP 404: Upload staging file not found on ECS"
}
```

---

## 4. Security Boundaries & Guardrails

### 4.1 Path Traversal & Filesystem Hardening
- **Strict Relative Path Validation**: All filesystem operations on the Worker must resolve relative to `WORKER_ROOT_DIR / "raw" / "sources"`.
- **Traversal Prevention**: Any path containing `..`, absolute root indicators (`/`), or null bytes is immediately rejected.
- **Symlink Protection**: Worker never follows symbolic links during file tree enumeration, deletion, or ZIP extraction.
- **Soft Deletion**: No files or directories are permanently removed by web requests; deletions are moved into `.agent1-trash/`.
- **Active Source Lock**: If a source is actively `processing` in the LLM Wiki queue, deletion requests are rejected with `SourceBusyError`.

---

### 4.2 Prompt Security & Source Scanning

The Worker implements two layers of defense against prompt attacks (`worker/prompt_security.py`):
1. **Runtime Input Guarding (`guard_user_input`)**:
   - Evaluates incoming questions before retrieval or inference.
   - Fast regex scanning for known attack patterns (jailbreaks, "ignore previous instructions", role manipulation, system prompt leakage requests).
   - Bounded DeepSeek classification fallback if pattern matching is inconclusive.
   - If blocked, immediately outputs a localized polite refusal (`refusal_text`) wrapped in the AI notice, without consuming retrieval or LangGraph steps.
2. **Text Source Scanning (`scan_text_sources`)**:
   - Scans uploaded markdown, text, and JSON documents prior to atomic publishing.
   - Flags suspicious adversarial injection markers embedded within source materials.
   - Dispatches warnings to ECS (`upload_security_warnings`) for administrative audit logging without breaking standard document publishing.

---

### 4.3 Secret Management & Transport Authentication

- **Secret Generation**: The shared secret must be a cryptographically strong random token:
  ```bash
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
  ```
- **Symmetric Authentication**: The identical string must be set in `ecs/.env` (`WORKER_SHARED_SECRET`) and `worker/.env` (`WORKER_SHARED_SECRET`).
- **File Permissions**: Environment files containing secrets must be restricted:
  ```bash
  chmod 600 ecs/.env
  chmod 600 worker/.env
  ```
- **Log Sanitation**: Secrets, provider API keys, and session cookies must never be logged or echoed in diagnostic outputs.

---

## 5. Configuration & Environment Variables

### 5.1 ECS Configuration (`ecs/.env`)

| Variable | Type | Default | Description |
|---|---|---|---|
| `APP_NAME` | string | `Uchat Knowledge Base` | Public brand and application title |
| `PUBLIC_BASE_URL` | string | `http://120.77.250.227:8000` | Fully qualified external URL of the ECS service |
| `ROOT_PATH` | string | `""` | Optional URL prefix when deployed behind a reverse proxy |
| `DATA_ROOT` | path | `/root/agent_7_14/ecs-data` | Absolute path to ECS data directory |
| `DATABASE_PATH` | path | `/root/agent_7_14/ecs-data/agent_jobs.db` | Absolute path to SQLite database file |
| `ALLOWED_TEAMS` | string | `tian_gong,walker_s2,walker_c1` | Initial bootstrap robot IDs |
| `WORKER_SHARED_SECRET` | string | *Required* | Shared secret matching Worker configuration |
| `WORKER_TIMEOUT` | integer | `240` | Worker response timeout in seconds |
| `FILE_COMMAND_TIMEOUT` | integer | `60` | File management command timeout in seconds |
| `TEAM_MAX_UPLOAD_BYTES` | integer | `21474836480` | Maximum upload storage per team (default: 20 GB) |
| `SESSION_COOKIE_NAME` | string | `agent1_session` | Name of the session cookie |
| `SESSION_HOURS` | integer | `8` | Session lifetime in hours |
| `COOKIE_SECURE` | boolean | `false` | Set to `true` when deployed with HTTPS |
| `COOKIE_SAMESITE` | string | `lax` | Cookie SameSite policy (`lax`, `strict`, `none`) |
| `DEFAULT_ADMIN_PASSWORD` | string | `Admin#2026!Secured89` | Seed password for initial admin creation |
| `WXWORK_TOKEN` | string | `""` | Enterprise WeChat application callback token |
| `WXWORK_AESKEY` | string | `""` | Enterprise WeChat 43-character EncodingAESKey |
| `WXWORK_CORPID` | string | `""` | Enterprise WeChat CorpID |
| `WXWORK_AGENTID` | string | `""` | Enterprise WeChat AgentID |
| `WXWORK_CORPSECRET` | string | `""` | Enterprise WeChat application secret |

---

### 5.2 Worker Configuration (`worker/.env`)

*Crucial*: Always use fully resolved absolute paths. Do not leave a literal `$HOME` in `worker/.env` as python-dotenv does not expand environment variables.

| Variable | Type | Default / Example | Description |
|---|---|---|---|
| `SERVER_URL` | url | `ws://120.77.250.227:8000/ws/client` | WebSocket endpoint on ECS gateway |
| `WORKER_SHARED_SECRET` | string | *Required* | Exact same shared secret as ECS |
| `ALLOWED_TEAMS` | string | `tian_gong,walker_s2,walker_c1` | Initial bootstrap robot IDs |
| `BASE_DIR` | path | `/home/<user>/Documents/agent_7_14/agent1/agent` | Absolute path to LLM Wiki project root |
| `STAGING_DIR` | path | `.../agent1/agent/.agent1-worker/staging` | Temporary download and staging area |
| `TRASH_DIR` | path | `.../agent1/agent/.agent1-trash` | Soft delete destination |
| `QA_WORKERS` | integer | `3` | Number of concurrent QA worker processing lanes |
| `DOWNLOAD_WORKERS` | integer | `2` | Number of concurrent file download workers |
| `FILE_OPERATION_WORKERS`| integer | `1` | Serialized file manager worker pool |
| `FILE_MANAGER_MAX_ENTRIES`| integer | `10000` | Max entries returned in source tree enumeration |
| `DEEPSEEK_API_KEY` | string | *Worker-only secret* | API key for DeepSeek completions |
| `DEEPSEEK_MODEL` | string | `deepseek-v4-flash` | Model for QA streaming and retrieval |
| `DEEPSEEK_MERGE_MODEL` | string | `deepseek-v4-pro` | Model for section merging and contradiction reviews |
| `DEEPSEEK_BASE_URL` | url | `https://api.deepseek.com` | DeepSeek API base endpoint |
| `DEEPSEEK_TIMEOUT` | integer | `240` | Timeout in seconds for DeepSeek API requests |
| `DEEPSEEK_SECTION_MAX_TOKENS` | integer | `8192` | Max token budget per section call |
| `DEEPSEEK_STRUCTURED_RETRIES` | integer | `1` | Retry attempts for structured JSON responses |
| `DEEPSEEK_TRANSPORT_RETRIES` | integer | `1` | Retry attempts for network transport errors |
| `WIKI_QA_MAX_PAGES` | integer | `8` | Maximum Wiki pages included in final answer prompt |
| `WIKI_QA_MAX_PAGE_CHARS` | integer | `24000` | Maximum character budget across retrieved pages |
| `QA_REASONING_MAX_CANDIDATES` | integer | `15` | Max candidate pages retrieved by FTS5 |
| `QA_REASONING_MAX_PAGES` | integer | `6` | Max pages loaded into LangGraph reasoning node |
| `QA_STRICT_ROBOT_SCOPE` | boolean | `true` | Confines evidence strictly to selected robot topic |
| `DOWNLOAD_TIMEOUT` | integer | `1800` | Download timeout in seconds (30 minutes) |
| `MAX_UPLOAD_BYTES` | integer | `5368709120` | Max single archive upload limit (5 GB) |
| `MAX_ZIP_FILES` | integer | `20000` | Max file count limit for ZIP extraction |
| `MAX_ZIP_EXTRACTED_BYTES` | integer | `10737418240` | Max total uncompressed size limit (10 GB) |
| `MAX_ZIP_SINGLE_FILE_BYTES`| integer | `2147483648` | Max single extracted file size (2 GB) |
| `LLM_WIKI_QUEUE_FILE` | path | `.../agent1/agent/.llm-wiki/ingest-queue.json` | Path to LLM Wiki queue file |
| `LLM_WIKI_CACHE_FILE` | path | `.../agent1/agent/.llm-wiki/ingest-cache.json` | Path to LLM Wiki cache file |
| `LLM_WIKI_POLL_SECONDS` | integer | `2` | Interval in seconds for monitoring queue files |
| `LLM_WIKI_MONITOR_TIMEOUT` | integer | `7200` | Heartbeat resend timeout in seconds |
| `LLM_WIKI_RESCAN_AFTER_PUBLISH` | boolean | `false` | MUST remain false to prevent duplicate tasks |
| `CONVERSATION_MAX_TURNS` | integer | `6` | Max prior conversation turns retained in memory |
| `CONVERSATION_MAX_SESSIONS`| integer | `1000` | Max active browser sessions tracked in memory |
| `PROMPT_GUARD_ENABLED` | boolean | `true` | Enables input prompt injection protection |
| `PROMPT_GUARD_TIMEOUT` | integer | `20` | Timeout in seconds for prompt guard classification |
| `PROMPT_GUARD_CONCURRENCY` | integer | `2` | Max concurrent prompt guard evaluations |
| `PROMPT_SCAN_MAX_FILE_BYTES` | integer | `2097152` | Max size per text file scanned (2 MB) |
| `PROMPT_SCAN_MAX_TOTAL_BYTES`| integer | `10485760` | Max total bytes scanned per upload (10 MB) |
| `PROMPT_SCAN_MAX_WARNINGS` | integer | `1000` | Max warnings recorded per scan |

---

## 6. Operational Workflows & Deployment Guide

### 6.1 Git-Based Fast-Forward Deployment & Upgrades

Production deployments and updates are executed via Git fast-forward merges, automated dependency syncing with `uv`, and process supervision in `tmux`.

#### ECS Gateway Upgrade
To update the ECS server:
```bash
# Execute the automated deployment script on the ECS host
./scripts/pull_and_restart_ecs.sh
```
What this script performs:
1. Validates the Git checkout, `ecs/.env`, and `ecs-data/agent_jobs.db`.
2. Creates an atomic backup under `/root/agent_7_14-deploy-backups/<timestamp>` using SQLite's online backup API (ensuring WAL consistency) and archives existing data files.
3. Fetches `origin/main` and merges via `git merge --ff-only FETCH_HEAD`.
4. Synchronizes locked dependencies using `./scripts/uv_sync.sh ecs`.
5. Verifies syntax across all modules with `compileall`.
6. Gracefully terminates any existing `agent-7-14-ecs` tmux session.
7. Starts the ECS server in a fresh tmux session and polls `/health` until healthy.

#### Worker Computer Upgrade
To update the Worker computer:
```bash
# Execute the automated deployment script on the Worker host
./scripts/pull_and_restart_worker.sh
```
What this script performs:
1. Validates the Git checkout, `worker/.env`, and the live LLM Wiki project directory.
2. Backs up `worker/.env` and live LLM Wiki state (`raw`, `wiki`, `.llm-wiki`) into `.agent1-deploy-backups/<timestamp>`.
3. Fetches `origin/main` and merges via `git merge --ff-only FETCH_HEAD`.
4. Synchronizes locked dependencies using `./scripts/uv_sync.sh worker`.
5. Compiles modules with `compileall` and validates machine readiness via `check_worker_machine.sh`.
6. Restarts the Worker in tmux session `agent-7-14-worker`.
7. Confirms process vitality.

---

### 6.2 Initial Machine Bootstrap

#### 1. ECS Server Setup
```bash
# Clone the repository
git clone <repo-url> /root/agent_7_14 && cd /root/agent_7_14

# Install uv (https://docs.astral.sh/uv/) if not present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install locked ECS virtual environment
./scripts/uv_sync.sh ecs

# Configure environment
cp ecs/.env.example ecs/.env
# Edit ecs/.env: set PUBLIC_BASE_URL, DATA_ROOT, DATABASE_PATH, WORKER_SHARED_SECRET
chmod 600 ecs/.env

# Start ECS service in tmux
tmux new-session -d -s agent-7-14-ecs "./scripts/run_ecs.sh"

# Verify health
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

#### 2. Worker Computer Setup
```bash
# Clone the repository
git clone <repo-url> $HOME/Documents/agent_7_14 && cd $HOME/Documents/agent_7_14

# Install uv if not present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install locked Worker virtual environment
./scripts/uv_sync.sh worker

# Configure environment
cp worker/.env.example worker/.env
# Edit worker/.env:
#   - Set SERVER_URL=ws://120.77.250.227:8000/ws/client
#   - Set WORKER_SHARED_SECRET (exact same value as ECS)
#   - Set DEEPSEEK_API_KEY
#   - Set BASE_DIR, STAGING_DIR, TRASH_DIR with fully resolved absolute paths (no literal $HOME)
chmod 600 worker/.env

# Prepare LLM Wiki project directory
mkdir -p $HOME/Documents/agent_7_14/agent1/agent/raw/sources
mkdir -p $HOME/Documents/agent_7_14/agent1/agent/wiki

# Open LLM Wiki GUI on $HOME/Documents/agent_7_14/agent1/agent
# Verify Source Watch: ON and Auto Ingest: ON

# Start Worker service in tmux
tmux new-session -d -s agent-7-14-worker "./scripts/run_worker.sh"

# Verify connection on ECS
curl -s http://120.77.250.227:8000/health | grep '"worker_online": true'
```

---

### 6.3 Local Development & Validation Commands

From the repository root:

```bash
# 1. Check Python syntax and compilation across all targets
python3 -m compileall -q ecs worker shared scripts

# 2. Install development and test dependencies
./scripts/uv_sync.sh dev

# 3. Run the automated test suite
.venv-dev/bin/python -m pytest -q

# 4. Run ECS locally for testing
./scripts/run_ecs.sh

# 5. Run Worker locally for testing (after configuring test .env)
./scripts/run_worker.sh
```

---

### 6.4 Operational Troubleshooting & Diagnostics

#### DeepSeek API Failures
LLM Wiki or Worker logs may indicate:
```text
Generation failed: error sending request for url (https://api.deepseek.com/chat/completions)
```
Diagnostic steps on the Worker machine:
1. Verify outbound network connectivity and DNS resolution:
   ```bash
   curl -I https://api.deepseek.com
   getent hosts api.deepseek.com
   ```
2. Check for active proxy environment variables that might interfere with requests:
   ```bash
   env | grep -iE 'http_proxy|https_proxy|all_proxy'
   ```
3. Check DeepSeek API credit balances and rate limit status.
4. Distinguish transport timeouts (handled by `DEEPSEEK_TRANSPORT_RETRIES`) from authentication failures.

#### Duplicate Ingestion Prevention
If duplicate ingestion tasks appear in the queue:
- Confirm `LLM_WIKI_RESCAN_AFTER_PUBLISH=false` in `worker/.env`.
- Ensure multiple Worker instances are not running simultaneously. Check running processes:
  ```bash
  ps aux | grep "worker.main"
  ```

#### Reconnection Grace Period Diagnostics
When observing ECS logs:
- `Worker WebSocket detached (granting 120s reconnection grace period)`: Normal when Worker restarts or network blips occur. In-flight streams wait up to 120 seconds for the Worker to reconnect.
- `Worker reconnected within 120s grace period; active background jobs preserved`: Indicates successful reconnection without job failure.

---

### 6.5 Production Hardening Checklist

Before wide public release:
- [ ] Configure a domain name (e.g. `kb.example.com`) pointing to the ECS server.
- [ ] Deploy Nginx or Caddy as a reverse proxy with TLS/HTTPS certificates.
- [ ] Set `COOKIE_SECURE=true` in `ecs/.env`.
- [ ] Update `SERVER_URL` in `worker/.env` to secure WebSocket (`wss://kb.example.com/ws/client`).
- [ ] Close direct external access to port 8000 via cloud security groups; allow only ports 80/443.
- [ ] Configure automated offsite backup rotation for `agent_jobs.db` and live LLM Wiki source files.
- [ ] Migrate tmux sessions to systemd services (`agent-7-14-ecs.service` and `agent-7-14-worker.service`) for automatic process recovery upon system reboot.
