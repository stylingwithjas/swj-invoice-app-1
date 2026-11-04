-- PENDING: review/apply separately after 20261104220104. Never rerun the original migration.
-- Not applied by this build. Run within an approved database deployment only.
begin;
create table if not exists cash_review_history (
 revision_id uuid primary key default gen_random_uuid(), transaction_id text not null,
 prior_review jsonb not null, replaced_at timestamptz not null default now()
);
alter table cash_review_history enable row level security;
revoke all on cash_review_history from public,anon,authenticated;
grant all on cash_review_history to service_role;
drop function if exists cash_confirm_review(text,text,text,jsonb,text);
create or replace function cash_confirm_review(p_transaction text,p_fingerprint text,p_kind text,p_refs jsonb,p_note text,p_expected_revision timestamptz default null)
returns void language plpgsql security invoker set search_path=public as $$
declare r text; old cash_bank_reviews; tx cash_bank_transactions; current_fingerprint text;
begin
 select * into tx from cash_bank_transactions where transaction_id=p_transaction for update;
 if not found or tx.pending or tx.removed then raise exception 'Posted bank transaction required'; end if;
 -- Python fingerprint is a digest of immutable comparison fields, independently checked here.
 current_fingerprint=md5(tx.transaction_id||'|'||tx.account_id||'|'||tx.date::text||'|'||round(tx.amount*100)::bigint::text);
 if current_fingerprint<>p_fingerprint then raise exception 'Bank transaction changed; refresh before matching'; end if;
 select * into old from cash_bank_reviews where transaction_id=p_transaction;
 if found then
   if old.fingerprint=p_fingerprint and old.kind=p_kind and old.source_refs=p_refs then return; end if;
   if p_expected_revision is null or old.confirmed_at<>p_expected_revision then raise exception 'Review changed; reload before an audited correction'; end if;
   insert into cash_review_history(transaction_id,prior_review) values(p_transaction,to_jsonb(old));
   delete from cash_match_refs where transaction_id=p_transaction;
   update cash_bank_reviews set fingerprint=p_fingerprint,kind=p_kind,source_refs=p_refs,note=p_note,confirmed_at=clock_timestamp() where transaction_id=p_transaction;
 elsif p_expected_revision is not null then raise exception 'Previous review is unavailable; reload';
 end if;
 insert into cash_bank_reviews(transaction_id,fingerprint,kind,source_refs,note) values(p_transaction,p_fingerprint,p_kind,p_refs,p_note)
 on conflict(transaction_id) do nothing;
 for r in select jsonb_array_elements_text(p_refs) loop
 insert into cash_match_refs(source_ref,transaction_id) values(r,p_transaction) on conflict(source_ref) do nothing;
 if (select transaction_id from cash_match_refs where source_ref=r)<>p_transaction then raise exception 'Evidence already matched to another bank transaction'; end if;
 end loop;
end $$;
revoke all on function cash_confirm_review(text,text,text,jsonb,text,timestamptz) from public,anon,authenticated;
grant execute on function cash_confirm_review(text,text,text,jsonb,text,timestamptz) to service_role;
commit;
