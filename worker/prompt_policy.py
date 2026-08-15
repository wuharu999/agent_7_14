from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_WORKER_ROOT = Path(__file__).resolve().parent
_POLICY_FILES = {
    "scenario": _WORKER_ROOT / "policies" / "scenario_compiler.md",
    "requirements": _WORKER_ROOT / "skills" / "engineer-scenario-requirements" / "SKILL.md",
    "assessment": _WORKER_ROOT / "skills" / "assess-scenario-feasibility" / "SKILL.md",
    "solution": _WORKER_ROOT / "skills" / "compile-and-validate-robot-solution" / "SKILL.md",
}


@lru_cache(maxsize=len(_POLICY_FILES))
def policy_text(name: str) -> str:
    """Read one packaged, allowlisted prompt policy; never accept a path from input."""
    try:
        path = _POLICY_FILES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown prompt policy: {name}") from exc
    return path.read_text(encoding="utf-8")


def policy_sections(name: str, headings: tuple[str, ...]) -> str:
    """Return only allowlisted Markdown sections from one packaged policy."""
    text = policy_text(name)
    selected: list[str] = []
    current: list[str] = []
    keep = False
    for line in text.splitlines():
        if line.startswith("## "):
            if keep and current:
                selected.extend(current)
            heading = line[3:].strip()
            keep = heading in headings
            current = [line] if keep else []
        elif keep:
            current.append(line)
    if keep and current:
        selected.extend(current)
    return "\n".join(selected).strip()


def scenario_analysis_policy() -> str:
    return "\n\n".join(
        (
            policy_text("scenario"),
            policy_sections("requirements", ("Workflow", "Output")),
            policy_sections("assessment", ("Workflow", "Guardrails", "Output")),
        )
    )


def scenario_clarification_policy() -> str:
    return "\n\n".join(
        (
            policy_text("scenario"),
            policy_sections(
                "requirements",
                ("Workflow", "Conversation rules", "Completion gate"),
            ),
        )
    )
