# Final changes

- Added SQLite users and login sessions.
- Added salted `scrypt` password storage.
- Added editor/admin roles and removed the obsolete viewer role.
- Added HttpOnly session cookie and CSRF protection.
- Added basic failed-login throttling.
- Protected upload, upload history/status and source management.
- Added `/manage` source-tree browser.
- Added Worker `list_sources` and `delete_source` commands.
- Added serialized file-operation queue.
- Added path traversal and symlink protection.
- Added active-ingestion deletion protection.
- Added soft deletion to `.agent1-trash`.
- Added file audit log.
- Disabled automatic LLM Wiki rescan by default to prevent duplicate ingestion alongside Source Watch.
- Added `scripts/create_user.py`.
- Replaced pip/requirements dependency installation with locked `uv` ECS,
  Worker, and development environments.

## Python 3.10 Worker compatibility

- Replaced the Python 3.11-only `asyncio.timeout()` Cerebras streaming wrapper
  with `asyncio.wait_for()` and bounded queue polling.
- Added regression coverage for runtimes where `asyncio.timeout` is unavailable.
- Added root `pyproject.toml` and `uv.lock`; deployment scripts now synchronize
  `.venv-ecs` and `.venv-worker` with `uv` while retaining their interpreters.

## Public QA provider failover and retrieval privacy

- Removed visible Wiki slug/path citations from prompts, streaming chunks, and
  final answers while retaining ordinary links and approved Wiki images.
- Added DeepSeek V4 Flash failover with a five-minute Cerebras circuit breaker
  and one half-open recovery probe.
- Added bounded robot/topic filename discovery so stale `index.md` files do not
  hide existing Walker C1 pages; unrelated unindexed pages remain unavailable.

## QA conversation and language update

- Added a browser-generated conversation ID stored in `localStorage`.
- Questions from the same browser conversation are deterministically routed to the same QA worker lane.
- Added recent in-memory conversation history so follow-up questions retain context even though each Claude CLI invocation is a separate subprocess.
- Added a **New conversation** button that intentionally resets the browser conversation ID.
- Added answer-language selection for Simplified Chinese, Traditional Chinese, Korean, Japanese, English, Portuguese, Russian, and Spanish.
- Added read-only Claude tool pre-approval (`Read`, `Glob`, `Grep`) and stricter service prompts so Claude reads the wiki silently instead of asking the website user for permission.
- Removed public-web-search instructions from the bundled `CLAUDE.md`; QA is now explicitly based on the local project files.

## Robot management and source-tree reconciliation

- A successful **Refresh source tree** now makes the top-level Worker `raw/sources/` folders authoritative for the robot list and every robot selector.
- Stale robot metadata from `ALLOWED_TEAMS`, old user records, or upload history is removed after that first successful reconciliation and is not re-created on ECS restart.
- Added an administrator-only **Remove robot** action. The Worker first moves the complete robot source folder into `.agent1-trash`; only then does ECS remove the robot metadata and editor assignments.
- Robot removal is blocked while one of that robot's sources is actively ingesting.
- Worker startup creates only the shared `raw/sources/` root and no longer recreates a deleted configured robot folder.

## Reliable administrator account creation

- Expensive password hashing now runs outside the ECS event loop so user creation does not pause HTTP and Worker WebSocket handling.
- If a successful create response is lost in transit, the administrator page refreshes the user list and reports verified success when the account was committed.
- The create button is disabled while a request is in progress to prevent accidental duplicate submissions.
- Editors sent to an administrator-only URL during login now land on `/manage`; the administrator APIs remain inaccessible to them.
- Added `scripts/deploy_worker_from_downloads.sh` for safe ZIP deployment directly on the Worker computer without ECS SSH access.

## Account settings menu

- Added one shared ChatGPT-style account control with a circular initial avatar and username across the QA, source manager, upload, upload-status, and administrator pages.
- Moved source management, upload, knowledge-base export, and sign-out actions into the shared account settings menu on every application route.
- The user-management link appears only when `/api/me` identifies the signed-in user as an administrator.
- Removed standalone user-management links from the source manager and upload pages, and removed the standalone wiki-export buttons from the chat area.

## Worker validation logging

- Invalid robot or source paths are now returned as normal file-manager validation failures instead of being logged as internal Worker ERROR tracebacks.
- Added regression coverage for traversal-style and invalid-team source paths.

## Upload monitor team propagation

- Fixed normal uploads failing after publication with `monitor_source() missing 1 required keyword-only argument: 'team'`.
- The Worker now passes the selected robot team to LLM Wiki monitoring, optional rescans, and authored-wiki monitoring.
- Existing source files from an affected upload remain in place and can be reconciled after restarting the corrected Worker.

## Upload status and account rendering fixes

- The upload page now renders the signed-in role instead of exposing the `__ROLE__` template placeholder.
- The global LLM Wiki panel reads a shared queue file only once, even when several robots use the same LLM Wiki project.
- The Worker resends the latest global snapshot after reconnecting, so an ECS restart does not leave the global panel empty while the queue is unchanged.
- Current-upload monitoring now matches both team-prefixed shared-project paths and legacy paths.
- Exhausted LLM Wiki retries are reported as failed; active retries remain retrying.
- Status monitoring now follows the upstream LLM Wiki queue schema, where `maxRetries` is absent and the retry limit is fixed at three.
- Upload support now uses upstream-compatible `.mdx` instead of unsupported `.markdown`.
- Worker publishing now relies exclusively on LLM Wiki Source Watch and contains no `/sources/rescan` call, preventing duplicate ingestion even if an old environment setting is incorrect.
- The Worker machine check loads `.env` through the Python configuration parser, so values containing spaces such as `CLAUDE_EXTRA_ARGS=--model haiku` no longer break the script.
- Protected HTML routes, including `/admin/users`, redirect signed-out users to the login page instead of displaying an API-style JSON error.
