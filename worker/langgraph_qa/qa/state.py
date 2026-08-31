from typing import TypedDict, List, Dict, Any, Optional


class QAState(TypedDict, total=False):
    # Core User Inputs & Observability
    request_id: str
    elapsed_ms: float
    question: str
    language: str        # e.g., "zh" (Chinese), "en" (English)
    robot_topic: str     # Knowledge scope dropdown (e.g., "全部机器人", "天工行者DEX", "Walker_S2_EDU探索者", etc.)
    active_topic: Dict[str, Any]
    strict_robot_scope: bool
    recent_history: List[Dict[str, str]]
    defer_final_answer: bool

    # Planner Node
    standalone_question: str
    topic_relation: str          # "continue", "refine", "switch", or "ambiguous"
    current_subject: Optional[str]
    history_used: List[str]
    history_ignored: List[str]
    scope_analysis: Dict[str, Any]
    queried_entity_type: str
    explicit_entities: List[str]
    scope_relation: str
    canonicalized_entities: List[Dict[str, Any]]
    intent: str                  # "how_to", "explicit_api", "concept", "comparison", "troubleshooting"
    preferred_abstraction: str  # "application_or_workflow", "sdk_or_module", "api_or_interface"
    search_queries: List[str]

    # Search & Retrieval Nodes
    search_results: List[Dict[str, Any]]
    retrieval_round: int
    llm_call_count: int

    # Reasoner Node
    selected_pages: List[str]
    selected_images: List[Dict[str, Any]]
    need_more_search: bool
    planner_faithful: bool
    entity_type_consistent: bool
    evidence_sufficient: bool
    unsupported_assumptions: List[str]
    scope_consistency: Dict[str, Any]
    additional_search_queries: List[str]
    uncertainties: List[Dict[str, Any]]
    uncertainties_to_check: List[str]
    answer_plan: Dict[str, Any]

    # Evidence Loading Node
    loaded_evidence: List[Dict[str, Any]]

    # Final Output
    answer: str
    answer_system: str
    answer_user: str
