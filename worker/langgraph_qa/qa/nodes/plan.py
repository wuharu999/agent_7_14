import logging
from typing import Dict, Any, List
from langchain_core.messages import SystemMessage, HumanMessage

from worker.langgraph_qa.qa.state import QAState
from worker.langgraph_qa.qa.schemas import PlannerOutput, PlanOutput
from worker.langgraph_qa.qa.model import get_chat_model


log = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """You are the Intent Planner for a Robotics Knowledge Q&A Agent.

Behavioral Policy & Intent Planning Rules:
1. Current Request Priority: The current user question and selected UI scope are the active state and reflect the user's immediate goal.
2. Anti-Topic Anchoring: Conversation history is archived context. Use it ONLY to resolve a clear reference in the current message (for example "它", "这个机器人", "it", or "this robot"). NEVER introduce an entity from history merely because the current request is underspecified.
3. Topic Relation: Return `continue` or `refine` only when the current message contains such a reference. If archived history exists but the current message has no reference, return `switch`, leave `current_subject` null, and do not add historical entities to the standalone question or search queries.
4. Scope Analysis: Return `scope_analysis` independently from topic_relation. Classify the current request against the selected UI scope as `in_scope`, `related_scope`, `cross_scope`, `out_of_scope`, or `ambiguous`.
   - `cross_scope` is intentional multi-product work such as a comparison and must continue with separated evidence.
   - `out_of_scope` means the current message clearly asks about a different product; do not rewrite it into the selected product.
   - A topic change within the same robot is not out of scope.
5. Solution-Oriented Abstraction:
   - "application_or_workflow": For general "how do I..." questions, workflows, or capability queries. Prefer complete platforms (e.g. ThinkerStudio, standard teleoperation workflows) over low-level motor/joint APIs.
   - "sdk_or_module": For subsystem, SDK, or module integration questions.
   - "api_or_interface": ONLY when the user explicitly asks for ROS topics, parameters, messages, or low-level API calls.
6. Intent Classification:
   - "how_to": Workflow, procedure, or solution requests.
   - "explicit_api": Inquiries for specific ROS topics, parameters, messages, or code APIs.
   - "concept": Definitions, architecture overviews, or conceptual explanations.
   - "comparison": Comparing robot models, algorithms, or peripherals.
   - "troubleshooting": Fault diagnosis, error codes, and maintenance issues.
7. FTS5 Search Queries: Generate 1 to 3 concise, high-signal lexical search terms (Chinese/English keywords) optimized for SQLite FTS5 BM25 search. Strip conversational filler words.
8. Scope & Language: Incorporate the selected robot/domain context if specified ({robot_topic}), and format standalone output matching the target language ({language}).

Parameters:
- Selected Robot/Topic Scope: {robot_topic}
- Target Answer Language: {language}
"""

HISTORY_REFERENCE_MARKERS = (
    "它", "这个机器人", "这个平台", "该机器人", "这款", "刚才那个", "上面提到的",
    "it", "this robot", "this platform", "that robot", "the above robot",
)

def _needs_history_resolution(question: str) -> bool:
    normalized = question.lower()
    return any(marker in normalized for marker in HISTORY_REFERENCE_MARKERS)


def _compact_history(messages: List[Dict[str, Any]]) -> str:
    lines = []
    for message in messages[-2:]:
        content = " ".join(str(message.get("content", "")).split())[:600]
        if content:
            lines.append(f"{message.get('role', 'user')}: {content}")
    return "\n".join(lines)


