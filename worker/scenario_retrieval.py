from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import jieba

from worker.config import (
    SCENARIO_RETRIEVAL_CACHE_FILE,
    SCENARIO_RETRIEVAL_CANDIDATES,
    SCENARIO_RETRIEVAL_MAX_DOCUMENTS,
    SCENARIO_RETRIEVAL_MAX_PAGE_CHARS,
    SCENARIO_RETRIEVAL_MAX_TOTAL_CHARS,
    get_team_config,
)


_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_LOCAL_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((?!https?://)[^)]+\)", re.IGNORECASE)
_LOCAL_PATH_RE = re.compile(
    r"(?:(?:/home|/root|/tmp|/Users)/[^\s\]\[()<>]+|[A-Za-z]:[\\/][^\s\]\[()<>]+)",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{1,}")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_REFRESH_LOCK = threading.Lock()
_ROBOT_ALIASES: dict[str, tuple[str, ...]] = {
    "walker_c1": ("Walker C1", "WalkerC1", "walker-c1", "C1", "行者 C1"),
    "walker_s2": ("Walker S2", "WalkerS2", "walker-s2", "S2", "行者 S2"),
    "tian_gong": ("天工行者", "天工行者无界", "天工行者基础版", "Tiangong", "Tian Gong"),
}


@dataclass(frozen=True)
class EvidenceDocument:
    document_id: str
    kind: str
    title: str
    text: str
    digest: str


@dataclass(frozen=True)
class EvidenceSnapshot:
    revision: str
    documents: tuple[EvidenceDocument, ...]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tokens(value: str) -> list[str]:
    tokens = [match.group(0).casefold() for match in _WORD_RE.finditer(value)]
    if _CJK_RE.search(value):
        tokens.extend(
            token.strip().casefold()
            for token in jieba.lcut(value, cut_all=False)
            if len(token.strip()) >= 2
        )
    return list(dict.fromkeys(token for token in tokens if token))


def _title(path: Path, text: str, payload: dict[str, Any] | None) -> str:
    if payload:
        for key in ("name", "title", "capability_id"):
            if str(payload.get(key) or "").strip():
                return str(payload[key]).strip()
    heading = _HEADING_RE.search(text)
    return heading.group(1).strip() if heading else path.stem


def _links(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            raw.split("|", 1)[0].split("#", 1)[0].strip().casefold()
            for raw in _WIKI_LINK_RE.findall(text)
            if raw.split("|", 1)[0].split("#", 1)[0].strip()
        )
    )


class ScenarioEvidenceIndex:
    """Rebuildable Wiki/catalog-only FTS cache used by scenario analysis."""

    def __init__(self, wiki_root: Path, cache_file: Path = SCENARIO_RETRIEVAL_CACHE_FILE) -> None:
        if wiki_root.is_symlink():
            raise ValueError("Wiki retrieval root cannot be a symlink")
        self.wiki_root = wiki_root.resolve()
        self.cache_file = cache_file.resolve()
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

    def _source_paths(self) -> list[Path]:
        if not self.wiki_root.is_dir():
            return []
        paths: list[Path] = []
        for path in self.wiki_root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                path.resolve().relative_to(self.wiki_root)
            except ValueError:
                continue
            suffix = path.suffix.casefold()
            if suffix == ".md" or (
                suffix == ".json" and "capabilities" in path.relative_to(self.wiki_root).parts
            ):
                paths.append(path)
        return sorted(paths)

    def _manifest(self, paths: Iterable[Path]) -> tuple[str, list[tuple[Path, bytes, str]]]:
        rows: list[tuple[Path, bytes, str]] = []
        manifest = hashlib.sha256()
        for path in paths:
            data = path.read_bytes()
            digest = _sha256(data)
            relative = path.relative_to(self.wiki_root).as_posix()
            manifest.update(relative.encode("utf-8"))
            manifest.update(b"\0")
            manifest.update(digest.encode("ascii"))
            rows.append((path, data, digest))
        return manifest.hexdigest(), rows

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.cache_file)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                relative_path TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                links_json TEXT NOT NULL,
                digest TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                document_id UNINDEXED, title, body, tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        return connection

    def refresh(self) -> str:
        with _REFRESH_LOCK:
            revision, rows = self._manifest(self._source_paths())
            with self._connect() as connection:
                current = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'revision'"
                ).fetchone()
                if current and current["value"] == revision:
                    return revision
                connection.execute("DELETE FROM documents")
                connection.execute("DELETE FROM documents_fts")
                for path, data, digest in rows:
                    relative = path.relative_to(self.wiki_root).as_posix()
                    text = data.decode("utf-8", errors="replace")
                    payload: dict[str, Any] | None = None
                    kind = "wiki"
                    if path.suffix.casefold() == ".json":
                        kind = "capability"
                        try:
                            loaded = json.loads(text)
                            payload = loaded if isinstance(loaded, dict) else None
                        except json.JSONDecodeError:
                            payload = None
                    title = _title(path, text, payload)
                    searchable = " ".join([text, *(_tokens(title + " " + text))])
                    document_id = "DOC-" + _sha256(relative.encode("utf-8"))[:20].upper()
                    connection.execute(
                        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            document_id,
                            relative,
                            kind,
                            title,
                            text,
                            json.dumps(_links(text), ensure_ascii=False),
                            digest,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO documents_fts VALUES (?, ?, ?)",
                        (document_id, title, searchable),
                    )
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES('revision', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (revision,),
                )
            return revision

    def search(
        self,
        query: str,
        *,
        model_id: str,
        candidates: int = SCENARIO_RETRIEVAL_CANDIDATES,
        max_documents: int = SCENARIO_RETRIEVAL_MAX_DOCUMENTS,
    ) -> EvidenceSnapshot:
        revision = self.refresh()
        aliases = " ".join(_ROBOT_ALIASES.get(model_id, ()))
        terms = _tokens(query + " " + model_id.replace("_", " ") + " " + aliases)
        if not terms:
            return EvidenceSnapshot(revision, ())
        match_query = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:80])
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*, bm25(documents_fts, 4.0, 1.0) AS rank
                FROM documents_fts
                JOIN documents AS d USING(document_id)
                WHERE documents_fts MATCH ?
                ORDER BY rank ASC
                LIMIT ?
                """,
                (match_query, max(1, candidates)),
            ).fetchall()
            selected = list(rows)
            selected_ids = {str(row["document_id"]) for row in selected}
            linked_targets = {
                link
                for row in selected[:10]
                for link in json.loads(str(row["links_json"]))
            }
            if linked_targets:
                for row in connection.execute("SELECT * FROM documents ORDER BY document_id"):
                    stem = Path(str(row["relative_path"])).stem.casefold()
                    if stem in linked_targets and row["document_id"] not in selected_ids:
                        selected.append(row)
                        selected_ids.add(str(row["document_id"]))
            model_terms = set(_tokens(model_id.replace("_", " ")))
            query_terms = set(terms)
            selected.sort(
                key=lambda row: (
                    -len((query_terms | model_terms) & set(_tokens(str(row["title"])))),
                    float(row["rank"]) if "rank" in row.keys() else 9999.0,
                    str(row["document_id"]),
                )
            )
            documents: list[EvidenceDocument] = []
            total = 0
            for row in selected:
                text = str(row["body"])[:SCENARIO_RETRIEVAL_MAX_PAGE_CHARS]
                remaining = SCENARIO_RETRIEVAL_MAX_TOTAL_CHARS - total
                if remaining <= 0:
                    break
                text = text[:remaining]
                if not text:
                    continue
                documents.append(
                    EvidenceDocument(
                        document_id=str(row["document_id"]),
                        kind=str(row["kind"]),
                        title=str(row["title"]),
                        text=text,
                        digest=str(row["digest"]),
                    )
                )
                total += len(text)
                if len(documents) >= max(1, min(max_documents, candidates)):
                    break
        return EvidenceSnapshot(revision, tuple(documents))


def retrieve_scenario_evidence(
    query: str, model_id: str, *, limit: int | None = None
) -> EvidenceSnapshot:
    team = get_team_config(model_id)
    return ScenarioEvidenceIndex(team.wiki_dir).search(
        query,
        model_id=model_id,
        max_documents=limit if limit is not None else SCENARIO_RETRIEVAL_MAX_DOCUMENTS,
    )


def anonymous_text(value: str) -> str:
    def wiki_label(match: re.Match[str]) -> str:
        raw = match.group(1)
        if "|" in raw:
            return raw.split("|", 1)[1].strip()
        return "linked Wiki concept"

    value = _WIKI_LINK_RE.sub(wiki_label, value)
    value = _LOCAL_MARKDOWN_LINK_RE.sub(lambda match: match.group(1), value)
    return _LOCAL_PATH_RE.sub("[internal reference removed]", value)


def anonymous_context(documents: Iterable[EvidenceDocument]) -> str:
    return "\n\n".join(
        f"===== APPROVED EVIDENCE {index} ({doc.kind}) =====\n{anonymous_text(doc.text)}"
        for index, doc in enumerate(documents, start=1)
    )
