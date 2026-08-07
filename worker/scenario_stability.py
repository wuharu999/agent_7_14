from __future__ import annotations

from typing import Any

from shared.scenario_state import evaluate_state, validate_state


def evaluate_stability(scenario_state: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic gate result; model readiness is intentionally advisory."""
    validate_state(scenario_state)
    evaluated = evaluate_state(scenario_state)
    return {
        "minimum_gate": evaluated["minimum_gate"],
        "stability": evaluated["stability"],
        "automatic_analysis_allowed": bool(evaluated["stability"]["stable"]),
    }
