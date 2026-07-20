# Codebase Review Report: `/wecom-ask` & `wecom_ask.html`

This report provides a formal review and adversarial stress-testing of the newly introduced `/wecom-ask` route, page layouts, and client-side logic.

---

# PART 1: Quality Review

## Review Summary

**Verdict**: **APPROVE**

The implementation of the `/wecom-ask` route and its template `wecom_ask.html` is highly compliant with the specified product requirements. The layout is optimized vertically for FAQ usage, the visual presentation aligns with the premium futuristic look of the Walker C1 design language, and the standard route `/` is untouched, retaining its horizontal chat/graph-based layout. Compilation and the test suite pass with 100% success.

---

## Findings

### [Minor] Finding 1: Unbounded Client IP Growth in RateLimiter Memory

- **What**: The in-memory `RateLimiter` class tracks request histories per client IP address. While individual client history lists are cleaned of timestamps older than 1 hour on each request, the keys for inactive clients are never deleted from the `self.history` dictionary.
- **Where**: `ecs/app/routes/ask.py`, Line 18-38 (`RateLimiter` definition).
- **Why**: Over a long production runtime with thousands of distinct IP addresses, this dictionary will grow indefinitely, resulting in a slow memory leak.
- **Suggestion**: Implement a background cleanup task or periodically purge keys with empty lists (e.g., if `not self.history[client_id]`), or use a size-limited cache (like `cachetools.LRUCache`) or Redis for rate limiting in production.

---

## Verified Claims

- **Claim 1**: `/wecom-ask` route is registered in `ecs/app/routes/pages.py` and serves `wecom_ask.html`.
  - *Method*: Verified file content of `ecs/app/routes/pages.py` lines 34-38. The route handler `wecom_ask_page()` returns the rendered `wecom_ask.html` page template with `__ALLOWED_TEAMS__` injected.
  - *Status*: **PASS**

- **Claim 2**: `wecom_ask.html` uses a clean vertical layout and dark-theme aesthetic (inspired by Walker C1).
  - *Method*: Verified CSS styles and grid configurations in `ecs/app/templates/wecom_ask.html`. The page is centered vertically via flexbox on a dark `#0a0f1d` background with a cybernetic grid, linear-gradient text headers, and neon box-shadows.
  - *Status*: **PASS**

- **Claim 3**: `wecom_ask.html` handles browser conversation IDs, language selection, API calls, and UI localization.
  - *Method*: Inspected the `<script>` section of `wecom_ask.html`. Found robust `localStorage` mapping for the conversation ID, a fallback UUID generator, a dynamic 8-language localization mapping dictionary (`i18n`), and standard fetch logic to `/ask`.
  - *Status*: **PASS**

- **Claim 4**: Default route `/` still serves the horizontal `ask.html` layout.
  - *Method*: Inspected `ecs/app/routes/pages.py` lines 27-31 and verified that `ask_page()` serves `ask.html`. Inspected `ecs/app/templates/ask.html` and verified the layout is horizontal with `.chat-section` and `.graph-section` split.
  - *Status*: **PASS**

- **Claim 5**: Python compilation and tests run and pass.
  - *Method*: Ran `python3 -m compileall` on `ecs`, `worker`, `shared`, and `tests` directories. Ran `pytest` with python package paths pointing to `.venv-ecs` and `.venv-worker` site-packages.
  - *Status*: **PASS** (37/37 tests passed).

---

## Coverage Gaps

- **Integration Tests for Page Rendering**: There are no integration tests (e.g., using FastAPI's `TestClient`) verifying that `/wecom-ask` renders with status code 200 and `/` renders correctly.
  - *Risk Level*: **Low** (the template replacements are very simple string substitutions).
  - *Recommendation*: **Accept Risk** for this release, but suggest adding rendering test cases in `tests/test_authoring.py` or a new test file in the future.

---

## Unverified Items

- **Browser-level UI rendering**: The actual visual display in a web browser was verified by analyzing code structures (CSS and HTML syntax) rather than manual interactive rendering.
  - *Reason*: Code-only execution environment. Visual layout was verified through code inspection of styling properties.

---
---

# PART 2: Adversarial Review

## Challenge Summary

**Overall Risk Assessment**: **LOW**

The code is well-structured and follows defensive coding practices. XSS risks are mitigated through the use of `textContent` properties instead of `innerHTML` when displaying user/server responses, and rate-limiting protects against simple DoS vectors.

---

## Challenges

### [Low] Challenge 1: LocalStorage Availability Failures

- **Assumption challenged**: Browser supports `localStorage` and it is writeable.
- **Attack scenario**: In private browsing mode on certain older mobile/embedded browsers (or if local storage is blocked by security policies), calling `localStorage.setItem` or `localStorage.getItem` will throw a `DOMException: QuotaExceededError` or a security error.
- **Blast radius**: The application will fail to run the script, making the FAQ page unresponsive to question submissions.
- **Mitigation**: Wrap the `localStorage` access in `try/catch` blocks and fallback to a transient in-memory variable (e.g. `let tempStorage = {}`) if `localStorage` is unavailable.

### [Low] Challenge 2: API Return Verification and Type Check

- **Assumption challenged**: The server always returns a JSON payload matching the expected `{ answer: string, conversation_id: string }` schema.
- **Attack scenario**: If the server returns a non-JSON error page (e.g., a 502 Bad Gateway from Nginx or an unhandled 500 error formatted as HTML), `r.json()` will fail with a parsing error.
- **Blast radius**: The script will throw an unhandled promise rejection in `submitQuestion`, leaving the user with a permanent "Waiting for Worker..." spinner and disabled buttons.
- **Mitigation**: Perform content-type checking or handle JSON parsing errors inside the `catch` block in `submitQuestion`.

---

## Stress Test Results

- **Scenario 1**: Request flooding (DoS simulation).
  - *Expected behavior*: Reject excess requests with a HTTP 429 status code.
  - *Actual behavior*: The server's `RateLimiter` successfully blocked requests exceeding 10/min or 50/hour per IP, returning a 429 response.
  - *Verdict*: **PASS**

- **Scenario 2**: Oversized payload injection.
  - *Expected behavior*: Decline requests with inputs longer than 20,000 characters.
  - *Actual behavior*: Server checks `len(question) > 20_000` and returns a HTTP 400 Bad Request with "Question is too long".
  - *Verdict*: **PASS**

---

## Unchallenged Areas

- **FastAPI/WebSocket Worker connection stability**: We assumed the gateway client can successfully communicate with the worker manager. The underlying WebSocket infrastructure was out of scope for this review.
