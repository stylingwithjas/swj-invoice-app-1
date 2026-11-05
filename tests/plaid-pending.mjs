import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const { PGlite } = await import(process.env.SWJ_PGLITE || '@electric-sql/pglite');
const db = new PGlite();
try {
  await db.exec('create role anon; create role authenticated; create role service_role bypassrls;');
  for (const file of ['plaid_tables', 'plaid_transaction_sync', 'plaid_pending_reconciliation']) {
    await db.exec(readFileSync(new URL(`../sql/${file}.sql`, import.meta.url), 'utf8'));
  }
  await db.exec(`
    insert into plaid_items(item_id, access_token) values ('item','fixture');
    insert into plaid_accounts(account_id,item_id) values ('card','item'), ('other','item');
    insert into plaid_transactions(transaction_id,item_id,account_id,date,amount,pending,category,category_source,job_invnum) values
      ('linked','item','card','2026-11-03',122,true,'Truck & Gas','manual','JOB'),
      ('same-amount','item','card','2026-11-03',122,true,'Meals','manual',null),
      ('wrong-card','item','other','2026-11-03',122,true,'Meals','manual',null),
      ('already-posted','item','card','2026-11-03',122,false,'Storage','manual','POSTED-JOB'),
      ('settled-auth','item','card','2026-11-03',122,true,'Meals','manual','PENDING-JOB');
  `);
  const posted = (transaction_id, pending_transaction_id) => ({
    transaction_id, pending_transaction_id, account_id:'card', date:'2026-11-04',
    amount:130, pending:false, merchant_name:'Changed merchant', category:'Other', category_source:'rule',
  });
  const commit = (expected, next, rows, removed = []) => db.query(
    'select commit_plaid_transaction_sync($1,$2,$3,$4::jsonb,$5::text[])',
    ['item',expected,next,JSON.stringify(rows),removed],
  );
  await commit(null,'cursor1',[posted('posted','linked'),posted('unmatched','wrong-card'),posted('already-posted','settled-auth')]);
  const rows = (await db.query('select transaction_id, category, job_invnum, amount from plaid_transactions order by transaction_id')).rows;
  assert.deepEqual(rows,[
    {transaction_id:'already-posted',category:'Storage',job_invnum:'POSTED-JOB',amount:'130'},
    {transaction_id:'posted',category:'Truck & Gas',job_invnum:'JOB',amount:'130'},
    {transaction_id:'same-amount',category:'Meals',job_invnum:null,amount:'122'},
    {transaction_id:'unmatched',category:'Other',job_invnum:null,amount:'130'},
    {transaction_id:'wrong-card',category:'Meals',job_invnum:null,amount:'122'},
  ]);
  await assert.rejects(commit(null,'stale',[]), /checkpoint changed/);
  await commit('cursor1','cursor2',[posted('posted',null)]);
  assert.equal((await db.query("select category from plaid_transactions where transaction_id='posted'")).rows[0].category,'Truck & Gas');
  console.log('PASS provider-linked settlement, changed amount, category/job preservation, same amount and other card isolation, retry safety');
} finally {
  await db.close();
}
