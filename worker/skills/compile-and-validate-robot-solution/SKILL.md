---
name: compile-and-validate-robot-solution
description: Compile an evidence-backed feasible robot scenario into a traceable task graph, reviewable behavior tree, acceptance tests, and validation feedback loop. Use after feasibility assessment when sequencing capability contracts, generating BehaviorTree.CPP-compatible artifacts, defining simulation or pilot tests, checking failure and recovery paths, or feeding verified results back into the capability catalog.
---

# Compile And Validate Robot Solution

## Required reference

Read [compilation-and-validation-policy.md](references/compilation-and-validation-policy.md) before generating a task graph, behavior tree, or validation plan.

## Preconditions

Require a confirmed scenario, confirmed requirements, a frozen capability-catalog revision, a completed feasibility matrix, and explicit treatment of every `must` gap. Do not compile an unsupported narrative into an executable-looking artifact.

## Workflow

1. Select output mode from the technical conclusion: compile a solution for feasible states; create a bounded prototype plan for `prototype_required`; create an evidence-acquisition plan for `currently_unproven`; reject solution compilation for `infeasible`.
2. Freeze artifact versions and build a trace table from source statement → scenario → requirement → acceptance criterion → capability or composition → task/behavior-tree node → test → measurement.
3. Construct a technology-neutral task graph first. Bind each leaf to one capability contract or an explicit external/human action.
4. Verify input/output compatibility, preconditions, hold conditions, postconditions, resource ownership, timing, concurrency, and cancellation behavior across edges.
5. Add only scenario-required guards, fallbacks, retries, timeouts, recovery, and safe-stop paths. Do not add decorative behavior-tree complexity.
6. Lower the task graph to a reviewable behavior tree. Use BehaviorTree.CPP XML as the default interchange target when an executable format is requested; keep runtime-specific ports and plugins explicit.
7. Generate at least one acceptance test for every hard criterion and every safety path. Add counterexamples for unit, frame, model, version, stale information, energy, missing evidence, and recovery failures where applicable.
8. Run document-level validation before simulation or pilot execution. Record observed results without upgrading capability status automatically.
9. Classify failures as requirement, knowledge, matching, composition, implementation, environment, test, or nondeterminism defects.
10. Feed verified measurements back as proposed capability-boundary updates and regression fixtures. Keep source evidence immutable.
11. Release only when the agreed gate passes; otherwise return the smallest next experiment.

## Architecture rules

- Treat the capability/requirement graph as the source model and the behavior tree as a compiled target.
- Never generate leaf nodes without a bound capability contract, external action, or declared gap.
- Preserve end-to-end traceability rather than hiding decisions inside free-form node text.
- Keep direct robot control outside this Skill unless a separate, explicitly authorized and safety-governed runtime is provided.
- For an MVP, prefer one bounded scenario, one robot, one catalog revision, reviewable XML, and offline or simulated validation.

## Output

Return the selected output mode, trace links, task graph or bounded experiment plan, behavior tree when allowed, node bindings, unresolved compilation errors, acceptance and regression tests, validation report, release decision, and proposed evidence/capability updates. Validate compositions and trace links with the project schemas.
