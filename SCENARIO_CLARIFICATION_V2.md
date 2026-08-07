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
   confirmed before creating a new scenario-state version.

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
block.

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
