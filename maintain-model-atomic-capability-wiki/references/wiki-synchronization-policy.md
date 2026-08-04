# Wiki synchronization policy

## Source and target

- Keep source files, source Wiki entries, and original documents read-only.
- Write to a separate target capability Wiki section.
- Use draft changesets and optimistic concurrency against a recorded base revision.
- Read every changed entry back after writing.

## Operation classes

| Action | Use when | Default execution |
|---|---|---|
| `create` | No semantically equivalent entry exists | Write draft |
| `update` | Meaning and stable identity remain unchanged | Write draft update |
| `implementation-instance` | Same semantic capability has a distinct model/version/interface realization | Write linked draft |
| `split` | One existing entry contains independently useful effects | Propose successor entries |
| `merge-proposal` | Existing entries are true semantic duplicates | Propose canonical entry and redirects |
| `conflict` | Model, version, unit, interface, range, or evidence disagrees | Preserve both claims; block merge |
| `deprecate` | Authoritative evidence explicitly removes or replaces the capability | Draft lifecycle change |
| `source-removed` | A prior supporting statement disappeared from a new source revision | Flag for review; do not infer removal |
| `delete-proposal` | Agent-created duplicate or erroneous draft should be hard-deleted | Never execute without separate authority |
| `skip` | No semantic content changed | No write |
| `blocked` | Model scope, evidence, permissions, or contract is insufficient | No write |

## Identity rules

Preserve `capability_id` when wording, evidence, metrics, unknowns, or non-semantic metadata changes.

Create a successor or implementation instance when the primary effect, acted-on object, model boundary, or interface meaning changes materially.

Use the tuple below for duplicate and implementation analysis:

```text
semantic_key
+ normalized model_id
+ body-part/environment scope
+ interface type and semantic endpoint
```

Do not use title similarity alone.

## Deletion and source removal

The absence of a claim in a new document is not evidence that the product lost the capability.

Map removal as follows:

- explicit authoritative removal or replacement → `deprecate`;
- source statement disappeared → `source-removed`;
- agent-created duplicate draft → `delete-proposal`;
- published historical entry → preserve history and use lifecycle metadata.

## Incremental synchronization

For every changed source:

1. identify changed evidence claims;
2. find target entries referencing those claims;
3. recompute only affected entries and their dependencies;
4. compare against the recorded base revision;
5. emit the smallest changeset;
6. block writes on concurrent target edits;
7. read back and verify successful writes.

## Idempotency

A rerun with the same model, source snapshot, target base revision, and rules must not create duplicate entries or duplicate evidence.

Use stable source IDs, evidence IDs, capability IDs, and changeset IDs. Sort arrays only when order has no semantic meaning.

## Coverage report

Report at least:

- total in-scope sources;
- processed, unchanged, excluded, blocked, and unprocessed sources;
- extracted claims;
- atomic entries;
- non-capability candidates by class;
- every operation count;
- validation failures;
- schema gaps;
- target write and read-back results.

Completeness is false when `unprocessed_sources > 0` or `blocked_sources > 0`.
