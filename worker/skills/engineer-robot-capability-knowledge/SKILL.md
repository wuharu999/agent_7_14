---
name: engineer-robot-capability-knowledge
description: Extract, normalize, review, and maintain evidence-backed atomic robot capability contracts from a source Karpathy Wiki, SDK manuals, usage guides, tool documentation, and test results. Use when curating a separate capability Wiki, reconciling duplicate or conflicting claims, defining reusable capability boundaries, or updating capability knowledge after verification.
---

# Engineer Robot Capability Knowledge

## Required references

Read [capability-contract.md](references/capability-contract.md) before extraction. Read [wiki-and-evidence-policy.md](references/wiki-and-evidence-policy.md) before reading or writing either Wiki.

## Workflow

1. Fix the product, model, hardware, software/firmware, document version, and target catalog revision. Preserve unresolved names verbatim.
2. Read the source Wiki and original materials as read-only evidence. Record precise source locators.
3. Extract triggerable actions and observable effects. Separate capabilities from parameters, resources, algorithms, middleware, prerequisites, and composite business tasks.
4. Apply every atomicity test in the contract. Split multiple independently selectable or testable effects; stop before implementation details become meaningless fragments.
5. Normalize vendor-independent meaning into a semantic key while retaining model, version, body part, endpoint, and environment in the implementation scope.
6. Record inputs, outputs, preconditions, hold conditions, postconditions, TSEI limits, quality metrics, failure states, recovery facts, dependencies, incompatible resources, evidence, and unknowns.
7. Search the target capability Wiki by stable ID, semantic key, alias, scope, and interface. Classify each candidate as new, update, implementation instance, duplicate, conflict, or deprecation proposal.
8. Build an idempotent changeset and validate it against the current project schema. Report any L5 contract field that the schema cannot yet represent; do not silently discard it or invent incompatible JSON.
9. Write only draft entries or draft updates unless explicit publication authority exists. Preserve human decisions and append audit metadata.
10. Record every repeatable test with test level, conditions, sample size, passed count, measured value, and evidence locator. Feed verified results back into bounded capability status without rewriting documentary evidence.

## Evidence rules

- Mark document-extracted knowledge `draft`.
- Mark it `reviewed` only after authorized evidence and atomicity review.
- Mark it `verified` only after repeatable tests prove the stated effect within the stated boundary.
- Treat an interface as proof that a command surface exists, not proof of physical performance.
- Treat undocumented behavior as `unknown`, not supported or unsupported.
- Keep extraction confidence, evidence level, and measured execution success separate.

## Boundaries

- Keep the source Wiki and original materials read-only.
- Do not delete or publish target records without explicit authority.
- Do not infer performance limits from examples, marketing language, or adjacent models.
- Do not create scenario-specific capabilities such as “reception” when the reusable effects are sensing, orienting, speaking, and navigating.

## Output

Return atomic capability records, dependency edges, evidence links, measurement records, conflicts, unknowns, schema gaps, and an auditable target-Wiki changeset. Make every claim traceable to a source or repeatable test.
