/* Numeric staging invoices and their vN revisions only; never drafts/system records. */
(function(root){
 function next(rows,fallback=202093){
  let max=0;
  for(const row of rows||[]){
   const match=String(row.invnum||'').match(/^(\d+)(?:v\d+)?$/i);
   if(match){const n=Number(match[1]);if(Number.isSafeInteger(n)&&n>max)max=n;}
  }
  return max>0&&Number.isSafeInteger(max+1)?max+1:fallback;
 }
 root.SWJInvoiceNumber={next};
})(typeof window==='undefined'?globalThis:window);
