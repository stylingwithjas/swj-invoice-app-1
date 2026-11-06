'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
  let factoryOptions, calls = [], release;
  const window = {
    supabase: {createClient(url, key, options) { factoryOptions = options; return {}; }},
    fetch: async (url, options) => {calls.push({url, options}); return new Response('[]', {status:200});},
    swjOwnerReady: new Promise(resolve => {release = resolve;})
  };
  vm.runInNewContext(fs.readFileSync('assets/swj-owner-data.js', 'utf8'), {window, Request, Response, URL, Headers, Error});
  window.supabase.createClient('https://fixture.supabase.co', 'public-key');
  const queryFetch = factoryOptions.global.fetch;
  const request = queryFetch('https://fixture.supabase.co/rest/v1/invoices?select=invnum', {
    method:'PATCH', headers:{apikey:'browser-key', Authorization:'Bearer browser-key', Prefer:'return=representation', 'Content-Type':'application/json'},
    body:JSON.stringify({client:'Fixture'})
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length,0,'data waits for session verification');
  release(true);
  await request;
  assert.equal(calls.length,1);
  assert.equal(calls[0].url,'/api/webhook?data=invoices&query=select%3Dinvnum');
  assert.equal(calls[0].options.credentials,'same-origin');
  assert.equal(calls[0].options.headers.has('Authorization'),false);
  assert.equal(calls[0].options.headers.has('apikey'),false);
  assert.equal(calls[0].options.headers.get('Prefer'),'return=representation');
  assert.deepEqual(JSON.parse(calls[0].options.body),{client:'Fixture'});
  window.swjOwnerReady = Promise.resolve(false);
  const denied = await queryFetch('https://fixture.supabase.co/rest/v1/invoices');
  assert.equal(denied.status,401);
  assert.equal(calls.length,1,'signed-out data never reaches network');
  window.swjOwnerReady = Promise.resolve(true);
  await assert.rejects(queryFetch('https://attacker.example/rest/v1/invoices'),/Unsupported/);
  console.log('Owner data browser checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
