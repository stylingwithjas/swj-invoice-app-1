# Rental/Service Split & Price-Led Tax Method

**Status: internal business method note, not CPA-certified.** Built per Gabe's Oct 25, 2026
decision to proceed without a CPA review; WA DOR taxpayer-assistance validation remains an
optional later step, not a gate for shipping this. Applies to invoices created on or after
this method shipped — existing invoices are never restated or re-billed.

## Why a split at all

Washington sales tax applies to the **rental** of tangible personal property (the furniture,
decor, and accessories physically placed in the home). It does not apply to pure **service**
labor (design consultation, staging labor, photography) the same way. Taxing the full invoice
amount — as this app did before this method shipped — overstates what's actually owed and
remits money to the state that the business was never required to collect in the first place.

## The 50/50 base-package split

The default rental/service split for the base staging package is **50% rental / 50%
service**, confirmed by Gabe on Oct 25, 2026. It is derived from the app's own extension
pricing, not picked arbitrarily:

- A 30-day extension is priced at the invoice total ÷ 3, and an extension is **100% rental**
  (continued use of the same furniture, no new design/install labor).
- 30 days is two-thirds of the base package's 45-day rental term.
- So the 45-day rental value implied by the extension rate is `(total ÷ 3) × (45/30) = total ÷ 2`
  — half the package price. The other half is the design/install service.

This ratio is a constant (`BASE_RENTAL_SHARE`, currently 0.5) applied to `base_price` only.
It is stored per-invoice at creation (`rental_share_base`) so a future change to the global
default never silently reclassifies an already-issued invoice.

## Add-on line classification

Each add-on line (room category, custom line, extra bedroom) is tagged `rental` or `service`
at creation time, using the same heuristic the tax report has used for Retailing vs. Service
& Other since before this rebuild: a line is `service` if its description contains "photo",
"consult", or "design fee" (case-insensitive); everything else — every room/category addon,
which is physical furniture placed in the home — defaults to `rental`. Extension lines are
always `extension`, treated as 100% rental for tax purposes.

## Price-led tax computation

The **total the client is charged does not change** because of this method — Jasmine still
arrives at the same number she always would (base + add-ons, with tax added at the full rate,
exactly as before). That total is the "chosen price." What changes is which part of that
already-fixed total counts as taxable rental revenue vs. non-taxed service revenue vs. sales
tax, computed by **backing the tax out of the total** instead of adding it on top of the full
subtotal:

```
rental_share  = (base_price × rental_share_base + Σ rental-tagged addon prices)
              ÷ (base_price + Σ all addon prices)                     // blended, 0..1

pretax        = total ÷ (1 + location_rate × rental_share)            // = revenue
rental_portion = pretax × rental_share
tax           = location_rate × rental_portion                       // = total − pretax
```

`location_rate` is the destination-based WA combined rate for the property's ZIP (the same
`/api/taxrate` lookup already used today). The client-facing invoice still shows one package
line, one add-ons section, **one separately stated sales tax line, and the total** — the same
shape as before. The displayed "Subtotal" is `pretax` (revenue), not the raw base+add-ons sum;
because less of the fixed total is now counted as tax, `pretax` is slightly *higher* than the
raw base+add-ons sum used to be under the old method, and the displayed tax is correspondingly
lower. This is the entire point: money that used to be over-remitted as sales tax is now
correctly recognized as revenue instead.

## Extensions

An extension's fee does not change — it is still `total ÷ 3` per 30-day period, per the
original signed contract. Applying the same price-led formula with `rental_share = 1.0`
(100% rental) backs the correct tax out of that already-agreed fee:

```
pretax = extension_fee ÷ (1 + location_rate × 1.0)
tax    = extension_fee − pretax
```

Verified against invoice #2020129's extension ($3,800 ÷ 3 = $1,266.67 pre-tax at WA state's
8.20% combined rate in that jurisdiction ≈ $1,370.53 stated rate) — consistent with the
contract's already-agreed number, not a new fee.

## B&O classification (tax report)

For the monthly tax report (not the client invoice), each line's `pretax` revenue is bucketed
by its tagged type:

- **Retailing** (rental lines, including extensions): B&O rate 0.471%.
- **Service & Other** (service-tagged lines): B&O rate 1.5% at this business's current
  revenue size (tiers above $1M/$5M noted but not yet applicable).

## What this method does NOT do

- It does not change any already-issued, paid, or signed invoice.
- It does not change base/add-on catalog pricing.
- It is not a certified B&O classification — treat the report's category tags as a
  best-effort allocation to be reviewed before filing, same disclaimer as before.
