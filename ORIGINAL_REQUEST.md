# Original User Request

## Initial Request — 2026-07-17T16:10:51+08:00

# Teamwork Project Prompt — Draft

> Status: Launched

Redesign the chat interface to support two layouts: a default horizontal web page, and a dedicated vertical route for WeCom users inspired by the FAQ platform reference. Additionally, add more robot learning topics to the wiki, implement folder collapse/expand functionality on the source manager page, display upload timestamps next to upload IDs, and streamline the supported file extensions text on the upload page by removing duplicates.

Working directory: /home/eason/Documents/agent_7_14
Integrity mode: demo

## Requirements

### R1. UI Layouts (Horizontal and Vertical)
Provide two variations of the asking page:
1. The default website route should use a horizontal layout (with the graph on the side).
2. Create a dedicated vertical layout route intended for WeCom users. Structurally, it should be modeled after the reference FAQ platform (https://eduiot.ubtrobot.com/v1/faq-platform/), but visually, it MUST have a highly premium, futuristic aesthetic inspired by the Walker C1 product page (https://www.ubtrobot.com/en/humanoid/products/walker-c1/).

### R2. Wiki Expansion
Add more robot learning topics to the local wiki knowledge base to match the topics presented in the graph.

### R2. Manage Sources Page Updates
On the `/manage` page, implement a collapsible/expandable folder tree so users can hide or reveal the contents of each upload. Additionally, append or display the upload timestamp alongside the upload folder ID to help users identify their uploads.

### R3. Upload Page Simplification
On the `/upload` page, resolve the duplicated supported file extensions text. Consolidate the "What files can I upload?" section to use direct file extensions (e.g., .pdf, .docx) and preserve the explanatory text for ZIP archives.

## Acceptance Criteria

### UI Layouts (Horizontal & Vertical)
- [ ] The default `/` route serves a horizontal layout with a side-by-side chat and interactive graph.
- [ ] A new dedicated route (e.g., `/wecom-ask`) serves a vertical chat layout.
- [ ] The vertical layout must look significantly more premium and futuristic than the basic FAQ platform, drawing specific visual and stylistic cues from the Walker C1 product page.

### Wiki Expansion
- [ ] New robot learning topics are added to the wiki repository or the node graph arrays are expanded further.

### Manage Sources Updates
- [ ] Upload folders in the UI render with `<details>`/`<summary>` tags or equivalent JS toggles to enable collapsing/expanding.
- [ ] The Python backend passes upload timestamps to the template, and they are rendered next to the Upload IDs on the `/manage` page.

### Upload Page Simplification
- [ ] The redundant text blocks listing document types (e.g., "文本和数据 MD, Markdown, TXT, CSV, JSON, HTML, XML, YAML") are removed from `upload.html`.
- [ ] The simplified extension list (e.g., `.pdf, .docx, .md, .txt`) is used instead.
- [ ] The explanation paragraph specifically for ZIP archives is retained verbatim.

## Follow-up — 2026-07-17T08:26:04Z

The user has clarified that the "Wiki Expansion" requirement ONLY applies to the frontend GUI graph. Please DO NOT create any actual markdown files or text files in the knowledge base/database for these topics. Please delete any markdown files you just created for these topics and focus exclusively on the UI changes (HTML/CSS) for the horizontal and vertical routes, and the Manage/Upload page updates.
