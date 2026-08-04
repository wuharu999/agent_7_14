# Matching and gap policy

## Three-stage matching

1. Candidate recall: use semantics, aliases, tags, interfaces, and graph search. False positives are acceptable here.
2. Contract verification: compare effect, object, inputs/outputs, scope, preconditions, acceptance criteria, TSEI constraints, version, and evidence.
3. Composition verification: check mandatory nodes, data and control edges, pre/hold/postcondition continuity, resources, safety, and recovery.

## Match states

- `verified_satisfied`: evidence and every hard gate satisfy the requirement.
- `conditional`: satisfiable only under explicit additional preconditions.
- `partial`: some required effects or constraints are unsatisfied.
- `composite`: a verified capability composition satisfies it.
- `unproven`: plausible, but evidence or required facts are insufficient.
- `not_satisfied`: evidence shows at least one mandatory gate fails.
- `requirement_incomplete`: the requirement cannot yet be judged.

Keep state, confidence, and evidence level separate.

## Scenario conclusions

- Feasible: all `must` nodes are verified or verified composite matches.
- Feasible with conditions: all `must` nodes are satisfied if listed conditions are enforced.
- Prototype required: no known hard failure, but decisive evidence requires a bounded experiment.
- Currently unproven: blocking knowledge gaps remain.
- Infeasible: at least one mandatory constraint has no acceptable realization path.

## Deployment conclusions

Evaluate only after technical gates:

- `viable`: the technical result and confirmed operating/economic facts meet the deployment gates.
- `viable_with_conditions`: viability depends on explicit process, environment, utilization, or commercial conditions.
- `business_case_incomplete`: technical work may proceed, but decisive operating or economic facts remain unknown.
- `not_viable`: a confirmed deployment gate fails.

Do not average technical, safety, and economic values into one score. Safety, regulatory, and mandatory technical constraints remain hard gates.

## Gap diagnosis

Classify before recommending:

- missing evidence
- parameter or configuration
- composition or orchestration
- interface/data incompatibility
- missing software capability
- missing external sensing or actuation
- platform hardware/physical limit
- environmental/process incompatibility
- unacceptable safety or regulatory gap

## Realization ladder

Rank viable options from least invasive:

1. obtain documentation or run a verification test
2. configure an existing capability
3. compose existing capabilities
4. add an adapter or middleware
5. develop a software capability
6. add a sensor, end effector, compute, or external system
7. modify platform hardware
8. change the customer process or relax the requirement
9. replace the platform or reject the scenario

Every option must state evidence basis, prerequisites, covered and residual constraints, effort band, safety risk, reversible next experiment, and exit criterion.
