from typing import Dict, Any, List, Optional, Tuple

from worker.langgraph_qa.qa.state import QAState
from worker.langgraph_qa.runtime import get_runtime


def read_wiki_file(rel_path: str) -> Optional[Tuple[str, str]]:
    """Read a markdown file from the wiki directory safely."""
    if not rel_path or not isinstance(rel_path, str):
        return None

    wiki_root = get_runtime().wiki_root
    clean_rel = rel_path.removeprefix("wiki_export/").lstrip("/")
    candidate = wiki_root.joinpath(clean_rel)
    try:
        full_path = candidate.resolve(strict=True)
        full_path.relative_to(wiki_root)
    except (FileNotFoundError, OSError, ValueError):
        return None
    if candidate.is_symlink() or not full_path.is_file():
        return None
    with full_path.open("r", encoding="utf-8", errors="ignore") as file:
        return full_path.relative_to(wiki_root).as_posix(), file.read()

    return None


def load_evidence_node(state: QAState) -> Dict[str, Any]:
    """Read the full Markdown content of selected pages and uncertainty files (0 LLM calls)."""
    runtime = get_runtime()
    selected_pages = state.get("selected_pages", [])[: runtime.max_pages]
    loaded_evidence: List[Dict[str, Any]] = []
    seen_paths = set()
    remaining_chars = runtime.max_page_chars

    uncertainties_in = state.get("uncertainties", [])
    uncertainties_to_check = state.get("uncertainties_to_check", [])
    uncertainties_out: List[Dict[str, Any]] = []

    # 1. Load primary selected evidence pages
    for page_path in selected_pages:
        res = read_wiki_file(page_path)
        if res:
            path_str, content = res
            if path_str not in seen_paths and remaining_chars > 0:
                bounded_content = content[:remaining_chars]
                seen_paths.add(path_str)
                loaded_evidence.append({
                    "path": path_str,
                    "content": bounded_content,
                })
                remaining_chars -= len(bounded_content)
            if "queries/" in path_str:
                uncertainties_out.append({
                    "path": path_str,
                    "content": content[:1000],
                })

    # 2. Load any extra uncertainty query paths
    extra_uncertainty_targets = []
    for item in uncertainties_in:
        if isinstance(item, str):
            extra_uncertainty_targets.append(item)
        elif isinstance(item, dict) and "path" in item:
            extra_uncertainty_targets.append(item["path"])

    for item in uncertainties_to_check:
        if isinstance(item, str):
            extra_uncertainty_targets.append(item)

    for u_target in extra_uncertainty_targets:
        res = read_wiki_file(u_target)
        if res:
            path_str, content = res
            if (
                path_str not in seen_paths
                and len(loaded_evidence) < runtime.max_pages
                and remaining_chars > 0
            ):
                bounded_content = content[:remaining_chars]
                seen_paths.add(path_str)
                loaded_evidence.append({
                    "path": path_str,
                    "content": bounded_content,
                })
                remaining_chars -= len(bounded_content)
            if not any(u.get("path") == path_str for u in uncertainties_out):
                uncertainties_out.append({
                    "path": path_str,
                    "content": content[:1000],
                })
        else:
            # Add string note if not a file path
            if not any(u.get("note") == u_target for u in uncertainties_out if "note" in u):
                uncertainties_out.append({"note": u_target})

    return {
        "loaded_evidence": loaded_evidence,
        "uncertainties": uncertainties_out,
    }
