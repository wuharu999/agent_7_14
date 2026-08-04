---
name: assess-scenario-feasibility
description: Assess technical feasibility and deployment viability for confirmed robot scenarios using evidence-backed atomic capabilities or capability compositions, then diagnose gaps and rank realization paths. Use when producing a requirement-capability correspondence table, enforcing hard TSEI and safety gates, distinguishing absent capability from missing evidence, or deciding whether a robot scenario can be implemented and sustainably deployed.
---

# Assess Scenario Feasibility

## Required reference

Read [matching-and-gap-policy.md](references/matching-and-gap-policy.md) before assigning any match state or implementation path.

## Preconditions

Require a confirmed `ScenarioSpec`, requirement graph, versioned capability catalog, normalized units and frames, and evidence status. Return blocking unknowns to requirement engineering instead of forcing a conclusion.

## Workflow

1. Retrieve candidates by effect, object, target state, semantic key, aliases, compatible ports, and plausible compositions. Treat semantic retrieval as recall only.
2. Check effect and object compatibility before parameters.
3. Check scope: model, version, body part, environment, interface, prerequisites, and evidence status.
4. Map requirement inputs and outputs to capability ports. Verify direction, units, coordinate frames, formats, semantics, freshness, and permissions.
5. Apply every hard acceptance, safety, and TSEI constraint as a gate. Never let an aggregate score compensate for a failed hard gate.
6. Evaluate capability compositions: mandatory nodes, data edges, pre/hold/postcondition continuity, resource conflicts, and evidence for every required edge.
7. Assign one state from the reference policy. Keep match state separate from confidence and evidence level.
8. Diagnose each non-satisfied or uncertain gate before proposing a realization path.
9. Generate options from least invasive to most invasive. State coverage, prerequisites, effort band, risk, residual gap, verification method, and exit criterion.
10. Produce the technical conclusion only after every `must` requirement has a state and every gap has a next action or explicit rejection.
11. After technical gates, assess deployment viability using task frequency, human baseline, intervention rate, failure loss, environment modification, operating cost, utilization, workflow impact, responsibility boundary, and payback target. Keep incomplete economics separate from technical failure.

## Guardrails

- Do not equate text similarity with satisfaction.
- Do not equate an available API or topic with verified physical performance.
- Do not turn missing documentation into `not satisfied`.
- Do not use draft claims as verified safety evidence.
- Do not recommend software optimization for physical payload, reach, braking, thermal, or structural deficits without engineering evidence.
- Prefer a conservative `unproven` result over an unsupported feasible claim.

## Output

Lead with two independent conclusions:

- technical: feasible, feasible with conditions, prototype required, currently unproven, or infeasible
- deployment: viable, viable with conditions, business case incomplete, or not viable

Then return one row per atomic requirement with candidate capability or composition, gate results, match state, evidence, unknowns, confidence basis, realization options, residual risk, and next experiment. Validate the result with `schemas/feasibility-assessment.schema.json`.
