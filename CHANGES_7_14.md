# Final changes

- Added SQLite users and login sessions.
- Added salted `scrypt` password storage.
- Added viewer/editor/admin roles.
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
- Updated bootstrap scripts for the Tsinghua PyPI mirror and retry handling.

## QA conversation and language update

- Added a browser-generated conversation ID stored in `localStorage`.
- Questions from the same browser conversation are deterministically routed to the same QA worker lane.
- Added recent in-memory conversation history so follow-up questions retain context even though each Claude CLI invocation is a separate subprocess.
- Added a **New conversation** button that intentionally resets the browser conversation ID.
- Added answer-language selection for Simplified Chinese, Traditional Chinese, Korean, Japanese, English, Portuguese, Russian, and Spanish.
- Added read-only Claude tool pre-approval (`Read`, `Glob`, `Grep`) and stricter service prompts so Claude reads the wiki silently instead of asking the website user for permission.
- Removed public-web-search instructions from the bundled `CLAUDE.md`; QA is now explicitly based on the local project files.
