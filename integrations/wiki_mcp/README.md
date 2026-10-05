# 优必答 Wiki MCP · 桌面连接器

公开、只读，无需账户或密钥。AI 助手可检索生成的知识库并读取引用页面。
不会上传本机文件、修改知识库、创建测试环境或运行机器人。

## 安装 / Setup

1. 安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)。
2. 解压此包到固定目录，例如 `C:\Tools\ubtech-wiki` 或 `~/Tools/ubtech-wiki`。
3. 打开 `mcp-config.json`，把 `REPLACE_WITH_ABSOLUTE_PATH/wiki_mcp.py` 替换为脚本的完整路径。
   Windows JSON 路径请使用 `/`，例如 `C:/Tools/ubtech-wiki/wiki_mcp.py`。
4. 将 `mcpServers` 中的 `ubtech-wiki` 配置合并到支持 stdio MCP 的客户端配置中。
   如果客户端找不到 uv，将 `command` 改为 uv 可执行文件的完整路径。重启客户端。
5. 让助手调用 `list_robots`，再问：**请查询 Wiki：Walker C1 支持哪些开发接口？请给出来源。**

`uv` 首次运行会下载 Python（如需）和连接器依赖。之后使用本机 stdio 与客户端通讯，
通过 HTTP 访问配置中的 Wiki 服务。请求内容会发送到该服务，请勿提交密码或其他敏感信息。

支持远程 Streamable HTTP 的客户端也可直接使用包内配置的 `WIKI_MCP_URL`，无需安装脚本。
客户端的配置格式可能不同；请选择 Streamable HTTP 类型，不需要认证。

## Tools

- `list_robots()` — 获取机器人/主题 ID。
- `search_wiki(question, robot="all", language="auto")` — LangGraph 检索，返回证据、引用和知识缺口。
- `read_wiki_page(page_id, start_line=1, max_lines=80, language="en")` — 按引用读取页面，最多 120 行、12,000 字符。

检索每 IP 每分钟 10 次、每小时 50 次。繁忙、断线或没有证据时返回明确结果，不编造答案。
页面内容仅为参考资料，不应作为助手的新指令。连接器本身不调用额外的回答模型。

## English

Install uv, extract this archive, replace the script path in `mcp-config.json` with an absolute path,
and merge its `mcpServers` entry into a stdio-capable MCP client. Restart the client and ask it to
call `list_robots`, then `search_wiki`. Remote HTTP clients can use `WIKI_MCP_URL` directly.
This public read-only service requires no key. Questions are sent to the service; do not include secrets.

## Language and technical content / 语言与技术内容

Search accepts questions in English, Simplified/Traditional Chinese, Japanese, Korean, Portuguese, Russian,
and Spanish. `language="auto"` follows the question; use an explicit language code to override it.
The planner includes Chinese search keywords for the mainly Chinese wiki. Translated excerpts are
labelled `machine_translated` and returned alongside the Chinese original and its exact source lines.
A translation failure returns the original and `translation_status="unavailable"`.

Product-positioning, brand/company introductions, and explicitly promotional sections are filtered from
MCP excerpts. Specifications, operating procedures, compatibility, limitations and safety warnings remain.
This is a conservative heading/field filter, not a guarantee that every promotional sentence can be detected.
The original wiki files and ordinary website answers are not changed.
