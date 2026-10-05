from __future__ import annotations

import datetime

from worker.config import get_team_config


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
