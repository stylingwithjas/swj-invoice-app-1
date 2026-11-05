/* Complete-coverage reads. Never turn a partial page set into a normal total. */
(function(root){
 async function all(factory,{key,pageSize=500,maxPages=1000}={}){
  const rows=[],seen=new Set();let expected=null,offset=0;
  try{
   if(!key)throw Error('A stable record key is required.');
   for(let page=0;page<maxPages;page++){
    const response=await factory().range(offset,offset+pageSize-1);
    if(response.error)throw response.error;
    if(!Array.isArray(response.data)||!Number.isSafeInteger(response.count)||response.count<0)throw Error('Could not confirm the total record count.');
    if(expected===null)expected=response.count;
    if(response.count!==expected)throw Error('Records changed while loading. Refresh to load a consistent list.');
    for(const row of response.data){
     const id=row[key];if(id===undefined||id===null||seen.has(String(id)))throw Error('Records repeated or changed while loading. Refresh to retry.');
     seen.add(String(id));rows.push(row);
    }
    offset+=response.data.length;
    if(offset===expected)return {data:rows,error:null,count:expected,complete:true};
    if(offset>expected||!response.data.length)throw Error('The response is incomplete. Refresh to retry.');
    // Advance by rows received, not requested: a server cap may be smaller.
   }
   throw Error('Record loading reached its safety limit. Narrow the filter or retry.');
  }catch(error){return {data:null,error:{message:error.message||'Could not load complete records.'},count:expected,complete:false};}
 }
 function warning(id,message,parent){
  let node=document.getElementById(id);
  if(!message){node?.remove();return;}
  if(!node){node=document.createElement('div');node.id=id;node.setAttribute('role','alert');node.style.cssText='padding:14px 16px;margin:12px;border:1px solid #a65b4e;border-radius:10px;background:#f8ece8;color:#66392f;font-size:14px;line-height:1.5';(parent||document.body).prepend(node);}
  node.textContent=message;
 }
 root.SWJRecords={all,warning};
})(typeof window==='undefined'?globalThis:window);
