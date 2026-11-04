# Business cash and checking reconciliation

Preview-only application change. Do not merge until owner review.

## Source rules

- Receipts: existing paid split events, legacy recorded paid invoices, and paid extensions, by received date. No signature gate. Test records and existing non-business/system namespaces excluded. Legacy fallback amounts are disclosed for reconciliation.
- Checking: bank cash and settlement evidence only. Stripe deposits, Amex repayments and Venmo wallet funding never create another sale or expense.
- Costs: existing posted positive Amex purchases, explicit completed mover payments, reviewed bank fees, and actual Stripe balance-transaction fees. Unknown card credits and customer refunds are flagged for reconciliation, not automatically restated into historical taxes.
- Stripe fees use `fee`, plus standalone Stripe fee debits. Charge minus payout is never used as a fee estimate.
- Cash after commitments: lower of bank current/available balance minus current card balances once, approved unpaid mover hours not covered by actual payments, and owner-confirmed remaining tax reserve/cushion. Expected Stripe payouts are excluded. Missing or stale sources and unmatched checking movements block this estimate.
- Monthly margin excludes allocated sales tax, remains provisional, and is unavailable when tax allocation or revenue is unavailable. Lifetime job contribution includes sales tax and excludes shared overhead, so it is explicitly not net profit.
- No invoice/pricing/payment/tax-history writes. Review records do not send payments or update original bank/card transactions.

## Installation and setup

1. Original migration `20261104220104 checking_cash_reconciliation` is already applied. Do not rerun it. `sql/checking_cash.sql` now reflects its installed five-argument review function. New tables have RLS and no anon/authenticated grants. Server service_role access only.
2. **Pending, not applied:** `sql/checking_review_history.sql` adds audited correction history and the six-argument review function. Review and apply this separately only during an approved database deployment. Until then movement confirmations return a setup-required error before accepting evidence. The recovered source had drifted ahead of the installed schema; this follow-up is not a repeat of the original migration.
3. Deploy this branch to a Vercel preview with the existing `SUPABASE_KEY`, admin-session configuration, Plaid credentials and `STRIPE_RECONCILE_KEY` (restricted live read key). Do not expose keys in client code. No additional serverless function is required.
4. Open `/cash.html`, sign in through the existing same-site admin gate. Preview must have its own valid server password configuration; production cookies do not bypass preview authentication.
5. Owner connects business checking through Plaid Link. No login credentials are collected by SWJ. This creates a separate checking Item; existing Amex Items are untouched.
6. Select the account, explicitly confirm review start and remaining reserves, then sync checking. Retry if Plaid history is still preparing. All pages must finish before an atomic cursor commit; otherwise the previous cursor is retained.
7. Review posted bank movements. Amex payments require exact posted negative card credit totals within seven days. Direct mover payments require exact recorded actual-payment totals within seven days. Stripe settlements require a paid USD payout with matching ID and amount. A source reference cannot settle two bank rows.
8. Enter actual mover payments by unique external reference and job, or import the provided five-column CSV after reviewing its preview. A repeated identical reference is idempotent; conflicting contents fail the entire batch. Optional time-entry links require the exact same job and amount and cannot be reused. This is not an automatic Venmo integration.
9. Sync Stripe fees for each reporting month. A failed or stale sync cannot certify full fee coverage. Data-source timestamps refer to fetched data; banks may post movements later.

## Validation and remaining review

Python tests cover October-to-November carryover, split/test/extension receipts, transfer exclusion, actual fees, mover commitment matching, changed/pending/stale evidence and incomplete cursor behavior. Existing suite also runs.

Still requires owner bank consent and verification of real bank/payment matches in preview. No main merge is authorized. Unreviewed refunds and historical mover evidence remain visible limitations, not guessed values. Existing tax reports retain their allocation logic; this is not a filing-policy change.

## Recovery

