---
name: engineer-scenario-requirements
description: Turn vague robot deployment goals and customer narratives into confirmed, testable atomic requirement graphs through progressive questioning and Time-Space-Information-Energy constraint analysis. Use when a scenario lacks measurable acceptance criteria, mixes outcomes with implementation preferences, or needs success, exception, safety, and recovery paths before feasibility matching.
---

# Engineer Scenario Requirements

## Required references

Read [requirement-contract.md](references/requirement-contract.md) before decomposition. Read [questioning-and-tsei-policy.md](references/questioning-and-tsei-policy.md) whenever facts are missing or constraints interact.

## Workflow

1. Preserve the original customer statements. Separate business goal, scenario facts, requirements, implementation preferences, assumptions, and unknowns.
2. Build a `ScenarioSpec`: initiator, actors, objects, external systems, start/end state, spatial and temporal boundary, exclusions, success marker, environment profile, operating frequency, human baseline, intervention policy, failure consequence, modification allowance, and economic unknowns.
3. Expand one normal run as trigger → action → observable result. Split conjunctions into candidate atomic nodes and dependency edges.
4. Add exception, safety, and recovery paths only where the scenario makes them relevant.
5. Express each node as one effect with an acceptance method. Capture priority, criticality, inputs, outputs, dependencies, and TSEI constraints.
6. Choose the primary driver dimension from the acceptance variable; retain other dimensions as coupled constraints. Record derived constraints and their assumptions.
7. Ask no more than three high-value questions per round. Prioritize safety gates and answers that can change feasibility, architecture, or cost.
8. Update only affected nodes after each answer. Never repeat resolved questions or turn silence into a fact.
9. Read back the requirement graph, assumptions, exclusions, blocking unknowns, and acceptance criteria. Obtain confirmation before marking requirements confirmed.
10. Validate the `ScenarioSpec`, atomic requirements, and constraint derivations against their project schemas. Reject silent loss of scene-level facts.

## Conversation rules

- Ask for desired outcomes and acceptance evidence before asking about implementation technology.
- Convert “fast,” “safe,” “accurate,” “stable,” and “nearby” into observable thresholds.
- Keep known, assumed, and unknown states distinct.
- Stop final feasibility judgment while a blocking hard or safety requirement remains unknown.
- State non-blocking assumptions explicitly and make them easy to correct.

## Completion gate

Proceed to feasibility assessment only when the `ScenarioSpec` is confirmed; every `must` node has a subject, trigger, object, one observable effect, and acceptance method; every safety or regulatory node is testable; dependencies are coherent; and blocking technical or safety unknowns are resolved.

## Output

Return a versioned `ScenarioSpec`, atomic requirement graph, constraint-derivation records, acceptance criteria, source traceability, assumptions, exclusions, prioritized questions, and blocking unknowns.
