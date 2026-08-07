# Scenario Clarification and Evidence-Backed Analysis V2

## Codex implementation plan

### Purpose

Replace the current `Grill Me` questionnaire and capability-match workbench with a persistent, evidence-aware scenario clarification system. The system must ask one high-value question at a time, prevent repeated questions, allow early analysis after a minimum requirement gate, automatically analyze when the conclusion becomes stable, and present versioned results in a draggable report drawer on the same page.

The implementation must preserve the existing Cloud/Worker architecture, Wiki ingestion, authentication, uploads, WeCom integration, and completed assessment data unless a migration in this plan explicitly requires otherwise.

Before editing code, read the repository `AGENTS.md`, inspect the current schemas and tests, and produce a short file-level impact map. Do not perform a broad rewrite of unrelated systems.

---

## 1. Confirmed product decisions

These decisions are requirements, not open questions:

1. Ask one clarification question at a time.
2. Each question normally shows three relevant options plus a fourth `I want something else` choice.
3. Selecting `I want something else` opens a free-text field and does not advance until the user submits it.
4. Provide a separate `I don't know yet` action. Do not treat it as equivalent to `Other`.
5. The only hard minimum before manual `Analyze now` is enabled is:
   - a confirmed customer goal; and
   - a confirmed task workflow with a trigger, major steps, and end outcome.
6. After the minimum gate passes, the user may request analysis early. The result must clearly preserve unresolved conditions and evidence gaps.
7. If the user tries to analyze before the minimum gate passes, do not generate a feasibility report. Ask the next missing goal/workflow question or save the session as a draft.
8. The system automatically starts analysis when its conclusion-stability gate passes.
9. Before automatic analysis, show a short countdown with `Keep asking` and `Analyze now` actions.
10. When analysis finishes, stay on the same page and slide up a report drawer occupying about half of the viewport.
11. The report drawer scrolls internally and can be dragged or collapsed to return to the preserved chat.
12. The report opens conclusion-first. Technical details remain collapsed by default.
13. `Continue refining` returns to chat without discarding the report.
14. Completed reports are immutable revisions. Preserve earlier revisions and mark the newest applicable revision as current.
15. After a report exists, classify new messages as `report_question`, `requirement_change`, `new_scenario`, or `unclear`.
16. Confirm a proposed requirement change before mutating scenario state or starting a new report revision.
17. If requirements change during a running analysis, finish the current snapshot, preserve its report, mark it superseded when appropriate, and analyze the latest coalesced scenario version afterward.
18. Support resuming through:
   - a signed-in account across devices;
   - an anonymous same-browser resume token; and
   - a private, revocable, read-only report share link.
19. Share links expose only approved report content. They must not expose the clarification transcript, private evidence paths, raw prompts, or internal account information.
20. Waiting UI shows workflow stages plus sanitized AI summaries. It must never stream raw Claude reasoning or subprocess output.

---

## 2. Product objective and non-goals

### Objective

Help an FDE, sales user, engineer, or future customer convert a vague deployment idea into a defensible robot-fit decision. The result should distinguish:

- what the customer confirmed;
- what the knowledge base or vendor documentation supports;
- what engineering calculations infer;
- what has been tested and under which operating conditions;
- what remains unverified;
- which environment changes could make the scenario feasible; and
- who owns each next action.

### Non-goals

- Do not expose chain-of-thought or hidden model reasoning.
- Do not claim that an SDK interface proves an end-to-end deployment behavior.
- Do not convert missing evidence into `not supported`.
- Do not publish a precise feasibility percentage without a validated quantitative model.
- Do not make an unreviewed external delivery commitment or precise R&D schedule.
- Do not require a generic 33-field questionnaire or a fixed number of turns.
- Do not ask customers for technical information that should be retrieved from the Wiki, confirmed by a vendor, or measured in an experiment.

---

## 3. Existing problems to resolve

Verify each of these against the current source before implementing:

