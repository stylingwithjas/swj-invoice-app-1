-- Plaid integration (Amex spending snapshot, Nov 2026). Run once in the Supabase SQL editor
-- before the Spending tab can show any real data — nothing in the app will have written to
-- these tables until this has been run AND PLAID_CLIENT_ID/PLAID_SECRET are set in Vercel.
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
