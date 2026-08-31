from __future__ import annotations

from typing import Any

from worker.langgraph_qa.qa.state import QAState


_MESSAGES = {
    "zh-CN": "这个问题询问的是「{entity}」，当前选择的主题是「{topic}」。请切换到对应主题后继续提问。",
    "zh-TW": "這個問題詢問的是「{entity}」，目前選擇的主題是「{topic}」。請切換到對應主題後繼續提問。",
    "en": "This question is about “{entity}”, while the selected topic is “{topic}”. Please switch to the matching topic and ask again.",
    "ja": "この質問は「{entity}」についてですが、現在のトピックは「{topic}」です。該当するトピックに切り替えて、もう一度質問してください。",
    "ko": "이 질문은 ‘{entity}’에 관한 것이지만 현재 선택된 주제는 ‘{topic}’입니다. 해당 주제로 전환한 후 다시 질문해 주세요.",
    "pt": "Esta pergunta é sobre “{entity}”, mas o tópico selecionado é “{topic}”. Mude para o tópico correspondente e pergunte novamente.",
    "ru": "Вопрос относится к «{entity}», а выбрана тема «{topic}». Переключитесь на подходящую тему и задайте вопрос снова.",
    "es": "La pregunta trata sobre «{entity}», pero el tema seleccionado es «{topic}». Cambia al tema correspondiente y vuelve a preguntar.",
}


def scope_stop_node(state: QAState) -> dict[str, Any]:
    entities = state.get("canonicalized_entities", [])
    explicit = state.get("explicit_entities", [])
    entity = ""
    for item in entities:
        if isinstance(item, dict):
            entity = str(item.get("canonical_name") or item.get("mentioned_name") or "").strip()
            if entity:
                break
    entity = entity or (str(explicit[0]) if explicit else "另一个机器人")
    topic = str(state.get("robot_topic") or "当前机器人")
    language = str(state.get("language") or "zh-CN")
    return {"answer": _MESSAGES.get(language, _MESSAGES["zh-CN"]).format(entity=entity, topic=topic)}
