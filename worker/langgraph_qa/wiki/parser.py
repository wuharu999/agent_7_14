import os
import re
import yaml
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

# Map common terms to rich bilingual aliases
ENTITY_ALIAS_MAP: Dict[str, List[str]] = {
    "thinkerstudio": [
        "ThinkerStudio",
        "ThinkerStudio 遥操数采平台",
        "ThinkerStudio teleoperation platform",
        "遥操平台",
        "数采平台",
        "遥操作数采平台",
        "Thinker Studio",
    ],
    "xrobotoolkit": [
        "XRoboToolkit",
        "XRoboToolkit SDK",
        "XR 具身智能 SDK",
        "XR具身智能SDK",
        "X-Robo-Toolkit",
    ],
    "walker-s2-industrial": [
        "Walker S2 Industrial",
        "Walker S2 工业人形机器人",
        "Walker S2 工业版",
        "Walker S2",
        "Walker_S2",
        "WalkerS2",
        "Walker-S2-Industrial",
    ],
    "walker-s2-edu-explorer": [
        "Walker S2 EDU",
        "Walker_S2_EDU探索者",
        "Walker S2 EDU 探索者",
        "Walker S2 教育版",
        "Walker S2 探索者",
        "Walker S2 Explorer",
        "Walker_S2_EDU",
    ],
    "walker-c1-edu": [
        "Walker C1",
        "Walker_C1_EDU共创者",
        "Walker C1 EDU",
        "Walker C1 共创者",
        "Walker C1 教育版",
        "Walker_C1",
        "Walker_C1_EDU",
        "Astron",
        "Walker C1 (Astron)",
    ],
    "tiangong-walker-dex": [
        "天工行者DEX",
        "Tiangong Walker DEX",
        "DEX 灵巧手机器人",
        "天工行者 DEX",
        "Tiangong DEX",
        "TienKung DEX",
        "TienKung-DEX",
        "Tiangong-DEX",
    ],
    "tianxing-walker-series": [
        "天工行者无界&无疆",
        "Tianxing Walker Series",
        "天工行者",
        "天工行者无界",
        "天工行者无疆",
        "天工无界",
        "天工无疆",
        "天工系列",
        "TienKung",
        "Tianxing",
    ],
    "pico-motion-tracker": [
        "Pico Motion Tracker",
        "Pico体感追踪器",
        "Pico体感遥操作",
        "Pico teleoperation",
        "Pico遥操",
        "Pico Motion",
        "Pico体感",
        "Pico 追踪器",
    ],
    "inspire-hand": [
        "Inspire Hand",
        "因时灵巧手",
        "InspireRH5DG2",
        "因时五指灵巧手",
        "Inspire-Hand",
        "InspireHand",
    ],
    "brainco-hand": [
        "BrainCo Hand",
        "强脑灵巧手",
        "Revo 2",
        "BrainCo Revo 2",
        "强脑五指灵巧手",
        "BrainCo",
    ],
    "astron": [
        "Astron",
        "阿童木",
        "Astron 双足机器人",
        "Walker C1 (Astron)",
        "Astron 机器人",
    ],
    "cruzr": [
        "Cruzr",
        "克鲁泽",
        "Cruzr 轮式服务机器人",
        "Cruzr 机器人",
    ],
    "cadebot": [
        "CadeBot",
        "凯迪宝",
        "CadeBot 配送机器人",
        "CadeBot 机器人",
    ],
    "ugot": [
        "UGOT",
        "UGOT 机器人",
        "多形态教育机器人",
        "UGOT 多形态机器人",
    ],
    "yanshee": [
        "Yanshee",
        "偃师",
        "Yanshee 教育机器人",
        "Yanshee 机器人",
    ],
    "creabot": [
        "CreaBot",
        "积木机器人",
        "CreaBot 创想机器人",
    ],
    "tk-motionbuilder": [
        "MotionBuilder",
        "TK-MotionBuilder",
        "动作重定向工具",
        "动作捕捉与编辑",
    ],
    "tk-vslam": [
        "vSLAM",
        "TK-vSLAM",
        "视觉SLAM系统",
        "定位建图系统",
    ],
    "ubtech-embodied-intelligence-industry-college": [
        "具身智能产业学院",
        "产业学院建设方案",
        "产教融合方案",
        "具身智能产教融合",
        "产业学院",
    ],
    "6s-service-center": [
        "6S服务中心",
        "6S运维中心",
        "机器人6S服务",
        "6S服务",
    ],
    "walker-s2-dexterous-hand": [
        "Walker S2 灵巧手",
        "Walker S2 自研灵巧手",
        "优必选灵巧手",
    ],
    "inspire-rh5dg2-e4": [
        "Inspire RH5DG2-E4",
        "因时五指灵巧手 E4",
        "因时灵巧手",
    ],
    "revo-2-tactile": [
        "Revo 2 触觉灵巧手",
        "BrainCo Revo 2 Tactile",
        "强脑触觉灵巧手",
    ],
    "livox-mid360": [
        "Livox Mid-360",
        "览沃激光雷达",
        "Livox LiDAR",
        "Mid360",
    ],
    "orbbec-gemini-335l": [
        "Orbbec Gemini 335L",
        "奥比中光深度相机",
        "Gemini 335L",
        "RGB-D相机",
    ],
    "nvidia-jetson-orin": [
        "NVIDIA Jetson AGX Orin",
        "Jetson Orin",
        "Orin 算力板",
    ],
    "jetson-agx-thor": [
        "NVIDIA Jetson AGX Thor",
        "Jetson Thor",
        "Thor 算力平台",
    ],
    "ganfeng-battery": [
        "赣锋锂电池",
        "Ganfeng Battery",
        "Walker S2 动力电池",
    ],
    "tienkung-3-ros2-sdk": [
        "TienKung 3 ROS2 SDK",
        "天工3 ROS2 SDK",
        "TienKung-3-ROS2-SDK",
    ],
    "tienkung-pro-ros2-sdk": [
        "TienKung Pro ROS2 SDK",
        "天工Pro ROS2 SDK",
    ],
    "walker-s2-ros2-sdk": [
        "Walker S2 ROS2 SDK",
        "Walker S2 二次开发SDK",
    ],
    "s2-api-tiny": [
        "S2 API Tiny",
        "Walker S2 Tiny SDK",
        "轻量API",
    ],
    "rosa-2": [
        "ROSA 2.0",
        "ROSA",
        "优必选机器人操作系统",
    ],
    "manipulation-framework": [
        "Manipulation Framework",
        "双臂操作框架",
        "操作控制框架",
    ],
}


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content with syntax glitch pre-cleaning."""
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        yaml_text = match.group(1)
        body = match.group(2)
        # Pre-clean YAML anomalies like "]tags:" or trailing concatenated keys
        cleaned_yaml = re.sub(r'(\])([a-zA-Z_]+:)', r'\1\n\2', yaml_text)
        cleaned_yaml = re.sub(r'([\"\'\d])([a-zA-Z_]+:)', r'\1\n\2', cleaned_yaml)
        try:
            meta = yaml.safe_load(cleaned_yaml) or {}
            if isinstance(meta, dict):
                return meta, body
        except Exception:
            pass

        # Fallback regex extraction if yaml.safe_load fails
        fallback_meta: Dict[str, Any] = {}
        for line in yaml_text.splitlines():
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip().strip('"\'')
                if k and v:
                    fallback_meta[k] = v
        return fallback_meta, body
    return {}, content


def infer_document_role_and_level(rel_path: str, meta: Dict[str, Any], body: str) -> Tuple[str, int]:
    """
    Infer document role and abstraction level accurately.
    Roles (14): application, tool, workflow, capability, robot, hardware, sdk,
                module, api, interface, configuration, comparison, unresolved_query,
                source, reference
    Levels (0-4):
      0: Complete product / application / user-facing solution / robot platform
      1: Workflow / high-level capability / decision knowledge
      2: SDK / subsystem / module / hardware component
      3: API / topic / interface / configuration / parameter / reference / policy
      4: Raw source / unresolved query
    """
    path_obj = Path(rel_path)
    stem = path_obj.stem.lower()
    section = path_obj.parts[0].lower() if len(path_obj.parts) > 1 else "root"
    raw_tags = meta.get("tags")
    if raw_tags is None:
        tags = []
    elif isinstance(raw_tags, list):
        tags = [str(t).lower() for t in raw_tags if t is not None and isinstance(t, (str, int, float, bool))]
    elif isinstance(raw_tags, (str, int, float, bool)):
        tags = [str(raw_tags).lower()]
    else:
        tags = []
    meta_type = str(meta.get("type", "")).lower()

    # 1. Direct section rules
    if section == "queries" or stem == "unanswered":
        return "unresolved_query", 4
    if section == "sources":
        return "source", 4
    if section == "comparisons" or stem.startswith("comparison") or "comparison" in tags or meta_type == "comparison":
        return "comparison", 1
    if section == "root":
        return "reference", 3

    # 2. Configuration & Parameter pages (Level 3)
    if any(k in stem or any(k in t for t in tags) for k in [
        "param", "parameter", "config", "calibration", "coordinate", "zero-point",
        "joint-parameter", "imu-interface-param", "pose-parameters", "control-parameters", "setting"
    ]):
        return "configuration", 3

    # 3. Administrative / Policy / Warranty / Standard / Certification / Service pages (Level 3)
    if any(k in stem or any(k in t for t in tags) for k in [
        "warranty", "policy", "guarantee", "states", "emergency-stop", "certification",
        "standard", "qualification", "6s-service", "service-standard", "faq", "troubleshooting",
        "logistics", "maintenance", "aftersale"
    ]):
        return "reference", 3

    # 4. Low-level APIs, ROS2 Topics, Messages, Services, Interfaces (Level 3)
    if any(k in stem or any(k in t for t in tags) for k in [
        "interface", "interfaces", "msg", "srv", "cmd", "topic", "api", "ctrl",
        "status", "sdk-demo", "mc-", "hric-", "sensor-msgs", "std-msgs",
        "geometry-msgs", "trajectory-msgs", "protocol"
    ]):
        if any(k in stem or any(k in t for t in tags) for k in ["interface", "topic", "msg", "srv", "cmd"]):
            return "interface", 3
        return "api", 3

    # 5. User-Facing Applications, Software Platforms, and Tools (Level 0)
    if any(k in stem for k in [
        "thinkerstudio", "motionbuilder", "tk-motionbuilder", "tk-vslam",
        "huisikaiwu", "ju-shen-tian-gong-app", "teleoperation-platform"
    ]) or any(t in ["application", "app", "platform", "studio", "gui", "software-platform", "工具软件", "软件平台", "生态工具"] for t in tags):
        return "application", 0

    if (
        stem in ["tool", "tools", "calibrator", "debugger", "teaching-pendant", "示教器"]
        or "tool" in tags
        or any(k in stem for k in ["calibrator", "debugger", "teaching-pendant", "示教器"])
    ) and not any(k in stem or any(k in t for t in tags) for k in ["toolkit", "sdk"]):
        return "tool", 0

    # 6. Modules & Subsystems (Level 2)
    if any(k in stem or any(k in t for t in tags) for k in [
        "module", "proc_manager", "proc-manager", "lyre", "motion-control",
        "manipulation-framework", "vision-perception", "slam-package", "battery-management",
        "battery-driver", "teleop-module", "retargeting-module", "chassis-driver",
        "dual-master", "three-master", "node", "service-package"
    ]):
        return "module", 2

    # 7. SDKs & Drivers & Frameworks (Level 2)
    if any(k in stem or any(k in t for t in tags) for k in [
        "sdk", "driver", "framework", "toolkit", "xrobotoolkit", "rosa", "tiny-api", "s2-api-tiny"
    ]):
        return "sdk", 2

    # 8. Hardware, Sensors, Peripherals, Dexterous Hands, Batteries, Compute Boards (Level 2)
    if any(k in stem or any(k in t for t in tags) for k in [
        "hardware", "sensor", "lidar", "camera", "battery", "orin", "thor", "jetson",
        "livox", "orbbec", "gemini", "mid360", "revo", "rh5dg2", "inspire-hand",
        "brainco-hand", "dexterous-hand", "ganfeng", "actuator", "motor", "pico-motion-tracker", "hand"
    ]):
        return "hardware", 2

    # 9. Robot Hardware Platforms (Complete Systems - Level 0)
    if stem in [
        "walker-s2-industrial", "walker-s2-edu-explorer", "walker-c1-edu",
        "tiangong-walker-dex", "tianxing-walker-series", "astron", "cruzr",
        "cadebot", "ugot", "yanshee", "creabot", "walker-s2", "walker-c1", "ubtech"
    ] or any(t in ["humanoid-robot", "service-robot", "education-robot", "delivery-robot", "人形机器人", "机器人"] for t in tags):
        return "robot", 0

    # 10. Concepts Section Defaults (Level 1)
    if section == "concepts":
        if any(k in stem or any(k in t for t in tags) for k in [
            "workflow", "pipeline", "procedure", "teleoperation", "training",
            "deployment", "boot", "startup", "hot-swap", "solution", "guide",
            "process", "step", "retargeting", "data-collection"
        ]):
            return "workflow", 1
        return "capability", 1

    return "capability", 1


def extract_capabilities(meta: Dict[str, Any], body: str, filename_stem: str) -> List[str]:
    """Extract semantic capabilities from metadata tags and body text."""
    capabilities = set()
    raw_tags = meta.get("tags")
    if raw_tags is None:
        tag_list = []
    elif isinstance(raw_tags, list):
        tag_list = [str(t) for t in raw_tags if t is not None and isinstance(t, (str, int, float, bool))]
    elif isinstance(raw_tags, (str, int, float, bool)):
        tag_list = [str(raw_tags)]
    else:
        tag_list = []

    text = (body + " " + str(filename_stem) + " " + " ".join(tag_list)).lower()

    # Mapping of capability identifiers to search keywords
    cap_rules = {
        "teleoperation": ["遥操", "遥操作", "teleoperation", "teleop"],
        "data_collection": ["数采", "数据采集", "data collection", "data_collection"],
        "motion_retargeting": ["重定向", "retargeting", "motion retargeting"],
        "bipedal_walking": ["双足", "步态", "行走", "bipedal", "walking", "gait"],
        "dexterous_manipulation": ["灵巧手", "抓取", "操作", "manipulation", "dexterous"],
        "voice_interaction": ["语音", "voice", "speech"],
        "slam_navigation": ["slam", "导航", "建图", "navigation", "mapping"],
        "rl_training": ["强化学习", "rl", "isaaclab", "isaacsim", "reinforcement learning"],
        "simulation": ["仿真", "simulation", "mujoco", "isaac"],
        "visual_perception": ["视觉", "perception", "camera", "rgb-d", "深度感知"],
        "motion_control": ["运控", "运动控制", "motion control"],
        "pose_estimation": ["姿态估计", "pose estimation", "body tracking"],
        "safety_protection": ["急停", "保护", "safety", "emergency stop"],
        "battery_management": ["电池", "bms", "battery"],
    }

    for cap_id, keywords in cap_rules.items():
        if any(kw in text for kw in keywords):
            capabilities.add(cap_id)

    # Also include explicit domain tags as capabilities if clean
    for tag in tag_list:
        tag_clean = tag.strip().lower()
        if tag_clean and len(tag_clean) > 2 and tag_clean not in ["robot", "entity", "concept"]:
            capabilities.add(tag_clean)

    return sorted(list(capabilities))


def clean_summary(body: str, max_chars: int = 280) -> str:
    """Generate a clean text summary stripped of Markdown formatting and HTML."""
    if not body:
        return ""

    # Remove code blocks
    text = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    # Remove markdown images and links
    text = re.sub(r"!\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    # Remove HTML tags
    text = re.sub(r"<.*?>", "", text)
    # Remove headings and formatting markers
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[\*\_`~>#]", " ", text)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > max_chars:
        return text[:max_chars].strip() + "..."
    return text


def extract_headings(body: str) -> List[str]:
    """Extract markdown headings from body text."""
    headings = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            if heading:
                headings.append(heading)
    return headings


def extract_media_references(body: str, page_path: str) -> List[Dict[str, Any]]:
    """
    Extract image references with normalized paths and surrounding textual context.
    Captures the preceding heading and surrounding paragraph for precise categorization.
    """
    media = []
    lines = body.splitlines()
    current_heading = ""

    for idx, line in enumerate(lines):
        line_stripped = line.strip()
        if line_stripped.startswith("#"):
            current_heading = line_stripped.lstrip("#").strip()

        # Check for image markdown pattern ![alt](path)
        img_matches = re.findall(r"!\[(.*?)\]\((.*?)\)", line)
        for alt, img_path in img_matches:
            raw_path = img_path.strip()
            # Normalize path: strip leading ../ or ./
            normalized_path = re.sub(r"^(\.\./|\./)+", "", raw_path)
            if "media" in normalized_path:
                # Find surrounding paragraph context (2 lines before and 2 lines after)
                start_l = max(0, idx - 2)
                end_l = min(len(lines), idx + 3)
                context_lines = [lines[i].strip() for i in range(start_l, end_l) if not lines[i].strip().startswith("![")]
                context_text = " ".join(context_lines)

                media.append({
                    "path": normalized_path,
                    "alt": alt.strip(),
                    "source_page": page_path,
                    "heading": current_heading,
                    "context": context_text[:300].strip(),
                })
    return media


def generate_aliases(filename_stem: str, title: str, meta_tags: List[str]) -> List[str]:
    """Generate comprehensive cross-lingual Chinese/English aliases for an entity."""
    aliases = set()
    stem_str = str(filename_stem) if filename_stem is not None else ""
    title_str = str(title) if title is not None else ""

    # Check known mapped aliases
    if stem_str in ENTITY_ALIAS_MAP:
        aliases.update(ENTITY_ALIAS_MAP[stem_str])

    if title_str:
        aliases.add(title_str)
        # Handle Chinese/English combinations in title
        if " " in title_str:
            for part in title_str.split():
                if len(part) > 1:
                    aliases.add(part)

    # Standard stem conversions
    stem_clean = stem_str.replace("-", " ").replace("_", " ")
    if stem_clean:
        aliases.add(stem_clean)
    if stem_str:
        aliases.add(stem_str)

    return sorted(list(aliases))


def parse_wiki_file(file_path: Path, wiki_root: Path) -> Dict[str, Any]:
    """Parse a single Wiki markdown file into a structured dictionary."""
    rel_path = file_path.relative_to(wiki_root)
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_content = f.read()

    meta, body = parse_frontmatter(raw_content)
    raw_title = meta.get("title") or meta.get("name") or file_path.stem.replace("-", " ").title()
    title = str(raw_title)

    raw_tags = meta.get("tags")
    if raw_tags is None:
        tags = []
    elif isinstance(raw_tags, str):
        tags = [raw_tags]
    elif isinstance(raw_tags, list):
        tags = [str(t) for t in raw_tags if t is not None and isinstance(t, (str, int, float, bool))]
    else:
        tags = [str(raw_tags)]
    meta["tags"] = tags

    raw_related = meta.get("related")
    if raw_related is None:
        related = []
    elif isinstance(raw_related, str):
        related = [raw_related]
    elif isinstance(raw_related, list):
        related = [str(r) for r in raw_related if r is not None and isinstance(r, (str, int, float))]
    else:
        related = [str(raw_related)]
    meta["related"] = related

    raw_sources = meta.get("sources")
    if raw_sources is None:
        sources = []
    elif isinstance(raw_sources, str):
        sources = [raw_sources]
    elif isinstance(raw_sources, list):
        sources = [str(s) for s in raw_sources if s is not None and isinstance(s, (str, int, float))]
    else:
        sources = [str(raw_sources)]
    meta["sources"] = sources

    role, level = infer_document_role_and_level(str(rel_path), meta, body)
    headings = extract_headings(body)
    media = extract_media_references(body, str(rel_path))
    aliases = generate_aliases(file_path.stem, title, tags)
    capabilities = extract_capabilities(meta, body, file_path.stem)
    summary = clean_summary(body)

    wiki_section = rel_path.parts[0] if len(rel_path.parts) > 1 else "root"
    is_uncertainty = (wiki_section == "queries" or meta.get("uncertainty", False) or file_path.stem == "unanswered")

    return {
        "path": str(rel_path),
        "title": title,
        "wiki_section": wiki_section,
        "document_role": role,
        "abstraction_level": level,
        "capabilities": capabilities,
        "aliases": aliases,
        "tags": tags,
        "related": related,
        "headings": headings,
        "body": body,
        "media": media,
        "sources": sources,
        "uncertainty": is_uncertainty,
        "summary": summary,
    }