1. `grill_scenario()` asks Claude to cover a broad fixed checklist and uses turn/field counts as a completion heuristic.
2. Clarification state is primarily browser-local and is not a durable, versioned server-side scenario record.
3. The system passes accumulated history back to a stateless subprocess but does not enforce semantic question deduplication in Python.
4. The clarification prompt and the engineering skill disagree about how many questions to ask per round.
5. The current `/api/capability-match/analyze` endpoint returns `202`, while the workbench JavaScript appears to expect an immediate completed result.
6. The workbench renders fields such as `feasibility_score`, `overall_status`, and `rd_power_needed` that are not consistently represented by the assessment schema.
7. The front end inserts model-generated content into raw HTML and inline event handlers.
8. Four capability abstraction levels are duplicated across schemas, prompts, deterministic gates, reports, UI, and tests.
9. The maintained Wiki capability schema and shared runtime capability schema are not canonicalized. A missing abstraction level may default to L0 and incorrectly trigger the current hard gate.
10. Raw Claude NDJSON must not be forwarded to the browser, especially given the prior oversized-line transport failure.

---

## 4. Target user experience

### 4.1 Clarification state

The same page moves through these states:

```text
draft -> clarifying -> minimum_ready -> stability_countdown -> analyzing -> report_ready
                      ^                    |                         |
                      |                    v                         v
                      +---- keep asking ---+-------------------- refining
```

The server owns the authoritative state. Refreshing, reconnecting, changing devices after sign-in, or collapsing the report drawer must not lose progress.

### 4.2 Question card

Show one question at a time with:

- the question;
- an optional short reason such as `This determines whether fixed-coordinate replay is sufficient`;
- up to three evidence-aware suggested options;
- `4. I want something else`;
- `I don't know yet`; and
- an unobtrusive progress indication such as `Clarifying the task workflow` rather than a fake percentage.

Suggested options are not enum constraints. Preserve the user's original free text and normalize it into scenario state separately.

### 4.3 Preventing apparent repetition

Every question has a stable `semantic_key`. Do not ask another question with the same resolved key.

If the previous answer was too vague and a more precise value is necessary, mark the new question as a refinement:

```text
You previously selected "small packages." The current evidence has a decision boundary at 260 mm, so I still need the maximum width.
```

Persist `refines_question_id`, `previous_answer`, and `missing_precision`. Do not pretend the previous answer was forgotten.

### 4.4 Minimum gate and early ending

`Analyze now` is disabled until both conditions are confirmed:

1. Goal: the intended customer/business outcome.
2. Workflow: trigger, major steps, and end outcome.

Once the gate passes, manual early analysis is allowed even when other customer facts are unknown. Store `analysis_trigger = user_requested_early` and make the report explicit about incomplete requirements.

If the user leaves before the minimum gate passes, save the session as a resumable draft. Do not create a misleading feasibility report.

### 4.5 Automatic analysis

Automatically analyze when the stability evaluator determines that no remaining customer-answerable unknown is likely to change the top-level conclusion.

Show a five-second countdown with:

- `Keep asking`;
- `Analyze now`; and
- a short explanation of why the scenario is considered stable.

`Keep asking` cancels only the countdown. It does not discard scenario state.

### 4.6 Report drawer

When a report becomes ready:

- slide a bottom drawer to approximately 50% viewport height on desktop;
- use a larger mobile height, approximately 80-90%;
- preserve chat behind the drawer;
- allow dragging, keyboard controls, and explicit expand/collapse buttons;
- keep report scrolling inside the drawer;
- restore the last drawer position after a refresh when practical.

The initial report view contains:

1. Fit conclusion.
2. Conditions attached to the conclusion.
3. Main evidence.
4. Blocking or high-impact unknowns.
5. Recommended next experiment or confirmation.
6. `Continue refining`.
7. `Show technical details`.
8. Report revision selector and current/superseded status.

Do not show a four-level capability tree or an invented percentage gauge in the standard user view.

### 4.7 Post-report messages

Classify each new message without immediately mutating state:

- `report_question`: answer using the current report and its approved evidence; do not create a revision.
- `requirement_change`: show the proposed state patch and ask the user to confirm it.
- `new_scenario`: offer to create a new scenario session.
- `unclear`: ask which intent the user meant.

Only a confirmed requirement change creates a new scenario version and makes the current report potentially stale.

---

## 5. Scenario-state contract

