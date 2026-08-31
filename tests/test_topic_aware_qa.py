from __future__ import annotations

from worker.langgraph_qa.qa.graph import route_after_plan
from worker.langgraph_qa.qa.nodes import plan as planner
from worker.topic_policy import (
    canonicalize_product_names,
    final_response_policy_text,
    retrieval_policy_text,
    sanitize_customer_output,
)


def _state(**overrides):
    state = {
        "active_topic": {"topic_type": "robot_scope"},
        "strict_robot_scope": True,
        "scope_relation": "in_scope",
        "queried_entity_type": "robot",
        "explicit_entities": ["Walker S2 EDU 探索者"],
    }
    state.update(overrides)
    return state


def test_robot_mismatch_requires_all_semantic_conditions() -> None:
    assert route_after_plan(_state(scope_relation="out_of_scope")) == "scope_stop"


def test_tool_question_is_not_suppressed_by_robot_scope() -> None:
    assert route_after_plan(_state(scope_relation="out_of_scope", queried_entity_type="tool")) == "search"


def test_tool_answer_policy_answers_object_before_topic_suggestion() -> None:
    policy = " ".join(final_response_policy_text().split())
    assert "answer that object first" in policy
    assert "may be added only when evidence clearly connects" in policy


def test_dex_heavy_history_does_not_enter_generic_question(monkeypatch) -> None:
    monkeypatch.setattr(planner, "get_chat_model", lambda: None)
    result = planner.plan_node({
        "question": "有哪些遥操作方案？",
        "robot_topic": "全部机器人",
        "recent_history": [{"role": "assistant", "content": "天工行者DEX 的能力说明"}],
    })
    assert "DEX" not in result["standalone_question"]
    assert all("DEX" not in query for query in result["search_queries"])
    assert result["history_used"] == []


def test_robot_history_does_not_rewrite_thinkerstudio_question(monkeypatch) -> None:
    monkeypatch.setattr(planner, "get_chat_model", lambda: None)
    result = planner.plan_node({
        "question": "ThinkerStudio 是什么？",
        "robot_topic": "Walker_S2_EDU探索者",
        "recent_history": [{"role": "assistant", "content": "Walker S2 EDU 探索者"}],
    })
    assert result["standalone_question"] == "ThinkerStudio 是什么？"
    assert "Walker" not in " ".join(result["search_queries"])


def test_three_point_zero_alias_never_leaks() -> None:
    output = canonicalize_product_names("TienKung 3.0 与天工行者3.0")
    assert output == "天工行者DEX 与天工行者DEX"
    assert "3.0" not in output


def test_ambiguous_two_point_zero_does_not_pick_a_product() -> None:
    output = canonicalize_product_names("TienKung 2.0")
    assert output == "未明确的天工行者旧版型号"
    assert all(name not in output for name in ("天工行者无界", "天工行者无疆", "天工行者基础版"))


def test_policy_forbids_cross_robot_capability_transfer() -> None:
    policy = retrieval_policy_text()
    assert "Never transfer a capability from one robot to another" in policy


def test_no_evidence_language_is_customer_facing() -> None:
    output = sanitize_customer_output("The Wiki does not contain information about this.", "en")
    assert output == "The requested information is not currently confirmed."


def test_final_sanitizer_removes_every_internal_term() -> None:
    terms = "Wiki knowledge base retrieval BM25 FTS5 Jieba search results candidate pages reranking planner reasoner LangGraph LLM calls prompts system instructions internal scope labels internal abstraction labels"
    output = sanitize_customer_output(terms, "en").lower()
    for forbidden in ("wiki", "knowledge base", "retrieval", "bm25", "fts5", "jieba", "planner", "reasoner", "langgraph", "llm calls", "prompts"):
        assert forbidden not in output


def test_generic_non_robot_question_is_not_topic_prefixed(monkeypatch) -> None:
    monkeypatch.setattr(planner, "get_chat_model", lambda: None)
    result = planner.plan_node({
        "question": "有哪些开发者工具？",
        "robot_topic": "Walker_S2_EDU探索者",
        "recent_history": [{"role": "assistant", "content": "Walker S2 EDU 探索者"}],
    })
    assert "Walker" not in " ".join(result["search_queries"])
    assert "开发者工具" in " ".join(result["search_queries"])


def test_pronoun_follow_up_uses_history(monkeypatch) -> None:
    monkeypatch.setattr(planner, "get_chat_model", lambda: None)
    result = planner.plan_node({
        "question": "它支持吗？",
        "robot_topic": "全部机器人",
        "recent_history": [{"role": "user", "content": "ThinkerStudio 是什么？"}],
    })
    assert "ThinkerStudio" in result["standalone_question"]
    assert result["history_used"] == ["recent conversation"]
