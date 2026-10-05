# Grill/log inclusion and unused-code cleanup

Implemented on 2026-10-05 after approval of the [unused-code audit](unused-code-audit-2026-10-05.md). Changes are local; no commit, push, cloud restart, or database migration was performed.

## Repository inclusion

`browser_parse/` is no longer ignored wholesale. Its 130 eligible source files include the log-analysis and Grill UI, FastAPI gateway, Docker worker, Dockerfile, dependency locks, proxy configuration, tests, and job runtime. Required `.agents/skills/` and `.codex/agents/` runtime assets are included. Credentials, databases, uploads, virtual environments, dependencies, build output, old release metadata, and release archives remain ignored.

No source symlinks or host `/tmp` source dependencies were found. Temporary job storage and the in-memory Grill test-store upload directory still legitimately use temporary directories. The runtime source is local to this project; dependencies and provider credentials still need setup on the destination host.

See [cloud setup](browser_tools.md#cloud-setup-from-this-repository). Navigation now uses the configured portal URL; when running tools without portal integration, unrelated portal links are omitted instead of sending users to the old server IP. Existing `.env` files are preserved by the setup command.

## Removed

| Area | Retired code | Replacement / retained behavior |
| --- | --- | --- |
| Worker QA | Old provider router, retrieval/page-expansion methods, old prompts and stream bridge | Active LangGraph retrieval and streaming; public-output filters and local page catalog retained |
| Wiki and knowledge helpers | Static guide, unused keyword/content gate, unused summary parser and client factory | Dynamic Wiki guide, active QA pipeline, unanswered-question logging |
| Gateway/database/schema helpers | Unused command wrapper, robot lookup, old QA schema aliases | Existing active routes, commands, and schemas |
| Tools | Unused allocation and Grill convenience wrappers, old JSON-message final-report parser | Current resource APIs, atomic follow-up creation, final output-file handling |
| Browser | Unbundled `brief.ts` and its dedicated tests | Current evidence submission and bounded context replay |
| Containers | Old Cube/QEMU `HostVM.Dockerfile` | Current Docker worker recipe |
| Dependencies | Root ECS `reportlab`; its now-orphaned `pillow` lock entry | No production import required either package; locked tools analysis dependencies are unchanged |

Production source decreased by **1,188 lines net**, measured against the pre-cleanup workspace snapshot, excluding tests, documentation, and lockfiles. This includes retired prompts/constants and differs from the audit's narrower callable-line estimate. Existing unrelated edits were preserved.

Tests specific to retired APIs were removed or moved to the active path. Retained coverage exercises LangGraph policy composition, streaming with a single executor thread, source-file preservation, and long structured reports read from the runner's final output file rather than debug messages.

## Retained / follow-up

- Email code and `users.email` remain for the planned provider integration. No live records or database schema were removed.
- Grill hibernation/resume remains pending integration review: its worker-side hibernate request has no matching gateway endpoint. Removing it requires a separate lifecycle decision.
- CLI wrappers, runtime agent assets, operational scripts, migrations, fallback implementations, and dynamically loaded components remain.
- Source-only root tests require explicit `ALLOWED_TEAMS=tian_gong,walker_s2,walker_c1`, matching both committed environment examples. An initial clean run without configuration exposed an existing mismatch between config and database bootstrap defaults (2 failures); setting the documented configuration resolves it.

## Validation

| Check | Result |
| --- | --- |
| Portal/Worker suite | 246 passed |
| Grill/log backend suite | 419 passed, plus 5 subtests |
| Browser frontend suite | 153 passed |
| TypeScript and production Vite build | Passed |
| Clean source-only export | Both Python suites pass with temporary databases and explicit documented robot configuration |
| Packaging/static checks | 146 Python files parse; shell scripts pass syntax checks; all Docker COPY inputs and required runtime agent assets are included |

The clean export contains only Git-tracked/non-ignored source, excluding local `.env`, installed dependencies, build output and databases. Python dependencies were supplied by the existing development environment. Tests use synthetic fixtures/mocks and temporary databases; they do not prove real model jobs or a cloud deployment. Actual Docker image construction and live cloud behavior were not tested. Dependency deprecation warnings remain.

Reproduce with an isolated data directory (substitute its absolute path):

```sh
ALLOWED_TEAMS=tian_gong,walker_s2,walker_c1 \
DATABASE_PATH=/tmp/agent714-check/portal.db DATA_ROOT=/tmp/agent714-check/data \
.venv-dev/bin/python -m pytest tests -q

cd browser_parse
PYTHONPATH=. ../.venv-dev/bin/python -m pytest tests_backend -q
npm test -- --run
npm run build
```

The execution sandbox caused even a minimal `asyncio.to_thread` check to hang. Python suites were therefore run with approved execution outside that sandbox, with temporary data paths.
