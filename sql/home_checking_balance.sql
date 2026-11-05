-- Owner-entered cash snapshot. No bank connection or historical ledger changes.
alter table public.cash_settings
  add column if not exists manual_balance_cents bigint,
  add column if not exists manual_balance_as_of date;