Add a canonical, versioned `ScenarioState` schema. A practical initial representation is a validated JSON document plus an append-only event log.

Required concepts:

```json
{
  "session_id": "SCNSESSION-*",
  "state_version": 1,
  "status": "clarifying",
  "initial_intent": "Original user text",
  "goal": {
    "original_text": "",
    "normalized_value": "",
    "confirmation": "unknown"
  },
  "workflow": {
    "trigger": "",
    "steps": [],
    "end_outcome": "",
    "confirmation": "unknown"
  },
  "actors": [],
  "objects": [],
  "environment": {},
  "operating_profile": {},
  "allowed_modifications": {},
  "human_intervention": {},
  "acceptance_criteria": [],
  "facts": [],
  "assumptions": [],
  "requirements": [],
  "unresolved_issues": [],
  "question_history": [],
  "candidate_solution_paths": [],
  "minimum_gate": {
    "passed": false,
    "missing": []
  },
  "stability": {
    "stable": false,
    "reason": "",
    "remaining_user_decisions": []
  }
}
```

Facts, assumptions, requirements, and issues must preserve:

- original wording;
- normalized value;
- knowledge state: `known`, `assumed`, `unknown`, or `conflicted`;
- source owner: customer, Wiki, vendor, calculation, simulation, bench, pilot, or field;
- evidence locator when applicable;
- affected requirement or decision;
- whether it can change the conclusion; and
- last state version that changed it.

Never silently turn absence into a fact.

---

## 6. Clarification orchestration

### 6.1 One model call per user turn

Use one bounded clarification subprocess call per submitted answer where possible. Python must retrieve a compact set of relevant capabilities and evidence boundaries before building the clarification request.

The subprocess receives:

- the current validated `ScenarioState`;
- the new user message or selected answer;
- a compact evidence/capability context;
- unresolved issues;
- semantic keys already asked and resolved; and
- the structured response schema.

It returns:

```json
{
  "state_patch": [],
  "candidate_questions": [],
  "candidate_issues": [],
  "intent": "requirement_answer",
  "model_readiness_opinion": {
    "stable": false,
    "reason": ""
  }
}
```

Use JSON Patch-like operations or another explicit patch contract. Do not let the model replace the complete state document.

### 6.2 Question contract

Each candidate question includes:

```json
{
  "question_id": "Q-*",
  "semantic_key": "object.package.max_width_mm",
  "question": "What maximum package width must the system support?",
  "reason_for_asking": "The current evidence changes at approximately 260 mm.",
  "decision_impact": ["feasibility", "architecture"],
  "can_change_conclusion": true,
  "blocking": true,
  "target_owner": "customer",
  "answer_type": "single_select_or_custom",
  "options": ["260 mm or less", "261-411 mm", "More than 411 mm"],
  "prerequisite_keys": [],
  "refines_question_id": null
}
```

The UI, not the model, appends the standard fourth `Other` option and the separate `I don't know yet` action.

### 6.3 Deterministic patch validator

Python owns state mutation. Reject or quarantine a patch that:

- changes a confirmed fact without user confirmation;
- removes unresolved safety or feasibility issues silently;
- changes another session;
- introduces unsupported fields;
- exceeds size limits;
- claims evidence not present in the supplied context; or
- converts an unknown value into a supported claim without evidence.

Increment `state_version` only after a validated patch is committed.

### 6.4 Deterministic question selector

Claude proposes questions; Python selects the one shown.

Filter out candidates when:

- the semantic key is already resolved;
- an equivalent question was answered;
- prerequisites are unresolved;
- the customer is not the correct owner;
- the answer exists in retrieved evidence;
- the question cannot affect safety, feasibility, architecture, cost, acceptance, or a required report condition; or
- it is merely an empty optional field.

Rank the remaining candidates in this order:

1. Safety or regulatory consequence.
2. Can change the top-level fit conclusion.
3. Can change required hardware, software, environment modification, or human intervention.
4. Can change acceptance testing.
5. Can materially change cost or schedule.

If the top candidate is a refinement, explain why more precision is necessary.

### 6.5 Wrong-owner routing

Do not ask the customer questions such as `What is the robot's navigation repeatability?` when the answer belongs to technical evidence.