The branch can be reverted without changing existing ledgers. Leave new tables intact to preserve review evidence. Never drop tokens or payment evidence as a rollback shortcut. A reviewed transaction changed by the bank is marked stale and blocks the cash estimate; use its review control to submit an explicit correction. The previous review is retained in `cash_review_history`, and a version check prevents overwriting a concurrent review. Evidence reservations are replaced atomically.

## Existing security finding

Supabase reports RLS disabled on existing `invoices` and `tasks`. This build does not enable it without compatible policies because the current client reads/writes rely on those tables. Treat permissions remediation as a separate required security task: inventory user/client/server access, introduce authenticated policies or move writes behind the server, test all flows, then enable RLS. See https://supabase.com/docs/guides/database/postgres/row-level-security.

## Recovery verification (November 4, 2026)

- Recovered changes are committed locally, with the merged invoice fixes and five-item navigation included.
- Read-only database inspection confirmed ten installed cash tables with RLS enabled. `cash_review_history` is absent; `cash_confirm_review` has the original five arguments. The follow-up SQL remains **unapplied**.
- 236 Python tests pass. The actual-app invoice/finance/board DOM suite passes. `tests/cash-dom.mjs` verifies carryover, known commitments, navigation and quoted CSV using synthetic data and GET only.
- Chromium visual QA could not start: local socket creation was denied (`Operation not permitted`). No iPhone/mobile visual sign-off is claimed.
- Owner bank consent, approved schema follow-up deployment, actual payment matching and live preview verification remain required before this build can be considered operational. No checking-build production deployment or merge is authorized by the invoice-menu merge approval.

## Option A completion review

The original checking build is merged in PR #88. The owner applied the separate review-history SQL; read-only verification confirmed the history table, six-argument function, RLS and server-only grants. Do not rerun either schema script.

This follow-up closes remaining receipt-driver gaps: Home's profitability views and Finance job results include paid partials/extensions by received month, regardless of signature. Browser payment classification is tested against both server cash-ledger and report classification for legacy, split, extension, invalid-date, zero, test and system-record cases. Planning exclusions cannot remove actual received payments from revenue. Existing planning allocations remain estimates; actual job contributions use the cash ledger's tagged Amex purchases, recorded completed mover payments and actual Stripe fees, with logged hours shown as commitments instead of paid costs. Monthly and lifetime job contributions are presented separately and are not called net profit.

Validation: 238 Python tests; invoice/Finance/Board DOM suite; Home snapshot/profitability checks; offline cash UI checks. No invoice, banking or tax records changed by verification. The deployed cash page is reachable and its read-only snapshot loads. Checking has not been connected yet, so consent, reserve confirmation, actual settlement matches and the revised preview's visual approval remain outstanding. Venmo is not a connected automatic transaction feed: the existing reviewed-payment record/import requires actual payment references and per-job detail, never a checking transfer total. No direct Venmo integration is claimed.

Review branch only for this follow-up; do not merge until owner sign-off. No additional schema changes are needed for this code.


## Clarified mover workflow

Venmo will not sync automatically. The selected business checking debit is the evidence of payment. In Review checking activity, choose **Assign as mover paid**, enter mover and job, and optionally split one debit across multiple mover/job allocations. The allocations must equal the posted bank amount exactly. Pending/changed payments and unconfirmed wallet funding are rejected. Optional approved-hour links must match the job and amount. Payment references are derived from the checking transaction, so retrying the same assignment does not duplicate costs. The bank movement itself is excluded from costs; only its assigned mover-payment records count once.

`sql/checking_bank_mover_assignment.sql` is a **new, unapplied** follow-up that records allocations and movement confirmation in one transaction. If confirmation fails, all allocation writes roll back. Do not rerun earlier SQL. Saved bank-derived mover costs cannot be reclassified independently in the generic review control; changing their allocation requires a coordinated audited correction rather than leaving orphan costs. The original optional historical evidence entry is retained, but newly synced payments do not require separate entry or a Venmo statement import.
