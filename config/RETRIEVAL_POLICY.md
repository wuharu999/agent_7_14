# Retrieval policy

Interpret the customer's current request before searching product information.
Determine the current entity type and whether the selected topic should constrain
that entity. Conversation history may resolve explicit references, but it must not
inject an old robot into a new tool, solution, API, SDK, workflow, or general request.

- `in_scope`: the explicit robot matches the selected robot scope.
- `related_scope`: the object is a related tool, application, workflow, SDK, API,
  hardware item, or solution. Continue and answer that object itself.
- `cross_scope`: the customer intentionally compares or connects multiple products.
  Continue, search each product separately, and keep their evidence distinct.
- `out_of_scope`: a strict robot scope is selected and the current request explicitly
  asks about a different robot. Application code may stop before retrieval.
- `ambiguous`: the entity or alias cannot be resolved safely. Continue with neutral
  searches and do not invent a canonical product.

For high-level tasks, prefer a complete supported solution or workflow. For direct
tool, application, SDK, API, or hardware questions, retrieve and answer that object
itself. Similar names, family membership, related links, and shared interfaces do not
prove compatibility. Never transfer a capability from one robot to another without
direct evidence.

One additional corrected local search is allowed when the planner introduced stale
context or selected evidence is inconsistent. Do not create an unbounded loop.
