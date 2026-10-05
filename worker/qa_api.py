from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
from collections.abc import Awaitable, Callable, Iterator, Sequence
from pathlib import Path
from typing import TypeVar

from worker.qa_response import (
    _STREAM_SAFETY_HOLDBACK,
    _safe_answer,
    check_predefined_responses,
    generic_error_response,
    is_internal_processing_error,
    with_ai_notice,
)
from worker.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_TIMEOUT,
    get_team_config,
)
from worker.conversation_store import ConversationTurn
from worker.prompt_security import GuardDecision, refusal_text
from worker.qa_images import strip_qa_image_markdown
from worker.terminology import (
    sanitize_customer_output,
)

log = logging.getLogger("worker.qa_api")

EXCLUDED_FILENAMES = {"index.md", "overview.md", "log.md"}
WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
BRACKET_REFERENCE_RE = re.compile(
    r"\[([^\]\n]{1,2048})\](?:\(([^)\n]{1,4096})\))?"
)
CJK_BRACKET_REFERENCE_RE = re.compile(r"【([^】\n]{1,2048})】")
BARE_MARKDOWN_PATH_RE = re.compile(
    r"(?<![\w])((?:[A-Za-z]:[\\/]|/|\.{1,2}/)?"
    r"(?:[^\s\[\]()<>]+[\\/])*[^\s\[\]()<>]+\.md)(?![\w])",
    re.IGNORECASE,
)
SOURCE_SECTION_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:sources?|references?|source files?|"
    r"参考(?:资料|来源|文件|文献)?|引用(?:资料|来源)?|资料来源|"
    r"fuentes?|referencias?|fontes?|источники|출처)"
    r"[ \t]*[:：]?[ \t]*(?:\n|$)",
    re.IGNORECASE,
)
UNSUPPORTED_SYNTHESIS_SECTION_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__)?[ \t]*"
    r"(?:核心结论|核心結論|综合判断|綜合判斷|总体结论|總體結論|"
    r"最终结论|最終結論|结论|結論|overall[ \t]+conclusion|"
    r"final[ \t]+conclusion|summary[ \t]+judg(?:e)?ment|"
    r"conclus[aã]o(?:[ \t]+geral)?|conclusi[oó]n(?:[ \t]+general)?|"
    r"общий[ \t]+вывод|вывод|종합[ \t]*판단|결론|総合判断)"
    r"[ \t]*(?:\*\*|__)?[ \t]*[:：]?[ \t]*(?:\r?\n|$)",
    re.IGNORECASE,
)
UNSUPPORTED_SYNTHESIS_CLAIM_RE = re.compile(
    r"(?:以上|上述|前面|這些)?(?:内容|信息|说法|回答|答案|叙述|描述)?"
    r"(?:仅|只|只是|仅仅|僅)(?:来自|出自|来源|源于|基于)[^\n。！？.!?]{0,30}"
    r"(?:wiki|知识库|文档|资料|文件)|"
    r"(?:没有|无|并无|缺乏|缺少)(?:任何|实际|确凿|充分)?(?:证据|实证|依据)"
    r"(?:支持|证明|佐证|证实|可供)?|"
    r"未(?:经|能)(?:证实|核实|验证)|"
    r"仅供参考|仅供內部參考|"
    r"官方口径|官方銷售口徑|官方销售口径|销售口径|銷售口徑|"
    r"官方称|官方稱|官方说法|官方說法|官方强调|官方強調|"
    r"量化对比数据|量化對比數據|无量化对比数据|無量化對比數據|"
    r"文档中仅记录|文档中僅記錄|文档仅记录|文檔僅記錄|"
    r"除上述[^\n。！？.!?]{0,12}(?:外|以外)[^\n。！？.!?]{0,12}"
    r"(?:未提供|没有提供|無提供)|"
    r"(?:no|not|without)[ \t]+(?:any[ \t]+)?(?:evidence|proof)"
    r"|\bnot[ \t]+backed[ \t]+by[ \t]+(?:any[ \t]+)?evidence\b"
    r"|\bunverified\b|\bunsubstantiated\b"
    r"|\bwithout[ \t]+(?:supporting[ \t]+)?evidence\b",
    re.IGNORECASE,
)
_BlockingResult = TypeVar("_BlockingResult")
_BLOCKING_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="qa-blocking",
)
_STREAM_BOUNDARY_RE = re.compile(r"(?:\r?\n|[。！？；，!?]|[.,;:](?=\s))")
_STREAM_END_CHARS = "。！？；，!?.,;:"


