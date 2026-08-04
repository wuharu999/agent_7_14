# Compilation and validation policy

## Source and target models

Use the confirmed requirement graph and capability contracts as source models. Produce:

1. Trace matrix
2. Technology-neutral task graph
3. Bound behavior tree
4. Acceptance and regression tests
5. Validation feedback

Behavior trees are compiled orchestration artifacts, not the canonical knowledge base.

## Output mode

- `feasible` / `feasible_with_conditions`: produce a solution task graph and reviewable behavior tree.
- `prototype_required`: produce a bounded experiment graph, not a production-looking solution.
- `currently_unproven`: produce an evidence-acquisition plan.
- `infeasible`: produce a rejection record and the smallest condition that would justify reassessment.

## Task graph checks

For every node and edge, check:

- bound requirement and capability IDs
- input/output semantics, units, frames, and freshness
- preconditions, hold conditions, and postconditions
- ordering, concurrency, timeout, cancellation, and resource ownership
- success, failure, fallback, recovery, and safe-stop behavior
- evidence and unresolved gaps

Reject compilation when a mandatory leaf has no binding or an edge relies on an unstated conversion.

## Behavior-tree lowering

- Use Sequence for mandatory ordered work.
- Use Fallback/Selector for explicit alternatives or recovery.
- Use conditions as guards, not hidden business logic.
- Add retries and timeouts only when requirements and failure policy define them.
- Bind each leaf to one capability contract, external system action, or human action.
- Prefer BehaviorTree.CPP XML as an interchange target when executable output is requested.

## Validation ladder

1. Structural validation: IDs, bindings, schema, units, frames, and traceability.
2. Contract validation: pre/hold/postconditions and hard gates.
3. Offline or simulated scenario tests.
4. Bench test under controlled conditions.
5. Bounded pilot with safety governance.
6. Field regression and monitored operation.

Do not claim a higher level than executed.

## Minimum assertions

- Every hard criterion has at least one test.
- Every conclusion traces to a requirement, capability, source, or measurement.
- Failed hard gates cannot compile as success paths.
- Missing evidence remains unproven.
- Failure and safe-stop paths are observable.
- Repeat runs do not create duplicate capability records.
- Verified measurements propose bounded capability updates and regression fixtures.

## MVP gate

Use one robot, one bounded scenario, one frozen catalog revision, human-reviewed task/behavior trees, and offline or simulated validation. Do not directly control a robot unless a separate authorized runtime and safety process are explicitly in scope.
