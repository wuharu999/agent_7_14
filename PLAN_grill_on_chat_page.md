# Plan: Grill-Me on the chat page (wiki-grounded, DeepSeek-only)

Status: in grilling (design interview). Decisions marked ✅ confirmed, ⬜ open.

## Goal

Fold the capability-match "grill me" flow into the public QA chat page (`/`,
`ask.html`). No separate `/capability-match` route. A button on the chat page
switches the same conversation box into grill mode; the grill and its report
happen inline; returning to chat keeps referencing the same conversation
history. Answers are grounded in the wiki. The capability-match stack is
removed. Provider is DeepSeek API only — no Claude Code, no coding agents.

## Design decisions

- ✅ **Q1 — Entry point**: `#scenarioAssessment` becomes a `<button>` that stays
  on `/` and toggles the chat box into grill mode in place. No page navigation.
- ✅ **Q2 — Grill mode UI**: normal textarea + Ask swap to the grill composer
  (scenario description + start). QA conversation stays visible above; grill
  questions appear as bubbles in the same thread.
- ✅ **Q3 — History sharing**: grill records the same `conversation_id` and
  appends the grill Q&A into the shared `localStorage` history
  (`agent1_chat_history`), so the QA model sees grill turns as prior context.
- ✅ **Q4 — QA grounded in grill outcome**: the clarified scenario / report is
  injectable into QA context, not just remembered as raw chat text.
- ✅ **Q5 — Report rendering**: a collapsible drawer/section overlaid on the
  chat page, opened from the finished grill session. Reuse the drawer markup
  and JS from `capability_match.html`, wired to the scenario report endpoints.
- ✅ **Q6 — Old route**: keep `/capability-match` temporarily (redirect to `/`
  or deep links), stop linking to it from the chat UI. Remove once the merged
  flow is stable.
- ✅ **Q7 — Visibility**: grill button public on the chat page, matching current
  behavior (hidden only when the worker capability is off).
- ✅ **Q8 — Scope**: relocate the full scenario-clarification pipeline as-is
  first; simplify later if chat UX demands it.
- ✅ **Q9 — Report content**: replace the feasibility report (matches/gaps vs.
  capability catalog) with a **wiki-grounded answer** written for the clarified
  scenario — like the QA answer, not a capability-catalog match.
- ✅ **Q10 — Grill question generation**: DeepSeek-only (`scenario_clarification.py`
  already uses `create_deepseek_client`). No subprocess/coding-agent invocation.
  The wiki evidence index (`scenario_retrieval`) stays as the retrieval backend.
- ✅ **Q11 — Removal scope**: **B — remove the whole capability-match stack**:
  `/capability-match` route + chat link, admin capabilities page
  (`/admin/capabilities`, `admin_capabilities.html`), capability catalog jobs,
  feasibility matching. Keep DB tables for migration safety; stop writing to them.
- ✅ **Q12 — Report delivery**: the report is delivered as a **chat response**
  (a bot bubble in the shared conversation), not a drawer or an export.
  No PDF/Markdown export; no report drawer.
- ✅ **Q13 — Persistence**: keep server persistence (scenario_sessions table) +
  resume; same behavior, relocated. Also lets chat history reference it reliably.
- ✅ **Q14 — Drawer**: reuse drawer HTML/CSS/JS from `capability_match.html`
  inside `ask.html`.
- ✅ **Q15 — Flow boundary**: grill takes over the input while active; a
  "back to chat" button returns to QA. No interleaving QA-mid-grill in v1.
- ✅ **Q16 — conversation_id on grill**: pass the current `conversation_id` to
  the scenario session create; grill questions can reference the ongoing chat
  topic.
- ✅ **Q17 — Session resume**: returning to grill mode always re-enters the
  **continued** grill session bound to the current conversation (the same
  session, not a fresh one). Only "New conversation" clears it, and that
  removes **both** the chat history and the grill session together.
- ✅ **Q18 — Report production**: **A** — keep the `analyze_scenario`
  multi-stage pipeline (decomposition → evidence evaluation → composition) but
  feed it **wiki evidence only** (drop the capability catalog), and change the
  report schema so the report is a wiki-grounded scenario answer with no
  catalog matches/gaps. Already DeepSeek-only and wiki-evidence-aware via
  `evidence_context`.
