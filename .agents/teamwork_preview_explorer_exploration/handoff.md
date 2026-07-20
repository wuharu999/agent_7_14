# Handoff Report — Codebase Exploration

## 1. Observation

### 1.1 Test Execution Errors
Running `python3 -m pytest` with the local virtualenv packages yielded 6 failures (out of 36 tests) with the following verbatim tracebacks:
```text
tests/test_authoring.py:58: 
...
worker/authoring.py:150: TypeError: run_claude_process() missing 1 required keyword-only argument: 'team'
```
```text
E           AttributeError: <module 'worker.publisher' from '/home/eason/Documents/agent_7_14/worker/publisher.py'> does not have the attribute 'RAW_SOURCES_DIR'
/usr/lib/python3.14/unittest/mock.py:1480: AttributeError
```
```text
tests/test_prompt_security.py:309: TypeError: QuestionJob.__init__() missing 1 required positional argument: 'team'
```

### 1.2 Path Resolution
In `worker/config.py` (lines 58-65):
```python
    team_base = WORKER_ROOT_DIR / team
    return TeamConfig(
        team_name=team,
        base_dir=team_base,
        raw_sources_dir=team_base / "raw" / "sources",
        wiki_dir=team_base / "wiki",
        llm_wiki_queue_file=team_base / ".llm-wiki" / "ingest-queue.json",
        llm_wiki_cache_file=team_base / ".llm-wiki" / "ingest-cache.json",
        llm_wiki_api_url=f"http://127.0.0.1:{port}/api/v1"
    )
```
However, the actual live project directories listed by `list_dir` in `agent1/agent` are:
- `raw/sources/tian_gong/`
- `wiki/`
- `.llm-wiki/`

There are no folders named `tian_gong/raw/sources` or `tian_gong/wiki` under `agent1/agent/`.

### 1.3 Shared Secrets
- In `ecs/.env`: `WORKER_SHARED_SECRET=replace-with-a-long-random-secret` (line 6)
- In `worker/.env`: `WORKER_SHARED_SECRET=EnvhfcQZ0ss_pjeSOCtbDPMnI60_tfKhPFHlGdR0mnrKmhigjGtIDFqz_5s2EFXn` (line 3)

---

## 2. Logic Chain
1. Since the `run_claude_process` function in `worker/claude_process.py` requires a keyword-only argument `team` (Observation 1.1), calling it from `worker/authoring.py` or unit tests without passing `team` triggers a `TypeError` at runtime.
2. Since `worker.publisher` no longer declares a module-level `RAW_SOURCES_DIR` variable (as it now uses team-specific configurations from `get_team_config`), unit tests that attempt to patch `worker.publisher.RAW_SOURCES_DIR` fail with `AttributeError` (Observation 1.1).
3. Since `QuestionJob` expects `team` as its third positional argument, unit tests constructing it without `team` fail with `TypeError` (Observation 1.1).
4. Because the `get_team_config` function constructs paths nested inside a team folder (e.g. `/tian_gong/raw/sources`), but the actual files are placed at `/raw/sources/tian_gong/` (Observation 1.2), the Worker's `FileManager` cannot locate, list, or delete the correct files, causing the file-management UI to appear empty.
5. Because the `WORKER_SHARED_SECRET` environment values do not match between ECS and Worker configurations (Observation 1.3), WebSocket handshakes and HTTP requests to download files will be rejected with an HTTP 401 Unauthorized or Connection closed status.

---

## 3. Caveats
- We did not connect to the live ECS container or attempt to run the WebSocket server dynamically, as our task is strictly read-only and scoped to investigation.
- We assumed the existing directory structure in `agent1/agent/raw/sources/tian_gong` is the target layout to preserve, as required by the user rule to not replace or delete live data.

---

## 4. Conclusion
The codebase is structurally complete and satisfies the architectural blueprints. However, it cannot be run or tested successfully in its current state due to:
1. Mismatched shared secret configuration in the `.env` files.
2. An architectural mismatch in team directory mappings inside `worker/config.py`.
3. Incomplete function/class calls lacking the `team` argument in `worker/authoring.py` and unit tests.
4. Outdated unittest mocking targeting `publisher.RAW_SOURCES_DIR`.

---

## 5. Verification Method

### 5.1 Project Verification Command
To verify if tests pass, run:
```bash
PYTHONPATH=/home/eason/Documents/agent_7_14/.venv-ecs/lib/python3.14/site-packages:/home/eason/Documents/agent_7_14/.venv-worker/lib/python3.14/site-packages python3 -m pytest
```

### 5.2 Invalidation Conditions
The conclusion is invalidated if:
- Ingestion or file management is intended to be run under completely segregated team base folders, and the files in `agent1/agent/raw/sources` are legacy/stale files that must be migrated.
