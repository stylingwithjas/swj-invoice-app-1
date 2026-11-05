# Tax safeguards and confirmed audit bug fixes

Branch: `codex/tax-audit-fixes`, independently based on production `1bd330e`.
Security PR #91 remains a separate draft. This branch does not activate its private
client links or database restrictions.

## OWNER MERGE HOLD

Do not merge, enable auto-merge, deploy to production or modify production data.
Gabriel must first confirm the already-sent invoice is **approved AND paid**, then
provide his explicit thumbs-up to merge. Future assistants should remind him of
that hold and request approval; completion of tests or invoice payment is not
itself permission. The existing client approval/signature/payment flow stays live.

## Tax safeguards

- Regular and luxury staging generators send street, city, ZIP and invoice date to
  the official Washington DOR address-rate lookup. DOR XML provides the matched
  street/number, jurisdiction and period, alongside the rate.
- Correct result-code handling: 0 is an address match; 2/4 are updated/corrected
  address matches requiring Jasmine to confirm the displayed property. ZIP-only
  1/3/5 results do not permit invoice output. See the official definitions:
  https://dor.wa.gov/wa-sales-tax-rate-lookup-url-interface
- Every address/ZIP/city/date edit invalidates the current evidence immediately,
  including partial/cleared ZIPs. Superseded responses cannot change the rate.
  Failed or pending lookups cannot authorize the old displayed rate. No default
  10.25% is presented as a verified property rate.
- Signed, 24-hour evidence stores the requested/matched address, DOR result,
  location code, rate, period and check time on future invoice payloads. Output
  checks compare that evidence against the current invoice and reject tampering,
  stale evidence, changed rates/addresses and a different quarter/date.
- Backend PDF/generator-save/new-generator-payment paths also reject invalid
  evidence. Drafts remain saveable while verification is incomplete.
- A manually overridden tax amount differing from the existing formula's taxable
  base × verified rate requires an explicit review checkbox and explanation. The
  system does not silently recalculate or prohibit a deliberately documented
  exception. This prevents silent acceptance, not every possible owner error.
- Sending a generated PDF requires its verified snapshot to match the form; an
  edited invoice must be regenerated before sending the attachment.
- No room/package prices, rental/service split formulas, discounts, tax formulas,
  payroll calculations or historical invoice values were changed. Stored paid
  invoices retain their canonical rendering path and do not receive current rates.

### Scope and limits

The DOR endpoint supplies its current period. A new generator request for another
quarter is blocked rather than applying today's rate to a historical/future date.
A separate historical-rate verification path is not implemented. This restriction
is intentional and must be reviewed before release if backdated generator output
is required; stored paid invoice rendering remains available.

The photography generator retains its separate pricing and tax behavior. These
checks validate property rate evidence, not legal taxability, exemption eligibility
or the business's rental/service classification. A documented manual exception can
still produce a different tax amount; no blanket claim of tax compliance is made.

Extensions and existing Board balance/payment workflows retain their stored-rate
behavior and are not re-priced. This build does not restate older records or
invalidate existing client links. Business authentication and signed client links
are handled by the separate security PR #91.

## Confirmed bug fixes

| Area | Result |
| --- | --- |
| Legacy tax location | Uses structured city/ZIP regardless of pricing mode; address fallback recognizes terminal postal ZIPs rather than a five-digit street/unit number. Missing locations remain flagged. |
| PDF downloads | ASCII fallback plus UTF-8 filename header supports accented/non-Latin client names and strips header-control characters. |
| Invoice numbering | Only numeric staging IDs and vN revisions influence the next suggestion. Draft/system/photography IDs cannot inflate it. |
| Task carry-forward | Source task/date gets a deterministic UUID. Ignore-duplicate upserts make retries/concurrent refreshes idempotent and do not overwrite an already completed carried task. Original tasks remain. |
| Finance settings | A database error preserves the previous in-memory configuration, shows the error and never reports Saved. |
| Navigation | Adds the existing photography destination to Home/Board More menus. |
| Unknown routes | Removes Home's catch-all rewrite so invalid links receive a real 404. |

Invoice numbering is still a suggestion, not an atomic multi-device reservation.
Existing duplicate tasks are not automatically deleted. Date-sensitive statutory
classification and every external integration have not been independently audited
by this change. Future pagination work and broader Finance/usability improvements
remain outside this focused bug-fix PR. The login DOM race is fixed in PR #91.

## Validation

- 251 Python tests pass with socket/DNS connections disabled. New tests cover DOR
  code distinctions, confirmation, proof mutation/expiry/address/date/rate mismatch,
  quarter mismatch, explicit manual tax review, denial before PDF/save/Stripe calls,
  historical paid rendering, Unicode headers and legacy address parsing.
- Actual regular AND luxury generator DOM flows: unverified output denied before
  any payment/PDF request; verified evidence reaches mocked Stripe/PDF requests;
  $2,650 pretax / $1,325 rental at 10.55% remains $139.79 tax / $2,789.79 total.
- Dedicated tax DOM tests cover pending/failed/stale/ZIP-only results, city/date
  forwarding, corrected-address confirmation, manual review invalidation and cached
  PDF snapshot matching.
- Invoice-number/task-carry retry/completion tests and Finance failed/successful
  save tests pass. Existing invoice/Finance/Board/Proposal DOM and Home tests pass.
  Pricing-only legacy fixtures explicitly assume verified tax; dedicated tax suites
  exercise the actual guard.
- All eight public-address live DOR checks match an independent direct text-format
  DOR response. Results and period are recorded in `docs/tax-rate-qa.json`; no
  client addresses, stored invoices or payment actions were used.
- Python and changed inline/shared JavaScript syntax checks and diff checks pass.
- Real deployed browser/mobile verification is still required. This environment's
  Chromium runner is restricted, and the previous preview request was blocked by
  Vercel protection. No deployed end-to-end or real email/signature/payment test is
  claimed. Production data, permissions and deployment are unchanged.

## Release review

1. Respect the owner hold above. Obtain approval only after invoice approval AND
   payment have been confirmed, not merely after this draft's tests pass.
2. Verify the preview's server-only `SUPABASE_KEY` is configured; it signs tax
   evidence and must never enter the browser. Confirm timezone/date handling and
   required address-confirmation/manual-review controls on actual mobile screens.
3. Test retry/correction, draft reopen, contributions/deposit, rate-card and PDF/send
   flows with isolated fixtures. No real client communications or charges.
4. If this and #91 are both approved, reconcile their shared API/HTML changes while
   retaining BOTH authentication and tax guards; rerun their combined tests before
   release. Neither PR implicitly authorizes the other or its database cutover.
