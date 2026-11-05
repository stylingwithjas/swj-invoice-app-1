# Audit remediation — security foundation

Branch: `codex/audit-remediation`, based on production `1bd330e`.

## Implemented in this first reviewable change

- Owner session and same-origin checks before invoice API reads/writes, payment-link creation/deactivation, refunds, PDF rendering, agent/search actions and invoice-counter access.
- Legacy Plaid actions and Stripe payout snapshots require the owner session.
- Stripe event ingestion rejects a missing signature, a bad signature, or missing webhook-secret configuration. Scheduled reminder/report endpoints reject missing cron-secret configuration.
- Owner data queries use a same-origin, authenticated server bridge. The public browser credential cannot choose a different upstream, schema, arbitrary RPC or server-only table. Relationship joins are rejected to prevent crossing the table allowlist.
- Existing query builders, filters, count headers and JSON responses retained. Employee approval now requires owner login. Price guides use the owner path to load private pricing defaults.
- Login keeps page nodes in place while making underlying content inert, preventing asynchronous loaders from accessing deleted nodes. Owner data requests wait for the session check.

## Validation

253 Python tests pass with socket connection, DNS and connection helpers disabled. New tests cover denied financial requests before body processing/network calls, cross-origin writes, revocation-check use, missing Stripe/cron verification, data allowlist/relationship isolation, body limits, and server-only credentials.

`node tests/owner-data.cjs` checks session gating, request mapping, retained payload/preferences and removal of browser authorization headers. `node tests/home-briefing.cjs` passes. JavaScript syntax checks pass for the changed shared scripts.

## Not yet ready for production cutover

This is a security foundation, not a completed remediation of all audit findings.

1. Employee login/time-entry queries still need a scoped server session; password hashes must stop being returned to employee browsers.
2. Client proposal/signature and crew confirmation access need scoped, unguessable capabilities. Existing predictable-number links need a deliberate compatibility/reissue decision; no messages should be sent automatically.
3. General email dispatch remains a known unprotected endpoint until employee/crew notifications use narrowly scoped server actions. Do not claim all APIs are protected.
4. Direct anonymous database grants/RLS are unchanged. Only revoke them after all owner, employee and client paths pass end-to-end tests. This change alone does **not** close public database exposure.
5. Verify Vercel preview contains the configured owner session, revocation table, Stripe webhook secret and cron secret. Missing required secrets now fail closed.
6. Browser integration and deployed preview testing are still required; no production data, permissions, stored financial figures or business calculations changed.

Next audit phases: verified-address tax safeguards and provenance, ZIP parsing, Unicode PDF filenames, invoice numbering, duplicate carry-forward tasks, save error handling, and Finance/navigation simplification.
