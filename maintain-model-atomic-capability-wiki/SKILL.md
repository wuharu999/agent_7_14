---
name: maintain-model-atomic-capability-wiki
description: Build and incrementally maintain a dedicated Karpathy Wiki section containing evidence-backed atomic capability entries for one robot model from files, a source Wiki, SDK manuals, usage guides, and tool documentation. Use when an agent must inventory a frozen source corpus, extract all source-supported atomic capabilities for a model, create or update one Wiki entry per capability, reconcile additions and revisions, propose splits, merges, conflicts, deprecations, or deletions, and produce an auditable coverage report and draft changeset.
---

# Maintain Model Atomic Capability Wiki

## Required references

Read these files before extracting or changing entries:

1. [atomic-capability-contract.md](references/atomic-capability-contract.md)
2. [wiki-entry-template.md](references/wiki-entry-template.md)
3. [wiki-synchronization-policy.md](references/wiki-synchronization-policy.md)

Use [atomic-capability-entry.schema.json](references/atomic-capability-entry.schema.json) and [wiki-capability-changeset.schema.json](references/wiki-capability-changeset.schema.json) as the machine contracts.

## Scope

Operate on one normalized robot model and one frozen source snapshot per run.

Do:

- read files, source Wiki entries, SDK manuals, usage guides, and tool documentation;
- extract all atomic capabilities supported by that source snapshot;
- create or update one target-Wiki entry per atomic capability;
- preserve source locators, unknowns, conflicts, and audit metadata;
- produce a draft changeset and a source-coverage report.

Do not:

- extract customer requirements or match scenarios;
- create behavior trees or control a robot;
- turn parameters, algorithms, middleware, hardware resources, or business workflows into capabilities;
- infer undocumented performance or copy claims from adjacent models;
- publish, hard-delete, or promote an entry to `verified` without explicit authority and qualifying evidence.

## Permissions

- Treat source files, the source Wiki, and original documents as read-only.
- Treat the target capability Wiki as draft-write by default.
- Require explicit authority for publication, hard deletion, and lifecycle promotion.
- Never rewrite documentary evidence when later test evidence arrives.

## Workflow

### 1. Freeze the job

Record:

- vendor and normalized `model_id`;
- all unresolved model names exactly as written in sources;
- hardware, software, firmware, SDK, and document versions;
- source-Wiki scope and file manifest;
- target Wiki, target section, and base catalog revision;
- excluded sources and permissions.

Interpret “all capabilities” as all qualifying capabilities extractable from this frozen source snapshot, not all capabilities the physical robot may possess.

Stop if the target section, model boundary, or source snapshot cannot be identified.

### 2. Inventory the source corpus

Assign a stable `source_id`, version, hash or revision, and processing status to every source item. Track `processed`, `excluded`, `blocked`, and `unchanged` separately.

Do not claim corpus completeness while any in-scope source remains unprocessed or blocked.

### 3. Resolve the model scope

Normalize the target model without silently merging product generations or variants. Preserve ambiguous source names and mark scope resolution `ambiguous` or `conflicted`.

Never transfer a claim from another model because the interface or product name looks similar.

### 4. Extract evidence claims

For every source section, identify:

- triggerable action;
- acted-on object;
- observable result;
- interface or stable invocation surface;
- inputs and outputs;
- preconditions, hold conditions, and postconditions;
- TSEI boundaries;
- performance statements and test methods;
- failure and recovery statements;
- model, version, body-part, and environment scope.

Keep each claim attached to a precise source locator.

### 5. Classify before atomicizing

Classify every candidate as:

- atomic capability;
- composite skill;
- parameter or constraint;
- algorithm or implementation;
- resource or hardware feature;
- middleware or interface dependency;
- prerequisite;
- business or scenario task;
- unsupported marketing clue;
- blocked by missing evidence.

Only atomic capabilities proceed to target-entry generation.

### 6. Apply the atomicity gate

Require one primary observable effect, one coherent trigger contract, independent selection and acceptance, replaceability, and cross-scenario reuse.

Split independently useful effects. Stop splitting before internal implementation fragments lose independent matching or acceptance value.

Reject names that require “and” to express multiple primary effects. Use `动词_对象_必要限定` naming.

### 7. Build the capability contract

Create an entry conforming to the entry schema and Wiki template.

Keep:

- documentary facts;
- engineering interpretation;
- assumptions and unknowns;
- repeatable measurements

as separate fields. An exposed command interface proves only that the command surface exists; it does not prove that the physical target is reached.

### 8. Reconcile with the target Wiki

Search by stable ID, semantic key, aliases, normalized model, scope, and interface. Classify the intended action using the synchronization policy.

Preserve stable IDs when meaning is unchanged. Create a new implementation instance or successor when model scope, primary effect, or interface meaning changes materially.

### 9. Produce and validate a draft changeset

Generate only the smallest evidence-backed changes.

Resolve the following script paths relative to this `SKILL.md`, regardless of the current working directory.

Run:

```bash
python scripts/validate_capability_entry.py <entry.json>
python scripts/validate_wiki_changeset.py <changeset.json>
```

Do not write entries while validation errors remain. Report schema gaps instead of dropping fields.

### 10. Write and verify drafts

Write only approved draft operations supported by the available Wiki tool. After writing:

1. read the target entry back;
2. compare its stable ID, revision, content hash, evidence links, and lifecycle state;
3. record success, conflict, or partial failure in the audit log.

Never implement a `delete-proposal` as a hard deletion without separate explicit authority.

### 11. Report coverage

Return:

- source snapshot and target catalog revision;
- created and updated drafts;
- implementation instances;
- split and merge proposals;
- conflicts and blocked candidates;
- deprecation and deletion proposals;
- processed, excluded, blocked, unchanged, and unprocessed sources;
- atomic candidates rejected as non-capabilities;
- validation failures and schema gaps;
- resulting draft catalog revision.

## Evidence and lifecycle gates

- E1 marketing narrative is a discovery clue and cannot create a formal capability entry alone.
- E2 interface/manual evidence can create `draft`.
- E3 authorized engineering review can promote to `reviewed`.
- E4 repeatable simulation or bench evidence can support `verified` only within measured boundaries.
- E5 repeatable field evidence can support `verified` within the stated operational boundary.

Keep evidence level, extraction confidence, and measured execution success separate.

## Completion conditions

Complete a run only when:

- every in-scope source has a coverage status;
- every written entry passes the atomicity and machine-contract gates;
- every factual claim has a locator or is explicitly unknown;
- every operation is idempotent against the recorded base revision;
- every target write has been read back and verified;
- the coverage report contains no hidden unprocessed source.
