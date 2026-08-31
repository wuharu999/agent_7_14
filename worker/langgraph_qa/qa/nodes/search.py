from typing import Dict, Any, List
from worker.langgraph_qa.qa.state import QAState
from worker.langgraph_qa.runtime import get_runtime
from worker.langgraph_qa.wiki.search import search_wiki, SearchResult


def search_node(state: QAState) -> Dict[str, Any]:
    """Execute local SQLite FTS search using jieba tokenization (0 LLM calls)."""
    retrieval_round = state.get("retrieval_round", 0)
    additional_queries = state.get("additional_search_queries", [])

    # If this is a second-pass retrieval and additional_search_queries exists, use them
    if retrieval_round > 0 and additional_queries:
        queries_to_run = additional_queries
    else:
        queries_to_run = state.get("search_queries", [])

    if not queries_to_run:
        queries_to_run = [state.get("question", "")]

    robot_topic = state.get("robot_topic", "全部机器人")
    existing_results = state.get("search_results", [])

    # Map existing results by path to avoid duplicates and preserve or update scores
    results_by_path: Dict[str, Dict[str, Any]] = {}
    for r in existing_results:
        item = r if isinstance(r, dict) else r.to_dict()
        results_by_path[item["path"]] = item

    for q in queries_to_run:
        if not q or not isinstance(q, str) or not q.strip():
            continue
        runtime = get_runtime()
        res_list: List[SearchResult] = search_wiki(
            query=q.strip(),
            robot_topic=robot_topic,
            limit=runtime.max_candidates,
            db_path=runtime.search_db,
        )
        for res in res_list:
            res_dict = res.to_dict()
            path = res_dict["path"]
            if path not in results_by_path:
                results_by_path[path] = res_dict
            else:
                # If score in new query is higher, update score
                if res_dict["bm25_score"] > results_by_path[path].get("bm25_score", 0):
                    results_by_path[path]["bm25_score"] = res_dict["bm25_score"]

    combined_results = list(results_by_path.values())
    # Sort combined results by score descending
    combined_results.sort(key=lambda x: x["bm25_score"], reverse=True)

    return {
        "search_results": combined_results[: get_runtime().max_candidates],
        "retrieval_round": retrieval_round + 1,
    }
