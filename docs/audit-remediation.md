# Audit remediation — single-owner access and private approval links

Branch: `codex/audit-remediation`, based on production `1bd330e`. Draft PR #91.

## Access model

Jasmine is the only internal user. Her existing signed, remembered owner session
protects business data, invoice generation/saves, email, payment-link/refund actions,
financial reports, search/agent/distance actions, receipt rendering and counters.
No new owner password or per-action prompt was added.

Owner pages retain the existing Supabase query builders through a same-origin
server bridge. It rejects unknown tables/RPCs, related-table joins, cross-origin
writes and unverified/revoked sessions. Server credentials never enter the browser.
Login keeps existing DOM nodes inert instead of deleting them during async loads.

Clients have no accounts: email/PDF → Approve → electronic signature → Stripe.
Approval links carry a signed capability restricted to one invoice, one signer
(client or cosigner), deployment origin and environment. Tokens last one year.
No password, client account or extra verification step was added. Changing an
invoice number, role, token or issuing environment invalidates access. Only client
proposal fields are returned; job notes, costs, wages, credentials, signature images
and signature IPs stay private. Voided proposals cannot be signed. Contract-link
refresh requires the owner session.

Approval links are minted before email preview and refreshed by the email server
in both the plain-text and HTML paths. Pending-countersigner emails also use a
private link. Existing Stripe URLs, payment calculations and PDF/contract content
are unchanged. Private pages send no-referrer/no-store directives.

Crew confirmation links are separately scoped to their invoice and role. They can
only read assignment details and record confirmed/declined availability. The server
derives the notification recipient/content; a crew link cannot send arbitrary email
or alter financial fields. Replies use optimistic concurrency protection. Crew links
are prefetched when the editor opens to retain synchronous iPhone SMS behavior;
if a user taps before preparation completes, the app asks them to tap Text again
once the private link is ready rather than sending an insecure link.

## Hours tools

Read-only inspection found existing hours records and no open shift. Those tools
were retained. Hours and approval pages now require Jasmine's owner login; selecting
a team member chooses whose hours she manages. Public employee password-hash
loading and browser password comparison were removed. Stored records, rates,
hours, approvals and payroll calculations were not changed. Existing owner employee
management remains available. No employee account system was introduced.

## Webhooks and scheduled sends

Stripe event ingestion requires a configured secret and valid signature. Missing
verification fails closed. Vercel scheduled sends require the cron secret. Existing
manual report/digest buttons use owner-authenticated POST requests rather than
public GETs. General email dispatch is owner-only. No clients were contacted during
testing; no real payments, refunds, notifications or signatures were submitted.

## Database/storage cutover — not applied

`sql/owner_access_cutover.sql` enables RLS and removes anonymous/authenticated/PUBLIC
table, view, sequence and function access, retaining service-role access. It also
removes legacy anonymous signed-contract read/upload policies. The bucket is already
private; existing signed download URLs and server uploads are retained.

This SQL was tested against an isolated PostgreSQL database with synthetic records.
It does not insert, update or delete business rows. It is deliberately not applied
to production in this preview build. Until the application and SQL are released,
production still has the audited public-access exposure.

## Existing approval links

**Owner release hold:** do not merge anything or change production access until
the owner confirms the already-sent invoice is **approved AND paid**. Keep this
build on the preview branch. The client can approve, sign and pay through the
unchanged current production flow. Do not auto-resend or replace their link.
Finishing preview work does not authorize a merge, production deployment or database
cutover. Obtain explicit owner merge approval after that condition is satisfied.

Legacy URLs with only `?inv=` cannot remain publicly usable: they contain no private
proof of access. Once this build is released they show a clear request to obtain a
new private link. Jasmine can still view old invoices from the Archive while signed
in and can prepare replacement invitations through Board's existing signature-invite
preview. Resending is an explicit owner action; no bulk or automatic resend occurs.
Existing emailed PDF attachments, signed contract documents and Stripe payment links
are untouched. Review pending outstanding invitations before production cutover.

## Configuration and release sequence

1. Verify owner-session/revocation-table configuration, service credential,
   Stripe webhook secret and cron secret are available in the target environment.
   `SWJ_PRIVATE_LINK_SECRET` is optional; if absent, a domain-separated key is derived
   from the server-only Supabase credential. Rotating that credential invalidates
   old capabilities unless a stable dedicated private-link secret was configured.
   Changing Jasmine's password does not invalidate client capabilities.
2. Deploy the matching application to preview and complete real browser smoke tests
   with isolated fixtures. Do not send real emails/payments or sign client documents.
3. Obtain owner approval for the application/cutover and the legacy-link handling.
4. Deploy the application, then apply the reviewed cutover SQL. Do not revoke grants
   before the replacement owner/public server paths are live.
5. Verify unauthenticated table/view/RPC/storage requests fail, owner pages load/save,
   private client links read/sign only their invoice, and signed documents remain
   available. Compare business-record counts and financial fixtures before/after.
   Do not revert to public access if a path fails: repair the authenticated path.

No production merge or SQL permission changes were performed by this build.

## Validation

- 268 Python tests pass with socket connections/DNS disabled. Covers owner boundaries,
  revocation, cross-origin denial, missing/bad Stripe and cron verification, scoped
  client/crew tokens, filtered client responses, private email links, allowed signing
  flow, manual owner sends and denial before database/payment/email actions.
- `tests/owner-cutover.mjs`: actual cutover SQL in isolated PostgreSQL; all 19 business
  tables, card view and RPC denied to public roles; anonymous contract reads/uploads
  denied; service access and every fixture row retained.
- `tests/private-access-dom.mjs`: actual proposal signing code, token forwarding,
  unchanged Stripe URL, no client password, missing-token rejection, crew reply,
  cold-login DOM retention and synchronous SMS-link prefetch.
- Existing invoice/Finance/Board/Proposal DOM, owner-request and Home tests pass.
- A Chromium integration test is provided in `tests/private-access-browser.cjs`,
  but this environment denies Chromium's required socket calls, so it did not run.
  The prior preview smoke attempt was blocked by Vercel access protection. Deployed
  browser testing and live cutover verification remain required before release.

Remaining audit phases: tax safeguards and provenance, ZIP parsing, Unicode PDF
filenames, invoice numbering, carry-forward duplication, save-error handling and
Finance/navigation simplification. These are outside this security change.
