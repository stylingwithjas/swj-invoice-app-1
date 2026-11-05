-- Apply before releasing the Amex automatic sync. Existing categories and transactions stay in place.
alter table public.plaid_items
  add column if not exists transactions_cursor text,
  add column if not exists transactions_synced_at timestamptz;

create or replace view public.plaid_card_summary as
  select a.account_id, a.item_id, i.institution_name, a.name, a.mask, a.current_balance,
         a.statement_balance, a.minimum_payment, a.next_payment_due_date, a.last_statement_date,
         a.balance_synced_at, i.transactions_synced_at
  from public.plaid_accounts a join public.plaid_items i on i.item_id = a.item_id;

-- Commit a complete Plaid update and its cursor together. A concurrent sync from the
-- same starting cursor must retry from the new checkpoint.
create or replace function public.commit_plaid_transaction_sync(
  p_item_id text,
  p_expected_cursor text,
  p_next_cursor text,
  p_rows jsonb,
  p_removed_ids text[]
) returns void
language plpgsql
set search_path = ''
as $$
begin
  update public.plaid_items
    set transactions_cursor = p_next_cursor, transactions_synced_at = now()
    where item_id = p_item_id and transactions_cursor is not distinct from p_expected_cursor;
  if not found then
    raise exception 'Amex sync checkpoint changed. Retry the sync.';
  end if;

  insert into public.plaid_transactions (
    transaction_id, item_id, account_id, date, merchant_name, name, amount,
    pending, plaid_category, category, category_source, updated_at
  )
  select row.transaction_id, p_item_id, row.account_id, row.date, row.merchant_name,
    row.name, row.amount, row.pending, row.plaid_category, row.category, row.category_source, now()
  from jsonb_to_recordset(p_rows) as row(
    transaction_id text, account_id text, date date, merchant_name text, name text,
    amount numeric, pending boolean, plaid_category text, category text, category_source text
  )
  on conflict (transaction_id) do update set
    account_id = excluded.account_id, date = excluded.date, merchant_name = excluded.merchant_name,
    name = excluded.name, amount = excluded.amount, pending = excluded.pending,
    plaid_category = excluded.plaid_category, updated_at = excluded.updated_at
  where plaid_transactions.item_id = p_item_id;

  delete from public.plaid_transactions
    where item_id = p_item_id and transaction_id = any(p_removed_ids);
end;
$$;

revoke execute on function public.commit_plaid_transaction_sync(text, text, text, jsonb, text[])
  from public, anon, authenticated;
grant execute on function public.commit_plaid_transaction_sync(text, text, text, jsonb, text[])
  to service_role;
