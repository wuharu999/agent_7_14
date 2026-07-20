# Detailed Execution Plan

We will execute the enhancement project in structured milestones:

## Milestone 2: System Config & Test Fixes
- Fix `WORKER_SHARED_SECRET` in `ecs/.env` to match `worker/.env`.
- Fix `worker/config.py` path mappings for `raw_sources_dir`, `wiki_dir`, `llm_wiki_queue_file`, and `llm_wiki_cache_file` to match the actual directory structure.
- Fix `worker/authoring.py` where `run_claude_process` is called to supply the required `team` argument.
- Update `tests/test_authoring.py` and `tests/test_prompt_security.py` to fix unit tests.

## Milestone 3: UI Layouts (Horizontal & Vertical)
- Check how `/` is served on ECS.
- Ensure the default `/` serves the horizontal chat page with the side-by-side interactive node graph.
- Create `/wecom-ask` route in `ecs/app/routes/pages.py`.
- Create a stunning vertical premium layout template for WeCom Ask route inspired by Walker C1 product page style.
- Set up responsive styles or separate views.

## Milestone 4: Wiki Expansion
- Retrieve the existing nodes in the frontend graph array (inside `ask.html` or similar).
- Cross-reference with files in `agent1/agent/wiki/`.
- Generate or add the missing topics (e.g., as `.md` files in `concepts/` or `entities/` or updating graph structures as necessary).

## Milestone 5: Manage Sources & Upload Updates
- Implement collapsible folder trees using HTML `<details>` and `<summary>` in `manage.html` (or equivalent CSS/JS).
- Update the `/manage` route and template to display the upload timestamp next to the upload IDs.
- Update `upload.html` to remove the redundant text blocks list of document types and simplify the extension list.

## Milestone 6: E2E Testing
- Write automated test scripts/cases to verify the UI layouts, wiki nodes, manage page folder collapses, timestamps, and upload extension lists.
- Perform run and validation.

## Milestone 7: Verification & Victory Audit
- Run all unit and integration tests.
- Run the Forensic Auditor.
- Verify everything is clean and report to parent Sentinel.
