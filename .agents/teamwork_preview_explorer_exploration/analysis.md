# Codebase Exploration Analysis Report

## Summary of Findings
We have completed a comprehensive read-only exploration of the codebase for the two-machine knowledge-base upload and QA system. The system uses a FastAPI web server on ECS to manage public QA, user sessions, audit logging, and file uploads, which are then processed via an outbound WebSocket connection by a Worker machine. 

Our investigation revealed that while the core layout and routing architectures are present and well-designed, there are several key gaps and mismatch issues in path mapping, CLI signatures, and environment configurations that currently prevent tests from passing and operations from working as intended in a live environment.

---

## 1. Diagnostics Run

### 1.1 File Search (`find . -maxdepth 4 -not -path '*/.*' -type f | sort`)
The repository structure is structured as follows:
- **ECS (FastAPI Gateway)**: Main entry point is `ecs/app/main.py`. The business routes reside in `ecs/app/routes/`. Web interface templates reside in `ecs/app/templates/`.
- **Worker (LLM Wiki Wrapper)**: Main entry point is `worker/main.py` which spawns `WorkerManager` (`worker/manager.py`). Claude CLI wrappers reside in `worker/claude_process.py` and `worker/claude_runner.py`.
- **Wiki KB**: Resides in `agent1/agent/` with the index, overview, log, unanswered logs, and individual markdown files.
- **Scripts**: Maintenance scripts like `create_user.py` and bootstrap scripts reside in `scripts/`.
- **Tests**: Pytest-based unit and integration tests are located in `tests/`.

### 1.2 Keyword Grep Diagnostics
Running the required grep command revealed the following crucial mappings:
- **Conversation Lane Mapping**: Question messages from WebSocket route using a Blake2s hash of `conversation_id` modulo `QA_WORKERS` to guarantee lane persistence.
- **Language Synchronization**: Supported languages (`zh-CN`, `zh-TW`, `ko`, `ja`, `en`, `pt`, `ru`, `es`) are synchronized between the front-end page selector, route validations, and passed as arguments in the `run_claude` wrapper to translate user prompt constraints and internal error messages.
- **LLM Wiki Monitoring & Rescan Prevention**: Configured via `LLM_WIKI_RESCAN_AFTER_PUBLISH=false` inside the worker to prevent duplicate ingestions alongside Source Watch. Ingestion status updates and retries are fetched directly from `.llm-wiki/ingest-queue.json` and `ingest-cache.json`.

---

## 2. Codebase Locations

### 2.1 ECS Server Code, Routes, & Templates
- **Entry point**: `ecs/app/main.py`
- **Configuration**: `ecs/app/config.py` (reads from `ecs/.env`)
- **Database operations**: `ecs/app/database.py` (manages SQLite connection, schema creation, migrations, audit logging)
- **Routes (`ecs/app/routes/`)**:
  - `/` (Home page / QA chat): GET route in `pages.py`, serving `templates/ask.html`
  - `/login`: GET route in `pages.py` (`templates/login.html`), POST route in `auth.py`
  - `/logout`: POST route in `auth.py`
  - `/upload` (Documentation upload): GET route in `pages.py` (`templates/upload.html`), POST route in `uploads.py`
  - `/manage` (Source manager): GET route in `pages.py` (`templates/manage.html`), POST route (soft delete) in `manage.py`
  - `/uploads/{upload_id}` (Upload status page): GET route in `pages.py` (`templates/upload_status.html`), API in `uploads.py`
- **Templates**:
  - `ecs/app/templates/ask.html`: The main user-facing question page.
  - `ecs/app/templates/login.html`: Secure login interface.
  - `ecs/app/templates/upload.html`: Authenticated multi-file drag-and-drop upload screen.
  - `ecs/app/templates/upload_status.html`: Real-time upload and global LLM Wiki ingestion progress status.
  - `ecs/app/templates/manage.html`: Relative-path source tree listing and soft-deletion control center.

### 2.2 Worker Code, QA, & Claude CLI
- **Entry point**: `worker/main.py`
- **Worker Management**: `worker/manager.py` (starts loops for sender, connection, global monitoring, QA workers, downloaders, and file operations)
- **QA Processing Logic**: Spawns multiple `qa_worker` loops pulling tasks from `self.question_queues`. Each task uses `worker/prompt_security.py` to guard user inputs, then fetches context history via `worker/conversation_store.py`, and calls the runner.
- **Claude CLI Subprocess Logic**:
  - `worker/claude_runner.py`: Packages system and user prompts with conversation history, maps error strings for localization, and raises `ClaudePolicyViolation` or `ClaudeProcessError`.
  - `worker/claude_process.py`: Prepares the subprocess command using pre-approved read-only tools (`Read`, `Glob`, `Grep`) and strictMCP configurations. Disallows mutable tools (`Bash`, `Edit`, `Write`) and forces `--permission-mode dontAsk` so Claude does not ask the user for permission. It calls `asyncio.create_subprocess_exec` to execute `claude` CLI.