Assign unresolved issues to:

- customer clarification;
- Wiki retrieval;
- vendor confirmation;
- calculation;
- simulation;
- bench test;
- pilot test; or
- field test.

When no valuable customer question remains, technical unknowns become report actions rather than more interrogation.

### 6.6 Stability evaluator

Python makes the final stability decision using validated state and issue metadata. The model's readiness opinion is advisory.

The scenario is stable when:

1. The minimum goal/workflow gate passes.
2. At least one preliminary fit path or hard-failure path can be described.
3. No unresolved customer-owned issue has `can_change_conclusion = true`.
4. Every remaining high-impact issue has a non-customer owner and concrete next action.
5. Remaining unknowns can alter conditions, confidence, or validation work, but are not expected to change the categorical conclusion.

Do not use a fixed turn count. A soft checkpoint may be shown after a configurable number of questions, but it must not silently force completion.

---

## 7. Two-type capability model and operating envelopes

Migrate the four-level capability hierarchy to two capability types:

- `building_block`: a directly callable interface or engineering primitive with one bounded observable effect.
- `operational_behavior`: a reusable end-to-end robot behavior with one independently testable outcome.

Map legacy records deterministically:

| Legacy | Initial migration |
| --- | --- |
| L0 primitive driver | building_block |
| L1 atomic skill | Review rule based on public trigger/effect; migrate with warning when ambiguous |
| L2 composite skill | operational_behavior |
| L3 scenario module | Move/reference as ScenarioSpec, CapabilityComposition, or SolutionArtifact, not a capability |

Treat atomicity as a contract-quality property, not a hierarchy level.

Add condition-scoped verification profiles containing:

- indoor/outdoor and workspace type;
- lighting;
- terrain and weather;
- workspace dynamics and human presence;
- object and payload boundary;
- speed, throughput, or duty cycle;
- hardware, firmware, SDK, and software versions;
- test level;
- sample size and passed count;
- measured values and units;
- evidence locator;
- support state: `supported`, `conditional`, `unproven`, or `not_supported`;
- limitations; and
- unresolved unknowns.

Replace the L0 hard gate with an evidence-and-contract gate:

- A building block can satisfy an engineering interface requirement.
- A building block alone cannot prove an end-to-end customer behavior.
- An operational behavior satisfies a deployment requirement only inside a matching, evidenced operating envelope.
- Enabling building blocks without operational evidence produce `prototype_required` or `insufficient_evidence`, not an invented fixed effort estimate.

Canonicalize the maintained Wiki entry schema and runtime capability schema so generated entries cannot silently default to an incorrect type.

---

## 8. Analysis snapshots and report revisions

### 8.1 Immutable snapshot rule

Every analysis job binds to:

- `session_id`;
- `scenario_state_version`;
- capability catalog revision;
- relevant Wiki/evidence revision;
- analysis pipeline version; and
- language.

Do not read mutable latest state halfway through a job.

### 8.2 Changes during analysis

If the scenario changes while analysis for version `Vn` is running:

1. Continue the current job.
2. Save its report revision against `Vn`.
3. Mark it `superseded` if a newer scenario version exists.
4. Coalesce all pending changes to the newest version.
5. Queue at most one follow-up analysis for the newest version.
6. Do not start one analysis per rapid edit.

Use idempotency keys based on session, state version, catalog revision, pipeline version, and language.

### 8.3 Report statuses

Support at least:

- `current`;
- `superseded`;
- `failed`;
- `processing`; and
- `partial` for an early user-requested analysis with material unknowns.

Earlier report revisions remain viewable. Generate a safe, factual diff summary from changed scenario facts, requirements, conclusions, conditions, and actions.

### 8.4 User-facing categorical conclusion

Lead with one of:

- `fit`;
- `fit_with_conditions`;
- `prototype_required`;
- `insufficient_evidence`; or
- `not_a_fit`.

Keep technical and deployment conclusions separately in the detailed schema when useful, but map them consistently to the user-facing category.

---

## 9. Report structure based on the technical validation example

Build the report incrementally from typed records rather than asking one final model call to invent a monolithic document.

Required sections:

