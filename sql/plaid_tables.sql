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
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Phase 1 (Nov 2026): one row per CARD, not per login. Amex Business can expose more than one
-- card under a single Plaid Item/login (the spec's "two business Amex cards"), so the balance
-- snapshot moved here from plaid_items — each card gets its own statement balance, due date,
-- minimum and sync stamp, independent of how many Items it took to connect them.
create table if not exists plaid_accounts (
  id bigint generated always as identity primary key,
  account_id text not null unique,
  item_id text not null references plaid_items(item_id) on delete cascade,
  name text,
  mask text,  -- last 4 digits only, e.g. "1006" — never a full card number
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
  account_id text references plaid_accounts(account_id) on delete set null,
  date date not null,
  merchant_name text,
  name text,
  -- Plaid convention: positive amount = money OUT (a spend), negative = a credit/refund.
  amount numeric not null,
  pending boolean not null default false,
  -- category: auto-assigned by an unambiguous payee/vendor rule at sync time, left null
  -- (uncategorized) when ambiguous — Jasmine/Gabe can always override by hand afterward.
  -- plaid_category is Plaid's own suggestion, kept as a read-only reference, never overwritten.
  category text,
  plaid_category text,
  -- category_source distinguishes a rule-assigned category from a manual edit, so a later sync
  -- or a rule-table change never silently overwrites a human's own correction (lib/ledger.py's
  -- categorize() only ever auto-assigns into a null category, never replaces an existing one).
  category_source text check (category_source in ('rule', 'manual')),
  -- job_invnum: optional, independent of category — which job (invoices.invnum) this spend is
  -- tagged to, for the per-job real-cost view. Null means business overhead (or not yet
  -- reviewed), never silently assumed to belong to whichever job happens to be "current".
  job_invnum text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists plaid_transactions_date_idx on plaid_transactions (date desc);
create index if not exists plaid_transactions_item_idx on plaid_transactions (item_id);
create index if not exists plaid_transactions_account_idx on plaid_transactions (account_id);
create index if not exists plaid_transactions_job_idx on plaid_transactions (job_invnum);
create index if not exists plaid_accounts_item_idx on plaid_accounts (item_id);

-- RLS: plaid_items gets NO anon policy at all — once RLS is enabled, the anon key (the one
-- embedded in every page's client-side JS) has zero access to this table, full stop. Only
-- api/webhook.py, using the service-role key (which bypasses RLS), can ever read or write the
-- access_token. plaid_accounts is anon-readable (balances, due dates — no secret there) but
-- never anon-writable; only the server's sync/refresh jobs touch it.
alter table plaid_items enable row level security;
alter table plaid_accounts enable row level security;
alter table plaid_transactions enable row level security;

create policy "plaid_accounts_anon_select" on plaid_accounts
  for select using (true);

create policy "plaid_transactions_anon_select" on plaid_transactions
  for select using (true);
-- Row-level policy only gates WHICH rows anon can touch (every row, same as before) — the
-- actual column restriction is the GRANT below. Phase 1 (Nov 2026): previously this let the
-- anon key update *any* column on a row (amount, date, item_id included), not just the
-- category the Spending tab was actually meant to let her edit — narrowed column-by-column now
-- that job_invnum (job-tagging) is also meant to be anon-editable, alongside category.
create policy "plaid_transactions_anon_update" on plaid_transactions
  for update using (true) with check (true);
revoke update on plaid_transactions from anon;
grant update (category, category_source, job_invnum) on plaid_transactions to anon;

-- Home's Amex card and the Payroll page's per-card strip read balances through this view, never
-- plaid_items/plaid_accounts directly — same reasoning as plaid_items itself: the view runs
-- with its owner's privileges, bypassing plaid_accounts' RLS for exactly these columns, while
-- access_token (never selected here, and not even a column of this table) stays unreachable.
create or replace view plaid_card_summary as
  select a.account_id, a.item_id, i.institution_name, a.name, a.mask, a.current_balance,
         a.statement_balance, a.minimum_payment, a.next_payment_due_date, a.last_statement_date,
         a.balance_synced_at
  from plaid_accounts a join plaid_items i on i.item_id = a.item_id;

grant select on plaid_card_summary to anon;
