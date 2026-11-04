-- Additive checking/cash reconciliation. Does not alter invoices, tax figures or Amex rows.
-- Apply once (safe to rerun) using a migration owner; service_role only.
begin;
create table if not exists cash_bank_items (
 item_id text primary key, access_token text not null, institution_name text,
 cursor text, synced_at timestamptz, sync_pending boolean not null default true, created_at timestamptz not null default now()
);
create table if not exists cash_bank_accounts (
 account_id text primary key, item_id text not null references cash_bank_items(item_id),
 name text, mask text, current_balance numeric, available_balance numeric, currency text,
 balance_synced_at timestamptz
);
create table if not exists cash_bank_transactions (
 transaction_id text primary key, account_id text not null references cash_bank_accounts(account_id),
 date date not null, amount numeric not null, pending boolean not null default false,
 pending_transaction_id text, name text, removed boolean not null default false,
 updated_at timestamptz not null default now()
);
create index if not exists cash_bank_transactions_account_date on cash_bank_transactions(account_id,date);
create table if not exists cash_settings (
 id text primary key check(id='business'), account_id text references cash_bank_accounts(account_id),
 review_from date, tax_reserve numeric check(tax_reserve>=0), operating_buffer numeric check(operating_buffer>=0),
 confirmed_at timestamptz not null default now()
);
create table if not exists cash_mover_payments (
 event_id text primary key, paid_date date not null, mover_name text not null, job_invnum text not null,
 amount numeric not null check(amount>0), source text not null check(source in ('manual_venmo','reviewed_csv')),
 fingerprint text not null, created_at timestamptz not null default now()
);
create table if not exists cash_mover_time_links (
 time_entry_id text primary key, event_id text not null references cash_mover_payments(event_id)
);
create table if not exists cash_stripe_balance (
 transaction_id text primary key, date date not null, amount numeric not null, fee numeric not null,
 currency text not null, type text not null, source_id text, invoice_ref text,
 synced_at timestamptz not null default now()
);
create table if not exists cash_source_sync (
 source text primary key, period text not null, completed_at timestamptz not null default now(),
 unique(source,period)
);
create table if not exists cash_bank_reviews (
 transaction_id text primary key references cash_bank_transactions(transaction_id),
 fingerprint text not null, kind text not null, source_refs jsonb not null default '[]',
 note text, confirmed_at timestamptz not null default now()
);
create table if not exists cash_match_refs (
 source_ref text primary key, transaction_id text not null references cash_bank_reviews(transaction_id)
);
-- Tokens, bank activity and review evidence are never exposed through public/anon views.
do $$ declare t text; begin
 foreach t in array array['cash_bank_items','cash_bank_accounts','cash_bank_transactions','cash_settings',
 'cash_mover_payments','cash_mover_time_links','cash_stripe_balance','cash_source_sync','cash_bank_reviews','cash_match_refs'] loop
 execute format('alter table %I enable row level security',t);
 execute format('revoke all on %I from anon, authenticated',t);
 execute format('grant all on %I to service_role',t);
 end loop;
end $$;
-- Atomic cursor advancement: never claim a complete bank sync after a partial database write.
create or replace function cash_commit_bank_sync(p_item text,p_expected_cursor text,p_next_cursor text,p_rows jsonb,p_removed jsonb)
returns void language plpgsql security invoker set search_path=public as $$
declare old_cursor text; r jsonb;
begin
 select cursor into old_cursor from cash_bank_items where item_id=p_item for update;
 if not found or old_cursor is distinct from p_expected_cursor then raise exception 'Concurrent bank sync; retry'; end if;
 for r in select value from jsonb_array_elements(p_rows) loop
 if not exists(select 1 from cash_bank_accounts where account_id=r->>'account_id' and item_id=p_item) then raise exception 'Account not in selected Item'; end if;
 insert into cash_bank_transactions(transaction_id,account_id,date,amount,pending,pending_transaction_id,name,removed)
 values(r->>'transaction_id',r->>'account_id',(r->>'date')::date,(r->>'amount')::numeric,(r->>'pending')::boolean,r->>'pending_transaction_id',r->>'name',false)
 on conflict(transaction_id) do update set account_id=excluded.account_id,date=excluded.date,amount=excluded.amount,
 pending=excluded.pending,pending_transaction_id=excluded.pending_transaction_id,name=excluded.name,removed=false,updated_at=now();
 end loop;
 update cash_bank_transactions set removed=true,updated_at=now() where transaction_id in(select jsonb_array_elements_text(p_removed))
 and account_id in(select account_id from cash_bank_accounts where item_id=p_item);
 update cash_bank_items set cursor=p_next_cursor,synced_at=now(),sync_pending=false where item_id=p_item;
