# Agent1 Knowledge Base

Agent1 provides a public, multilingual question-and-answer experience over
uploaded robotics documentation. The public web application is separated from a
private Worker that processes documents and retrieves approved knowledge-base
content.

## What it does

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
