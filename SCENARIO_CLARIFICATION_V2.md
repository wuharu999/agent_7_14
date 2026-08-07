# Scenario clarification and evidence-backed analysis V2

The `/capability-match` page is now a persistent one-question scenario studio.
It replaces the browser-only Grill Me checklist while retaining the legacy
assessment API and stored assessments during rollout.

## User flow

1. Describe the customer outcome and choose a robot model.
2. Answer one high-impact question at a time. Each question has up to three
   suggested answers, a separate custom-answer action, and a distinct
   `I don't know yet` action.
3. `Analyze now` becomes available when the customer goal and full workflow
   boundary (trigger, major steps, and end outcome) are confirmed.
4. Early analysis produces a partial report when material conditions remain.
   A stable scenario shows a five-second countdown before automatic analysis.
5. The immutable report opens in a draggable bottom drawer. Earlier revisions
   stay selectable; Markdown and PDF exports bind to the selected revision.
6. `Continue refining` preserves the report. A proposed requirement change is
   confirmed with an in-chat card before creating a new scenario-state version.

## State-machine correctness guarantees

- Deterministic stability takes precedence over fallback questions. A stable
  state has no current question and enters `stability_countdown` immediately.
- `scenario_sessions.status` and the current state-version status are updated
  together, including report completion and interrupted-analysis recovery.
- Claude may propose only allowlisted `set`, `append`, and `upsert` operations
  under customer specification roots. Both the structured-output schema and ECS
  enforce path-specific value contracts: bounded workflow steps and text,
  confirmation enums, finite unit-bearing envelopes, and typed collection
  records. Invalid advisory patches are logged and discarded without losing the
  customer's answer. Model updates to derived solution paths, IDs, versions,
  report pointers, and runtime status are forbidden.
- Every dynamic custom answer is also retained as a semantic customer fact,
  even when it is not one of the original built-in question keys.
- Before technical clarification, the Worker retrieves relevant catalog
  records and verification profiles from the selected model's live Wiki. Direct
  lexical matches and normalized Chinese/English concept tags contribute to
  ranking; an unrelated query returns no arbitrary capability fallback.
- A requirement change during analysis creates a newer state version without
  cancelling the old snapshot. Report finalization begins `BEGIN IMMEDIATE`,
  re-reads the authoritative state version, classifies the report, completes the
  old job, and records the newest pending reanalysis version in one transaction.
  After commit, that durable marker queues one idempotent analysis of the latest
  version. The confirmation route also rechecks the active job after saving so
  the inverse commit ordering cannot miss reanalysis.
- The most recent report pointer remains visible while refinement or reanalysis
  is running. The composer also remains available during analysis.
- SSE progress stays open until disconnect and resumes from `Last-Event-ID` or
  the explicit cursor. The browser reconnects after normal EOF.
- Progress text is constructed only from server stage labels and allowlisted
  integer counts. Raw Claude text, paths, prompts, and tool details are never
  progress summaries.
- Post-report messages use the Worker's structured intent classifier. When the
  Worker is unavailable or classification fails, ECS returns `unclear`; it does
  not guess from punctuation. A classified report question is answered by a
  second no-tools structured call using only approved report fields, with
  section citations rather than a generic conclusion summary.
- `I don't know yet` creates an explicit follow-up that lets the user provide a
  value, select a conservative assumption, or assign vendor/pilot validation.

Anonymous sessions store a high-entropy resume token in the browser. The ECS
stores only its SHA-256 hash. Signed-in sessions are owned by the account and
resume across devices. An anonymous session can be claimed after sign-in by
proving the resume token.

Private share links are high-entropy, revocable, read-only, and expiring. The
shared page includes only approved conclusion, condition, evidence-name,
unknown, and action fields. It excludes the chat transcript, raw prompts,
technical evidence paths, and account information.

## Additive database migration

ECS startup creates these tables idempotently:

- `scenario_sessions`
- `scenario_state_versions`
- `scenario_events`
- `scenario_analysis_jobs`
- `scenario_report_revisions`
- `scenario_share_links`

`scenario_sessions.pending_reanalysis_state_version` is an additive nullable
column used as the durable coalescing marker for analysis races.

Existing `scenario_assessments`, users, uploads, and other application tables
are not rewritten. Jobs interrupted by an ECS restart are marked failed with a
safe category; accepted scenario-state versions and completed reports remain.

## Rolling upgrade behavior

The ECS sends the established `grill_scenario` and `analyze_scenario` Worker
message types. Therefore the new ECS can run with the previous Worker during a
controlled rolling upgrade. The previous Worker can propose questions through
the compatibility envelope; the ECS still enforces one-question selection,
semantic deduplication, authoritative state, and the minimum gate.

After the Worker update, a `scenario_state` field activates the structured
clarification contract. Clarification and analysis then use separate bounded
queues:

```env
CLARIFICATION_WORKERS=1
CLARIFICATION_QUEUE_MAX=16
CAPABILITY_MATCH_WORKERS=1
CAPABILITY_MATCH_QUEUE_MAX=8
```

## Capability migration

The catalog uses two explicit capability types:

- `building_block`
- `operational_behavior`

Condition-scoped `verification_profiles` retain workspace, lighting,
terrain/weather, dynamics, object/payload, duty cycle, version, test level,
sample/pass count, measurements, evidence locator, support state, limitations,
and unknowns.

Legacy L0 records map to `building_block`; L2 records map to
`operational_behavior`; L1 records receive an explicit review warning; L3
scenario modules move outside the capability catalog. Missing types become
`unclassified` with a review warning and never silently default to a building
block. Legacy operational records with evidence references receive a
conditional verification profile whose unknown operating-envelope fields are
explicitly marked for backfill; the migration does not silently treat those
unknown boundaries as verified.

## Deployment sequence

Deploy ECS first using the normal backup/restart procedure. This applies the
additive SQLite migration and keeps the compatibility Worker envelope. Do not
claim structured Worker behavior until the separate Worker host has pulled the
same commit and passed its local checks.

On the Worker host:

```bash
cd "$HOME/Documents/agent_7_14"
./scripts/pull_and_restart_worker.sh
```

Confirm exactly one `agent-7-14-worker` session, then verify ECS `/health`
reports `worker_online: true`. Run a new anonymous clarification, signed-in
resume, early partial report, full stable report, revision export, and revoked
share-link check. Keep the legacy flow and stored assessments until this real
ECS/Worker validation is complete.
