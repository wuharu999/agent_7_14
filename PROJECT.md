# Project: Agent1 QA & Ingestion Enhancements

## Architecture
- **ECS (FastAPI Gateway)**: Web server managing user authentication, sessions, file uploads, WeCom callback routing, and audit logs. Communicates with Worker over WebSocket.
- **Worker (LLM Wiki & Claude Runner)**: Connects to ECS via WebSocket, processes Claude QA with read-only tools, handles file downloading, zip extraction, and publishes files.
- **LLM Wiki**: Ingests files under `raw/sources/` into the wiki knowledge base.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Exploration & Diagnosis | Run initial diagnostics and investigate existing codebases | None | DONE |
| 2 | System Config & Test Fixes | Fix shared secret mismatch, config path mapping, authoring TypeError, and restore broken tests | M1 | IN_PROGRESS |
| 3 | UI Layouts | Default `/` horizontal QA route | M2 | PLANNED |
| 4 | Wiki Expansion | Add missing robot learning topics to local wiki knowledge base to match UI graph nodes | M2 | PLANNED |
| 5 | Source Manager & Upload Updates | Implement folder collapsing/expanding, display upload timestamps, and simplify upload formats | M2 | PLANNED |
| 6 | E2E Testing Track | Write and run comprehensive E2E test cases across Tiers 1-4 | M3, M4, M5 | PLANNED |
| 7 | Verification & Victory Audit | Run tests, execute Forensic Audit, confirm layout, and finalise | M6 | PLANNED |

## Interface Contracts
- **WebSocket Protocol**:
  - Client question: `{"type": "question", "id": "q-...", "conversation_id": "...", "language": "...", "text": "..."}`
  - Client delete source: `{"type": "delete_source", "id": "delete-...", "path": "..."}`
  - Client list sources: `{"type": "list_sources", "id": "files-..."}`
- **Code Layout**:
  - ECS server: `ecs/`
  - Worker server: `worker/`
  - Shared modules: `shared/`
  - Wiki files: `agent1/agent/wiki/`
  - Test suites: `tests/`
