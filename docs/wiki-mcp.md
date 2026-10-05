# Public Wiki MCP

The desktop Q&A sidebar links to `/wiki-mcp`, an English/Chinese setup page with
an anonymous connector ZIP download. That sidebar link is hidden at widths of
900 px and below. The setup page itself remains usable on smaller screens.

## Tools and deployment

The official MCP Python SDK serves Streamable HTTP at `/mcp/`, underneath
`ROOT_PATH` when configured. For the current ECS deployment the endpoint is:

```text
http://120.77.250.227:8000/v1/faq-platform/mcp/
```

Use HTTPS at the reverse proxy before configuring clients that require HTTPS.
The downloaded stdio connector can also connect to the current HTTP endpoint;
requests sent over HTTP are unencrypted. No account or API key is required.

- `list_robots()` lists the current enabled Q&A topic IDs.
- `search_wiki(question, robot="all", language="auto")` reuses LangGraph planning,
  FTS retrieval, related-page expansion, and evidence selection. It does not run
  a second answer-generation step. The calling AI receives evidence and gaps.
- `read_wiki_page(page_id, start_line=1, max_lines=80, language="en")` reads a
  bounded excerpt from a generated wiki Markdown page.

Public access was explicitly selected for the generated wiki. This endpoint does
not provide raw uploads, user accounts, conversation history, logs, shell access,
file writes or robot control. Hidden paths, traversal, symlinks, non-Markdown
files, private query/audit directories, and oversized pages are rejected. Future
private documents must not be placed in the published wiki tree.

ECS uses its existing authenticated Worker WebSocket to perform these operations.
The Worker advertises `wiki_mcp` capability on connection. Until an updated Worker
with a wiki directory is connected, the setup page reports unavailable/preparing
and tool calls return a clear error immediately; they do not silently query an old
local wiki copy. Existing web Q&A remains independent.

Both the ECS and the QA Worker checkouts must be updated. Run
`scripts/uv_sync.sh ecs` and `scripts/uv_sync.sh worker` on the corresponding host,
then restart each service during a quiet period. Do not start a second QA Worker
while the previous one remains connected.

## Language and technical-content behavior

For non-Chinese questions the planner includes Chinese keyword translations
alongside API and product names, because most source pages are Chinese. Supported
output languages: `en`, `zh-CN`, `zh-TW`, `ja`, `ko`, `pt`, `ru`, `es`.
`search_wiki` defaults to the question's language; page reads default to English.

MCP excerpts blank explicitly promotional headings and labelled fields, including
产品定位, 市场定位, brand/company introductions and sales pitches. YAML frontmatter
is not returned. This conservative filter avoids generic headings such as features
and applications because they can contain technical information. It is not a
semantic guarantee that every promotional sentence is removed. The wiki files and
ordinary Q&A output are not rewritten. The filter lives in `worker/wiki_content.py`.

Original line numbers are retained, including gaps. Results include `citation`,
`line_numbers`, `next_line`, `truncated`, and any removed section headings. Translated
excerpts are separately labelled `machine_translated`; the original text and
citations remain authoritative. Failed/malformed translation returns the original
with `translation_status="unavailable"`. This does add a bounded model translation
call for non-Chinese excerpts; missing evidence never becomes a capability claim.

## Resource limits

Questions: 2,000 characters; page reads: at most 120 source lines / 12,000 characters;
search: at most six excerpts of 4,000 characters each. HTTP request bodies are
limited to 16 KiB. Each IP receives 60 protocol requests per minute / 300 per hour,
and 10 search/translated-read calls per minute / 50 per hour. Rate limits are
in-memory, reset on restart, and assume one ECS Uvicorn process. ECS admits at most
two outstanding wiki commands; the Worker has one MCP lane and an eight-item queue.
The existing web-Q&A lanes remain separate. Tool logs omit questions and page text.
Configure reverse-proxy forwarding only for trusted proxy IPs; never trust arbitrary
client `X-Forwarded-For` headers for rate limiting.

## Current target topology

- **120.77.250.227:** portal/database/MCP on port 8000; tools API/frontend on 8080;
  destination for the wiki/QA Worker and live wiki project.
- **Separate Worker machine(s):** Docker execution for Grill and log analysis,
  polling `http://120.77.250.227:8080`. Docker does not move onto ECS.

The wiki/QA Worker relocation requires an accessible current wiki project or
verified backup. Preserve `wiki/`, `raw/`, `.llm-wiki/` queues/cache/configuration,
and Worker configuration; do not substitute the old development wiki. Transfer
secrets privately, update absolute paths, set the QA connection to local ECS,
keep `LLM_WIKI_RESCAN_AFTER_PUBLISH=false`, and verify source counts, file hashes,
health and a grounded query before retiring the old Worker. The separate Wiki
ingestion service must move with its state or be explicitly configured to reach
the migrated source tree. Generated data and secrets are not pushed to GitHub.

## Protocol references

- [Official Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
- [MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

## Verification

`tests/test_wiki_mcp.py` covers real MCP initialization/list/call and errors, request
limits, ZIP contents and prefixed URLs, source boundaries, filtering, citations,
translation fallback, and English-query retrieval against a Chinese fixture wiki.
Model outputs in these tests are deterministic fixtures; they do not establish
translation accuracy on every production document or prove live Worker migration.