1. Conclusion.
2. Validation assumptions and information sources.
3. Customer equipment and environment specification.
4. Robot specification and relevant capability evidence.
5. Requirement-by-requirement validation results.
6. Proposed implementation workflow.
7. Recommended environment modifications, with effect and relative cost when evidence permits.
8. Unverified items and risks.
9. Next actions grouped by owner.
10. Expandable technical details, calculations, operating envelopes, evidence locators, and report metadata.

Use categorical row states:

- supported;
- supported under conditions;
- unverified;
- not supported; and
- awaiting confirmation.

For the locker-style scenario, the system must be able to distinguish customer facts such as package dimensions and allowed modifications from technical unknowns such as navigation repeatability, coordinated waist/arm control, collision checking, and balance validation.

---

## 10. Safe progress streaming

### 10.1 Transport

Keep the existing Worker-to-ECS WebSocket. For ECS-to-browser progress, prefer Server-Sent Events because progress is one-way and benefits from simple reconnection. Continue using HTTP for answers, cancellation, `Analyze now`, `Keep asking`, and refinement confirmation.

A browser WebSocket is acceptable only if existing infrastructure makes it materially simpler. Do not choose it merely to display an animation.

### 10.2 Progress-event schema

Add a strict event schema such as:

```json
{
  "event_id": "EVT-*",
  "session_id": "SCNSESSION-*",
  "analysis_job_id": "JOB-*",
  "scenario_state_version": 3,
  "type": "stage_summary",
  "stage": "evidence_retrieval",
  "status": "running",
  "message": "Checking robot capability evidence",
  "approved_facts": {
    "requirements_identified": 6,
    "documents_checked": 14
  },
  "created_at": "..."
}
```

Allowed stages include:

1. Understanding the workflow.
2. Extracting testable requirements.
3. Retrieving capability evidence.
4. Comparing operating envelopes.
5. Evaluating gaps and risks.
6. Building recommendations.
7. Generating the report revision.

### 10.3 Sanitized AI summaries

Never summarize raw Claude stdout or hidden reasoning.

Construct an `ApprovedProgressSnapshot` containing only allowlisted data:

- stage ID and status;
- fixed user-facing stage label;
- non-sensitive counts;
- approved public capability names;
- approved evidence display titles;
- categorical match counts;
- public gap names; and
- elapsed time.

The summary generator receives only that snapshot and returns a short schema-validated sentence. Apply:

- a strict output length;
- no Markdown/HTML unless explicitly sanitized;
- no URLs or paths;
- no tool names, prompts, system instructions, environment variables, stack traces, or raw excerpts;
- a final sensitive-pattern check; and
- a fixed-template fallback when generation or validation fails.

Use an allowlist as the primary security boundary. A forbidden-word filter is only defense in depth.

### 10.4 Reconnection

Support `Last-Event-ID` or an equivalent cursor. Persist enough recent stage events for a browser to reconnect without restarting an analysis job. A disconnected browser must not cancel the Worker job.

---

## 11. Persistence, identity, and sharing

Extend the existing SQLite migration system additively. A reasonable design is:

### `scenario_sessions`

- session ID;
- owner user ID, nullable;
- hashed anonymous resume token, nullable;
- status;
- current state version;
- current report revision ID, nullable;
- language and robot/model selection;
- created/updated timestamps; and
- soft-deletion metadata.

### `scenario_state_versions`

- session ID;
- state version;
- validated state JSON;
- change source;
- created timestamp; and
- actor identity when available.

### `scenario_events`

- append-only clarification, answer, intent, confirmation, countdown, and system events.

### `scenario_analysis_jobs`

- immutable snapshot identifiers;
- idempotency key;
- status and progress cursor;
- superseded flag;
- error category safe for users; and
- internal error correlation ID.

### `scenario_report_revisions`

- report revision ID and ordinal;
- snapshot references;
- structured report JSON;
- rendered Markdown/PDF metadata;
- current/superseded/partial status;
- diff summary; and
- timestamps.

### `scenario_share_links`

- hashed high-entropy token;
- report revision or current-report binding;
- `view_only` permission;
- expiry, revocation, and access timestamps;
- creator identity; and
- optional access-rate controls.

