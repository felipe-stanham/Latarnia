# TESTS.md — Regression Test Registry

Curated critical-path tests only. Each entry describes **what to verify**, the **concrete input(s)**, and the **expected output**. The `tester` agent generates a verification script from the spec, runs it, and reports pass/fail.

**Format:**

```
- **test_name:** [What to do, with concrete input] → [Expected result, with concrete output]
```

A vague verb is not enough. Always include real sample values — actual JSON, an actual URL with parameters, an actual file path, a precise status code or output string. If the description could be satisfied by two different implementations, it's too vague.

---

## How Caching Works

The `tester` agent caches generated scripts under `tests/cache/<test_name>.<ext>` alongside `tests/cache/<test_name>.hash` (SHA-256 of the test entry below). On each run:

- If the cached hash matches the current entry → reuse the cached script (no regeneration, no token cost).
- If the hash differs (the entry was edited) or no cache exists → regenerate the script and update the hash.
- If a cached script fails on unchanged spec → treat as a real regression first; only regenerate if you can show the script itself is broken (e.g., dependency renamed).

`tests/cache/` is committed to git — it's the project's accumulated test logic.

---

## Example Entries

```
- **api_health_check:** Send `GET /health` with no headers → HTTP 200 with body `{"status": "healthy", "version": "<any-string>"}`.
- **auth_rejects_expired_token:** Send `GET /api/resource` with header `Authorization: Bearer <token-issued-2020-01-01>` → HTTP 401 with body `{"error": "token_expired"}`.
- **csv_export_includes_headers:** Trigger CSV export for dataset `demo-2024` → First row of output CSV equals `id,name,created_at,status` (exact case and order).
- **price_calculator_applies_discount:** Call `compute_price(base=100.00, discount_code="SAVE10")` → Returns `90.00` (float, two decimal places).
```

Notice each example names exact inputs and exact expected outputs. The `tester` agent should never have to guess what "valid input" or "expected response" means.

---

## Tests

<!-- Add regression tests below. Only include tests that cover critical paths.
     When a scope is marked [DONE], promote its qualifying acceptance criteria here.
     Keep this list small and meaningful — do not dump every scope test in. -->
