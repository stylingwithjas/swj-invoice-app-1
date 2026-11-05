/* Run the actual cutover SQL against an isolated PostgreSQL WASM database. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
const {PGlite} = await import(process.env.SWJ_PGLITE || '@electric-sql/pglite');
const db = new PGlite();
try {
 await db.exec(`CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;
 CREATE SCHEMA storage; GRANT USAGE ON SCHEMA public, storage TO anon,authenticated,service_role;
 CREATE TABLE storage.objects (id integer, bucket_id text);
 ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;
 GRANT SELECT,INSERT ON storage.objects TO anon;
 CREATE POLICY "Allow anon read" ON storage.objects FOR SELECT TO anon USING (bucket_id='signed-contracts');
 CREATE POLICY "Allow anon upload" ON storage.objects FOR INSERT TO anon WITH CHECK (bucket_id='signed-contracts');
 INSERT INTO storage.objects VALUES (1,'signed-contracts');`);
 const tables = ['invoices','tasks','employees','time_entries','plaid_items','plaid_accounts','plaid_transactions','cash_source_sync','cash_bank_reviews','cash_match_refs','cash_review_history','admin_revoked_sessions','cash_bank_items','cash_bank_accounts','cash_mover_payments','cash_mover_time_links','cash_bank_transactions','cash_settings','cash_stripe_balance'];
 for (const name of tables) {
  await db.exec(`CREATE TABLE public.${name} (id integer, data jsonb); INSERT INTO public.${name} VALUES (1,'{"fixture":true}'); GRANT ALL ON public.${name} TO anon,authenticated;`);
 }
 await db.exec(`ALTER TABLE employees ENABLE ROW LEVEL SECURITY; CREATE POLICY "anon full access employees" ON employees FOR ALL USING (true) WITH CHECK (true);
 CREATE VIEW plaid_card_summary AS SELECT * FROM plaid_accounts;
 GRANT SELECT ON plaid_card_summary TO anon,authenticated;
 CREATE FUNCTION public.mark_extension_paid() RETURNS integer LANGUAGE sql AS 'SELECT 1';`);
 await db.exec('SET ROLE anon');
 assert.equal((await db.query('SELECT * FROM invoices')).rows.length,1);
 assert.equal((await db.query('SELECT * FROM storage.objects')).rows.length,1);
 await db.exec('RESET ROLE');
 await db.exec(fs.readFileSync('sql/owner_access_cutover.sql','utf8'));
 for (const role of ['anon','authenticated']) {
  await db.exec('SET ROLE '+role);
  for (const name of [...tables,'plaid_card_summary']) {
   await assert.rejects(db.query('SELECT * FROM public.'+name),/permission denied/);
  }
  await assert.rejects(db.query('SELECT public.mark_extension_paid()'),/permission denied/);
  await db.exec('RESET ROLE');
 }
 await db.exec('SET ROLE anon');
 assert.equal((await db.query('SELECT * FROM storage.objects')).rows.length,0,'contract rows are no longer anonymously readable');
 await assert.rejects(db.exec("INSERT INTO storage.objects VALUES (2,'signed-contracts')"),/row-level security/);
 await db.exec('RESET ROLE; SET ROLE service_role');
 for(const name of tables) assert.equal((await db.query('SELECT * FROM '+name)).rows.length,1,'service access and stored rows retained: '+name);
 assert.equal((await db.query('SELECT * FROM plaid_card_summary')).rows.length,1);
 assert.equal((await db.query('SELECT public.mark_extension_paid() AS result')).rows[0].result,1);
 await db.exec('RESET ROLE');
 assert.equal((await db.query("SELECT count(*)::int AS count FROM pg_policies WHERE schemaname IN ('public','storage')")).rows[0].count,0);
 assert.equal((await db.query("SELECT count(*)::int AS count FROM pg_tables WHERE schemaname='public' AND NOT rowsecurity")).rows[0].count,0);
 console.log('PASS isolated PostgreSQL: anon/authenticated blocked on all 19 tables, view and RPC; contract read/upload blocked; service access and all fixture rows preserved');
} finally { await db.close(); }
