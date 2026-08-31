import json
import logging
from pathlib import Path
from typing import Dict, Any, List
from langchain_core.messages import SystemMessage, HumanMessage

from worker.langgraph_qa.qa.state import QAState
from worker.langgraph_qa.qa.schemas import ReasonOutput
from worker.langgraph_qa.qa.model import get_chat_model
from worker.langgraph_qa.runtime import get_runtime

log = logging.getLogger(__name__)

REASONER_SYSTEM_PROMPT = """You are the Solution Reasoner & Reranker for a Robotics Knowledge Q&A Agent.

Your responsibilities:
1. Intent & Abstraction Matching:
   - Original Current User Question: "{question}"
   - Standalone User Goal: "{standalone_question}"
   - Planner Intent: {intent} (Preferred Abstraction: {preferred_abstraction})
   - Topic Relation: {topic_relation}; history used: {history_used}
   - Scope Analysis: {scope_analysis}
   - General "how-to" / capability questions -> Prioritize complete supported solutions, applications, and workflows (Level 0/1, e.g. ThinkerStudio, standard teleoperation pipelines) over low-level motor/joint APIs (Level 2/3).
   - Explicit API / topic / parameter questions -> Select exact API/interface pages (Level 3/2) as the primary solution.
2. Evidence Selection:
   - Select 3 to 6 primary pages (`selected_pages`) providing direct evidence.
3. Uncertainty Identification:
   - If candidate pages include open questions or known limitations from `queries/` or `uncertainty: True` pages (e.g. model compatibility questions, unverified specs), list them in `uncertainties_to_check`.
4. Evidence-Based Image Selection:
   - Select 0 to 3 images (`selected_images`) from Candidate Images ONLY if an image materially illustrates an architecture diagram, workflow pipeline, or UI configuration described in the answer plan.
   - If no candidate image is an architecture/workflow diagram, return an EMPTY list `[]`. Returning 0 images is valid and expected.
5. Answer Planning:
   - Formulate `primary_solution` (name of tool/workflow/API), `direct_answer_plan` (concise core resolution), and `supporting_points` (2-4 key logical implementation steps).
6. Retrieval Sufficiency:
   - If evidence is genuinely missing and retrieval round < 2, set `need_more_search = true` and provide 1-2 targeted `additional_search_queries`. Otherwise set `need_more_search = false`.
7. Planner Fidelity Guard:
   - Compare the standalone goal with the ORIGINAL CURRENT USER QUESTION and selected scope. Do not accept a historical entity unless topic_relation is `continue` or `refine` and history_used is non-empty.
   - If the planner introduced an unsupported entity or constraint, set `planner_faithful = false`, list it in `unsupported_assumptions`, provide a history-free `corrected_standalone_question`, and request one targeted local retry. Do not expose private reasoning.
8. Evidence Scope Check:
   - Return `scope_consistency.valid = false` when selected evidence would transfer a capability from another product into the active scope without direct support.
   - For `cross_scope`, keep each product's evidence distinct and compare only supported facts. If scope-consistent evidence is missing, request the one permitted local retry with scope-specific queries.

Context:
- Selected Robot Scope: {robot_topic}
- Target Answer Language: {language}
"""


def load_image_candidates(selected_paths: List[str]) -> List[Dict[str, Any]]:
    """Load and rank candidate images from image_catalog.jsonl, filtering out decorative/low-utility images."""
    images = []
    image_catalog_path = get_runtime().image_catalog
    if not image_catalog_path.exists():
        return images

    path_set = set(selected_paths)
    with image_catalog_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                item = json.loads(line)
                if item.get("source_page") in path_set:
                    usefulness = item.get("usefulness", "medium")
                    image_type = item.get("image_type", "unknown")
                    # Filter out decorative and low usefulness images
                    if usefulness == "low" or image_type in ("decorative", "logo"):
                        continue

                    # Score images based on utility and diagram type
                    score = 0
                    if usefulness == "high":
                        score += 3
                    elif usefulness == "medium":
                        score += 1

                    if image_type in ("architecture_diagram", "workflow_diagram"):
                        score += 3
                    elif image_type in ("UI_screenshot", "configuration_screenshot"):
                        score += 2
                    elif image_type in ("hardware_photo", "chart"):
                        score += 1

                    item["_score"] = score
                    images.append(item)

    images.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return images[:10]