Never store raw resume or share tokens after issuance. Use secure, constant-time token verification.

Allow an anonymous session to be claimed after sign-in. Require proof of the anonymous resume token and record the claim event.

---

## 12. API surface

Adapt names to repository conventions, but cover these operations:

```text
POST   /api/scenario-sessions
GET    /api/scenario-sessions/{session_id}
POST   /api/scenario-sessions/{session_id}/messages
POST   /api/scenario-sessions/{session_id}/answers
POST   /api/scenario-sessions/{session_id}/analyze-now
POST   /api/scenario-sessions/{session_id}/keep-asking
POST   /api/scenario-sessions/{session_id}/confirm-change
GET    /api/scenario-sessions/{session_id}/events
GET    /api/scenario-sessions/{session_id}/reports
GET    /api/scenario-sessions/{session_id}/reports/{revision_id}
POST   /api/scenario-sessions/{session_id}/claim
POST   /api/scenario-sessions/{session_id}/share
DELETE /api/scenario-sessions/{session_id}/share/{share_id}
GET    /s/scenario-report/{share_token}
```

All mutating routes require CSRF protection where applicable, idempotency protection, ownership checks, validation, and rate limits.

The legacy `/api/capability-match/grill` route should either become a compatibility adapter or be deprecated after the new flow is verified. Do not maintain two independent clarification engines.

---

## 13. Worker and queue design

Separate clarification work from full analysis work so repeated lightweight turns cannot starve report generation.

Recommended queues:

- `clarification_queue`: short, bounded, one subprocess call per user turn.
- `analysis_queue`: longer evidence-backed analysis jobs.
- existing capability-catalog organization queue: unchanged.

Requirements:

- one active clarification operation per session;
- one active analysis per immutable state version;
- at most one coalesced pending reanalysis per session;
- state-hash caching for identical clarification requests;
- bounded retries with idempotent result application;
- user-safe errors separated from internal logs; and
- no raw prompt or subprocess stream forwarded to ECS/browser progress.

If the clarification subprocess fails, preserve the accepted user answer and state version. Show a retry action. Do not silently substitute the same generic fallback questions repeatedly.

---

## 14. Expected file-level impact

Confirm exact paths before editing. Expected areas include:

### Existing files likely to change

- `shared/schemas/atomic-capability.schema.json`
- `shared/schemas/scenario-spec.schema.json`
- `shared/schemas/feasibility-assessment.schema.json`
- `shared/schemas/capability-composition.schema.json`
- `maintain-model-atomic-capability-wiki/references/atomic-capability-entry.schema.json`
- capability-maintenance skill contracts and templates
- `worker/capability_matcher.py`
- `worker/capability_catalog.py`
- `worker/manager.py`
- `ecs/app/database.py`
- `ecs/app/routes/capability_match.py`
- `ecs/app/templates/capability_match.html`
- `ecs/app/templates/ask.html` if entry-point integration is retained
- capability-match and schema validation tests
- user guide and validation documentation

### New modules to consider

- `worker/scenario_state.py`
- `worker/scenario_clarification.py`
- `worker/scenario_stability.py`
- `worker/analysis_progress.py`
- `shared/schemas/scenario-session.schema.json`
- `shared/schemas/clarification-turn.schema.json`
- `shared/schemas/analysis-progress-event.schema.json`
- `shared/schemas/report-revision.schema.json`
- focused tests for each module and route

Prefer small focused modules over expanding `capability_matcher.py` and the existing single HTML template indefinitely.

---

## 15. Implementation phases

### Phase 0: Audit and characterization

1. Read `AGENTS.md` and repository validation instructions.
2. Enumerate every L0-L3 reference and every `Grill Me` reference.
3. Map current request/response schemas and asynchronous job lifecycle.
4. Add characterization tests for current persisted assessments and exports.
5. Confirm current browser/API mismatch before changing behavior.

Deliverable: impact map and passing baseline tests.

### Phase 1: Canonical contracts and migration

1. Add scenario-session, clarification-turn, progress-event, and report-revision schemas.
2. Canonicalize capability schemas.
3. Add the two capability types and verification profiles.
4. Implement a non-destructive legacy L0-L3 migration with warnings for ambiguous L1 entries.
5. Update validation scripts before changing runtime code.

