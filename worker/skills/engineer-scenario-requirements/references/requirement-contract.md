# Atomic requirement contract

## Definition

An atomic requirement states that, under specified conditions, one subject must perform one action on one object and produce one observable, testable result.

## Required meaning

Record:

- stable requirement ID
- original source statements and known/assumed/unknown state
- subject, trigger, object, action, and target state
- preconditions, inputs, outputs, and one primary effect
- acceptance criteria and test method
- Time, Space, Information, and Energy constraints
- priority and criticality
- dependencies and AND/OR/optional logic
- assumptions, exclusions, and blocking unknowns
- confirmation and version status

Use the project `schemas/atomic-requirement.schema.json` for currently representable JSON fields. Report any additional L5 requirement as a schema gap.

Use `schemas/scenario-spec.schema.json` for scene-level boundary, environment, operating, and economic facts. Use `schemas/constraint-derivation.schema.json` for cross-dimension source → rule → consequence records. Do not force scene-level facts into arbitrary requirement nodes.

## Atomicity checks

- One node must have one independently judgeable result.
- Split independent acceptance results joined by “and.”
- Keep inseparable control details as constraints, not new requirements.
- Represent ordering and alternatives as graph edges, not compound prose.
- Keep business goals above the graph and implementation preferences outside the requirement unless the customer makes them mandatory.

## Requirement quality gate

A `must` requirement is matchable only when its subject, trigger, object, effect, acceptance method, critical constraints, dependencies, and knowledge state are explicit.
