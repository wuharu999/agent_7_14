import logging
from typing import Dict, Any, List
from langchain_core.messages import SystemMessage, HumanMessage

from worker.langgraph_qa.qa.state import QAState
from worker.langgraph_qa.qa.model import get_chat_model


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
    system = ANSWER_SYSTEM_PROMPT.format(robot_topic=robot_topic, language=language)
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
                "answer": res.content,
                "llm_call_count": call_count + 1,
            }
        except Exception:
            log.exception("LangGraph final answer failed; using deterministic fallback")

    # Structured template fallback if LLM API is unavailable
    primary_sol = answer_plan.get("primary_solution", "相关方案" if language == "zh" else "Relevant Solution")
    lines: List[str] = []

    if language == "en":
        lines.append(f"### Recommended Solution: {primary_sol}\n")
        lines.append(
            f"Regarding your query \"{standalone}\" (Scope: {robot_topic}), "
            f"based on the knowledge base documentation, the recommended approach is **{primary_sol}**.\n"
        )

        if loaded_evidence:
            lines.append("#### Key References & Notes:")
            for ev in loaded_evidence[:4]:
                path_name = ev["path"]
                summary = extract_clean_summary(ev.get("content", ""))
                lines.append(f"- **[{path_name}]({path_name})**: {summary[:120]}")
            lines.append("")

        if uncertainties:
            lines.append("#### Uncertainties & Known Limitations:")
            for u in uncertainties[:3]:
                if isinstance(u, dict):
                    u_path = u.get("path", "")
                    u_note = u.get("note", u.get("content", ""))
                    if u_path:
                        lines.append(f"- **[{u_path}]({u_path})**: {u_note[:120]}")
                    elif u_note:
                        lines.append(f"- *{u_note[:120]}*")
                else:
                    lines.append(f"- *{str(u)}*")
            lines.append("")

        if selected_images:
            lines.append("#### Relevant Architecture & Diagrams:")
            for img in selected_images[:3]:
                img_path = img.get("path", "")
                img_desc = img.get("supports_claim", "Diagram")
                lines.append(f"![{img_desc}]({img_path})\n")

        lines.append("#### Implementation Steps:")
        pts = answer_plan.get("supporting_points", [])
        if pts:
            for i, pt in enumerate(pts, 1):
                lines.append(f"{i}. {pt}")
        else:
            lines.append(f"1. Refer to the primary solution {primary_sol} in the documentation.")
    else:
        # Default Chinese output
        lines.append(f"### 推荐方案：{primary_sol}\n")
        lines.append(
            f"针对您提出的问题「{standalone}」（范围：{robot_topic}），"
            f"基于知识库中的文档，推荐使用 **{primary_sol}** 来达成目标。\n"
        )

        if loaded_evidence:
            lines.append("#### 核心参考资料与说明：")
            for ev in loaded_evidence[:4]:
                path_name = ev["path"]
                summary = extract_clean_summary(ev.get("content", ""))
                lines.append(f"- **[{path_name}]({path_name})**: {summary[:120]}")
            lines.append("")

        if uncertainties:
            lines.append("#### 已知不确定性与待确认事项：")
            for u in uncertainties[:3]:
                if isinstance(u, dict):
                    u_path = u.get("path", "")
                    u_note = u.get("note", u.get("content", ""))
                    if u_path:
                        lines.append(f"- **[{u_path}]({u_path})**: {u_note[:120]}")
                    elif u_note:
                        lines.append(f"- *{u_note[:120]}*")
                else:
                    lines.append(f"- *{str(u)}*")
            lines.append("")

        if selected_images:
            lines.append("#### 相关架构与流程图：")
            for img in selected_images[:3]:
                img_path = img.get("path", "")
                img_desc = img.get("supports_claim", "流程图")
                lines.append(f"![{img_desc}]({img_path})\n")

        lines.append("#### 实施要点：")
        pts = answer_plan.get("supporting_points", [])
        if pts:
            for i, pt in enumerate(pts, 1):
                lines.append(f"{i}. {pt}")
        else:
            lines.append(f"1. 查阅知识库中关于 {primary_sol} 的详细说明文档并按步骤配置。")

    return {
        "answer": "\n".join(lines),
        "llm_call_count": call_count,
    }
