# Shared login for browser_parse

The knowledge portal and browser_parse run as separate services on the same
host. Existing `editor` and `admin` accounts can use `/log` and `/grill`.
Knowledge uploads remain at the portal's existing `/upload` route.

Gateway configuration in `ecs/.env`:

```dotenv
BROWSER_TOOLS_URL=http://120.77.250.227:8080
```

browser_parse configuration in its `.env`:

```dotenv
PORTAL_AUTH_DB=/root/agent_7_14/ecs-data/agent_jobs.db
PORTAL_BASE_URL=http://120.77.250.227:8000/v1/faq-platform
PORTAL_SESSION_COOKIE_NAME=agent1_session
PORT=8080
```

Use the same hostname for both applications so the portal's HttpOnly cookie
is available to both. Browser_parse opens the session database read-only,
checks account activity, role, and expiry on every protected request, and
requires the session's CSRF token for browser mutations. Logout or disabling
an account immediately invalidates access to both applications. No passwords
or session records are copied. If the configured database is unavailable,
protected requests fail closed.

The gateway's `/tools/log` and `/tools/grill` links are login return paths and
redirect only to the configured sibling application. Grill deep links survive
login. The tool UI links back to knowledge Q&A and account settings.

Both HTML pages and browser data APIs require login. `/api/worker/*` retains
the existing, independent Worker bearer-token authentication. Existing job
and Grill databases, upload directories, and Worker configuration are retained.
The existing shared workspace visibility and job/session editing tokens remain
in force inside the authenticated tools.

Deploy the gateway through `scripts/pull_and_restart_ecs.sh`. Back up the
browser_parse code, environment, databases (SQLite online backup), and uploads
before syncing its tracked source and built `dist/`; never copy development
`.env`, virtualenvs, local data, or uncommitted Worker changes. Sync locked
Python dependencies and restart only its API process using `scripts/run_ecs.sh`.
Verify anonymous page redirects and API 401 responses, editor/admin access,
CSRF-protected uploads, logout across applications, and the gateway Worker health.

## Cloud setup from this repository

The complete tools application is in `browser_parse/`, including `backend/`,
`src/`, locked dependencies, `sandbox/Dockerfile`, the job runtime and its agent
skills, and `deploy/docker-compose.worker.yml`. It is ordinary repository source;
no sibling checkout, release archive, symlink, or host `/tmp` directory is needed.
Container `/tmp` is temporary job storage created by Docker at runtime.

On the ECS host, from the repository root:

```sh
cd browser_parse
npm ci
npm run build
uv sync --locked
test -f .env || cp .env.example .env
chmod 600 .env
```

Configure `.env` with the shared-login values above and a new worker token.
Use the actual portal database path and public URLs for your cloud host.
The API and all tools workers must share the tools worker token; it is separate
from the knowledge-base Worker's `WORKER_SHARED_SECRET`.
Start the tools API with `bash scripts/run_ecs.sh` after configuration.
The tools use their own `data/` directory; existing credentials, databases,
uploads, virtual environments, and generated frontend files are not in Git.

On each Docker worker host, use this same checkout and work in `browser_parse/`:

```sh
uv sync --locked
docker build -f sandbox/Dockerfile -t robot-log-analysis:local .
docker image inspect robot-log-analysis:local --format '{{.Id}}'
```

Configure a worker environment with the actual tools API URL, matching tools
worker token, unique worker ID, provider credentials, and the immutable image ID
printed above as `ROBOT_DOCKER_IMAGE`. Configure `deploy/squid.conf` for your
provider, then start the proxy and worker:

```sh
docker compose -f deploy/docker-compose.worker.yml up -d
uv run --env-file .env python -m backend.worker
```

These are tools workers, separate from `worker/manager.py`. Keep the portal's
single knowledge-base Worker connection unchanged. Follow
[the Docker worker guide](../browser_parse/docs/docker-worker.md) for resource
limits, proxy verification, service setup, and runtime validation. Build context
must be `browser_parse/`, rather than the parent repository directory.