class QAAPIError(RuntimeError):
    """Provider retrieval or answer generation failed safely."""


def ordered_links_in(markdown: str) -> list[str]:
    """Return normalized Obsidian targets in first-seen index order."""
    return list(
        dict.fromkeys(
            target.strip()
            for raw in WIKI_LINK_RE.findall(markdown)
            if (target := raw.split("|", 1)[0].split("#", 1)[0].strip())
        )
    )


class Wiki:
    """Local page catalog for filtering internal references from public answers."""

    def __init__(self, root: Path):
        expanded_root = root.expanduser()
        if expanded_root.is_symlink():
            raise ValueError("Wiki root cannot be a symlink")
        self.root = expanded_root.resolve()
        self.index_path = self.root / "index.md"
        if not self.index_path.is_file():
            raise FileNotFoundError(f"Wiki index not found: {self.index_path}")
        raw_index_text = self.index_path.read_text(encoding="utf-8")
        self.pages = self._build_page_map()
        self.index_slugs = ordered_links_in(raw_index_text)
        self.allowed_slugs = set(self.index_slugs)
        self.retrievable_slugs = self.allowed_slugs & self.pages.keys()

    def _build_page_map(self) -> dict[str, list[Path]]:
        paths_by_slug: dict[str, list[Path]] = {}
        for path in self.root.rglob("*.md"):
            if path.name in EXCLUDED_FILENAMES or path.is_symlink() or not path.is_file():
                continue
            try:
                relative = path.resolve().relative_to(self.root)
            except ValueError:
                continue
            keys = {path.stem, relative.with_suffix("").as_posix()}
            for key in keys:
                paths_by_slug.setdefault(key, []).append(path)
        return {slug: sorted(paths) for slug, paths in paths_by_slug.items()}


