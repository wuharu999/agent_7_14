import logging
from typing import Dict, Any, List
from langchain_core.messages import SystemMessage, HumanMessage

from worker.langgraph_qa.qa.state import QAState
from worker.langgraph_qa.qa.model import get_chat_model
from worker.topic_policy import (
    CANONICAL_TERMINOLOGY_PROMPT,
    final_response_policy_text,
    no_confirmed_information,
    sanitize_customer_output,
)


log = logging.getLogger(__name__)

ANSWER_SYSTEM_PROMPT = """You are a Solution-Oriented Robotics Wiki Q&A Assistant.

Behavioral Policy & Answering Instructions:
1. Grounded & Direct Answer: Provide a clear, solution-first answer based strictly on the loaded Wiki evidence and Answer Plan. Answer the user's direct question first before providing secondary technical details.
2. CRITICAL - Language Compliance: The entire response MUST be written in the specified Target Language: {language} (e.g. "zh" = Chinese, "en" = English).
3. Abstraction & Technical Accuracy:
   - For general how-to questions: Explain the primary supported solution/platform/workflow first.
   - For explicit API/topic inquiries: Directly provide the exact ROS topics, messages, interfaces, parameters, or commands.
4. Public Output: Do not expose source document names, local paths, Wiki links, citations, retrieval steps, or hidden reasoning.
5. Images: Do not emit Markdown image syntax. The Agent1 Worker attaches separately validated images after generation.
6. Uncertainty Surfacing: If any uncertainties, known limitations, or unresolved questions from `queries/` are present in context, explicitly inform the user about them.
7. For non Chinese languages, refer to 天工 as teinkung, not as "天工" or "Tiangong".
8. phrase 天工 2.0 lite should not be mentioned, use 天工行者 for chinese and teinkung lite for non Chinese languages
9. phrase 天工 2.0 plus should not be mentioned, use 天工行者无疆 for chinese and teinkung plus for non Chinese languages
10. phrase 天工 2.0 pro should not be mentioned, use 天工行者无界 for chinese and teinkung pro for non Chinese languages
11. phrase 天工 3.0 should not be mentioned, use 天工行者DEX for chinese and teinkung dex for non Chinese languages
12. The phrase wiki should not be mentioned, use 参考资料 for chinese and the translation of reference materials for non Chinese languages
13. The phrase walker s2 edu 探索者 is the chinese name, for none chinese languages use walker s2 edu explorer as the translation
13. The phrase walker c1 edu 共创者 is the chinese name, for none chinese languages use walker c1 edu as the translation

Parameters:
- Selected Robot/Topic Scope: {robot_topic}
- Target Language: {language}
"""


def extract_clean_summary(content: str) -> str:
    """Extract clean title or first descriptive body line from markdown content, skipping YAML frontmatter."""
    if not content:
        return "参考资料"

    lines = content.splitlines()
    in_yaml = False
    body_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            in_yaml = not in_yaml
            continue
        if in_yaml:
            continue
        if stripped:
            cleaned = stripped.lstrip("#").strip()
            if cleaned and not cleaned.startswith("```"):
                body_lines.append(cleaned)
                if len(body_lines) >= 2:
                    break

    return " — ".join(body_lines) if body_lines else "参考资料"


def final_answer_node(state: QAState) -> Dict[str, Any]:
    question = state.get("question", "")
    standalone = state.get("standalone_question", question)
    language = state.get("language", "zh")
    robot_topic = state.get("robot_topic", "全部机器人")
    answer_plan = state.get("answer_plan", {})
    loaded_evidence = state.get("loaded_evidence", [])
    selected_images = state.get("selected_images", [])
    uncertainties = state.get("uncertainties", [])
    call_count = state.get("llm_call_count", 0)

    # Format loaded evidence (up to 5000 chars per file to preserve full detail)
    evidence_text = "\n\n".join([
        f"--- File: {item['path']} ---\n{item['content'][:5000]}"
        for item in loaded_evidence
    ])

    images_text = "\n".join([
        f"- Path: {img.get('path')} | Support Claim: {img.get('supports_claim')}"
        for img in selected_images
    ])

    uncertainties_text = "\n".join([
        f"- {u.get('path', '')}: {u.get('content', u.get('note', ''))[:300]}" if isinstance(u, dict) else f"- {str(u)}"
        for u in uncertainties
    ])

    plan_text = (
        f"Primary Solution: {answer_plan.get('primary_solution', 'N/A')}\n"
        f"Plan: {answer_plan.get('direct_answer_plan', '')}\n"
        f"Points: {', '.join(answer_plan.get('supporting_points', []))}"
    )

    prompt = f"""
User Question: {question}
Standalone Intent: {standalone}

Answer Plan:
{plan_text}

Selected Image Descriptions:
{images_text or 'None'}

Uncertainties & Known Limitations:
{uncertainties_text or 'None'}

Loaded Wiki Evidence:
{evidence_text or 'No direct evidence file loaded.'}

Generate the final answer in target language '{language}':
"""
    system = (
        ANSWER_SYSTEM_PROMPT.format(robot_topic=robot_topic, language=language)
        + "\n\n" + final_response_policy_text()
        + "\n\n" + CANONICAL_TERMINOLOGY_PROMPT
    )
    if state.get("defer_final_answer"):
        return {
            "answer_system": system,
            "answer_user": prompt,
            "llm_call_count": call_count + 1,
        }

    if llm := get_chat_model():
        try:
            res = llm.invoke([
                SystemMessage(content=system),
                HumanMessage(content=prompt)
            ])
            return {
                "answer": sanitize_customer_output(str(res.content), language),
                "llm_call_count": call_count + 1,
            }
        except Exception:
            log.exception("LangGraph final answer failed; using deterministic fallback")

    primary_sol = str(answer_plan.get("primary_solution") or "").strip()
    points = [str(point).strip() for point in answer_plan.get("supporting_points", []) if str(point).strip()]
    if not loaded_evidence:
        fallback = no_confirmed_information(language)
    elif str(language).lower().startswith("en"):
        fallback = (f"Recommended approach: {primary_sol}." if primary_sol else "Here is the confirmed approach.")
        if points:
            fallback += "\n" + "\n".join(f"{index}. {point}" for index, point in enumerate(points, 1))
    else:
        fallback = (f"建议采用：{primary_sol}。" if primary_sol else "以下是目前确认可行的做法。")
        if points:
            fallback += "\n" + "\n".join(f"{index}. {point}" for index, point in enumerate(points, 1))
    return {
        "answer": sanitize_customer_output(fallback, language),
        "llm_call_count": call_count,
    }
