/* Stable source-task/date identifiers make carry-forward retries idempotent. */
(function(root){
 async function rows(tasks,date){
  return Promise.all(tasks.map(async task=>{
   if(!task.id)throw Error('Cannot identify a source task.');
   const bytes=new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode('swj-carry/v1|'+task.id+'|'+date)));
   bytes[6]=(bytes[6]&15)|128;bytes[8]=(bytes[8]&63)|128;
   const hex=[...bytes.slice(0,16)].map(n=>n.toString(16).padStart(2,'0')).join('');
   const id=hex.slice(0,8)+'-'+hex.slice(8,12)+'-'+hex.slice(12,16)+'-'+hex.slice(16,20)+'-'+hex.slice(20);
   const row={...task,id,date,sort_order:task.sort_order||0};delete row.created_at;
   return row;
  }));
 }
 root.SWJTaskCarry={rows};
})(typeof window==='undefined'?globalThis:window);
