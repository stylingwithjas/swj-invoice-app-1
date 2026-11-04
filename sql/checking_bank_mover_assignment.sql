-- NEW follow-up: atomic mover allocation from checking evidence. Not yet applied.
-- Requires the already-installed checking and review-history functions. Do not rerun them.
BEGIN;
CREATE OR REPLACE FUNCTION public.cash_assign_bank_movers(
 p_transaction text, p_fingerprint text, p_rows jsonb, p_note text,
 p_expected_revision timestamptz DEFAULT NULL
) RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE tx cash_bank_transactions; r jsonb; refs jsonb; total numeric;
BEGIN
 SELECT * INTO tx FROM cash_bank_transactions WHERE transaction_id=p_transaction FOR UPDATE;
 IF NOT FOUND OR tx.pending OR tx.removed OR tx.amount<=0 THEN
  RAISE EXCEPTION 'A posted outgoing checking payment is required';
 END IF;
 IF md5(tx.transaction_id||'|'||tx.account_id||'|'||tx.date::text||'|'||round(tx.amount*100)::bigint::text)<>p_fingerprint THEN
  RAISE EXCEPTION 'Checking payment changed; refresh';
 END IF;
 IF jsonb_typeof(p_rows)<>'array' OR jsonb_array_length(p_rows) NOT BETWEEN 1 AND 30 THEN
  RAISE EXCEPTION 'Assign 1–30 mover/job rows';
 END IF;
 SELECT sum((value->>'amount')::numeric),jsonb_agg('mover:'||(value->>'event_id'))
 INTO total,refs FROM jsonb_array_elements(p_rows);
 IF total IS DISTINCT FROM tx.amount THEN RAISE EXCEPTION 'Allocations must match checking amount'; END IF;
 FOR r IN SELECT value FROM jsonb_array_elements(p_rows) LOOP
  IF (r->>'paid_date')::date IS DISTINCT FROM tx.date OR (r->>'amount')::numeric<=0 THEN
   RAISE EXCEPTION 'Allocation date and amounts must match the posted payment';
  END IF;
 END LOOP;
 -- A failure in either function rolls back both the new costs and the movement review.
 PERFORM cash_add_mover_payments(p_rows);
 PERFORM cash_confirm_review(p_transaction,p_fingerprint,'mover_payment',refs,p_note,p_expected_revision);
END $$;
REVOKE ALL ON FUNCTION public.cash_assign_bank_movers(text,text,jsonb,text,timestamptz) FROM public,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cash_assign_bank_movers(text,text,jsonb,text,timestamptz) TO service_role;
COMMIT;
