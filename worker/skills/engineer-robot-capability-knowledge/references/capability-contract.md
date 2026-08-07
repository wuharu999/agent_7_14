# Atomic capability contract

## Definition

An atomic capability is a reusable contract stating that, under explicit preconditions and boundaries, a robot implementation can be triggered to produce one observable effect that can be independently selected, replaced, matched, and accepted.

## Required meaning

Record at least:

- stable ID and vendor-independent semantic key
- one action/effect and its object
- implementation scope: vendor, model, version, body part, environment
- trigger and inputs
- outputs and observable result
- preconditions, hold conditions, and postconditions
- Time, Space, Information, and Energy constraints
- quality metrics and test methods
- bounded measurements: test level, conditions, sample size, pass count, value, unit, and evidence locator
- failure states and documented recovery behavior
- dependencies and incompatible resources
- interface and precise evidence locators
- unknowns, evidence level, and lifecycle status

Use the project `schemas/atomic-capability.schema.json` for currently representable JSON fields. Report unrepresentable contract fields as schema gaps.

## Atomicity tests

A record is atomic only when:

1. It has one primary observable effect.
2. It has one coherent trigger/parameter contract.
3. It can be selected and accepted independently.
4. Replacing its implementation does not change the scenario meaning.
5. It is reusable across scenarios.

Split records containing independently useful effects. Do not split parameters, algorithms, hardware resources, middleware, or internal control steps into capabilities unless they expose an independently triggerable and testable effect.

## Two capability types

- `building_block`: a directly callable interface or primitive with one bounded observable effect.
- `operational_behavior`: a reusable end-to-end behavior with one independently testable outcome.

Keep scenario tasks, compositions, and solution artifacts outside the capability catalog. Atomicity is a contract-quality property. Never default a missing type to a building block.

## Measurement rule

Do not store a performance number without its operating boundary. A measured result must retain test level, environment, object/load, speed or duty cycle where relevant, sample size, passed count, value, unit, and raw evidence locator. A maximum observed value is not a generally verified limit.

An operational behavior is supported only within a matching verification profile. An SDK interface can support an engineering interface requirement but never proves the end-to-end deployment behavior by itself.
