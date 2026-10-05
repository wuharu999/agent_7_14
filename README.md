# Agent1 Knowledge Base

[English](#english) | [简体中文](#简体中文)

## English

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

## 简体中文

Agent1 基于上传的机器人技术文档，提供面向公众的多语言知识问答服务。
公共 Web 应用与私有 Worker 分开运行，由 Worker 处理文档并检索允许访问的知识库内容。

### 主要功能

- 为桌面 AI 客户端提供公开、只读的 Wiki MCP，支持中文知识检索和技术摘录翻译；详见 [MCP 配置与部署](docs/wiki-mcp.md)。
- 允许授权用户上传和管理支持格式的文档。
- 根据上传的资料构建私有知识库。
- 使用所选语言，流式输出有资料依据的回答。
- 保留浏览器中的近期对话上下文，并支持新建对话。
- 支持编辑者和管理员登录后的管理操作。
- 可选启用中文模板告警邮件和每周活动报告；详见[邮件配置指南](docs/email-notifications.md)。

### 本地开发

依赖版本锁定在 `uv.lock` 中。

```bash
./scripts/uv_sync.sh dev
.venv-dev/bin/python -m pytest -q
```

首次运行时，根据示例创建各机器的环境配置文件，并填写对应设置。已有配置时不要覆盖：

```bash
cp ecs/.env.example ecs/.env
cp worker/.env.example worker/.env
```

然后在两个独立终端中分别启动 ECS 服务和 Worker：

```bash
./scripts/run_ecs.sh
```

```bash
./scripts/run_worker.sh
```

不要提交环境配置文件、上传的文档、生成的知识库数据、数据库、日志或发布压缩包。
密钥和部署设置只应保存在需要它们的机器上。

### 邮件功能：已准备好，可稍后配置

邮件功能为可选项，**默认关闭**（`EMAIL_ENABLED=false`）。不配置邮箱或 SMTP 凭据，
应用仍可正常运行。关闭时不会将通知加入发送队列，也不会发送邮件或运行每周邮件任务。
准备启用之前，请将邮件凭据字段留空。

中文告警模板、每周报告和发送队列已实现。后续启用步骤：

1. 准备一个专用 QQ 邮箱，并生成 SMTP 授权码。
2. 参考 `ecs/.env.example`，在现有 `ecs/.env` 中填写 `EMAIL_SMTP_USERNAME`、
   `EMAIL_SMTP_PASSWORD` 和 `EMAIL_FROM`，不要覆盖已有环境配置文件。
3. 为平台中处于启用状态的管理员账号填写真实收件邮箱。这些管理员会收到告警和每周报告。
4. 按照[中文邮件配置指南](docs/email-notifications.md)启用邮件、检查配置、发送测试邮件并重启 ECS 网关。

现在即可预览周报模板，无需配置邮箱，也不会发送邮件：

```sh
.venv-dev/bin/python -m ecs.app.email_admin preview --output /tmp/robot-weekly-email.html
```

预览使用示例数据。周报计划在北京时间每周一 09:00 发送，目前统计知识门户的用户活动。

### 项目目录

- `ecs/`：公共 FastAPI 应用、身份认证、文件上传和状态页面。
- `worker/`：私有文档处理服务和 LangGraph Wiki 问答服务，将最终回答流式传输到浏览器。
- `browser_parse/`：Grill 和日志分析的界面、API、Docker Worker 及任务运行代码。
- `shared/`：共享数据模型和校验工具。
- `scripts/`：本地配置、验证和部署脚本。
- `tests/`：自动化回归测试。

此公开 README 不包含部署凭据和详细运维步骤。运维人员可查阅 `AGENTS.md`，
了解部署目标、配置要求和操作流程。

Grill 和日志分析代码已包含在本仓库中。安装 API 和构建 Worker 镜像的方法见
[云端配置指南](docs/browser_tools.md#cloud-setup-from-this-repository)。

### 部署结构

门户、数据库、MCP 接口和工具 API 使用 **120.77.250.227**。Wiki/QA Worker 和线上 Wiki
计划在完成并验证数据迁移后，以独立进程运行在该 ECS 上。
Grill 和日志分析的 **Docker 任务仍在独立 Worker 机器上运行**，通过轮询 ECS 的
8080 端口访问工具 API。门户使用 8000 端口。

数据迁移的前置条件见 [MCP 部署说明](docs/wiki-mcp.md#current-target-topology)。
不得用较旧的开发环境 Wiki 覆盖当前生产数据。