def reason_node(state: QAState) -> Dict[str, Any]:
    standalone = state.get("standalone_question", state.get("question", ""))
    search_results = state.get("search_results", [])
    robot_topic = state.get("robot_topic", "全部机器人")
    language = state.get("language", "zh")
    intent = state.get("intent", "how_to")
    pref_abstraction = state.get("preferred_abstraction", "application_or_workflow")
    retrieval_round = state.get("retrieval_round", 1)
    call_count = state.get("llm_call_count", 1)
    question = state.get("question", standalone)
    topic_relation = state.get("topic_relation", "ambiguous")
    history_used = state.get("history_used", [])
    scope_analysis = state.get("scope_analysis", {})

    candidate_paths = [r["path"] for r in search_results[:15]]
    image_candidates = load_image_candidates(candidate_paths)

    candidates_summary = "\n".join([
        f"- [{r.get('abstraction_level', 2)}] {r.get('title')} ({r.get('path')})\n"
        f"  Role: {r.get('document_role', 'workflow')} | Section: {r.get('wiki_section', '')} | Score: {r.get('bm25_score', 0):.2f}"
        + (" | [Uncertainty Page]" if r.get("wiki_section") == "queries" or r.get("uncertainty") else "")
        + (" | [1-Hop Expanded]" if r.get("boosted") else "")
        + f"\n  Tags: {', '.join(r.get('tags', [])[:5])}"
        + f"\n  Summary: {r.get('snippet', '').strip()}"
        for r in search_results[:15]
    ])

    image_summary = "\n".join([
        f"- Path: {img['path']} | Source: {img['source_page']} | Type: {img.get('image_type', 'unknown')} | Alt: {img['description']}"
        for img in image_candidates[:8]
    ])

    if llm := get_chat_model():
        try:
            structured_llm = llm.with_structured_output(ReasonOutput, method="function_calling")

            prompt = f"""
Candidate Wiki Pages:
{candidates_summary or 'None'}

Candidate Images:
{image_summary or 'None'}

Evaluate evidence and select solution pages, images, and answer plan:
"""
            res: ReasonOutput = structured_llm.invoke([
                SystemMessage(content=REASONER_SYSTEM_PROMPT.format(
                    question=question,
                    standalone_question=standalone,
                    intent=intent,
                    preferred_abstraction=pref_abstraction,
                    topic_relation=topic_relation,
                    history_used=history_used or "none",
                    scope_analysis=scope_analysis,
                    robot_topic=robot_topic,
                    language=language,
                )),
                HumanMessage(content=prompt)
            ])
            if not isinstance(res, ReasonOutput):
                raise ValueError("reasoner returned no structured output")

            selected_imgs = [img.model_dump() for img in res.selected_images]
            needs_fidelity_retry = not res.planner_faithful and retrieval_round < 2
            needs_scope_retry = not res.scope_consistency.valid and retrieval_round < 2
            retry_queries = res.additional_search_queries[:3]
            if (needs_fidelity_retry or needs_scope_retry) and not retry_queries:
                retry_queries = [f"{robot_topic} {question}".strip()]
            return {
                "selected_pages": res.selected_pages,
                "selected_images": selected_imgs,
                "need_more_search": (res.need_more_search or needs_fidelity_retry or needs_scope_retry) and retrieval_round < 2,
                "additional_search_queries": retry_queries,
                "planner_faithful": res.planner_faithful,
                "unsupported_assumptions": res.unsupported_assumptions,
                "scope_consistency": res.scope_consistency.model_dump(),
                **({
                    "standalone_question": res.corrected_standalone_question or question,
                    "search_results": [],
                } if (needs_fidelity_retry or needs_scope_retry) else {}),
                "uncertainties": res.uncertainties_to_check,
                "answer_plan": {
                    "primary_solution": res.primary_solution,
                    "direct_answer_plan": res.direct_answer_plan,
                    "supporting_points": res.supporting_points,
                },
                "llm_call_count": call_count + 1,
            }
        except Exception:
            log.exception("LangGraph reasoner failed; using deterministic fallback")

    # Rule-based fallback if LLM API is skipped/unavailable
    def is_solution_page(path: str) -> bool:
        return path not in ("index.md", "log.md", "unanswered.md")

    scored_candidates = []
    for r in search_results:
        if not is_solution_page(r.get("path", "")):
            continue
        base_score = float(r.get("bm25_score", 0.0))
        level = r.get("abstraction_level", 2)

        if pref_abstraction == "api_or_interface" or intent == "explicit_api":
            role = r.get("document_role", "")
            if role in ("interface", "api", "hardware"):
                adjusted = base_score + 4.5
            elif level == 3:
                adjusted = base_score + 2.0
            elif level == 2:
                adjusted = base_score + 2.0
            elif level in (0, 1):
                adjusted = base_score
            elif level == 4 or r.get("wiki_section") == "sources":
                adjusted = base_score - 4.0
            else:
                adjusted = base_score
        else:
            if level == 0:
                adjusted = base_score + 4.0
            elif level == 1:
                adjusted = base_score + 2.5
            elif level == 2:
                adjusted = base_score + 0.5
            elif level == 3:
                adjusted = base_score - 1.0
            elif level == 4 or r.get("wiki_section") == "sources":
                adjusted = base_score - 3.0
            else:
                adjusted = base_score

        item_copy = dict(r)
        item_copy["_adjusted_score"] = adjusted
        scored_candidates.append(item_copy)

    scored_candidates.sort(key=lambda x: x["_adjusted_score"], reverse=True)

    selected_pages = [r["path"] for r in scored_candidates[:5]]
    if not selected_pages and search_results:
        selected_pages = [search_results[0]["path"]]

    primary_solution = scored_candidates[0]["title"] if scored_candidates else (search_results[0]["title"] if search_results else "Wiki Knowledge Base")

    # Extract uncertainties from candidate results
    uncertainties = []
    for r in search_results:
        if r.get("wiki_section") == "queries" or r.get("document_role") == "unresolved_query" or r.get("uncertainty"):
            path = r.get("path", "")
            if path and path not in uncertainties:
                uncertainties.append(path)

    # Fallback image selection: strictly high usefulness architecture/workflow diagrams
    # Skip image embedding for pure parameter/FAQ/math concept queries
    is_pure_text_query = any(k in standalone.lower() for k in ["波特率", "参数是多少", "保修期", "逆解", "正解", "概念"])
    selected_imgs = []
    if not is_pure_text_query and intent not in ("explicit_api", "concept"):
        for img in image_candidates:
            if img.get("usefulness") == "high" and img.get("image_type") in ("architecture_diagram", "workflow_diagram", "UI_screenshot"):
                selected_imgs.append({
                    "path": img["path"],
                    "supports_claim": f"Visual architecture/workflow for {img.get('description', primary_solution)}",
                    "utility": "high"
                })
                if len(selected_imgs) >= 2:
                    break

    return {
        "selected_pages": selected_pages,
        "selected_images": selected_imgs,
        "need_more_search": False,
        "planner_faithful": True,
        "unsupported_assumptions": [],
        "scope_consistency": {"valid": True, "unsupported_cross_scope_transfer": []},
        "additional_search_queries": [],
        "uncertainties": uncertainties,
        "answer_plan": {
            "primary_solution": primary_solution,
            "direct_answer_plan": f"Use the primary solution {primary_solution} documented in the wiki.",
            "supporting_points": [r["title"] for r in scored_candidates[:3]],
        },
        "llm_call_count": call_count,
    }
