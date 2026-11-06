-- REVIEWED CUTOVER SCRIPT: apply only AFTER the matching application deployment
-- and owner/client smoke tests. Not applied by this build. No business rows change.
-- All browser business data now uses the server's owner-session bridge; public
-- invoice/crew endpoints use server-verified capabilities and restricted DTOs.
BEGIN;

DO $cutover$
DECLARE
  table_name text;
  policy_row record;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'invoices','tasks','employees','time_entries','plaid_items','plaid_accounts',
    'plaid_transactions','cash_source_sync','cash_bank_reviews','cash_match_refs',
    'cash_review_history','admin_revoked_sessions','cash_bank_items',
    'cash_bank_accounts','cash_mover_payments','cash_mover_time_links',
    'cash_bank_transactions','cash_settings','cash_stripe_balance'
  ] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',table_name);
    -- No Supabase client-auth identities are used by this app: owner cookies are
    -- verified by the server. Service-role access bypasses RLS by design.
    FOR policy_row IN SELECT policyname FROM pg_policies
      WHERE schemaname='public' AND tablename=table_name
    LOOP
      EXECUTE format('DROP POLICY %I ON public.%I',policy_row.policyname,table_name);
    END LOOP;
    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.%I FROM PUBLIC, anon, authenticated',table_name);
    EXECUTE format('GRANT ALL PRIVILEGES ON TABLE public.%I TO service_role',table_name);
  END LOOP;
END
$cutover$;

REVOKE ALL PRIVILEGES ON TABLE public.plaid_card_summary FROM PUBLIC, anon, authenticated;
GRANT SELECT ON TABLE public.plaid_card_summary TO service_role;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, anon, authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
-- Invoker functions are not a public way around the revoked table permissions.
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO service_role;

-- Signed contracts are private already, but these legacy policies let anyone
-- with the public key list/read or upload them. Existing signed download URLs
-- and service-role uploads continue to work without these anonymous policies.
DROP POLICY IF EXISTS "Allow anon read" ON storage.objects;
DROP POLICY IF EXISTS "Allow anon upload" ON storage.objects;

COMMIT;
