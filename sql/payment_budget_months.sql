-- A planning overlay only. The original invoice and its payment remain the source of truth.
begin;
create table if not exists public.payment_budget_months (
  event_id text primary key,
  received_date date not null,
  amount_cents bigint not null check (amount_cents > 0),
  budget_month date not null check (
    extract(day from budget_month) = 1 and budget_month >= date_trunc('month', received_date::timestamp)::date
  )
);
alter table public.payment_budget_months enable row level security;
revoke all on public.payment_budget_months from public, anon, authenticated;
grant select, insert, update on public.payment_budget_months to service_role;
commit;
