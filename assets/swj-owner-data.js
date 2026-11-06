/* Keep existing Supabase query builders, send owner data through our server.
   No service credential is present in this file or in browser requests. */
(function () {
  'use strict';
  const sdk = window.supabase;
  if (!sdk || !sdk.createClient) return;
  const create = sdk.createClient.bind(sdk);
  sdk.createClient = function (url, key, options) {
    const nativeFetch = window.fetch.bind(window);
    const config = Object.assign({}, options, {
      global: Object.assign({}, options && options.global, {
        fetch: async function (input, init) {
          if (window.swjOwnerReady && !(await window.swjOwnerReady)) {
            return new Response(JSON.stringify({message:'Sign in to continue.', code:'SWJ_AUTH_REQUIRED'}), {status:401, headers:{'Content-Type':'application/json'}});
          }
          const request = new Request(input, init);
          const target = new URL(request.url);
          if (target.origin !== new URL(url).origin || !target.pathname.startsWith('/rest/v1/')) {
            throw new Error('Unsupported owner data request.');
          }
          const endpoint = '/api/webhook?data=' + encodeURIComponent(target.pathname.slice(9)) + '&query=' + encodeURIComponent(target.search.slice(1));
          const headers = new Headers();
          for (const name of ['Content-Type','Accept','Prefer','Range','Range-Unit']) {
            if (request.headers.has(name)) headers.set(name, request.headers.get(name));
          }
          const method = request.method;
          return nativeFetch(endpoint, {method, headers, credentials:'same-origin', cache:'no-store', signal:request.signal,
            body: ['GET','HEAD'].includes(method) ? undefined : await request.text()});
        }
      }),
      auth: {persistSession:false, autoRefreshToken:false, detectSessionInUrl:false}
    });
    return create(url, key, config);
  };
})();