def strip_retrieval_references(
    text: str,
    wiki: Wiki,
    allowed_slugs: set[str] | None = None,
) -> str:
    """Remove internal Wiki citations without touching ordinary links or images."""
    source_section = SOURCE_SECTION_RE.search(text)
    if source_section:
        text = text[: source_section.start()]

    known_slugs = {
        slug.casefold()
        for slug in (wiki.retrievable_slugs if allowed_slugs is None else allowed_slugs)
    }

    def replace(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        target = (match.group(2) or "").strip()
        folded_label = label.casefold()
        folded_target = target.casefold()
        label_is_slug = not target and folded_label in known_slugs
        contains_local_markdown = ".md" in folded_label or (
            bool(target)
            and ".md" in folded_target
            and not folded_target.startswith(("http://", "https://"))
        )
        if label_is_slug or contains_local_markdown:
            return ""
        return match.group(0)

    text = BRACKET_REFERENCE_RE.sub(replace, text)

    def remove_cjk_reference(match: re.Match[str]) -> str:
        label = match.group(1).strip().casefold()
        return "" if label in known_slugs or ".md" in label else match.group(0)

    text = CJK_BRACKET_REFERENCE_RE.sub(remove_cjk_reference, text)

    def remove_bare_markdown_path(match: re.Match[str]) -> str:
        path = match.group(1)
        if path.casefold().startswith(("http://", "https://")):
            return path
        return ""

    text = BARE_MARKDOWN_PATH_RE.sub(remove_bare_markdown_path, text)
    text = re.sub(r"[ \t]+([,.;!?，。；！？])", r"\1", text)
    return text


def strip_unsupported_synthesis(text: str) -> str:
    """Remove unsupported interpretive tails and known high-risk inference claims."""
    section = UNSUPPORTED_SYNTHESIS_SECTION_RE.search(text)
    if section:
        text = text[: section.start()]

    kept_lines = [
        line
        for line in text.splitlines(keepends=True)
        if not UNSUPPORTED_SYNTHESIS_CLAIM_RE.search(line)
    ]
    return re.sub(r"(?:\r?\n){3,}", "\n\n", "".join(kept_lines))


class UnsupportedSynthesisStreamFilter:
    """Suppress unsupported synthesis before it becomes visible in an SSE stream."""

    def __init__(self) -> None:
        self.buffer = ""
        self.dropping_conclusion = False

    def feed(self, text: str) -> str:
        if not text or self.dropping_conclusion:
            return ""
        self.buffer += text
        section = UNSUPPORTED_SYNTHESIS_SECTION_RE.search(self.buffer)
        if section:
            safe = strip_unsupported_synthesis(self.buffer[: section.start()])
            self.buffer = ""
            self.dropping_conclusion = True
            return safe

        boundaries = list(_STREAM_BOUNDARY_RE.finditer(self.buffer))
        if not boundaries and not self.buffer.rstrip()[-1:] in _STREAM_END_CHARS:
            return ""
        cutoff = boundaries[-1].end() if boundaries else len(self.buffer.rstrip())
        safe = strip_unsupported_synthesis(self.buffer[:cutoff])
        self.buffer = self.buffer[cutoff:]
        return safe

    def finish(self) -> str:
        if self.dropping_conclusion:
            return ""
        safe = strip_unsupported_synthesis(self.buffer)
        self.buffer = ""
        return safe


class RetrievalReferenceStreamFilter:
    """Release complete phrases while unfinished references remain buffered."""

    def __init__(self, wiki: Wiki, allowed_slugs: set[str] | None = None) -> None:
        self.wiki = wiki
        self.allowed_slugs = allowed_slugs
        self.buffer = ""
        self.dropping_source_section = False

    def feed(self, text: str) -> str:
        if not text or self.dropping_source_section:
            return ""
        self.buffer += text
        source_section = SOURCE_SECTION_RE.search(self.buffer)
        if source_section:
            safe = strip_retrieval_references(
                self.buffer[: source_section.start()], self.wiki, self.allowed_slugs
            )
            self.buffer = ""
            self.dropping_source_section = True
            return safe
        boundaries = list(_STREAM_BOUNDARY_RE.finditer(self.buffer))
        if not boundaries:
            return ""

        cutoff = boundaries[-1].end()
        prefix = self.buffer[:cutoff]
        unmatched_open = prefix.rfind("[")
        if unmatched_open > prefix.rfind("]"):
            cutoff = unmatched_open
        unmatched_cjk_open = prefix.rfind("【")
        if unmatched_cjk_open > prefix.rfind("】"):
            cutoff = min(cutoff, unmatched_cjk_open)
        if cutoff == 0:
            return ""
        safe = strip_retrieval_references(
            self.buffer[:cutoff], self.wiki, self.allowed_slugs
        )
        self.buffer = self.buffer[cutoff:]
        return safe

    def finish(self) -> str:
        if self.dropping_source_section:
            return ""
        safe = strip_retrieval_references(
            self.buffer, self.wiki, self.allowed_slugs
        )
        self.buffer = ""
        return safe


class DeepSeekClient:
    def __init__(self, model: str = DEEPSEEK_MODEL):
        if not DEEPSEEK_API_KEY:
            raise QAAPIError("DEEPSEEK_API_KEY is not configured")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise QAAPIError("openai is not installed") from exc
        self.client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=float(DEEPSEEK_TIMEOUT),
        )
        self.model = model
        self.timeout = DEEPSEEK_TIMEOUT

    @staticmethod
    def _options() -> dict[str, object]:
        return {
            "temperature": 0,
            "extra_body": {"thinking": {"type": "disabled"}},
        }

    def complete(self, system: str, user: str) -> str:
        result = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **self._options(),
        )
        content = result.choices[0].message.content
        if not content:
            raise QAAPIError("DeepSeek returned an empty response")
        return str(content)

    def stream(self, system: str, user: str) -> Iterator[str]:
        result = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            stream=True,
            **self._options(),
        )
        for chunk in result:
            if chunk.choices and (content := chunk.choices[0].delta.content):
                yield str(content)


async def _run_blocking(
    function: Callable[..., _BlockingResult],
    *args: object,
    **kwargs: object,
) -> _BlockingResult:
    result = _BLOCKING_EXECUTOR.submit(function, *args, **kwargs)
    try:
        while not result.done():
            await asyncio.sleep(0.005)
        return result.result()
    except asyncio.CancelledError:
        result.cancel()
        raise


