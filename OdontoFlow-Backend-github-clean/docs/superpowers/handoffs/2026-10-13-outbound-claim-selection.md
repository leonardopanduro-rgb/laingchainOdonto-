# Handoff — selected outbound dispatch

## Problem and result

The Python/LangChain receptionist owns a subset of an organization's outbound
messages. Claiming the entire organization could consume another adapter's
jobs. `POST /internal/outbound/claim` now accepts optional `outbound_ids` (1–50
strict positive integer IDs). Both claims and exhausted-lease cleanup respect
that selection and the authenticated tenant. The omitted-field contract is
unchanged. There are no migrations or scheduling changes.

## Evidence

- Pre-flight: clean backend worktree; full suite **494 passed** in 670.62s.
- TDD: selection tests before implementation **2 failed, 4 passed**, both
  failures because the valid new field was rejected by the old schema.
- Focused messaging + initial selection cases: **19 passed** in 63.17s.
- Final selection suite including strict booleans/strings and tenant isolation:
  **9 passed** in 14.73s.
- Full suite after implementation: **503 passed**, 21 existing deprecation
  warnings, in 704.45s. All database tests used local PostgreSQL
  `odontoflow_test` on port 5434, with no concurrent test process on that database.
- Independent reviewer `/root/reception_review`: no actionable finding in
  backend diff; authorization, tenant scope, SKIP LOCKED and retry limits preserved.
- The separate reception service also exercised real API/PostgreSQL in an
  opt-in profile→slots→book→reschedule→cancel→handoff→resume scenario. External
  Gemini and Chatwoot calls were simulated; see its own validation report.

## Allowed surfaces and rollout

Changed messaging schemas/router/service, matching tests, generated OpenAPI,
CHANGELOG and task spec/handoff only. No changes to protected services,
existing migrations or MediStock. No production deployment or webhook mutation.

Deploy this backend version before enabling the LangChain dispatcher: older
versions reject the selection field. The service fails instead of silently
falling back to an unfiltered organization-wide claim.
