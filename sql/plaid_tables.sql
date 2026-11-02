-- Plaid integration (Amex spending + balance snapshot, Nov 2026). Run once in the Supabase SQL
-- editor before the Spending tab or Home's Amex card can show any real data — nothing in the
-- app will have written to these tables until this has been run AND
-- PLAID_CLIENT_ID/PLAID_SECRET are set in Vercel.
--
-- Two tables, kept separate from the `invoices` table (which this app already queries with
-- select('*') / broad selects from many client-side pages) so a stored Plaid access_token — a
-- standing credential to read the business's full Amex transaction history — can never leak
-- through any of those existing queries.

create table if not exists plaid_items (
  id bigint generated always as identity primary key,
  item_id text not null unique,
  access_token text not null,
  institution_name text,
  -- Credit card balance snapshot (Plaid's Liabilities product), refreshed by
  -- plaid_refresh_balance() in api/webhook.py — on every Home page load and at the end of a
  -- transactions sync. statement_balance is what to pay in full by next_payment_due_date to
  -- avoid interest, same as the "Pay this much by this date" line on an Amex statement.
  current_balance numeric,
  statement_balance numeric,
  minimum_payment numeric,
  next_payment_due_date date,
  last_statement_date date,
  balance_synced_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists plaid_transactions (
  id bigint generated always as identity primary key,
  transaction_id text not null unique,
  item_id text not null references plaid_items(item_id) on delete cascade,
  date date not null,
  merchant_name text,
  name text,
  -- Plaid convention: positive amount = money OUT (a spend), negative = a credit/refund.
  amount numeric not null,
  pending boolean not null default false,
  -- Jasmine's own category (editable in the Spending tab); plaid_category is Plaid's own
  -- suggestion, kept as a read-only reference, never overwritten by her edits.
  category text,
  plaid_category text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists plaid_transactions_date_idx on plaid_transactions (date desc);
create index if not exists plaid_transactions_item_idx on plaid_transactions (item_id);

-- RLS: plaid_items gets NO anon policy at all — once RLS is enabled, the anon key (the one
-- embedded in every page's client-side JS) has zero access to this table, full stop. Only
-- api/webhook.py, using the service-role key (which bypasses RLS), can ever read or write the
-- access_token. plaid_transactions allows the anon key to read and edit the `category` column
-- (so the Spending tab can work without a new backend round-trip for every edit) but never to
-- insert or delete — new transactions only ever arrive via the sync job.
alter table plaid_items enable row level security;
alter table plaid_transactions enable row level security;

create policy "plaid_transactions_anon_select" on plaid_transactions
  for select using (true);
create policy "plaid_transactions_anon_update" on plaid_transactions
  for update using (true) with check (true);

-- Home's Amex balance card (today.html, buildS8) needs to read the balance snapshot above
-- without touching access_token — plaid_items itself has zero anon policies, so instead this
-- view selects only the non-sensitive columns. A view runs with its *owner's* privileges by
-- default (not the querying role's), so it bypasses plaid_items' RLS for exactly these columns
-- while access_token — never selected here — stays exactly as unreachable as before.
create or replace view plaid_account_summary as
  select item_id, institution_name, current_balance, statement_balance, minimum_payment,
         next_payment_due_date, last_statement_date, balance_synced_at
  from plaid_items;

grant select on plaid_account_summary to anon;