### 2.3 Wiki Knowledge Base & Node Graph
- **Wiki Directory**: `/home/eason/Documents/agent_7_14/agent1/agent`
- **Robot Learning files**:
  - Concepts: `agent1/agent/wiki/concepts/` (e.g. `强化学习运控.md`, `力位混合控制.md`, `人形机器人关节标零.md`, `一体化关节设计.md`, `双电池快换系统.md`, `语音交互大模型.md`, `科研教学用途.md`, `二次开发.md`)
  - Entities: `agent1/agent/wiki/entities/` (e.g. `inspire灵巧手.md`, `天工行者.md`, `天工行者无界.md`, `天工行者无疆.md`)
  - Index & Logs: `agent1/agent/wiki/index.md`, `overview.md`, `log.md`
- **Node Graph Arrays**:
  - **Frontend**: Hardcoded JavaScript objects mapping robot learning concepts/entities to force-directed graph node arrays inside `ecs/app/templates/ask.html` and `ask_prototype.html`.
  - **Backend**: **None**. No backend routes or Python scripts handle node graph arrays or relations dynamically.

### 2.4 Unit and Integration Tests
Located under `tests/`:
- `tests/test_authoring.py`: Mocks Claude runs and tests documentation authoring/publication.
- `tests/test_prompt_security.py`: Verifies input sanitization, policy bypass protections, and file scanners.
- `tests/test_security_migration.py`: Tests SQLite schema initialization and warnings payload.
- `tests/test_upload_batch.py`: Validates Starlette request handling, authorization rules, and frontend template scripts.

---

## 3. Implementation & Migration Status

### 3.1 Feature Checklist

| Requirement | Description | Status | Reference / Location |
|---|---|---|---|
| **Public QA Page** | Consistently route conversation, UI localization, "New conversation" action | **Completed** | `ecs/app/templates/ask.html`, `ecs/app/routes/ask.py`, `worker/manager.py` |
| **Authentication** | `/login`, `/logout`, viewer/editor/admin roles, HttpOnly session, CSRF | **Completed** | `ecs/app/auth.py`, `ecs/app/routes/auth.py`, `ecs/app/database.py` |
| **Ingestion** | Download zip, extract safely, atomic publish to `raw/sources/` | **Completed** | `worker/downloader.py`, `worker/zip_extractor.py`, `worker/publisher.py` |
| **Source Manager** | Relative-path tree list, soft delete to `.agent1-trash/`, block on processing | **Partially Implemented** | `worker/file_manager.py` / *Issue: Directory structure mismatch ignores existing files* |
| **Status Sync** | Ingestion progress metrics, retry count, active queue matching | **Completed** | `ecs/app/templates/upload_status.html`, `ecs/app/database.py` |
| **LLM Wiki Integration** | Rescan disabled (`LLM_WIKI_RESCAN_AFTER_PUBLISH=false`), ingest files monitor | **Completed** | `worker/config.py`, `worker/llm_wiki_monitor.py` |
| **Claude QA** | Read-only tools pre-approved, `--permission-mode dontAsk` | **Completed** | `worker/claude_process.py` |
| **WeCom** | /wecom/callback endpoint integration | **Completed** | `ecs/app/routes/wecom.py` |
| **Claude Authoring** | In-conversation document authoring and publishing | **Broken** | `worker/authoring.py` / *Issue: run_claude_process call lacks team argument* |

### 3.2 Database Migration Setup
The database schema migration is implemented directly in python in `ecs/app/database.py` at `initialize_database()`. The application uses sqlite3 and executes dynamic alter statements on startup if columns are missing:
- `created_by` in `uploads` table.
- `security_scan_complete` in `uploads` table.
- `retry_count`, `max_retries`, and `active_queue_count` in `upload_sources` table.
- `teams` in `users` table.

### 3.3 Identified Technical Issues / Bugs

1. **Claude Authoring `TypeError`**:
   - **File**: `worker/authoring.py` (lines 150-154)
   - **Bug**: `_run` calls `run_claude_process` without passing the required keyword-only argument `team`.
   - **Impact**: All authoring chats and articles generation fail with `TypeError: run_claude_process() missing 1 required keyword-only argument: 'team'`.

2. **Team Directory Path Mismatch**:
   - **File**: `worker/config.py` (lines 58-66)
   - **Bug**: `get_team_config` maps directories nested by team *before* `raw/sources/` (e.g. `agent1/agent/{team}/raw/sources`). However, the actual live project structure has `raw/sources/` *before* the team name (e.g. `agent1/agent/raw/sources/{team}/`).
   - **Impact**: The file manager (`worker/file_manager.py`) searches in empty nested dirs, skipping active sources. This renders source tree listing and soft deletion non-functional for existing files.

3. **Mismatched Shared Secrets**:
   - **File**: `ecs/.env` vs `worker/.env`
   - **Bug**: `WORKER_SHARED_SECRET` is set to `replace-with-a-long-random-secret` on the ECS server side, but uses a generated urlsafe token on the Worker machine.
   - **Impact**: The Worker cannot authenticate WebSocket connections or download files from ECS due to token mismatch.

4. **Broken Unit Tests**:
   - **Files**: `tests/test_authoring.py` and `tests/test_prompt_security.py`
   - **Bugs**:
     - Tests construct `QuestionJob` and call `run_claude_process` without the `team` parameter.
     - Tests mock `publisher.RAW_SOURCES_DIR` which has been refactored to `get_team_config(team).raw_sources_dir`.
   - **Impact**: Pytest fails with multiple `AttributeError` and `TypeError` exceptions.
