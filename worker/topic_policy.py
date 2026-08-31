from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml


_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
_GUIDE_PATH = _CONFIG_DIR / "TOPIC_ENTITY_GUIDE.md"
_RETRIEVAL_PATH = _CONFIG_DIR / "RETRIEVAL_POLICY.md"
_FINAL_PATH = _CONFIG_DIR / "FINAL_RESPONSE_POLICY.md"


@dataclass(frozen=True, slots=True)
class TopicContext:
    key: str
    label: str
    topic_type: str
    entities: tuple[str, ...]
    search_aliases: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "topic_type": self.topic_type,
            "entities": list(self.entities),
            "search_aliases": list(self.search_aliases),
        }


def _read_policy(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text.strip()
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"invalid policy frontmatter: {path}")
    metadata = yaml.safe_load(text[4:end]) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"policy frontmatter must be a mapping: {path}")
    return metadata, text[end + 5 :].strip()


@lru_cache(maxsize=1)
def _guide() -> tuple[dict[str, Any], str]:
    return _read_policy(_GUIDE_PATH)


@lru_cache(maxsize=1)
def retrieval_policy_text() -> str:
    return _read_policy(_RETRIEVAL_PATH)[1]


@lru_cache(maxsize=1)
def final_response_policy_text() -> str:
    return _FINAL_PATH.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def topic_entity_guide_text() -> str:
    return _GUIDE_PATH.read_text(encoding="utf-8").strip()


def _text_values(values: Any) -> tuple[str, ...]:
    return tuple(str(value).strip() for value in values or [] if str(value).strip())


@lru_cache(maxsize=128)
def resolve_topic(key: str, label: str = "") -> TopicContext:
    normalized_key = str(key or "all").strip()
    normalized_label = str(label or "").strip()
    for item in _guide()[0].get("topics", []):
        keys = _text_values(item.get("keys"))
        labels = _text_values(item.get("labels"))
        if normalized_key in keys or (normalized_label and normalized_label in labels):
            return TopicContext(
                key=normalized_key,
                label=normalized_label or (labels[0] if labels else normalized_key),
                topic_type=str(item.get("type", "robot_scope")),
                entities=_text_values(item.get("entities")),
                search_aliases=_text_values(item.get("search_aliases")),
            )
    visible_label = normalized_label or normalized_key
    return TopicContext(
        key=normalized_key,
        label=visible_label,
        topic_type="all_robots" if normalized_key == "all" else "robot_scope",
        entities=(visible_label,) if normalized_key != "all" else (),
        search_aliases=(visible_label,) if normalized_key != "all" else (),
    )


@lru_cache(maxsize=1)
def _canonical_rules() -> tuple[tuple[re.Pattern[str], str], ...]:
    rules: list[tuple[re.Pattern[str], str]] = []
    for item in _guide()[0].get("canonical_aliases", []):
        canonical = str(item.get("canonical", "")).strip()
        for pattern in item.get("patterns", []):
            if canonical and pattern:
                rules.append((re.compile(str(pattern), re.IGNORECASE), canonical))
    for item in _guide()[0].get("ambiguous_alias_groups", []):
        neutral = str(item.get("label", "")).strip()
        for pattern in item.get("patterns", []):
            if neutral and pattern:
                rules.append((re.compile(str(pattern), re.IGNORECASE), neutral))
    return tuple(rules)


def canonicalize_product_names(text: str) -> str:
    """Apply the single configured alias policy at prompt and output boundaries."""
    result = str(text)
    for pattern, replacement in _canonical_rules():
        result = pattern.sub(replacement, result)
    return result


def canonicalized_entities(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = canonicalize_product_names(str(value).strip())
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def topic_search_aliases(topic_label: str) -> list[str]:
    for item in _guide()[0].get("topics", []):
        labels = _text_values(item.get("labels"))
        if topic_label in labels:
            return list(_text_values(item.get("search_aliases")))
    return [] if topic_label in {"", "全部机器人", "All Robots"} else [topic_label]


def retrieval_query_variants(query: str, *, limit: int = 5) -> list[str]:
    original = str(query).strip()
    variants = [original] if original else []
    canonical = canonicalize_product_names(original)
    if canonical and canonical not in variants:
        variants.append(canonical)
    return variants[: max(1, limit)]


@lru_cache(maxsize=1)
def _forbidden_patterns() -> tuple[re.Pattern[str], ...]:
    metadata, _ = _read_policy(_FINAL_PATH)
    terms = sorted(_text_values(metadata.get("forbidden_terms")), key=len, reverse=True)
    return tuple(re.compile(re.escape(term), re.IGNORECASE) for term in terms)


_GAP_MESSAGES = {
    "zh-CN": "目前尚未确认您所询问的信息。",
    "zh-TW": "目前尚未確認您所詢問的資訊。",
    "ko": "요청하신 정보는 현재 확인되지 않았습니다.",
    "ja": "ご質問の情報は現時点では確認できていません。",
    "en": "The requested information is not currently confirmed.",
    "pt": "A informação solicitada ainda não está confirmada.",
    "ru": "Запрошенная информация пока не подтверждена.",
    "es": "La información solicitada aún no está confirmada.",
}


def no_confirmed_information(language: str) -> str:
    return _GAP_MESSAGES.get(str(language), _GAP_MESSAGES["zh-CN"])


def sanitize_customer_output(text: str, language: str = "zh") -> str:
    result = canonicalize_product_names(text)
    gap = no_confirmed_information(language)
    gap_patterns = (
        r"(?:the\s+)?(?:wiki|knowledge base)(?:\s+documentation)?\s+(?:(?:has|contains|provides)\s+no|does\s+not\s+(?:have|contain|provide))\s+(?:information|data|details)[^.。]*[.。]?",
        r"(?:Wiki|知识库)(?:中|里)?(?:没有|暂无|未找到|不包含)[^。.!！?？]*(?:信息|资料|内容|结果)[。.!！?？]?",
    )
    for pattern in gap_patterns:
        result = re.sub(pattern, gap, result, flags=re.IGNORECASE)
    for pattern in _forbidden_patterns():
        result = pattern.sub("", result)
    result = re.sub(r"[ \t]{2,}", " ", result)
    result = re.sub(r" *\n *", "\n", result)
    return result.strip()


CANONICAL_TERMINOLOGY_PROMPT = """Mandatory customer-facing identity policy:
Never output a legacy TianGong 2.0 or TianGong 3.0 name. Use the canonical identities defined
in TOPIC_ENTITY_GUIDE. In particular, every 3.0-style
TienKung alias must be written as `天工行者DEX`. A bare 2.0 alias is ambiguous and must not be
assigned to one product; ask for the exact edition or use its configured neutral label. Apply
the same policy to the question, history, evidence, and final answer."""

# The streaming filter keeps enough trailing text to prevent aliases or internal terms from
# crossing token boundaries before deterministic sanitation is applied.
TERMINOLOGY_HOLDBACK = 64