Deliverable: schema validation and migration tests.

### Phase 2: Persistent scenario state

1. Add additive database migrations.
2. Implement session, version, event, analysis-job, report-revision, and share-link repositories.
3. Add anonymous resume tokens and account claiming.
4. Add immutable state snapshots and optimistic version checks.

Deliverable: persistence and authorization tests.

### Phase 3: Clarification engine

1. Implement evidence-first context retrieval.
2. Implement the structured state-patch subprocess contract.
3. Add deterministic patch validation.
4. Add semantic question deduplication and refinement tracking.
5. Add wrong-owner routing.
6. Add minimum and stability gates.
7. Add countdown and manual early-analysis decisions.

Deliverable: deterministic unit tests plus the locker-scene integration test.

### Phase 4: Snapshot analysis and report revisions

1. Bind analysis to immutable state/catalog/evidence versions.
2. Replace the abstraction hard gate with evidence-and-contract matching.
3. Generate the structured report sections.
4. Implement superseded results and coalesced reanalysis.
5. Add report diffs and current-revision selection.
6. Keep Markdown/PDF export based on a specific report revision.

Deliverable: concurrency, revision, and export tests.

### Phase 5: Progress channel

1. Add strict progress-event emission at orchestrator stage boundaries.
2. Add an SSE endpoint with reconnection/cursor support.
3. Add allowlisted progress snapshots.
4. Add schema-constrained summary generation and fixed-template fallback.
5. Verify that raw subprocess events cannot reach the browser.

Deliverable: security and reconnect tests.

### Phase 6: Minimal same-page UI

1. Replace the auto-open Grill Me interface with the one-question flow.
2. Implement option cards, custom Other input, and `I don't know yet`.
3. Add persistent status and safe progress summaries.
4. Add the stability countdown.
5. Add the accessible draggable report drawer.
6. Add revision selection, technical-detail expansion, and `Continue refining`.
7. Add post-report intent confirmation.
8. Render all model text safely using DOM text APIs or a reviewed sanitizer.

Deliverable: browser interaction and XSS tests.

### Phase 7: Sharing, rollout, and documentation

1. Add revocable read-only share links.
2. Ensure shared pages exclude chat and private evidence metadata.
3. Add operational metrics and safe error correlation.
4. Update user and deployment documentation.
5. Roll out behind a feature flag or controlled route until real Worker validation passes.
6. Preserve a rollback path for the existing workbench during validation.

Deliverable: end-to-end validation on the deployed ECS/Worker pair.

---

## 16. Required tests and acceptance scenarios

### Clarification behavior

- One question is displayed at a time.
- The standard fourth Other option opens free text.
- `I don't know yet` records an unresolved value rather than custom content.
- Resolved semantic keys are not asked again.
- A refinement explicitly references the earlier insufficient answer.
- Questions owned by the vendor or an experiment are not asked of the customer.
- The minimum gate requires only confirmed goal and workflow.
- Manual early analysis is blocked before the minimum and permitted afterward.
- Stability does not depend on turn count.
- `Keep asking` cancels the countdown without losing state.

### Persistence and resume

- Refresh resumes the same question and state version.
- Signed-in users resume across devices.
- Anonymous browser tokens resume only their session.
- Anonymous sessions can be claimed after sign-in.
- Invalid, expired, revoked, or cross-user tokens are rejected.

### Analysis and revisioning

- Every job reads an immutable state snapshot.
- A requirement change during analysis does not mutate the running snapshot.
- The completed old result is stored and marked superseded.
- Multiple rapid edits coalesce into one latest reanalysis.
- Previous reports remain viewable.
- Ask-about-report messages do not create revisions.
- Confirmed requirement changes do create a new state version.

### Report behavior

- The bottom drawer opens automatically when the current report finishes.
- The drawer occupies approximately half the desktop viewport and is collapsible.
- Conclusion appears before technical details.
- Unknown evidence is represented as unverified/insufficient evidence, not unsupported.
- Environment modifications can turn not-fit-as-is into fit-with-conditions.
- Exports bind to a specific report revision.

