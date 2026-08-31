import json
import re
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional, Union

from worker.langgraph_qa.wiki.indexer import tokenize_text, init_jieba

TOPIC_SYNONYMS: Dict[str, List[str]] = {
    "全部机器人": [],
    "天工行者无界&无疆": [
        "天工行者无界", "天工行者无疆", "天工行者", "天工", "tienkung", "tianxing",
        "无界", "无疆", "tienkung-3", "tienkung-pro", "tienkung-plus", "walker-tienkung", "walker_tienkung"
    ],
    "天工行者DEX": [
        "天工行者dex", "dex", "tiangong-walker-dex", "tienkung-dex", "tiangong-dex", "灵巧手机器人"
    ],
    "Walker_C1_EDU共创者": [
        "walker c1", "walker_c1", "walker-c1", "c1 edu", "c1_edu", "共创者", "astron", "walker-c1-edu", "c1"
    ],
    "Walker_S2_EDU探索者": [
        "walker s2", "walker_s2", "walker-s2", "s2 edu", "s2_edu", "探索者", "walker s2 industrial", "walker-s2-industrial", "s2-api-tiny", "rosa-2.0", "s2"
    ],
    "运营": [
        "运营", "operations", "growth", "ka", "商业模式", "渠道", "生态", "收益模式", "产教融合"
    ],
    "方案": [
        "方案", "solutions", "9-solutions", "建设方案", "产业学院", "产教融合", "实训基地", "申报"
    ],
    "售后": [
        "售后", "aftersale", "9-aftersale", "faq", "常见问题", "排查", "故障", "troubleshooting", "维修", "保修", "warranty", "急停", "emergency-stop"
    ],
}


class SearchResult:
    def __init__(
        self,
        path: str,
        title: str,
        snippet: str,
        bm25_score: float,
        wiki_section: str,
        document_role: str,
        abstraction_level: int,
        tags: List[str],
        related: List[str],
        aliases: List[str],
        boosted: bool = False,
    ):
        self.path = path
        self.title = title
        self.snippet = snippet
        self.bm25_score = bm25_score
        self.wiki_section = wiki_section
        self.document_role = document_role
        self.abstraction_level = abstraction_level
        self.tags = tags
        self.related = related
        self.aliases = aliases
        self.boosted = boosted

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "snippet": self.snippet,
            "bm25_score": self.bm25_score,
            "wiki_section": self.wiki_section,
            "document_role": self.document_role,
            "abstraction_level": self.abstraction_level,
            "tags": self.tags,
            "related": self.related,
            "aliases": self.aliases,
            "boosted": self.boosted,
        }


def search_wiki(
    query: str,
    *,
    db_path: Union[Path, str],
    robot_topic: str = "全部机器人",
    scopes: Optional[List[str]] = None,
    roles: Optional[List[str]] = None,
    limit: int = 10,
) -> List[SearchResult]:
    """
    Execute SQLite FTS search with jieba query tokenization, punctuation sanitization,
    robot_topic soft boosting, and optional scope/role filtering.
    """
    if not query or not isinstance(query, str) or not query.strip():
        return []

    db_file = Path(db_path)
    if not db_file.exists():
        return []

    init_jieba()
    tokenized_query = tokenize_text(query)
    if not tokenized_query.strip():
        tokenized_query = query.strip()

    # Filter out pure punctuation tokens to avoid FTS5 syntax errors and empty AND matches
    clean_tokens = []
    for t in tokenized_query.split():
        cleaned = re.sub(r"[^\w\u4e00-\u9fff]|_", "", t).strip()
        if cleaned:
            clean_tokens.append(cleaned)

    if not clean_tokens:
        return []

    # Build AND query first to ensure all terms match, fall back to OR query
    and_query = " AND ".join(f'"{t}"' for t in clean_tokens)
    or_query = " OR ".join(f'"{t}"' for t in clean_tokens)

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    sql = """
        SELECT f.path, bm25(wiki_fts, 5.0, 4.0, 3.0, 2.0, 1.0) AS score, p.title, p.wiki_section, p.document_role, p.abstraction_level, p.metadata_json
        FROM wiki_fts f
        JOIN wiki_pages p ON f.path = p.path
        WHERE wiki_fts MATCH ?
        ORDER BY score ASC
        LIMIT ?
    """

    results: List[SearchResult] = []
    rows = []

    fetch_limit = max(limit * 4, 30)

    # Attempt AND query first
    try:
        cursor.execute(sql, (and_query, fetch_limit))
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        rows = []

    # If AND query returned fewer than limit results, fallback to OR query
    if len(rows) < limit:
        try:
            cursor.execute(sql, (or_query, fetch_limit))
            existing_paths = {r[0] for r in rows}
            or_rows = cursor.fetchall()
            for r in or_rows:
                if r[0] not in existing_paths:
                    rows.append(r)
        except sqlite3.OperationalError:
            pass

    conn.close()

    topic_synonyms = TOPIC_SYNONYMS.get(robot_topic, [])
    if not topic_synonyms and robot_topic and robot_topic != "全部机器人":
        topic_synonyms = [robot_topic.lower()]

    for path, score, title, wiki_section, doc_role, level, meta_json in rows:
        # Optional scope and role filtering
        if scopes and wiki_section not in scopes:
            continue
        if roles and doc_role not in roles:
            continue

        meta = json.loads(meta_json) if meta_json else {}
        tags = meta.get("tags", [])
        related = meta.get("related", [])
        aliases = meta.get("aliases", [])
        body_snippet = meta.get("summary", "")

        # Calculate soft boost based on robot_topic match and solution abstraction level
        base_score = abs(score)
        boost_factor = 1.0
        is_boosted = False

        if topic_synonyms:
            searchable_targets = [title.lower(), path.lower()] + [t.lower() for t in tags] + [a.lower() for a in aliases]
            matches_topic = any(
                any(syn.lower() in target for target in searchable_targets)
                for syn in topic_synonyms
            )
            if matches_topic:
                boost_factor += 0.3
                is_boosted = True

        # Boost complete solutions and workflows (level 0, 1) over low-level pages
        if doc_role in ("application", "workflow", "tool", "robot"):
            boost_factor += 0.25

        final_score = base_score * boost_factor

        results.append(
            SearchResult(
                path=path,
                title=title,
                snippet=body_snippet,
                bm25_score=final_score,
                wiki_section=wiki_section,
                document_role=doc_role,
                abstraction_level=level,
                tags=tags,
                related=related,
                aliases=aliases,
                boosted=is_boosted,
            )
        )

    # Sort by boosted score descending
    results.sort(key=lambda x: x.bm25_score, reverse=True)
    return results[:limit]
