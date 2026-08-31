import time
from typing import Dict, Any, List, Optional
import uuid
from langgraph.graph import StateGraph, START, END
from worker.langgraph_qa.qa.state import QAState
from worker.langgraph_qa.qa.nodes.plan import plan_node
from worker.langgraph_qa.qa.nodes.search import search_node
from worker.langgraph_qa.qa.nodes.expand_related import expand_related_node
from worker.langgraph_qa.qa.nodes.reason import reason_node
from worker.langgraph_qa.qa.nodes.load_evidence import load_evidence_node
from worker.langgraph_qa.qa.nodes.final_answer import final_answer_node


def should_search_again(state: QAState) -> str:
    """
    Conditional router: permits maximum 1 extra search round if evidence is insufficient.
    Guarantees hard ceiling of 4 LLM calls (nominal 3 LLM calls).
    """
    need_more = state.get("need_more_search", False)
    retrieval_round = state.get("retrieval_round", 1)
    if need_more and retrieval_round < 2:
        return "search"
    return "load_evidence"


def build_qa_graph():
    """Build and compile the bounded reasoning Q&A state graph."""
    workflow = StateGraph(QAState)

    # Add nodes
    workflow.add_node("plan", plan_node)
    workflow.add_node("search", search_node)
    workflow.add_node("expand_related", expand_related_node)
    workflow.add_node("reason", reason_node)
    workflow.add_node("load_evidence", load_evidence_node)
    workflow.add_node("final_answer", final_answer_node)

    # Add linear edges for initial pass
    workflow.add_edge(START, "plan")
    workflow.add_edge("plan", "search")
    workflow.add_edge("search", "expand_related")
    workflow.add_edge("expand_related", "reason")

    # Conditional edge after reasoning (2nd search pass or proceed to evidence loading)
    workflow.add_conditional_edges(
        "reason",
        should_search_again,
        {
            "search": "search",
            "load_evidence": "load_evidence",
        },
    )

    workflow.add_edge("load_evidence", "final_answer")
    workflow.add_edge("final_answer", END)

    return workflow.compile()


def run_qa_pipeline(
    question: str,
    language: str = "zh",
    robot_topic: str = "全部机器人",
    history: Optional[List[Dict[str, str]]] = None,
    stream: bool = False,
    request_id: Optional[str] = None,
    defer_final_answer: bool = False,
) -> Dict[str, Any]:
    """
    Public entry point for running the reasoning-first Wiki Q&A graph pipeline.
    """
    start_time = time.perf_counter()
    graph = build_qa_graph()
    req_id = request_id or f"req_{uuid.uuid4().hex[:8]}"

    initial_state: QAState = {
        "request_id": req_id,
        "question": question,
        "language": str(language).strip() or "zh-CN",
        "robot_topic": robot_topic or "全部机器人",
        "recent_history": history or [],
        "defer_final_answer": defer_final_answer or stream,
        "retrieval_round": 0,
        "llm_call_count": 0,
    }
    result = graph.invoke(initial_state)
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    result["request_id"] = req_id
    result["elapsed_ms"] = elapsed_ms
    return result
