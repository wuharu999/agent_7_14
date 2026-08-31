import json
import logging
import os
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


def _legacy_static_wiki_guide() -> str:
    """Deprecated historical guide retained only for source compatibility."""
    guide = """# Robotics Knowledge Base — System Guide (WIKI_GUIDE.md)

## Overview & Semantic Map

This guide provides a structured semantic map of the robotics knowledge base for runtime planning and reasoning.
The knowledge base covers humanoid robot hardware platforms, teleoperation & data collection systems,
software SDKs, dexterous hands, sensor peripherals, industrial solutions, and known uncertainty areas.

---

## 1. Major System Entrypoints & Solution Hierarchy

### Teleoperation & Data Collection (`entities/thinkerstudio.md`)
- **Primary Platform**: `ThinkerStudio` (遥操数采平台) is the complete end-to-end user-facing platform for humanoid teleoperation and multimodal data collection.
  - **Key Features**: Humanoid motion retargeting, VR/Pico body tracking, multi-channel camera data recording, trajectory quality inspection, and dataset export.
  - **Core Workflows**: `concepts/pico-body-tracking-teleoperation.md` describes whole-body teleoperation via `Pico Motion Tracker` and `XRoboToolkit`.
  - **Reasoning Policy**: For any "how to teleoperate / collect data" goal, always prefer `ThinkerStudio` (Level 0) and `pico-body-tracking-teleoperation` (Level 1) rather than reconstructing teleoperation from joint topics or low-level motor controllers.

### Robot Hardware Platforms (Level 0)
- **Walker S2 Series**:
  - `walker-s2-industrial.md`: Industrial-grade full-size humanoid robot platform designed for factory automation, inspection, and heavy-duty dexterous tasks. Features dual master control architecture, high-torque joint actuators, and 48V/60V power distribution.
  - `walker-s2-edu-explorer.md`: Educational and research version of Walker S2 (`Walker_S2_EDU探索者`), optimized for university labs, algorithm benchmarking, and open-source ROS2 secondary development.
- **Walker C1 Series**:
  - `walker-c1-edu.md` / `astron.md`: Lightweight humanoid bipedal platform (`Walker_C1_EDU共创者` / `Astron`), specialized for education, gait algorithm experiments, and motion capture integration.
- **Tiangong Walker Series (天工行者)**:
  - `tiangong-walker-dex.md`: Specialized dexterous manipulation platform (`天工行者DEX`), featuring high-degree-of-freedom dual dexterous hands and tactile sensors.
  - `tianxing-walker-series.md`: Standard humanoid platform (`天工行者无界&无疆`), covering TienKung 3.0, TienKung Pro, and TienKung Plus variants for whole-body dynamic locomotion.
- **Commercial & Educational Robots**:
  - `cruzr.md`: Wheeled service robot (`Cruzr`) for smart reception, navigation, and commercial interaction.
  - `cadebot.md`: Intelligent delivery robot (`CadeBot`) for restaurant and indoor transport.
  - `ugot.md`: Multi-morphology educational AI robotics kit (`UGOT`).
  - `yanshee.md`: Desktop humanoid AI educational robot (`Yanshee`).
  - `creabot.md`: Modular educational building block robot (`CreaBot`).

---

## 2. Software Development Kits & Subsystems (Level 2)

- **SDK Frameworks**:
  - `xrobotoolkit.md`: XR具身智能二次开发SDK, powering spatial tracking, teleoperation retargeting, and VR input bridging.
  - `tienkung-3-ros2-sdk.md` & `tienkung-pro-ros2-sdk.md`: ROS2-native SDKs for TienKung series, providing joint trajectory controllers, odometry publishers, and sensor message pipelines.
  - `walker-s2-ros2-sdk.md`: Full ROS2 secondary development SDK for Walker S2, exposing arm motion planning, gait control, and sensor streams.
  - `s2-api-tiny.md`: Lightweight standalone C++/Python API (`S2 API Tiny`) for embedded or high-frequency direct joint control without full ROS2 overhead.
  - `rosa-2.md`: Robot Operating System Architecture 2.0 (`ROSA 2.0`), UBTECH's core distributed middleware and system service manager.
- **Control & Perception Modules**:
  - `body-control-package.md` & `astron-motion-control.md`: Locomotion and balance control modules.
  - `manipulation-framework.md`: Dual-arm coordinated manipulation and inverse kinematics solver.
  - `proc_manager.md`: Process and lifecycle management daemon for onboard service nodes.
  - `tk-vslam.md`: Visual-inertial SLAM for indoor localization and mapping.
  - `tk-motionbuilder.md`: Motion retargeting and trajectory editing pipeline.

---

## 3. Dexterous Hands & Peripherals (Level 2)

- **Dexterous Hands Comparison**:
  - `inspire-hand.md` (`InspireRH5DG2-E4`): Under-actuated five-finger dexterous hand with micro-linear actuators, high payload-to-weight ratio, and position/current feedback.
  - `brainco-hand.md` (`Revo 2`): High-precision five-finger hand with tactile fingertip array sensors, supporting delicate tactile feedback and EMG gesture control.
  - `walker-s2-dexterous-hand.md`: UBTECH proprietary multi-DOF integrated hand for Walker S2.
- **Sensors & Compute Hardware**:
  - `livox-mid360.md`: Solid-state 3D LiDAR (360° FOV) for real-time point cloud generation and obstacle avoidance.
  - `orbbec-gemini-335l.md`: High-resolution RGB-D stereo camera for near-field manipulation and depth perception.
  - `nvidia-jetson-orin.md` & `jetson-agx-thor.md`: High-performance onboard AI edge computing platforms.
  - `ganfeng-battery.md`: High-capacity lithium battery module with integrated BMS monitoring and hot-swap support.

---

## 4. Industry Solutions & Services (Level 0 / 1 / 3)

- **Educational & Industrial Integration**:
  - `ubtech-embodied-intelligence-industry-college.md`: Comprehensive construction blueprint for Embodied Intelligence Industry Colleges (具身智能产业学院), integrating curriculum, training rigs, simulation centers, and industry certification.
  - `data-acquisition-center.md`: Multi-station humanoid data collection facility guidelines.
- **Support, Maintenance & Operations**:
  - `6s-service-center.md`: 6S Robot Service Center (6S服务中心) operational architecture: Sale, Spare Part, Service, Survey, Standard, Solution.
  - `concepts/emergency-stop.md` & `concepts/hot-swap-battery.md`: Standard safety, maintenance, and emergency protocols.
  - `concepts/warranty-policy.md`: Official repair, maintenance, and warranty terms.

---

## 5. Abstraction Hierarchy & Reasoning Rules

When planning answers, follow this abstraction precedence:

1. **Level 0 (Complete Solutions / Applications / Robots)**:
   - Always prefer complete platforms (`ThinkerStudio`, `Walker S2`, `Tianxing Walker`) when the user asks high-level "how do I accomplish X?" questions.
2. **Level 1 (Workflows & Capabilities)**:
   - Provide standard procedures (`pico-body-tracking-teleoperation`, `rl-training-pipeline`, `battery-boot-sequence`).
3. **Level 2 (SDKs & Modules & Hardware)**:
   - Introduce SDKs (`XRoboToolkit`, `Walker S2 ROS2 SDK`) and hardware options when implementation or architectural details are requested.
4. **Level 3 (APIs & Interfaces & Parameters)**:
   - Return specific topics (e.g. `/mc/leg/motion_ctrl`), parameters, or message definitions **only** when the user explicitly asks for ROS topics, code interfaces, or parameters.
5. **Level 4 (Raw Sources & Uncertain Queries)**:
   - Use source evidence to verify claims; surface known uncertainty from `queries/`.

---

## 6. Known Uncertainty Areas & Open Questions (`queries/`)

When answering questions touching these topics, explicitly surface the known uncertainty:
- **ThinkerStudio Compatibility**: `queries/does-thinkerstudio-support-all-tienkung-models.md` notes that documentation alternates between "天工行者", "天工无疆", and "天工行者无疆". Specific model compatibility should be stated with appropriate caveat.
- **Tiangong Walker DEX Brochure Encoding**: `queries/dex-brochure-encoding-issue.md` identifies potential text corruption in legacy PDF exports for DEX joint payload specs.
- **DEX vs TienKung 2.0 Pro Differences**: `queries/dex-vs-tienkung-2-differences.md` outlines unverified motor torque differences between DEX and standard Tianxing platforms.
- **Certification Bodies**: Dual certification standards (Ministry of Industry vs Vocational qualification) for robot service technicians.
- **Unanswered Topics**: Review `unanswered.md` for topics currently undergoing internal hardware validation.
"""
    return guide.strip() + "\n"


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
