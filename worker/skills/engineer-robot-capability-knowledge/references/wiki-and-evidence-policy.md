# Wiki and evidence policy

## Source and target

- Treat the source Karpathy Wiki and original documents as read-only.
- Write extracted knowledge to a separate target capability Wiki.
- Use draft changesets before publication.
- Make reruns idempotent with stable IDs and semantic keys.
- Preserve source-to-target links and an audit trail.

## Evidence ladder

1. Marketing or unsupported narrative: discovery clue only.
2. Interface or manual statement: documentary evidence of a claimed surface.
3. Reviewed engineering interpretation: evidence and atomicity checked.
4. Repeatable simulation or bench test: measured evidence under stated conditions.
5. Repeatable field validation: verified evidence for the stated operational boundary.

Never promote a claim because it sounds plausible. Keep documentary claims, engineering interpretations, and measurements separate.

## Conflict handling

Do not silently merge records when model, version, unit, mode, endpoint, parameter range, or test condition differs. Produce a conflict record containing both claims and the evidence needed to resolve them.

## Changeset result

Classify every action as create, update, implementation-instance, duplicate, conflict, deprecation-proposal, skip, or blocked. Report created and updated drafts separately from published facts.