def plan_node(state: QAState) -> Dict[str, Any]:
    question = state.get("question", "")
    raw_history = state.get("recent_history", [])
    history = [
        message
        for message in raw_history[-4:]
        if isinstance(message, dict)
    ] if isinstance(raw_history, list) else []
    robot_topic = state.get("robot_topic", "全部机器人")
    language = state.get("language", "zh")
    call_count = state.get("llm_call_count", 0)

    use_history = bool(history) and _needs_history_resolution(str(question))
    history_str = _compact_history(history) if use_history else ""

    if llm := get_chat_model():
        try:
            structured_llm = llm.with_structured_output(PlannerOutput, method="function_calling")

            prompt = f"""
Archived Conversation Context (available only to resolve a clear current reference):
{history_str or 'Not used: the current turn has no resolvable reference.'}

Selected Robot Scope:
{robot_topic}

Current User Question:
{question}

Formulate the topic relation, active subject, standalone query, intent, preferred abstraction, and 1-3 targeted search terms:
"""
            res: PlannerOutput = structured_llm.invoke([
                SystemMessage(content=PLANNER_SYSTEM_PROMPT.format(robot_topic=robot_topic, language=language)),
                HumanMessage(content=prompt)
            ])
            if not isinstance(res, PlannerOutput):
                raise ValueError("planner returned no structured output")

            scope_analysis = res.scope_analysis.model_dump()
            scope_analysis["active_scope"] = robot_topic
            return {
                "standalone_question": res.standalone_question,
                "topic_relation": res.topic_relation if use_history else ("switch" if history else "ambiguous"),
                "current_subject": res.current_subject if use_history else None,
                "history_used": res.history_used if use_history else [],
                "history_ignored": res.history_ignored if use_history else (["recent conversation"] if history else []),
                "scope_analysis": scope_analysis,
                "intent": res.intent,
                "preferred_abstraction": res.preferred_abstraction,
                "search_queries": res.search_queries[:3] if res.search_queries else [question],
                "llm_call_count": call_count + 1,
            }
        except Exception:
            log.exception("LangGraph planner failed; using deterministic fallback")

    # Rule-based fallback if LLM API is unavailable
    q_lower = question.lower()
    is_api_question = any(k in q_lower for k in [
        "topic", "api", "msg", "srv", "消息", "接口", "参数", "帧率", "interface",
        "话题", "服务", "通信", "映射表", "报文", "协议", "uuid", "gatt", "baudrate", "波特率",
        "ros2", "ros 2", "action", "node", "节点", "驱动"
    ])
    is_comp_question = any(k in q_lower for k in ["对比", "区别", "比较", "versus", "vs", "difference"])
    is_trouble_question = any(k in q_lower for k in ["报错", "故障", "异常", "无法", "失败", "error", "fail", "bug", "issue", "排查"])
    is_concept_question = any(k in q_lower for k in ["概念", "定义", "what is", "concept", "架构", "原理"])

    if is_api_question:
        intent = "explicit_api"
        pref_abstraction = "api_or_interface"
    elif is_comp_question:
        intent = "comparison"
        pref_abstraction = "application_or_workflow"
    elif is_trouble_question:
        intent = "troubleshooting"
        pref_abstraction = "application_or_workflow"
    elif is_concept_question:
        intent = "concept"
        pref_abstraction = "application_or_workflow"
    else:
        intent = "how_to"
        pref_abstraction = "application_or_workflow"

    # Coreference resolution heuristic
    standalone = question
    if use_history:
        last_turn = history[-1].get("content", "")
        if len(question) < 25:
            standalone = f"{last_turn} {question}"

    # Clean stop words for keyword search
    clean_q = question
    for stop_word in [
        "接口是什么", "消息是什么", "是什么", "如何", "怎么", "怎样", "什么", "哪些",
        "的使用", "的是", "是", "请问", "我想了解", "我想要", "？", "?", "！", "!", "，", ",", "。"
    ]:
        clean_q = clean_q.replace(stop_word, " ")
    clean_q = " ".join(clean_q.split())
    if not clean_q:
        clean_q = question.strip() or "机器人"

    search_queries: List[str] = [clean_q]
    if robot_topic and robot_topic != "全部机器人":
        topic_query = f"{robot_topic} {clean_q}"
        if topic_query not in search_queries:
            search_queries.append(topic_query)

    # Ensure unique and non-empty queries
    deduped_queries = []
    for q in search_queries:
        q_str = q.strip()
        if q_str and q_str not in deduped_queries:
            deduped_queries.append(q_str)

    return {
        "standalone_question": standalone,
        "topic_relation": "continue" if use_history else ("switch" if history else "ambiguous"),
        "current_subject": None,
        "history_used": ["recent conversation"] if use_history else [],
        "history_ignored": ["recent conversation"] if history and not use_history else [],
        "scope_analysis": {
            "active_scope": robot_topic,
            "explicit_entities": [],
            "resolved_references": [],
            "relation": "ambiguous",
            "reason": "No live semantic scope classifier is configured.",
            "confidence": 0.0,
        },
        "intent": intent,
        "preferred_abstraction": pref_abstraction,
        "search_queries": deduped_queries[:3] if deduped_queries else [question],
        "llm_call_count": call_count,
    }
