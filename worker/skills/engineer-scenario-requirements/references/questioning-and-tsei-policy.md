# Questioning and TSEI policy

## Question priority

Ask no more than three questions per round. Rank questions by:

1. safety or regulatory consequence
2. ability to change feasibility
3. ability to change hardware or architecture
4. ability to change cost or schedule
5. usefulness for acceptance testing

Do not ask a question merely because a field is empty. Ask when its answer affects a decision.

## TSEI as constraints

Use action and observable result as the main axis. Treat TSEI as coupled constraint families:

- Time: trigger time, latency, duration, frequency, ordering, timeout, recovery.
- Space: pose, range, geometry, reach, clearance, frame, terrain, safety distance.
- Information: identity, semantics, accuracy, uncertainty, freshness, format, permissions, communication.
- Energy: payload, force, torque, current, power, endurance, charging, heat.

Assign the primary driver to the variable most directly judged by the acceptance test. Allow co-primary hard gates. Do not impose a fixed T→S→I→E order.

## Coupling

Record each derived constraint as source → consequence with rule/formula, assumptions, and confidence. Typical edges include:

- payload ↑ → torque/current/heat ↑ → endurance ↓ → charging time ↑
- speed ↑ → stopping distance ↑ → sensing rate and safety clearance ↑
- localization uncertainty ↑ → clearance requirement ↑ → route/time changes
- communication latency ↑ → information freshness ↓ → safe speed ↓

Return high-impact unknowns to the user; never hide them inside a score.

## Deployment questions

After safety and technical blockers, capture the smallest business facts needed to judge deployment viability: run frequency, human baseline time/cost, allowed intervention, failure loss, environment modification allowance/cost, annual operating cost, and target payback. Keep missing values `unknown`; do not invent a business case.
