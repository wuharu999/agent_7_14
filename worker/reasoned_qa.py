"""Bounded LangGraph orchestration for public Wiki Q&A.

This module owns only a derived, local search index under ``.agent1-worker``.
The generated Wiki remains the source of truth and no raw upload is read here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Protocol, Sequence, TypedDict
from uuid import uuid4

from worker.config import (
    DEEPSEEK_TIMEOUT,
    QA_REASONING_MAX_CANDIDATES,
    QA_REASONING_MAX_PAGES,
    WIKI_QA_MAX_PAGE_CHARS,
    WORKER_ROOT_DIR,
)

log = logging.getLogger(__name__)

_IMAGE_LINK = re.compile(r"!\[(?P<alt>[^\]\n]{0,200})\]\((?P<path>[^)\n]+)\)")
_WIKI_LINK = re.compile(r"(?<!!)\[\[([^\]\n]+)\]\]")
_HEADING = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")
_TEXT_TOKEN = re.compile(r"[\w\-+.]+", re.UNICODE)
_IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
_EXCLUDED_PAGES = {"index.md", "log.md", "overview.md", "unanswered.md"}
_ARCHITECT_LOCK = threading.Lock()
_HISTORY_REFERENCE_MARKERS = (
    "它", "这个机器人", "该机器人", "这个平台", "该平台", "这款", "刚才那个", "上面提到的",
    "it", "this robot", "that robot", "this platform", "that platform", "the above robot",
)
_TEAM_SEARCH_ALIASES = {
    "tian_gong": ("tian gong", "tiangong", "tienkung", "天工"),
    "walker_s2": ("walker s2", "walker-s2", "walker_s2", "s2"),
    "walker_c1": ("walker c1", "walker-c1", "walker_c1", "c1"),
}


class ChatProvider(Protocol):
    timeout: int

    def complete(self, system: str, user: str) -> str: ...

    def stream(self, system: str, user: str) -> Iterator[str]: ...


class GraphState(TypedDict, total=False):
    question: str
    team: str
    language: str
    history: list[Any]
    standalone_question: str
    topic_relation: str
    current_subject: str | None
    history_used: list[str]
    history_ignored: list[str]
    scope_analysis: dict[str, Any]
    clarification_required: bool
    clarification_answer: str
    intent: str
    preferred_abstraction: str
    search_queries: list[str]
    additional_queries: list[str]
    search_results: list[dict[str, Any]]
    retrieval_round: int
    selected_pages: list[str]
    selected_images: list[dict[str, Any]]
    need_more_search: bool
    planner_faithful: bool
    unsupported_assumptions: list[str]
    scope_consistency: dict[str, Any]
    uncertainties_to_check: list[str]
    answer_plan: dict[str, Any]
    evidence: list[dict[str, str]]
    answer_system: str
    answer_user: str
    llm_calls: int


@dataclass(frozen=True)
class SearchResult:
    path: str
    title: str
    snippet: str
    bm25_score: float
    wiki_section: str
    document_role: str
    abstraction_level: int
    tags: list[str]
    related: list[str]
    uncertainty: bool

    def as_dict(self) -> dict[str, Any]:
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
            "uncertainty": self.uncertainty,
        }


def langgraph_available() -> bool:
    try:
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    return True


def _safe_relative(root: Path, candidate: Path) -> Path | None:
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (FileNotFoundError, OSError, ValueError):
        return None
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return None
    if candidate.is_symlink():
        return None
    return relative


def _frontmatter_and_body(markdown: str) -> tuple[dict[str, Any], str]:
    if not markdown.startswith("---\n"):
        return {}, markdown
    marker = markdown.find("\n---", 4)
    if marker < 0:
        return {}, markdown
    values: dict[str, Any] = {}
    for line in markdown[4:marker].splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            values[key.strip().casefold()] = value.strip().strip("[]")
    return values, markdown[marker + 4 :]


def _plain_text(markdown: str) -> str:
    text = re.sub(r"!?(\[[^\]]*\])\([^)]*\)", r"\1", markdown)
    text = re.sub(r"[`*_>#|]", " ", text)
    return " ".join(text.split())


def _tokenize(value: str) -> list[str]:
    try:
        import jieba

        words = jieba.lcut(value, cut_all=False)
    except ImportError:
        words = _TEXT_TOKEN.findall(value)
    return [word.strip() for word in words if word and _TEXT_TOKEN.fullmatch(word.strip())]


def _fts_terms(value: str) -> list[str]:
    return [term.replace('"', "") for term in list(dict.fromkeys(_tokenize(value)))[:16] if term.strip()]


def _fts_query(value: str, *, operator: str = "OR") -> str:
    return f" {operator} ".join(f'"{term}"' for term in _fts_terms(value))


def _title_for(path: Path, metadata: dict[str, Any], body: str) -> str:
    title = str(metadata.get("title") or "").strip()
    if title:
        return title
    heading = _HEADING.search(body)
    return heading.group(1).strip() if heading else path.stem.replace("-", " ")


def _role_for(relative: Path, metadata: dict[str, Any]) -> tuple[str, int]:
    section = relative.parts[0] if len(relative.parts) > 1 else "root"
    declared = str(metadata.get("type") or "").casefold()
    if section == "entities":
        return declared or "application", 0
    if section == "comparisons":
        return "comparison", 1
    if section == "concepts":
        return declared or "workflow", 1
    if section == "queries":
        return "unresolved_query", 4
    if section == "sources":
        return "source", 4
    return declared or "reference", 2


def _metadata_list(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


class WikiArchitect:
    """Build and query a compact local FTS5 index without changing the Wiki."""

    def __init__(self, wiki_root: Path):
        self.wiki_root = wiki_root.expanduser().resolve()
        index_key = hashlib.sha256(str(self.wiki_root).encode("utf-8")).hexdigest()[:16]
        self.output_dir = WORKER_ROOT_DIR / ".agent1-worker" / "qa-architect" / index_key
        self.database_path = self.output_dir / "search.db"
        self.catalog_path = self.output_dir / "wiki_catalog.jsonl"
        self.graph_path = self.output_dir / "related_graph.json"
        self.images_path = self.output_dir / "image_catalog.jsonl"
        self.guide_path = self.output_dir / "WIKI_GUIDE.md"
        self.manifest_path = self.output_dir / "manifest.json"

    def _page_paths(self) -> list[Path]:
        result: list[Path] = []
        for path in self.wiki_root.rglob("*.md"):
            relative = _safe_relative(self.wiki_root, path)
            if relative is not None and relative.name not in _EXCLUDED_PAGES:
                result.append(path)
        return sorted(result)

    def _signature(self, pages: Sequence[Path]) -> str:
        digest = hashlib.sha256()
        for page in pages:
            relative = page.relative_to(self.wiki_root).as_posix()
            stat = page.stat()
            digest.update(f"{relative}\0{stat.st_mtime_ns}\0{stat.st_size}\n".encode())
        return digest.hexdigest()

    def ensure(self) -> None:
        with _ARCHITECT_LOCK:
            if not self.wiki_root.is_dir():
                raise FileNotFoundError(f"Wiki root is unavailable: {self.wiki_root}")
            pages = self._page_paths()
            signature = self._signature(pages)
            try:
                previous = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = {}
            if (
                previous.get("signature") == signature
                and self.database_path.is_file()
                and self.catalog_path.is_file()
            ):
                return
            self._build(pages, signature)

    def _build(self, pages: Sequence[Path], signature: str) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        image_records: list[dict[str, Any]] = []
        for page in pages:
            relative = page.relative_to(self.wiki_root)
            try:
                raw = page.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            metadata, body = _frontmatter_and_body(raw)
            role, level = _role_for(relative, metadata)
            related = [
                match.split("|", 1)[0].split("#", 1)[0].strip()
                for match in _WIKI_LINK.findall(body)
                if match.split("|", 1)[0].strip()
            ]
            record = {
                "path": relative.as_posix(),
                "title": _title_for(relative, metadata, body),
                "wiki_section": relative.parts[0] if len(relative.parts) > 1 else "root",
                "document_role": role,
                "abstraction_level": level,
                "aliases": _metadata_list(metadata.get("aliases")),
                "tags": _metadata_list(metadata.get("tags")),
                "related": list(dict.fromkeys(related)),
                "summary": _plain_text(body)[:900],
                "uncertainty": relative.parts[0] == "queries" if relative.parts else False,
            }
            records.append(record)
            for image in _IMAGE_LINK.finditer(body):
                target = image.group("path").strip().strip("<>").split("#", 1)[0]
                image_path = (page.parent / target).resolve() if not target.startswith("media/") else (self.wiki_root / target).resolve()
                image_relative = _safe_relative(self.wiki_root, image_path)
                if image_relative is None or image_path.suffix.casefold() not in _IMAGE_SUFFIXES:
                    continue
                if "media" not in image_relative.parts:
                    continue
                alt = image.group("alt").strip()
                image_records.append(
                    {
                        "path": f"wiki/{image_relative.as_posix()}",
                        "source_page": record["path"],
                        "description": alt or record["title"],
                        "image_type": "decorative" if "logo" in image_relative.stem.casefold() else "unknown",
                        "usefulness": "medium" if alt else "low",
                    }
                )

        path_by_key: dict[str, str] = {}
        for record in records:
            path = str(record["path"])
            path_by_key[path.removesuffix(".md").casefold()] = path
            path_by_key[Path(path).stem.casefold()] = path
        edges = []
        for record in records:
            for target in record["related"]:
                destination = path_by_key.get(target.removesuffix(".md").casefold())
                if destination and destination != record["path"]:
                    edges.append({"from": record["path"], "to": destination, "relation": "related_to"})

        temp_db = self.database_path.with_suffix(f".tmp-{uuid4().hex}")
        connection = sqlite3.connect(temp_db)
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE wiki_fts USING fts5(path UNINDEXED, title, aliases, tags, headings, body)"
            )
            connection.execute(
                "CREATE TABLE page_meta (path TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)"
            )
            for record in records:
                page = self.wiki_root / str(record["path"])
                raw = page.read_text(encoding="utf-8", errors="replace")
                _, body = _frontmatter_and_body(raw)
                headings = " ".join(_HEADING.findall(body))
                connection.execute(
                    "INSERT INTO wiki_fts VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        record["path"],
                        " ".join(_tokenize(str(record["title"]))),
                        " ".join(_tokenize(" ".join(record["aliases"]))),
                        " ".join(_tokenize(" ".join(record["tags"]))),
                        " ".join(_tokenize(headings)),
                        " ".join(_tokenize(body)),
                    ),
                )
                connection.execute(
                    "INSERT INTO page_meta VALUES (?, ?)",
                    (record["path"], json.dumps(record, ensure_ascii=False)),
                )
            connection.commit()
        finally:
            connection.close()
        temp_db.replace(self.database_path)
        self._atomic_text(self.catalog_path, "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records))
        self._atomic_text(self.graph_path, json.dumps({"edges": edges}, ensure_ascii=False, indent=2))
        self._atomic_text(self.images_path, "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in image_records))
        guide_lines = ["# Runtime Wiki Guide", "", "Derived local map; the Markdown Wiki remains authoritative.", ""]
        for record in records[:80]:
            guide_lines.append(f"- [{record['document_role']}] {record['title']}: {record['summary'][:180]}")
        self._atomic_text(self.guide_path, "\n".join(guide_lines)[:15_000] + "\n")
        self._atomic_text(self.manifest_path, json.dumps({"signature": signature, "pages": len(records)}, ensure_ascii=False))
        log.info("Rebuilt bounded QA FTS index for %s (%d pages)", self.wiki_root, len(records))

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + f".tmp-{uuid4().hex}")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def search(
        self, query: str, *, team: str, limit: int = QA_REASONING_MAX_CANDIDATES
    ) -> list[dict[str, Any]]:
        self.ensure()
        and_query = _fts_query(query, operator="AND")
        or_query = _fts_query(query)
        if not and_query:
            return []
        connection = sqlite3.connect(self.database_path)
        try:
            statement = (
                "SELECT path, bm25(wiki_fts, 5.0, 4.0, 3.0, 2.0, 1.0) AS rank "
                "FROM wiki_fts WHERE wiki_fts MATCH ? ORDER BY rank LIMIT ?"
            )
            try:
                rows = connection.execute(statement, (and_query, limit * 4)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if len(rows) < limit:
                try:
                    extra_rows = connection.execute(statement, (or_query, limit * 4)).fetchall()
                except sqlite3.OperationalError:
                    extra_rows = []
                existing_paths = {str(path) for path, _ in rows}
                rows.extend(row for row in extra_rows if str(row[0]) not in existing_paths)
            results = []
            for path, rank in rows:
                metadata_row = connection.execute(
                    "SELECT metadata_json FROM page_meta WHERE path = ?", (path,)
                ).fetchone()
                if metadata_row is None:
                    continue
                metadata = json.loads(metadata_row[0])
                item = SearchResult(
                    path=metadata["path"], title=metadata["title"], snippet=metadata["summary"][:500],
                    bm25_score=float(-rank), wiki_section=metadata["wiki_section"],
                    document_role=metadata["document_role"], abstraction_level=int(metadata["abstraction_level"]),
                    tags=list(metadata["tags"]), related=list(metadata["related"]),
                    uncertainty=bool(metadata["uncertainty"]),
                ).as_dict()
                aliases = _TEAM_SEARCH_ALIASES.get(team.casefold(), ())
                searchable = " ".join(
                    [item["path"], item["title"], *item.get("tags", []), *metadata.get("aliases", [])]
                ).casefold()
                if aliases and any(alias in searchable for alias in aliases):
                    item["bm25_score"] += 0.3
                    item["boosted"] = True
                results.append(item)
            return sorted(results, key=lambda item: float(item["bm25_score"]), reverse=True)[:limit]
        finally:
            connection.close()

    def expand_related(self, candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        self.ensure()
        by_path = {str(item["path"]): dict(item) for item in candidates}
        connection = sqlite3.connect(self.database_path)
        try:
            metadata_by_path = {
                str(json.loads(metadata_json)["path"]): json.loads(metadata_json)
                for (metadata_json,) in connection.execute("SELECT metadata_json FROM page_meta")
            }
            for item in list(candidates)[:8]:
                for target in item.get("related", [])[:6]:
                    normalized = str(target).removesuffix(".md").casefold()
                    for path, metadata in metadata_by_path.items():
                        if path.removesuffix(".md").casefold() != normalized and Path(path).stem.casefold() != normalized:
                            continue
                        if path not in by_path:
                            expanded = SearchResult(
                                path=path, title=metadata["title"], snippet=metadata["summary"][:500], bm25_score=float(item.get("bm25_score", 0)) - 0.01,
                                wiki_section=metadata["wiki_section"], document_role=metadata["document_role"],
                                abstraction_level=int(metadata["abstraction_level"]), tags=list(metadata["tags"]),
                                related=list(metadata["related"]), uncertainty=bool(metadata["uncertainty"]),
                            ).as_dict()
                            expanded["expanded_related"] = True
                            by_path[path] = expanded
                        break
            return sorted(by_path.values(), key=lambda entry: float(entry.get("bm25_score", 0)), reverse=True)[:QA_REASONING_MAX_CANDIDATES]
        finally:
            connection.close()

    def load_evidence(self, paths: Sequence[str]) -> list[dict[str, str]]:
        loaded = []
        total = 0
        for relative_text in paths[:QA_REASONING_MAX_PAGES]:
            candidate = self.wiki_root / relative_text
            relative = _safe_relative(self.wiki_root, candidate)
            if relative is None or relative.as_posix() != relative_text or candidate.suffix.casefold() != ".md":
                continue
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            remaining = WIKI_QA_MAX_PAGE_CHARS - total
            if remaining <= 0:
                break
            bounded = text[:remaining]
            loaded.append({"path": relative_text, "text": bounded})
            total += len(bounded)
        return loaded

    def images_for(self, selected_paths: Sequence[str]) -> list[dict[str, str]]:
        try:
            lines = self.images_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        paths = set(selected_paths)
        return [
            image for line in lines if line and (image := json.loads(line)).get("source_page") in paths
            and image.get("usefulness") != "low" and image.get("image_type") != "decorative"
        ][:10]


def _json_object(value: str) -> dict[str, Any] | None:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _history_text(history: Sequence[Any]) -> str:
    parts = []
    for turn in history[-4:]:
        if isinstance(turn, dict):
            role = str(turn.get("role") or "user")
            content = str(turn.get("content") or "").strip()[:800]
            if content:
                parts.append(f"{role}: {content}")
            continue
        question = str(getattr(turn, "question", "")).strip()[:800]
        answer = str(getattr(turn, "answer", "")).strip()[:800]
        if question:
            parts.append(f"user: {question}")
        if answer:
            parts.append(f"assistant: {answer}")
    return "\n\n".join(parts) or "(No previous conversation.)"


def _needs_history_resolution(question: str) -> bool:
    normalized = question.casefold()
    return any(marker in normalized for marker in _HISTORY_REFERENCE_MARKERS)


def _scope_stop_message(scope: str, entities: Sequence[Any], language: str) -> str:
    mentioned = "、".join(str(entity) for entity in entities if str(entity).strip()) or "another product"
    if language.casefold().startswith("en"):
        return (
            f"The selected knowledge scope is {scope}, but your question explicitly asks about {mentioned}. "
            "Please switch scope or ask for a comparison."
        )
    return (
        f"当前知识范围是「{scope}」，但这个问题明确询问的是「{mentioned}」。"
        "请切换知识范围，或明确要求进行跨产品对比。"
    )


class ReasonedQA:
    """A finite graph: plan -> search -> expand -> reason -> load -> stream."""

    def __init__(self, wiki_root: Path, provider: ChatProvider):
        self.architect = WikiArchitect(wiki_root)
        self.provider = provider

    def _plan(self, state: dict[str, Any]) -> dict[str, Any]:
        question = str(state["question"])
        raw_history = state.get("history", ())
        history_items = list(raw_history)[-4:] if isinstance(raw_history, Sequence) and not isinstance(raw_history, str) else []
        use_history = bool(history_items) and _needs_history_resolution(question)
        history = _history_text(history_items) if use_history else "(Not used: no current-turn reference.)"
        fallback = {
            "standalone_question": question,
            "topic_relation": "continue" if use_history else ("switch" if history_items else "ambiguous"),
            "current_subject": None,
            "history_used": ["recent conversation"] if use_history else [],
            "history_ignored": ["recent conversation"] if history_items and not use_history else [],
            "scope_analysis": {
                "active_scope": state["team"], "explicit_entities": [], "resolved_references": [],
                "relation": "ambiguous", "reason": "No scope classifier response was available.", "confidence": 0.0,
            },
            "clarification_required": False,
            "clarification_answer": "",
            "intent": "explicit_api" if re.search(r"\b(api|topic|parameter|ros)\b|接口|话题|参数", question, re.I) else "how_to",
            "preferred_abstraction": "api_or_interface" if re.search(r"\b(api|topic|parameter|ros)\b|接口|话题|参数", question, re.I) else "application_or_workflow",
            "search_queries": [question],
        }
        prompt = (
            "Return JSON only with scope_analysis, topic_relation, current_subject, history_used, history_ignored, "
            "standalone_question, intent, preferred_abstraction, and search_queries. Current request and selected "
            "scope are authoritative. Use history only to resolve an explicit reference in the current question; do "
            "not anchor an underspecified new question to history. Classify scope_analysis.relation as in_scope, "
            "related_scope, cross_scope, out_of_scope, or ambiguous. For general how-to requests prefer a supported "
            "application or workflow; use API level only when explicitly requested.\n\n"
            f"Selected scope: {state['team']}\nHistory:\n{history}\n\nCurrent question:\n{question}"
        )
        try:
            response = _json_object(self.provider.complete("You plan bounded Wiki retrieval; do not answer the user.", prompt))
        except Exception as exc:
            raise RuntimeError("Reasoned QA planner request failed") from exc
        if response:
            queries = [str(item).strip() for item in response.get("search_queries", []) if str(item).strip()][:3]
            scope_analysis = response.get("scope_analysis")
            if not isinstance(scope_analysis, dict):
                scope_analysis = fallback["scope_analysis"]
            scope_analysis = {**scope_analysis, "active_scope": state["team"]}
            specific_scope = state["team"].casefold() not in {"", "all", "default"}
            clarification_required = specific_scope and str(scope_analysis.get("relation") or "") == "out_of_scope"
            fallback.update(
                standalone_question=str(response.get("standalone_question") or question).strip(),
                topic_relation=str(response.get("topic_relation") or fallback["topic_relation"]),
                current_subject=(str(response["current_subject"]).strip() if response.get("current_subject") and use_history else None),
                history_used=[str(item) for item in response.get("history_used", [])[:3]] if use_history else [],
                history_ignored=[str(item) for item in response.get("history_ignored", [])[:3]] if not use_history else [],
                scope_analysis=scope_analysis,
                clarification_required=clarification_required,
                clarification_answer=_scope_stop_message(
                    state["team"], scope_analysis.get("explicit_entities", []), state["language"]
                ) if clarification_required else "",
                intent=str(response.get("intent") or fallback["intent"]),
                preferred_abstraction=str(response.get("preferred_abstraction") or fallback["preferred_abstraction"]),
                search_queries=[] if clarification_required else (queries or [question]),
            )
            return {**fallback, "llm_calls": state.get("llm_calls", 0) + 1}
        return fallback

    def _search(self, state: dict[str, Any]) -> dict[str, Any]:
        queries = (
            state.get("additional_queries") or state.get("search_queries")
            if state.get("retrieval_round", 0)
            else state.get("search_queries")
        )
        results_by_path = {item["path"]: item for item in state.get("search_results", [])}
        for query in (queries or [state["question"]])[:3]:
            for item in self.architect.search(str(query), team=str(state["team"])):
                previous = results_by_path.get(item["path"])
                if previous is None or float(item["bm25_score"]) > float(previous.get("bm25_score", 0)):
                    results_by_path[item["path"]] = item
        return {
            "search_results": sorted(results_by_path.values(), key=lambda item: item["bm25_score"], reverse=True)[:QA_REASONING_MAX_CANDIDATES],
            "retrieval_round": state.get("retrieval_round", 0) + 1,
        }

    def _expand(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"search_results": self.architect.expand_related(state.get("search_results", []))}

    def _reason(self, state: dict[str, Any]) -> dict[str, Any]:
        candidates = state.get("search_results", [])[:QA_REASONING_MAX_CANDIDATES]
        fallback_selected = self._fallback_selection(candidates, state.get("preferred_abstraction", "application_or_workflow"))
        compact = [
            {key: candidate.get(key) for key in ("path", "title", "snippet", "document_role", "abstraction_level", "uncertainty")}
            for candidate in candidates
        ]
        image_candidates = self.architect.images_for([str(item.get("path") or "") for item in candidates])
        prompt = (
            "Return JSON only with planner_faithful, unsupported_assumptions, corrected_standalone_question, "
            "scope_consistency, selected_pages, selected_images, need_more_search, additional_search_queries, "
            "uncertainties_to_check, primary_solution, direct_answer_plan, and supporting_points. Choose only paths "
            "from candidates. Set planner_faithful false if the standalone question added an entity or constraint not "
            "in the current question or explicitly used history. Set scope_consistency.valid false if evidence would "
            "transfer a capability across products. Prefer complete supported solutions for general how-to requests, "
            "and exact interface pages for explicit API requests. Do not invent facts or paths.\n\n"
            f"Question: {state['question']}\nStandalone goal: {state['standalone_question']}\n"
            f"Selected scope: {state['team']}\nIntent: {state['intent']}\n"
            f"Candidates: {json.dumps(compact, ensure_ascii=False)}\n"
            f"Candidate images: {json.dumps(image_candidates[:8], ensure_ascii=False)}"
        )
        try:
            response = _json_object(self.provider.complete("You select grounded Wiki evidence; do not answer the user.", prompt))
        except Exception as exc:
            raise RuntimeError("Reasoned QA evidence selection request failed") from exc
        valid_paths = {str(candidate["path"]) for candidate in candidates}
        selected = [str(path) for path in (response or {}).get("selected_pages", []) if str(path) in valid_paths][:QA_REASONING_MAX_PAGES]
        selected = selected or fallback_selected
        planner_faithful = bool((response or {}).get("planner_faithful", True))
        scope_consistency = (response or {}).get("scope_consistency")
        if not isinstance(scope_consistency, dict):
            scope_consistency = {"valid": True, "unsupported_cross_scope_transfer": []}
        scope_valid = bool(scope_consistency.get("valid", True))
        fidelity_retry = not planner_faithful and state.get("retrieval_round", 1) < 2
        scope_retry = not scope_valid and state.get("retrieval_round", 1) < 2
        another_round = (
            bool((response or {}).get("need_more_search")) or fidelity_retry or scope_retry
        ) and state.get("retrieval_round", 1) < 2
        extra_queries = [str(value).strip() for value in (response or {}).get("additional_search_queries", []) if str(value).strip()][:3]
        if another_round and not extra_queries:
            extra_queries = [state["standalone_question"]]
        answer_plan = {
            "primary_solution": str((response or {}).get("primary_solution") or ""),
            "direct_answer_plan": str((response or {}).get("direct_answer_plan") or "Answer the current question from the selected evidence."),
            "supporting_points": [str(value) for value in (response or {}).get("supporting_points", [])[:4] if str(value).strip()],
        }
        uncertainty_paths = [
            str(value) for value in (response or {}).get("uncertainties_to_check", [])
            if str(value) in valid_paths
        ][:QA_REASONING_MAX_PAGES]
        selected_image_paths = {
            str(image.get("path")) for image in (response or {}).get("selected_images", [])
            if isinstance(image, dict)
        }
        selected_images = [
            image for image in image_candidates if str(image.get("path")) in selected_image_paths
        ][:3]
        if not selected_images:
            selected_images = self.architect.images_for(selected)[:3]
        return {
            "selected_pages": selected,
            "selected_images": selected_images,
            "need_more_search": another_round,
            "additional_queries": extra_queries,
            "answer_plan": answer_plan,
            "planner_faithful": planner_faithful,
            "unsupported_assumptions": [str(value) for value in (response or {}).get("unsupported_assumptions", [])[:5]],
            "scope_consistency": scope_consistency,
            "uncertainties_to_check": uncertainty_paths,
            **({
                "standalone_question": str((response or {}).get("corrected_standalone_question") or state["question"]),
                "search_results": [],
            } if fidelity_retry or scope_retry else {}),
            "llm_calls": state.get("llm_calls", 0) + (1 if response is not None else 0),
        }

    @staticmethod
    def _fallback_selection(candidates: Sequence[dict[str, Any]], abstraction: str) -> list[str]:
        scored = []
        for candidate in candidates:
            level = int(candidate.get("abstraction_level", 2))
            score = float(candidate.get("bm25_score", 0))
            if abstraction == "api_or_interface":
                score += 3 if level in (2, 3) else -1 if level == 4 else 0
            else:
                score += 4 if level == 0 else 2 if level == 1 else -3 if level == 4 else 0
            scored.append((score, str(candidate["path"])))
        return [path for _, path in sorted(scored, reverse=True)[:QA_REASONING_MAX_PAGES]]

    def _load(self, state: dict[str, Any]) -> dict[str, Any]:
        paths = list(state.get("selected_pages", []))
        paths.extend(path for path in state.get("uncertainties_to_check", []) if path not in paths)
        return {"evidence": self.architect.load_evidence(paths)}

    def _final_request(self, state: dict[str, Any]) -> dict[str, Any]:
        evidence = "\n\n".join(
            f"--- Evidence {item['path']} ---\n{item['text']}" for item in state.get("evidence", [])
        )
        system = (
            "You are a robotics knowledge assistant. Answer only with facts supported by supplied Wiki evidence. "
            "Answer the user's current question first. Preserve proper names exactly as written in the evidence. "
            "Never expose citations, page names, file paths, source lists, retrieval steps, hidden reasoning, or image Markdown. "
            "If evidence is insufficient, start the response with [KNOWLEDGE_GAP] and state the missing information briefly."
        )
        user = (
            f"Answer language: {state['language']}\nSelected scope: {state['team']}\n"
            f"Question: {state['question']}\nAnswer plan: {json.dumps(state.get('answer_plan', {}), ensure_ascii=False)}\n\n"
            f"Evidence:\n{evidence or '(No sufficient evidence was selected.)'}"
        )
        # The token stream happens immediately after this terminal graph node;
        # count it now so per-request observability reports the full 3-call
        # normal path rather than only the two structured decisions.
        return {
            "answer_system": system,
            "answer_user": user,
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    @staticmethod
    def _next_after_reason(state: dict[str, Any]) -> str:
        return "search" if state.get("need_more_search") and state.get("retrieval_round", 0) < 2 else "load"

    @staticmethod
    def _next_after_plan(state: dict[str, Any]) -> str:
        return "clarify" if state.get("clarification_required") else "search"

    @staticmethod
    def _clarify(state: dict[str, Any]) -> dict[str, Any]:
        return {"clarification_answer": str(state.get("clarification_answer") or "")}

    def prepare(self, *, question: str, team: str, language: str, history: Sequence[Any]) -> dict[str, Any]:
        from langgraph.graph import END, START, StateGraph

        graph = StateGraph(GraphState)
        graph.add_node("plan", self._plan)
        graph.add_node("clarify", self._clarify)
        graph.add_node("search", self._search)
        graph.add_node("expand", self._expand)
        graph.add_node("reason", self._reason)
        graph.add_node("load", self._load)
        graph.add_node("final_answer", self._final_request)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", self._next_after_plan, {"clarify": "clarify", "search": "search"})
        graph.add_edge("clarify", END)
        graph.add_edge("search", "expand")
        graph.add_edge("expand", "reason")
        graph.add_conditional_edges("reason", self._next_after_reason, {"search": "search", "load": "load"})
        graph.add_edge("load", "final_answer")
        graph.add_edge("final_answer", END)
        initial = {
            "question": question, "team": team, "language": language, "history": list(history),
            "retrieval_round": 0, "llm_calls": 0,
        }
        started = time.perf_counter()
        result = graph.compile().invoke(initial)
        log.info(
            "Reasoned QA complete: rounds=%d calls=%d candidates=%d selected=%d elapsed_ms=%.1f",
            result.get("retrieval_round", 0), result.get("llm_calls", 0), len(result.get("search_results", [])),
            len(result.get("selected_pages", [])), (time.perf_counter() - started) * 1000,
        )
        return result


async def _stream(iterator: Iterator[str], callback: Callable[[str], Awaitable[None]], timeout: int) -> str:
    sentinel = object()

    def next_item() -> object:
        try:
            return next(iterator)
        except StopIteration:
            return sentinel

    parts: list[str] = []
    async with asyncio.timeout(timeout):
        while True:
            item = await asyncio.to_thread(next_item)
            if item is sentinel:
                break
            token = str(item)
            parts.append(token)
            await callback(token)
    return "".join(parts)


async def run_reasoned_qa_stream(
    *, question: str, team: str, language: str, history: Sequence[Any], wiki_root: Path,
    provider: ChatProvider, on_token: Callable[[str], Awaitable[None]],
) -> str:
    """Run the finite retrieval graph and stream only the final answer tokens."""
    engine = ReasonedQA(wiki_root, provider)
    state = await asyncio.to_thread(
        engine.prepare, question=question, team=team, language=language, history=history
    )
    clarification = str(state.get("clarification_answer") or "").strip()
    if clarification:
        await on_token(clarification)
        return clarification
    answer = await _stream(
        provider.stream(state["answer_system"], state["answer_user"]), on_token, provider.timeout or DEEPSEEK_TIMEOUT
    )
    # Existing manager code validates and attaches only these hidden Markdown
    # image references; the final answer stream never exposes the file paths.
    images = state.get("selected_images", [])
    markers = [
        f"![{str(image.get('description') or 'Knowledge-base image')[:120]}]({image['path']})"
        for image in images if isinstance(image, dict) and str(image.get("path", "")).startswith("wiki/media/")
    ]
    return answer + ("\n\n" + "\n".join(markers) if markers else "")
