from __future__ import annotations

import datetime
import re

from worker.config import get_team_config

STOP_WORDS = {
    "的", "了", "是", "在", "什么", "怎么", "如何", "为", "吗", "呢", "吧", "啊",
    "我", "你", "他", "她", "它", "们", "这", "那", "哪", "个", "些", "有", "没",
    "不", "也", "就", "都", "而", "且", "或", "但", "如果", "因为", "所以", "可以",
    "需要", "一个", "这个", "那个",
}


def _keywords(text: str) -> list[str]:
    tokens = re.split(r"[\s,，。！？、；：\"'（）()/\\\-_\[\]【】]+", text.lower())
    return [token for token in tokens if len(token) > 1 and token not in STOP_WORDS]


def has_wiki_content(team: str, question: str) -> bool:
    keywords = _keywords(question)
    if not keywords:
        return True
    
    tc = get_team_config(team)
    if not tc.wiki_dir.exists():
        return False
        
    for path in tc.wiki_dir.rglob("*.md"):
        if path.name in {"log.md", "unanswered.md"}:
            continue
        if any(keyword in path.stem.lower() for keyword in keywords):
            return True
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:800].lower()
        except OSError:
            continue
        if any(keyword in head for keyword in keywords):
            return True
    return False


def log_unanswered(team: str, question: str) -> None:
    tc = get_team_config(team)
    unanswered_file = tc.wiki_dir / "unanswered.md"
    
    unanswered_file.parent.mkdir(parents=True, exist_ok=True)
    if unanswered_file.exists():
        existing = unanswered_file.read_text(encoding="utf-8", errors="ignore")
        if question in existing:
            return
    else:
        unanswered_file.write_text(
            "# 未命中问题收集\n\n> 以下问题在知识库中没有足够资料。\n\n## 问题列表\n\n",
            encoding="utf-8",
        )
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with unanswered_file.open("a", encoding="utf-8") as handle:
        handle.write(f"- [{now}] {question}\n")