end $$;
-- Import is idempotent. Reusing an external reference with changed figures fails, not overwrites.
create or replace function cash_add_mover_payments(p_rows jsonb) returns void
language plpgsql security invoker set search_path=public as $$
declare r jsonb; link_id text; existing text;
begin
 for r in select value from jsonb_array_elements(p_rows) loop
 insert into cash_mover_payments(event_id,paid_date,mover_name,job_invnum,amount,source,fingerprint)
 values(r->>'event_id',(r->>'paid_date')::date,r->>'mover_name',r->>'job_invnum',(r->>'amount')::numeric,r->>'source',r->>'fingerprint')
 on conflict(event_id) do nothing;
 select fingerprint into existing from cash_mover_payments where event_id=r->>'event_id' for update;
 if existing<>r->>'fingerprint' then raise exception 'Payment reference conflicts with an existing payment'; end if;
 for link_id in select jsonb_array_elements_text(coalesce(r->'time_entry_ids','[]')) loop
 insert into cash_mover_time_links(time_entry_id,event_id) values(link_id,r->>'event_id') on conflict(time_entry_id) do nothing;
 if (select event_id from cash_mover_time_links where time_entry_id=link_id)<>r->>'event_id' then raise exception 'Hours already linked to another payment'; end if;
 end loop;
 end loop;
end $$;
-- Exclusive matches stop one card credit, payout or mover payment settling two bank rows.
CREATE OR REPLACE FUNCTION public.cash_confirm_review(p_transaction text, p_fingerprint text, p_kind text, p_refs jsonb, p_note text)
 RETURNS void
 LANGUAGE plpgsql
 SET search_path TO 'public'
AS $function$
declare r text; old cash_bank_reviews; tx cash_bank_transactions; current_fingerprint text;
begin
 select * into tx from cash_bank_transactions where transaction_id=p_transaction for update;
 if not found or tx.pending or tx.removed then raise exception 'Posted bank transaction required'; end if;
 -- Python fingerprint is a digest of immutable comparison fields, independently checked here.
 current_fingerprint=md5(tx.transaction_id||'|'||tx.account_id||'|'||tx.date::text||'|'||round(tx.amount*100)::bigint::text);
 if current_fingerprint<>p_fingerprint then raise exception 'Bank transaction changed; refresh before matching'; end if;
 select * into old from cash_bank_reviews where transaction_id=p_transaction;
 if found and (old.fingerprint<>p_fingerprint or old.kind<>p_kind or old.source_refs<>p_refs) then raise exception 'Already reviewed differently; no overwrite'; end if;
 insert into cash_bank_reviews(transaction_id,fingerprint,kind,source_refs,note) values(p_transaction,p_fingerprint,p_kind,p_refs,p_note)
 on conflict(transaction_id) do nothing;
 for r in select jsonb_array_elements_text(p_refs) loop
 insert into cash_match_refs(source_ref,transaction_id) values(r,p_transaction) on conflict(source_ref) do nothing;
 if (select transaction_id from cash_match_refs where source_ref=r)<>p_transaction then raise exception 'Evidence already matched to another bank transaction'; end if;
 end loop;
end $function$
revoke all on function cash_commit_bank_sync(text,text,text,jsonb,jsonb) from public,anon,authenticated;
revoke all on function cash_add_mover_payments(jsonb) from public,anon,authenticated;
revoke all on function cash_confirm_review(text,text,text,jsonb,text) from public,anon,authenticated;
grant execute on function cash_commit_bank_sync(text,text,text,jsonb,jsonb) to service_role;
grant execute on function cash_add_mover_payments(jsonb) to service_role;
grant execute on function cash_confirm_review(text,text,text,jsonb,text) to service_role;
commit;
