# Atomic staging numbers and complete-record loading

Branch: `codex/numbering-pagination`, stacked on PR #92 including its final canonical-PDF correction (`e7fc579`).
Review this change against `codex/tax-audit-fixes`. PR #91 remains separate.

## OWNER MERGE HOLD

Do not merge anything, enable auto-merge, deploy to production, install SQL or modify
production records until Gabriel confirms the outstanding invoice is **approved AND
paid**, then explicitly gives his thumbs-up. Future assistants must remind him of
this hold and request that approval. No replacement client messages are authorized.

## Number allocation

- Both regular and luxury staging generators reserve from the same PostgreSQL
  allocator rather than reading max IDs independently or writing a serverless
  `/tmp` counter. A locked singleton row and unique reservation constraints serialize
  the operation across server instances. Existing numeric staging IDs/revisions
  determine the lower bound; draft, photography and system IDs do not.
- A fresh page has its own random request UUID. Network retries reuse that UUID,
  so a committed reservation whose reply was lost returns the same number. Duplicate
  clicks share one request. Late responses cannot overwrite a loaded record or a
  manually changed number. Initialization and first-save races share pending work.
- Manual numeric IDs and vN revisions use the same allocator and cannot take an
  already-issued/reserved ID. Existing invoices retain their original number and
  canonical editing path. Reserved draft metadata survives saving/reopening.
- Allocation proof is signed server-side; new generator PDF/save/payment paths
  verify it before output. Existing-number mode requires a saved invoice. Existing
  Board balance/extension payments keep their parent-invoice workflow; old editors
  cannot create an unreserved new payment. Pricing and payment amounts are unchanged.
- Opening an editor and abandoning it can leave a numbering gap. Reservations are
  not recycled or renumbered; they remain traceable. This is not gapless numbering.
- Photography retains its separate namespace/numbering. This allocator coordinates
  the site's staging creation paths; out-of-band database writes are outside it.

`sql/invoice_number_reservations.sql` creates two UI-bookkeeping tables and a
SECURITY INVOKER RPC. RLS is enabled, public/anon/authenticated privileges are
revoked, and only service_role can allocate. The endpoint additionally checks the
existing owner session and same-origin POST. Neither server credentials nor keys
are returned to the browser.

**The SQL is prepared and tested, not installed in production.** Until installed
in the preview's isolated database, the reservation endpoint reports unavailable;
it never guesses a fallback invoice number. No existing invoice data was changed.

## Record coverage

A shared reader requests exact counts and ranges with stable unique ordering. It
advances by the number actually received, allowing a server cap smaller than the
requested page size. It rejects missing/changing counts, duplicate or missing IDs,
premature empty pages and failed later pages. Partial data is discarded, not used
as a normal total. The bounded reader rejects more than 1,000 pages and asks the
user to narrow the filter; it does not silently truncate.

Covered reads:
- Home invoice/task lists, week view, pending carry-forward tasks, monthly card
  transactions and hours. Count-only monthly schedule/shopping queries use exact
  server counts. Source failures show an explicit incomplete/unavailable state.
- Board projects and stage-picker candidates. Incomplete lists cannot reach its
  existing automatic stage-transition routine.
- Finance invoices, project picker, employee entry list, allocation-period hours,
  monthly-performance hours and filtered Spending transactions. Costs/results are
  withheld when their source coverage cannot be confirmed.
- Old spending/home/board requests cannot overwrite a newer filter/view load.

Filters, tax/pricing/allocation/payroll formulas, task status rules and job status
transitions are preserved. Complete inputs may reveal records previously omitted
by a row cap; that is not a financial recalculation or stored-record correction.

Pagination isn't a transactionally frozen cross-table snapshot. If a count or
repeated key reveals concurrent changes, the user is asked to refresh. Other
legacy screens (such as Archive/Hours/Shopping) have not been converted by this
focused change; single-record lookups and deliberate recent-item previews remain
bounded intentionally. Very large complete lists may warrant UI virtualization
or server aggregation later; no infinite-capacity claim is made.

## Validation

- 259 Python tests pass with sockets/DNS disabled. New tests cover allocation proof,
  existing-record verification, owner boundary before body/RPC processing, disabling
  legacy counter writes, denial before PDF/payment calls, service-only RPC requests,
  old-editor payment denial and existing extension-parent lookup.
- Actual allocator SQL ran in isolated PostgreSQL (PGlite): 100 queued allocations
  were unique; replay used the same number; occupied/manual/revision checks passed;
  anon/authenticated access was denied; the installer could be rerun without reset;
  all original invoice fixtures were unchanged. This is not a hosted multi-connection
  concurrency/load test. PostgreSQL's row lock supplies cross-connection serialization.
- Two independent DOM editors received different reservations. Tests cover retry
  identity, duplicate-click/init races, manual conflicts, late replies, existing
  records, no fallback and proof transport in both actual generator flows.
- A 1,201-row fixture loaded completely despite a 127-row server cap. Count drift,
  missing counts, duplicate IDs, later-page failure and safety-limit tests pass.
- Actual Spending rendered all 1,201 filtered rows and the matching total; a later
  page failure removed that total. Actual Home/Board handlers withheld partial
  metrics/cards, and Board did not run stage transitions on failed coverage.
- Existing tax/price/Finance/Board/Proposal DOM, Home, task-retry and Finance save
  regressions pass. Changed Python and inline/shared JS parse; diff checks pass.
- No production SQL was applied, no business rows were edited, and no real messages,
  payments, refunds or client signatures were submitted.

## Release prerequisites

1. Respect the owner hold. This draft may be reviewed but not merged yet.
2. Reconcile #91/#92/this branch without dropping owner auth, tax evidence or number
   checks. Combined application/browser checks remain required before release.
3. Install the reviewed allocator SQL into an isolated preview database before
   exercising number reservation there. Verify owner session and server credential.
4. Test two real browser tabs/connections, manual IDs, saved drafts/revisions,
   existing unpaid edits, contributions/deposits and cold/retried requests. Reload
   old editors as part of rollout. Real mobile/browser verification remains pending.
5. Only after owner approval, install SQL before enabling the matching application.
   PR #91's separate data-access cutover must follow its own staged plan. Never
   disable allocation/auth/tax checks as a fallback if setup is missing.

No production deployment, database permission changes or merges were made.