- ✅ **Q19 — Admin removal**: remove `/admin/capabilities`, the admin capability
  page, the admin nav entry, and all admin capability routes/UI (organize
  catalog, inspect source changes, create draft stub). No admin capability
  tooling survives.
- ✅ **Q20 — Existing DB data**: keep all existing scenario_sessions /
  assessment / catalog rows untouched; no migration drop. The new chat-page
  grill only shows new sessions; historical capability reports are not surfaced.
- ✅ **Q21 — QA context injection**: store the grill outcome (clarified scenario
  + wiki-grounded answer) as the last assistant turn(s) in the shared
  `localStorage` history, so `/ask` includes them in its last-12-turns context
  naturally. No new API.
- ✅ **Q22 — Grill question loop**: keep the DeepSeek `clarify_scenario` state
  machine as-is; it asks about the scenario, not the catalog. Only the final
  report changes.
- ✅ **Q23 — Output-length safety**: DeepSeek output limits have caused
  truncation errors (the current single evaluation call uses
  `max_tokens=48000`, far above the model's real output cap; `truncated_output`
  is only detected+retried, never prevented). Split report generation into
  **multiple bounded calls** and merge with a stronger model:
  - Decomposition: bounded call (safe cap).
  - Evaluation: split into per-section bounded calls (e.g. 3 sections), each
    under the output cap instead of one giant 48000-token call.
  - Report composition: 3 separate section outputs (summary, engineering/
    tool support, PoC plan), each bounded.
  - Final merge: one **`deepseek-v4-pro`** call (`DEEPSEEK_MERGE_MODEL` env var,
    new config) combines the section outputs into the final wiki-grounded
    report.
  - All calls stay DeepSeek-only, no coding agents, temperature 0.

## Current implementation notes (facts)

- `ecs/app/templates/ask.html`: `#scenarioAssessment` is an `<a href="/capability-match">`
  (line 118), currently navigates away, prefills via `sessionStorage`
  (`agent1.scenario.prefill`).
- `ecs/app/routes/capability_match.py`: `/capability-match` page route,
  `/api/capability-match/analyze`, deprecated grill route, admin capabilities.
- `ecs/app/routes/scenario_sessions.py`: the live grill API —
  create/answers/analyze-now/keep-asking/messages/confirm-change/claim/events/
  reports/revisions/share/export.
- `worker/scenario_clarification.py`: DeepSeek-backed grill question
  generation + state patches (`create_deepseek_client`, `prompt_policy`).
- `worker/capability_matcher.py`, `worker/capability_catalog.py`: feasibility
  matching against the capability catalog (built from agent1 skills) — the
  stack to be removed per Q11-B.
- `worker/scenario_retrieval.py`: wiki evidence index (kept, Q10).
- Chat history: `agent1_chat_history` + `agent1_conversation_id` in
  localStorage; `/ask` receives `conversation_id` + last 12 turns.

## Execution order (draft, not yet finalized)

1. Backend: wiki-grounded report — `analyze_scenario` feeds wiki evidence only
   (drop catalog load), report schema drops matches/gaps (Q9/Q18).
2. Backend: output-length safety — chunk decomposition/evaluation/composition
   into bounded calls, merge sections with `deepseek-v4-pro`
   (`DEEPSEEK_MERGE_MODEL`) (Q23).
2. Backend: pass `conversation_id` into scenario session create (Q16).
3. Frontend: merge grill UI into `ask.html`, replace link with
   toggle button (Q1/Q2/Q5/Q14/Q15).
4. Frontend: shared-history wiring — grill turns + outcome in `localStorage`
   history; session bound to conversation_id; "New conversation" clears
   both chat and grill (Q3/Q4/Q16/Q17/Q21). Report rendered as a chat
   response (Q12).
5. Removal: delete capability-match stack — routes, templates, admin
   capability UI, catalog jobs, feasibility matching (Q11-B/Q19).
6. Route cleanup: redirect `/capability-match` (Q6); admin nav entry removed.
7. Tests + migration safety (existing DB rows untouched) + deploy.

## Status

Design interview complete. All 22 decisions confirmed (✅). No open frontier.
Next step: user confirmation, then implementation.
