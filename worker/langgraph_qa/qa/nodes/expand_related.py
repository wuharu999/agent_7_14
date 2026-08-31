import json
from pathlib import Path
from typing import Dict, Any, List, Set

from worker.langgraph_qa.qa.state import QAState
from worker.langgraph_qa.runtime import get_runtime


def load_catalog_maps(
    catalog_path: Path,
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Load both path-keyed and stem-keyed catalog lookup maps."""
    catalog_map: Dict[str, Dict[str, Any]] = {}
    stem_map: Dict[str, Dict[str, Any]] = {}

    if catalog_path.exists():
        with catalog_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    path = item["path"]
                    catalog_map[path] = item
                    stem_map[Path(path).stem] = item
    return catalog_map, stem_map


def expand_related_node(state: QAState) -> Dict[str, Any]:
    """Follow 1-hop explicit related links for top candidate search results (0 LLM calls)."""
    runtime = get_runtime()
    search_results = state.get("search_results", [])
    if (
        not search_results
        or not runtime.related_graph.exists()
        or not runtime.wiki_catalog.exists()
    ):
        return {"search_results": search_results}

    with runtime.related_graph.open("r", encoding="utf-8") as f:
        graph_data = json.load(f)

    edges = graph_data.get("edges", [])
    catalog_map, stem_map = load_catalog_maps(runtime.wiki_catalog)
    existing_paths: Set[str] = {r.get("path") if isinstance(r, dict) else r.path for r in search_results}

    # Gather 1-hop target paths from top 5 search candidates
    top_candidates = search_results[:5]
    top_paths = {r.get("path") if isinstance(r, dict) else r.path for r in top_candidates}
    top_stems = {Path(p).stem for p in top_paths if p}

    expanded_paths: Set[str] = set()

    for edge in edges:
        from_node = edge.get("from", "")
        from_stem = Path(from_node).stem
        if from_node in top_paths or from_stem in top_stems:
            target_node = edge.get("to", "")
            resolved_path = None
            if target_node in catalog_map:
                resolved_path = target_node
            elif target_node in stem_map:
                resolved_path = stem_map[target_node]["path"]
            elif Path(target_node).stem in stem_map:
                resolved_path = stem_map[Path(target_node).stem]["path"]

            if resolved_path and resolved_path not in existing_paths:
                expanded_paths.add(resolved_path)

    # Convert expanded paths to search result entries with relation boost
    new_entries = []
    for path in sorted(list(expanded_paths)):
        if path in catalog_map:
            cat_info = catalog_map[path]
            new_entries.append({
                "path": cat_info["path"],
                "title": cat_info["title"],
                "snippet": cat_info.get("summary", ""),
                "bm25_score": 5.0,  # Baseline score for 1-hop relation link
                "wiki_section": cat_info.get("wiki_section", ""),
                "document_role": cat_info.get("document_role", "workflow"),
                "abstraction_level": cat_info.get("abstraction_level", 1),
                "tags": cat_info.get("tags", []),
                "related": cat_info.get("related", []),
                "aliases": cat_info.get("aliases", []),
                "boosted": True,
            })

    # Standardize output format
    formatted_existing = [r if isinstance(r, dict) else r.to_dict() for r in search_results]
    updated_results = formatted_existing + new_entries
    return {"search_results": updated_results[: runtime.max_candidates]}