async def _retrieve_and_stream(
    question: str,
    *,
    team: str,
    language: str,
    topic_label: str = "",
    history: Sequence[ConversationTurn],
    on_token: Callable[[str], Awaitable[None]],
) -> str:
    from worker.langgraph_qa import stream_answer

    # These existing filters are output-boundary safeguards, not retrieval.
    # They keep a generated answer from exposing a path/reference or an
    # unsupported conclusion even if a provider ignores its final prompt.
    wiki = await _run_blocking(Wiki, get_team_config(team).wiki_dir)
    reference_filter = RetrievalReferenceStreamFilter(wiki, wiki.retrievable_slugs)
    synthesis_filter = UnsupportedSynthesisStreamFilter()

    async def safe_token(text: str) -> None:
        safe = synthesis_filter.feed(reference_filter.feed(text))
        if safe:
            await on_token(safe)

    raw_answer = await stream_answer(
        question=question,
        team=team,
        language=language,
        topic_label=topic_label,
        history=history,
        wiki_root=get_team_config(team).wiki_dir,
        provider=DeepSeekClient(),
        on_token=safe_token,
    )
    tail = synthesis_filter.feed(reference_filter.finish()) + synthesis_filter.finish()
    if tail:
        await on_token(tail)
    return strip_unsupported_synthesis(
        strip_retrieval_references(raw_answer, wiki, wiki.retrievable_slugs)
    ).strip()


async def run_qa_api_stream(
    question: str,
    *,
    team: str,
    language: str = "zh-CN",
    topic_label: str = "",
    history: Sequence[ConversationTurn] = (),
    on_chunk: Callable[[str, str, int], Awaitable[None]],
    on_replace: Callable[[str], Awaitable[None]] | None = None,
    guard_decision: GuardDecision | None = None,
) -> str:
    predefined = check_predefined_responses(question, language)
    if predefined:
        answer = with_ai_notice(predefined, language)
        await on_chunk(answer, "", 0)
        return answer
    if guard_decision and guard_decision.blocked:
        answer = with_ai_notice(refusal_text(language), language)
        await on_chunk(answer, "", 0)
        return answer

    pending_text = ""
    emitted_text = ""
    blocked_stream = False

    async def capture_token(text: str) -> None:
        nonlocal pending_text, emitted_text, blocked_stream
        if not text or blocked_stream:
            return
        pending_text += text
        if is_internal_processing_error(pending_text):
            blocked_stream = True
            pending_text = ""
            return
        safe_length = max(0, len(pending_text) - _STREAM_SAFETY_HOLDBACK)
        image_marker_start = pending_text.find("![")
        if image_marker_start >= 0:
            safe_length = min(safe_length, image_marker_start)
        if safe_length == 0:
            return
        safe_prefix = sanitize_customer_output(pending_text[:safe_length], language)
        pending_text = pending_text[safe_length:]
        emitted_text += safe_prefix
        await on_chunk(safe_prefix, "", 0)

    try:
        raw_answer = await _retrieve_and_stream(
            question,
            team=team,
            language=language,
            topic_label=topic_label,
            history=history,
            on_token=capture_token,
        )
        raw_safe_answer = _safe_answer(raw_answer, language)
        safe_answer = sanitize_customer_output(raw_safe_answer, language)
        if blocked_stream or raw_safe_answer != raw_answer:
            response = with_ai_notice(safe_answer, language)
            await on_chunk(response, "", 0)
            return response
        response = with_ai_notice(safe_answer, language)
        visible_response = strip_qa_image_markdown(response)
        if emitted_text and not visible_response.startswith(emitted_text) and on_replace is not None:
            await on_replace(visible_response)
            remaining = ""
        else:
            remaining = (
                visible_response[len(emitted_text):]
                if visible_response.startswith(emitted_text)
                else visible_response
            )
        if remaining:
            await on_chunk(remaining, "", 0)
        return response
    except Exception:  # Public QA boundary: log technical detail, return localized safe text.
        log.exception("Wiki Q&A providers failed")
        answer = with_ai_notice(generic_error_response(language), language)
        await on_chunk(answer, "", 0)
        return answer


async def run_qa_api(
    question: str,
    *,
    team: str,
    language: str = "zh-CN",
    topic_label: str = "",
    history: Sequence[ConversationTurn] = (),
    guard_decision: GuardDecision | None = None,
) -> str:
    async def discard(_text: str, _thinking: str, _thinking_tokens: int) -> None:
        return None

    return await run_qa_api_stream(
        question,
        team=team,
        language=language,
        topic_label=topic_label,
        history=history,
        on_chunk=discard,
        guard_decision=guard_decision,
    )
