# Agent1 Knowledge Base

Agent1 provides a public, multilingual question-and-answer experience over
uploaded robotics documentation. The public web application is separated from a
private Worker that processes documents and retrieves approved knowledge-base
content.

## What it does

- Provides a public read-only Wiki MCP for desktop AI clients, with Chinese-aware retrieval and translated technical excerpts; see [MCP setup and deployment](docs/wiki-mcp.md).
- Lets authorized users upload and manage supported documentation.
- Builds a private knowledge base from those sources.
- Streams evidence-grounded answers in the selected language.
- Keeps recent browser conversation context and supports a fresh conversation.
- Supports authenticated editor and administrator workflows.
- Sends optional Chinese template alerts and weekly activity emails; see [email setup](docs/email-notifications.md).

## Development

Requirements are locked in `uv.lock`.

```bash
./scripts/uv_sync.sh dev
.venv-dev/bin/python -m pytest -q
```

To run the application locally, create machine-specific environment files from
the provided examples, then start the ECS and Worker in separate terminals:

```bash
cp ecs/.env.example ecs/.env
cp worker/.env.example worker/.env
./scripts/run_ecs.sh
./scripts/run_worker.sh
```

Do not commit environment files, uploaded documents, generated knowledge-base
data, databases, logs, or release archives. Configure secrets and deployment
settings only on the machines that need them.

## Email — ready to configure later

Email is optional and **disabled by default** (`EMAIL_ENABLED=false`). The
application runs without a mailbox or SMTP credentials. While disabled, it
does not queue or send notifications, and the weekly email task does not run.
Leave the email credential fields empty until you are ready.

The Chinese alert templates, weekly report, and delivery queue are implemented.
To activate them later:

1. Prepare a dedicated QQ mailbox and generate its SMTP authorization code.
2. Fill in `EMAIL_SMTP_USERNAME`, `EMAIL_SMTP_PASSWORD`, and `EMAIL_FROM` in
   the existing `ecs/.env`, using `ecs/.env.example` as a reference. Do not
   overwrite an existing environment file.
3. Set real recipient email addresses on the platform's active administrator
   accounts. These administrators receive the alerts and weekly report.
4. Follow the [Chinese setup guide](docs/email-notifications.md) to enable
   email, check configuration, send a test, and restart the ECS gateway.

You can preview the weekly template now without configuring or sending email:

```sh
.venv-dev/bin/python -m ecs.app.email_admin preview --output /tmp/robot-weekly-email.html
```

The preview uses example numbers. Weekly reports are scheduled for Monday
09:00 Beijing time and currently cover knowledge-portal activity.

## Layout

- `ecs/` — public FastAPI application, authentication, uploads, and status UI.
- `worker/` — private ingestion and LangGraph Wiki Q&A service; final answers stream to the browser.
- `browser_parse/` — Grill and log-analysis UI, API, Docker worker, and job runtime.
- `shared/` — shared models and validation helpers.
- `scripts/` — local setup, validation, and deployment utilities.
- `tests/` — automated regression coverage.

Operational deployment details and credentials are intentionally kept out of
this public README. Operators should refer to `AGENTS.md` for deployment
targets, configuration, and operational procedures.

Grill and log analysis are included in this checkout. See
[the cloud setup guide](docs/browser_tools.md#cloud-setup-from-this-repository)
for installing the API and building the worker image.

## Deployment layout

The portal, database, MCP endpoint and tools API use **120.77.250.227**. The wiki/QA
Worker and live wiki are intended to run there as a separate process after the
verified data migration. Grill/log-analysis **Docker jobs remain on separate Worker
machines**, polling the tools API on port 8080. The portal is on port 8000.
See [MCP deployment](docs/wiki-mcp.md#current-target-topology) for the data-migration
prerequisites; an older development wiki must not replace the current production data.