### Progress security

- Only schema-valid allowlisted events reach the browser.
- Raw Claude stdout, prompts, tool arguments, paths, environment values, and stack traces are never forwarded.
- Summary-generation failure uses a fixed safe template.
- SSE reconnect does not restart the analysis.
- Oversized or malformed subprocess events cannot become browser events.

### Front-end security

- Model-generated question, option, summary, evidence, and report text cannot inject HTML or JavaScript.
- Inline `onclick` construction with model text is removed.
- CSRF, ownership, rate limiting, and idempotency protections cover new mutation routes.
- Read-only share links cannot access session APIs, transcript, or non-shared revisions.

### Capability migration

- Legacy L0 and L2 records migrate deterministically.
- Ambiguous L1 records receive an explicit migration warning or review status.
- L3 scenarios are not flattened into the capability catalog.
- Generated Wiki entries cannot silently default to `building_block`.
- A documented SDK interface does not prove deployment readiness.
- Navigation may be supported indoors under one profile and unproven outdoors or under different lighting.

---

## 17. Locker-scene reference acceptance test

Use a fixture modeled on the supplied parcel-locker technical validation report.

Initial user statement:

```text
We want Tiangong 3 to retrieve parcels from a Full Time Locker.
```

The clarification engine should prioritize questions such as:

1. Is this a supervised PoC, limited pilot, or unattended production deployment?
2. What is the exact retrieval workflow from trigger through delivery?
3. How much may the locker/environment be modified?
4. What package dimension and weight envelope must be supported?
5. Which locker size, opening, depth, door type, and height are in scope?
6. What observable result defines a successful run?

It should not ask the customer to supply robot navigation repeatability, SDK inverse-kinematics scope, ZMP stability, or collision-test results. Those become Wiki/vendor/test actions.

The report must be capable of stating:

- dimensional reach appears supported under stated assumptions;
- a package-width boundary changes the grasp conclusion;
- fixed-coordinate replay depends on navigation position and heading repeatability;
- low-cost environment changes such as markers, lighting, shelf tilt, or standardized trays can alter feasibility;
- collision, balance, dual-arm interference, real-machine error, and SDK scope remain separately identified verification items; and
- the conclusion is conditional rather than universally supported.

---

## 18. Observability

Record safe operational metrics without storing raw prompts in general application logs:

- clarification turns per session;
- candidate questions rejected as duplicates;
- refinement-question count;
- early-analysis rate;
- automatic-stability rate;
- `Keep asking` rate;
- time from initial scenario to first report;
- analysis queue wait and duration;
- reanalysis coalescing count;
- report revisions per session;
- progress-summary fallback rate;
- session resume and share-link usage; and
- human correction rate for extracted scenario facts.

Use these metrics to determine whether the system is reaching the real customer demand instead of merely asking more questions.

---

## 19. Definition of done

The feature is complete when:

1. All new schemas validate and legacy data migrates non-destructively.
2. The locker-scene acceptance flow works end to end.
3. Question repetition is deterministically prevented.
4. Minimum-gate, manual-early, and automatic-stability paths all work.
5. Refresh, cross-device resume, anonymous resume, account claiming, and read-only sharing are verified.
6. Analysis revisions remain immutable and concurrent edits coalesce correctly.
7. The report drawer and post-report refinement flow pass browser tests.
8. Progress streaming exposes only approved stages and sanitized summaries.
9. No raw Claude output or sensitive internal information reaches the user UI.
10. Markdown/PDF exports remain available for a selected report revision.
11. Existing knowledge Q&A, upload, catalog organization, authentication, and WeCom behavior continue to pass their tests.
12. The feature is validated with a real Claude CLI and the deployed ECS/Worker connection before the legacy flow is removed.

---

## 20. Codex execution instructions

Implement phase by phase. After each phase:

1. Run focused tests.
2. Run schema validation and Python compilation.
3. Report changed files, migrations, and unresolved risks.
4. Do not proceed past a failing contract or migration test.
5. Preserve unrelated user changes in the worktree.

Before final handoff, run the repository's documented validation suite, perform the locker-scene end-to-end test, and provide a concise migration and deployment sequence for ECS and Worker.
