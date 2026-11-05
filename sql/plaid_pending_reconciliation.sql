-- Replace the sync RPC in place. Existing callers without pending IDs continue to work.
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

  -- Use Plaid's link between the authorization and the posted charge. Match
  -- within the same card and connection, even when the amount or name changed.
  update public.plaid_transactions as posted
    set category = case
          when posted.category_source = 'manual' then posted.category
          when pending.category_source = 'manual' then pending.category
          else coalesce(posted.category, pending.category)
        end,
        category_source = case
          when posted.category_source = 'manual' then posted.category_source
          when pending.category_source = 'manual' then pending.category_source
          when posted.category is not null then posted.category_source
          else pending.category_source
        end,
        job_invnum = coalesce(posted.job_invnum, pending.job_invnum)
  from jsonb_to_recordset(p_rows) as incoming(transaction_id text, pending_transaction_id text)
  join public.plaid_transactions as pending
    on pending.transaction_id = incoming.pending_transaction_id
    and pending.item_id = p_item_id and pending.pending
  where posted.transaction_id = incoming.transaction_id
    and posted.item_id = p_item_id and not posted.pending
    and posted.account_id = pending.account_id;

  delete from public.plaid_transactions as pending
  using jsonb_to_recordset(p_rows) as incoming(transaction_id text, pending_transaction_id text),
    public.plaid_transactions as posted
  where posted.transaction_id = incoming.transaction_id
    and posted.item_id = p_item_id and not posted.pending
    and pending.transaction_id = incoming.pending_transaction_id
    and pending.item_id = p_item_id and pending.pending
    and posted.account_id = pending.account_id;

  delete from public.plaid_transactions
    where item_id = p_item_id and transaction_id = any(p_removed_ids);
end;
$$;

revoke execute on function public.commit_plaid_transaction_sync(text, text, text, jsonb, text[])
  from public, anon, authenticated;
grant execute on function public.commit_plaid_transaction_sync(text, text, text, jsonb, text[])
  to service_role;
