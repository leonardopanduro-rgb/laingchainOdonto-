# Outbound claim selection for the LangChain reception adapter

User scope: finish reception in Python/LangChain without n8n. The adapter owns
only its generated outbound IDs; claiming an entire organization's queue would
consume jobs that belong to another adapter.

Add an optional `outbound_ids` list (1–50 positive integers) to the claim request.
When present, both due claims and exhausted-lease cleanup must stay within that
selection AND the authenticated organization. Unknown IDs yield no rows. An
empty/invalid list must never broaden the query. Omitting the field preserves
the current API for existing dispatchers. Keep authorization, leases, retries,
SKIP LOCKED, atomic auditing and the test-provider exclusion unchanged.

Allowed surfaces: messaging schemas/router/service, matching integration tests,
generated API docs, CHANGELOG and this task's spec/handoff. No migrations or
changes to scheduling, tenancy, authentication, error contracts or MediStock.
