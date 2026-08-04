# Atomic capability contract

## Definition

An atomic capability is a reusable contract stating that, under explicit preconditions and boundaries, one robot implementation can be triggered to produce one primary observable effect that can be independently selected, replaced, matched, and accepted.

## Atomicity gate

Accept a candidate only when all conditions hold:

1. It has one primary observable effect.
2. It has one coherent trigger and parameter contract.
3. It can be selected and accepted independently.
4. Replacing its implementation does not change the scenario meaning.
5. It is reusable across customer scenarios.
6. Its model, version, body-part, and environment boundary can be stated.
7. Its factual fields are supported by evidence or explicitly marked unknown.

Split independently useful effects. Do not split parameters, algorithms, hardware resources, middleware, or internal control steps into capabilities unless they expose an independently triggerable and testable effect.

## Three knowledge levels

- Atomic capability: one reusable leaf contract.
- Composite skill: a reusable graph of atomic capabilities.
- Scenario task: a customer-specific orchestration with acceptance criteria.

Never flatten these levels into one capability list.

## Required meaning

Every entry must record:

- stable `capability_id`;
- vendor-independent `semantic_key`;
- normalized name in `动词_对象_必要限定` form;
- one action, object, and observable result;
- vendor, model, source names, versions, body part, environment, and selector;
- trigger, inputs, outputs, and invocation interfaces;
- preconditions, hold conditions, and postconditions;
- Time, Space, Information, and Energy constraints;
- quality metrics and test methods;
- failure conditions, observable signals, and only documented recovery behavior;
- dependency IDs and incompatible resources;
- precise evidence locators and evidence levels;
- extraction confidence and its basis;
- unknowns and lifecycle state.

## Interpretation rules

- “Publish a command” is not “reach the physical target.”
- “Supports technology X” is not an atomic capability until a triggerable effect is stated.
- A maximum payload, range, accuracy, current, or speed is a constraint or metric, not an independent capability.
- A source omission is unknown, not proof of support or absence.
- A capability from another model is not evidence for the target model.
- A marketing claim without an interface, test, or precise operational effect remains a discovery clue.

## Entry quality gate

An entry is invalid when:

- its name or effect contains multiple independently useful results;
- it lacks an invocation surface;
- it uses scenario names such as reception, patrol, or delivery as the capability effect;
- its scope mixes unresolved models without marking ambiguity;
- performance values have no evidence locator;
- `verified` lacks repeatable E4 or E5 evidence;
- a dependency refers to a missing or unstable capability ID.
