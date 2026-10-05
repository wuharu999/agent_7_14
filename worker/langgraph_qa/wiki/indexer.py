import json
import logging
import re
import sqlite3
import jieba
from collections import Counter
from pathlib import Path
from typing import List, Dict, Any, Optional, Sequence

from worker.langgraph_qa.wiki.parser import parse_wiki_file

DICT_PATH = Path(__file__).resolve().parents[2] / "config" / "dict.txt"
log = logging.getLogger(__name__)


def init_jieba():
    """Initialize jieba with the custom robotics dictionary if available."""
    if DICT_PATH.exists():
        jieba.load_userdict(str(DICT_PATH))


def tokenize_text(text: Any) -> str:
    """Tokenize Chinese/English text into space-delimited tokens using jieba."""
    if not text:
        return ""
    text_str = str(text)
    if not text_str.strip():
        return ""
    tokens = list(jieba.cut(text_str, cut_all=False))
    return " ".join(t.strip() for t in tokens if t.strip())


def classify_image(img: Dict[str, Any], page_meta: Dict[str, Any]) -> Dict[str, str]:
    """
    Classify image into 10 standard types and 3 usefulness tiers.
    Types: architecture_diagram, workflow_diagram, UI_screenshot,
           configuration_screenshot, hardware_photo, chart,
           code_screenshot, terminal_screenshot, logo, decorative, unknown
    Usefulness: high, medium, low
    """
    alt = str(img.get("alt") or "")
    heading = str(img.get("heading") or "")
    context = str(img.get("context") or "")
    path = str(img.get("path") or "").lower()
    title = str(page_meta.get("title") or "")
    raw_tags = page_meta.get("tags") or []
    if isinstance(raw_tags, list):
        clean_tags = [str(t) for t in raw_tags if t is not None]
    elif isinstance(raw_tags, (str, int, float)):
        clean_tags = [str(raw_tags)]
    else:
        clean_tags = []
    tags_str = " ".join(clean_tags)

    full_text = f"{alt} {heading} {context} {tags_str} {title}".lower()

    # 1. Logo & Decorative
    if any(k in full_text for k in ["logo", "图标", "徽标", "brand", "icon", "banner", "水印"]) or "logo" in path:
        return {"image_type": "logo", "usefulness": "low"}
    if any(k in full_text for k in ["decorative", "装饰", "placeholder", "空白", "divider"]):
        return {"image_type": "decorative", "usefulness": "low"}

    # 2. Architecture Diagram
    if any(k in full_text for k in ["架构", "框图", "系统框图", "硬件框图", "网络拓扑", "分布式架构", "architecture", "system diagram", "block diagram", "topology"]):
        return {"image_type": "architecture_diagram", "usefulness": "high"}

    # 3. Workflow Diagram
    if any(k in full_text for k in ["流程", "时序", "链路", "启动流程", "交互流程", "pipeline", "workflow", "sequence", "state machine", "状态机", "flowchart"]):
        return {"image_type": "workflow_diagram", "usefulness": "high"}

    # 4. Configuration Screenshot
    if any(k in full_text for k in ["配置", "参数设置", "标定界面", "零点标定", "示教器配置", "config screenshot", "calibration setting"]):
        return {"image_type": "configuration_screenshot", "usefulness": "high" if alt else "medium"}

    # 5. UI Screenshot
    if any(k in full_text for k in ["界面", "ui", "web端", "客户端", "app界面", "示教器界面", "软件界面", "前端", "screenshot", "view"]):
        return {"image_type": "UI_screenshot", "usefulness": "high" if alt else "medium"}

    # 6. Chart
    if any(k in full_text for k in ["图表", "曲线", "对比图", "性能图", "chart", "plot", "benchmark", "curve"]):
        return {"image_type": "chart", "usefulness": "high" if alt else "medium"}

    # 7. Code / Terminal Screenshot
    if any(k in full_text for k in ["代码截图", "终端截图", "code screenshot", "terminal", "命令行"]):
        return {"image_type": "code_screenshot", "usefulness": "medium"}

    # 8. Hardware Photo / Coordinate Diagram
    if any(k in full_text for k in ["坐标系", "实物", "外观", "外观图", "接口图", "零部件", "传感器", "电机", "手模", "雷达", "相机", "电池", "photo", "hardware", "structure"]):
        is_high = any(k in full_text for k in ["坐标系", "接口图", "结构图", "示意图"]) or (bool(alt) and len(alt) > 15)
        return {"image_type": "hardware_photo", "usefulness": "high" if is_high else "medium"}

    # Fallback
    if alt or heading:
        return {"image_type": "hardware_photo", "usefulness": "medium"}
    return {"image_type": "unknown", "usefulness": "low"}


