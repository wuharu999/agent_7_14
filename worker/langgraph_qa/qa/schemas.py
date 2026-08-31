from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field


IntentType = Literal["how_to", "explicit_api", "concept", "comparison", "troubleshooting"]
AbstractionType = Literal["application_or_workflow", "sdk_or_module", "api_or_interface"]
TopicRelation = Literal["continue", "refine", "switch", "ambiguous"]
ScopeRelation = Literal["in_scope", "related_scope", "cross_scope", "out_of_scope", "ambiguous"]


class ScopeAnalysis(BaseModel):
    active_scope: str = Field(description="Current UI-selected robot, team, or knowledge scope.")
    explicit_entities: List[str] = Field(default_factory=list, description="Entities explicitly named in the current user message.")
    resolved_references: List[str] = Field(default_factory=list, description="Entities resolved only from clear current-turn references.")
    relation: ScopeRelation = Field(description="Semantic relation between the current request and active_scope.")
    reason: str = Field(description="Short user-safe explanation of the scope classification.")
    confidence: float = Field(default=0.0, description="Classifier confidence from 0 to 1.")


class ScopeConsistency(BaseModel):
    valid: bool = Field(default=True, description="Whether selected evidence supports the requested scope without cross-product transfer.")
    unsupported_cross_scope_transfer: List[str] = Field(
        default_factory=list,
        description="Unsupported capability transfers between products or scopes found in the candidate evidence.",
    )


class SearchQuery(BaseModel):
    query: str = Field(description="Targeted lexical search query string without filler words.")
    rationale: Optional[str] = Field(default=None, description="Purpose or angle of this search query.")


class PlannerOutput(BaseModel):
    scope_analysis: ScopeAnalysis
    topic_relation: TopicRelation = Field(
        default="ambiguous",
        description="Relation of the current request to history: continue, refine, switch, or ambiguous.",
    )
    current_subject: Optional[str] = Field(
        default=None,
        description="Subject explicitly present in the current turn or required to resolve a current reference; otherwise null.",
    )
    history_used: List[str] = Field(
        default_factory=list,
        description="Short labels for history used only to resolve a current reference.",
    )
    history_ignored: List[str] = Field(
        default_factory=list,
        description="Short labels for archived context intentionally excluded from the active request.",
    )
    standalone_question: str = Field(
        description="The current user question rewritten into a self-contained standalone search target with references resolved."
    )
    intent: IntentType = Field(
        description="Intent type: 'how_to' (workflow/procedure), 'explicit_api' (specific API/ROS topic/param), 'concept' (explanation/definition), 'comparison' (comparing models/peripherals), 'troubleshooting' (faults/errors/aftersale)."
    )
    preferred_abstraction: AbstractionType = Field(
        description="Target solution abstraction: 'application_or_workflow' (prefer complete platform/workflow), 'sdk_or_module' (subsystems/SDKs), 'api_or_interface' (specific ROS topics/parameters/APIs only when explicitly requested)."
    )
    search_queries: List[str] = Field(
        description="1 to 3 targeted lexical search terms (Chinese/English keywords) optimized for FTS5 BM25 search."
    )


PlanOutput = PlannerOutput  # Backward-compatible alias


class ImageSelection(BaseModel):
    path: str = Field(description="Relative path of selected media file.")
    supports_claim: str = Field(description="Specific claim or topic supported by this image.")
    utility: str = Field(default="high", description="Utility level: high, medium, low.")


SelectedImage = ImageSelection  # Backward-compatible alias


class AnswerPlan(BaseModel):
    primary_solution: str = Field(description="The primary supported solution or tool identified.")
    direct_answer_plan: str = Field(description="Core answer message summary.")
    supporting_points: List[str] = Field(default_factory=list, description="Key supporting evidence points.")


class ReasonOutput(BaseModel):
    scope_consistency: ScopeConsistency = Field(default_factory=ScopeConsistency)
    planner_faithful: bool = Field(
        default=True,
        description="Whether the planner interpretation adds no unsupported entity or constraint beyond the current turn and active UI scope.",
    )
    unsupported_assumptions: List[str] = Field(
        default_factory=list,
        description="Entities or constraints introduced by the planner without support from the current turn or explicitly used history.",
    )
    corrected_standalone_question: Optional[str] = Field(
        default=None,
        description="A current-turn-faithful replacement only when planner_faithful is false.",
    )
    primary_solution: str = Field(description="The primary supported solution or tool identified.")
    selected_pages: List[str] = Field(description="List of selected Markdown page relative paths (3 to 6 max).")
    selected_images: List[ImageSelection] = Field(
        default_factory=list,
        description="0 to 3 relevant image references that materially support the answer."
    )
    need_more_search: bool = Field(
        default=False,
        description="True if evidence is insufficient and one more search pass is needed."
    )
    additional_search_queries: List[str] = Field(
        default_factory=list,
        description="Optional extra search queries if need_more_search is True."
    )
    uncertainties_to_check: List[str] = Field(
        default_factory=list,
        description="Any unresolved questions, limitations, or uncertainty points identified from queries/ or wiki."
    )
    direct_answer_plan: str = Field(description="Core answer message summary.")
    supporting_points: List[str] = Field(default_factory=list, description="Key supporting evidence points.")