def infer_edge_relation(from_meta: Dict[str, Any], to_meta: Optional[Dict[str, Any]]) -> str:
    """Infer conservative semantic relation between two nodes."""
    if not to_meta:
        return "related_to"

    from_role = from_meta.get("document_role", "")
    to_role = to_meta.get("document_role", "")
    from_level = from_meta.get("abstraction_level", 1)
    to_level = to_meta.get("abstraction_level", 1)

    if from_role in ("application", "workflow") and to_role in ("sdk", "tool", "hardware"):
        return "uses"
    if from_role in ("module", "api", "interface", "configuration") and to_role in ("robot", "sdk"):
        return "part_of"
    if from_role == "sdk" and to_role in ("workflow", "capability"):
        return "implements"
    if from_role == "comparison" or to_role == "comparison":
        return "alternative_to"
    if from_level < to_level:
        return "higher_level_than"
    if from_level > to_level:
        return "lower_level_than"

    return "related_to"


def _catalog_for_guide(catalog_records: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Return deterministic records for guide generation.

    The no-argument form remains compatible with the historical entrypoint by
    parsing the repository's default Wiki. The Architect build path passes its
    already-parsed catalog so the guide and catalog are always consistent.
    """
    if catalog_records is not None:
        records = [record for record in catalog_records if isinstance(record, dict)]
    else:
        wiki_root = Path(__file__).resolve().parents[2] / "wiki_export"
        if not wiki_root.exists():
            return []
        records = [
            parse_wiki_file(path, wiki_root)
            for path in sorted(wiki_root.rglob("*.md"), key=lambda item: str(item.relative_to(wiki_root)))
        ]

    # Keep one canonical record per path. This also makes output stable if an
    # upstream parser accidentally supplies duplicate records.
    by_path: Dict[str, Dict[str, Any]] = {}
    for record in sorted(records, key=lambda item: str(item.get("path", ""))):
        path = str(record.get("path", "")).strip()
        if path and path.endswith(".md") and path not in by_path:
            by_path[path] = record
    return list(by_path.values())


def _guide_records(
    records: Sequence[Dict[str, Any]],
    roles: Sequence[str],
    limit: int,
    keywords: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Select deterministic representative records by role and keywords."""
    role_set = set(roles)
    candidates = [record for record in records if record.get("document_role") in role_set]
    words = tuple(word.lower() for word in keywords)

    def sort_key(record: Dict[str, Any]) -> tuple[int, str]:
        haystack = " ".join(
            [
                str(record.get("path", "")),
                str(record.get("title", "")),
                " ".join(str(item) for item in record.get("tags", []) or []),
                " ".join(str(item) for item in record.get("capabilities", []) or []),
            ]
        ).lower()
        keyword_rank = next((index for index, word in enumerate(words) if word in haystack), len(words))
        return keyword_rank, str(record.get("path", ""))

    return sorted(candidates, key=sort_key)[:limit]


def _guide_entry(record: Dict[str, Any]) -> str:
    """Render one catalog-backed guide entry."""
    path = str(record.get("path", ""))
    title = str(record.get("title") or Path(path).stem)
    role = str(record.get("document_role") or "reference")
    level = record.get("abstraction_level", "?")
    summary = re.sub(r"\s+", " ", str(record.get("summary") or "")).strip()
    if len(summary) > 80:
        summary = summary[:77].rstrip() + "..."
    suffix = f" — {summary}" if summary else ""
    return f"- `{path}` — {title} (role={role}, level={level}){suffix}"


def generate_wiki_guide(catalog_records: Optional[Sequence[Dict[str, Any]]] = None) -> str:
    """Generate a compact semantic map from the parsed Wiki catalog.

    Only paths present in ``catalog_records`` are emitted as backticked
    Markdown references, preventing stale hand-maintained links in planner
    context. The optional argument preserves the old no-argument API.
    """
    records = _catalog_for_guide(catalog_records)
    section_counts = Counter(str(record.get("wiki_section") or "root") for record in records)
    role_counts = Counter(str(record.get("document_role") or "reference") for record in records)
    capability_counts: Counter[str] = Counter()
    for record in records:
        capability_counts.update(str(item) for item in (record.get("capabilities") or []) if item)

    by_level = {
        0: _guide_records(
            records,
            ("application", "tool", "robot"),
            5,
            ("thinkerstudio", "walker-s2", "tianxing", "tiangong", "cruzr", "cadebot"),
        ),
        1: _guide_records(
            records,
            ("workflow", "capability", "comparison"),
            6,
            ("teleoperation", "data-collection", "industry-college", "solution", "walking"),
        ),
        2: _guide_records(
            records,
            ("sdk", "module", "hardware"),
            7,
            ("xrobotoolkit", "ros2-sdk", "inspire", "brainco", "vslam", "motion"),
        ),
        3: _guide_records(
            records,
            ("api", "interface", "configuration", "reference"),
            5,
            ("topic", "joint", "control", "safety", "warranty"),
        ),
    }
    uncertainty_records = _guide_records(records, ("unresolved_query",), 5)

    lines = [
        "# Robotics Knowledge Base — System Guide",
        "",
        "This guide is generated from the same parsed catalog as the local FTS5 index. "
        "It is a compact planning map, not a replacement for the source Markdown pages.",
        "",
        "## Corpus map",
        "",
        f"The current catalog contains **{len(records)}** Markdown pages. "
        f"Sections: {', '.join(f'{name} ({section_counts[name]})' for name in sorted(section_counts))}.",
        f"Document roles: {', '.join(f'{name} ({role_counts[name]})' for name in sorted(role_counts))}.",
        "Capabilities are inferred from frontmatter tags and deterministic keyword rules; "
        "they are retrieval hints, not unsupported claims.",
        "",
        "## Solution hierarchy",
        "",
        "For a general goal such as how to teleoperate, collect data, navigate, or deploy a "
        "robot, start at the highest-level catalog evidence that fully answers the goal. "
        "Use lower-level SDK, interface, and parameter pages as supporting evidence. "
        "When the user explicitly asks for a topic, API, command, or exact configuration, "
        "the corresponding level-3 evidence may be primary.",
        "Common solution areas include ThinkerStudio and XRoboToolkit for teleoperation, "
        "Walker S2 and Tiangong Walker platforms, Inspire dexterous hands, and 6S service "
        "operations; the entries below identify the catalog pages supporting those areas.",
        "",
    ]

    level_descriptions = {
        0: "Complete products, applications, tools, and robot platforms",
        1: "Workflows, capabilities, and comparison/decision knowledge",
        2: "SDKs, modules, hardware, sensors, and supporting subsystems",
        3: "APIs, ROS topics, interfaces, configuration, and reference pages",
    }
    for level in range(4):
        lines.extend([f"### Level {level} — {level_descriptions[level]}", ""])
        if by_level[level]:
            lines.extend(_guide_entry(record) for record in by_level[level])
        else:
            lines.append("- No catalog records currently match this level.")
        lines.append("")

    lines.extend(
        [
            "## Capability signals",
            "",
            "The most common deterministic capability signals are "
            + ", ".join(f"**{name}** ({count})" for name, count in capability_counts.most_common(16))
            + ".",
            "Use these signals to broaden local search queries, then verify the answer against "
            "the selected full pages. Related links are a one-hop expansion hint and should not "
            "be treated as proof by themselves.",
            "",
            "## Known uncertainty",
            "",
            "Pages in the queries section and the root unanswered record represent unresolved or "
            "partially supported information. Surface those caveats when they bear on the "
            "user's question; do not turn them into confirmed facts.",
        ]
    )
    if uncertainty_records:
        lines.append("")
        lines.extend(_guide_entry(record) for record in uncertainty_records)
    else:
        lines.append("- No unresolved-query pages are present in this catalog.")

    lines.extend(
        [
            "",
            "## Image guidance",
            "",
            "Images are optional. Select a cataloged image only when its description and "
            "nearby page context materially support a specific answer claim, such as an "
            "architecture diagram, workflow, or configuration screen. Logos and decorative "
            "media should normally be omitted; zero images is a valid result.",
            "",
        ]
    )
    return "\n".join(lines)


def build_catalog_and_index(wiki_root: Path, output_dir: Path):
    """Build all _generated artifacts from the wiki export folder."""
    output_dir.mkdir(parents=True, exist_ok=True)
    init_jieba()

    db_path = output_dir / "search.db"
    catalog_path = output_dir / "wiki_catalog.jsonl"
    image_catalog_path = output_dir / "image_catalog.jsonl"
    related_graph_path = output_dir / "related_graph.json"
    wiki_guide_path = output_dir / "WIKI_GUIDE.md"

    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE wiki_pages (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            title TEXT,
            wiki_section TEXT,
            document_role TEXT,
            abstraction_level INTEGER,
            metadata_json TEXT
        );
    """)

    cursor.execute("""
        CREATE VIRTUAL TABLE wiki_fts USING fts5(
            search_title,
            search_aliases,
            search_tags,
            search_headings,
            search_body,
            path UNINDEXED
        );
    """)

    resolved_root = wiki_root.resolve(strict=True)
    wiki_files = []
    for candidate in wiki_root.rglob("*.md"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            candidate.resolve(strict=True).relative_to(resolved_root)
        except (FileNotFoundError, OSError, ValueError):
            continue
        wiki_files.append(candidate)
    wiki_files.sort(key=lambda path: path.relative_to(wiki_root).as_posix())
    catalog_records = []
    image_records = []

    # 1. Parse all wiki files
    for fpath in wiki_files:
        parsed = parse_wiki_file(fpath, wiki_root)
        catalog_records.append(parsed)

    # 2. Build stem lookup mapping for 100% edge resolution
    stem_to_path = {Path(p["path"]).stem: p["path"] for p in catalog_records}
    path_to_record = {p["path"]: p for p in catalog_records}
    path_set = set(path_to_record.keys())

    # 3. Populate SQLite DB and generate related edges
    related_graph = {"edges": []}

    for parsed in catalog_records:
        # Build FTS segmented search fields
        s_title = tokenize_text(parsed["title"])
        s_aliases = tokenize_text(" ".join(parsed["aliases"]))
        s_tags = tokenize_text(" ".join(parsed["tags"]))
        s_headings = tokenize_text(" ".join(parsed["headings"]))
        s_body = tokenize_text(parsed["body"])

        # Insert unsegmented metadata into wiki_pages
        cursor.execute(
            """
            INSERT INTO wiki_pages (path, title, wiki_section, document_role, abstraction_level, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                parsed["path"],
                parsed["title"],
                parsed["wiki_section"],
                parsed["document_role"],
                parsed["abstraction_level"],
                json.dumps(parsed, ensure_ascii=False),
            ),
        )

        # Insert segmented text into wiki_fts
        cursor.execute(
            """
            INSERT INTO wiki_fts (search_title, search_aliases, search_tags, search_headings, search_body, path)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (s_title, s_aliases, s_tags, s_headings, s_body, parsed["path"]),
        )

        # Related graph edges with resolved canonical paths and conservative semantic relations
        for rel in parsed.get("related", []):
            if not isinstance(rel, (str, int, float)):
                continue
            rel_str = str(rel)
            target_path = rel_str
            if rel_str in path_set:
                target_path = rel_str
            elif rel_str in stem_to_path:
                target_path = stem_to_path[rel_str]

            to_meta = path_to_record.get(target_path)
            relation = infer_edge_relation(parsed, to_meta)

            related_graph["edges"].append({
                "from": parsed["path"],
                "to": target_path,
                "relation": relation
            })

        # Image records with 10 image types and 3 usefulness tiers
        for img in parsed.get("media", []):
            if not isinstance(img, dict):
                continue
            classification = classify_image(img, parsed)
            desc = img.get("alt") or img.get("heading") or f"Image in {parsed.get('title', 'Page')}"
            image_records.append({
                "path": str(img.get("path") or ""),
                "source_page": parsed["path"],
                "description": str(desc),
                "image_type": classification["image_type"],
                "topics": parsed.get("tags", []),
                "related_entities": parsed.get("aliases", []),
                "usefulness": classification["usefulness"],
                "context": str(img.get("context") or ""),
            })

    conn.commit()
    conn.close()

    # Write wiki_catalog.jsonl
    with open(catalog_path, "w", encoding="utf-8") as f:
        for rec in catalog_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Write image_catalog.jsonl
    with open(image_catalog_path, "w", encoding="utf-8") as f:
        for img in image_records:
            f.write(json.dumps(img, ensure_ascii=False) + "\n")

    # Write related_graph.json
    with open(related_graph_path, "w", encoding="utf-8") as f:
        json.dump(related_graph, f, ensure_ascii=False, indent=2)

    # Write dynamic WIKI_GUIDE.md
    guide_content = generate_wiki_guide(catalog_records)
    with open(wiki_guide_path, "w", encoding="utf-8") as f:
        f.write(guide_content)

    log.info(
        "Built LangGraph Wiki artifacts output=%s pages=%d images=%d edges=%d guide_bytes=%d",
        output_dir,
        len(catalog_records),
        len(image_records),
        len(related_graph["edges"]),
        len(guide_content.encode("utf-8")),
    )
