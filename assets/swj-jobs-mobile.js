/* Read-only mobile projection of existing board records and card labels. */
let swjJobFilter='all';
function renderMobileJobs(invoices){
 const root=document.getElementById('swj-jobs-mobile');if(!root)return;
 const stages=['proposal','future_hold','confirmed','staged','destage','done'];
 const active=invoices.filter(inv=>stages.includes((inv.data||{}).stage||'proposal'));
 root.replaceChildren();
 const title=document.createElement('h1');title.className='swj-jobs-title';title.textContent='Your Projects';root.append(title);
 const subtitle=document.createElement('p');subtitle.className='swj-jobs-subtitle';subtitle.textContent='Everything in motion.';root.append(subtitle);
 const filters=document.createElement('div');filters.className='swj-job-filters';filters.setAttribute('aria-label','Filter projects by status');root.append(filters);
 const list=document.createElement('div');list.setAttribute('aria-live','polite');root.append(list);
 const cards=active.map(inv=>{
  // Reuse existing presentation, including its attention rules and amount formatter.
  const source=document.createElement('div');source.innerHTML=buildCardHTML(inv);
  const stage=(inv.data||{}).stage||'proposal';
  const card=document.createElement('button');card.type='button';card.className='swj-job-card';
  const name=document.createElement('strong');name.textContent=source.querySelector('.c-name')?.textContent||'Unknown';card.append(name);
  const address=document.createElement('span');address.textContent=source.querySelector('.c-addr')?.textContent||'Property address not set';card.append(address);
  const status=document.createElement('span');status.textContent=STAGE_META[stage].label;card.append(status);
  const tags=[...source.querySelectorAll('.tag')];
  // Urgency and dates are taken directly from the desktop card, never redefined here.
  const urgent=tags.find(t=>t.matches('.a,.w'));
  if(urgent){const line=document.createElement('span');line.textContent=urgent.textContent;card.append(line);}
  const date=tags.find(t=>/Staging |days? left|destage|Rental ended|Photos /i.test(t.textContent)&&t!==urgent);
  if(date){const line=document.createElement('span');line.textContent=date.textContent;card.append(line);}
  const meta=document.createElement('span');meta.className='swj-job-meta';
  const payment=document.createElement('span');payment.textContent=(inv.data||{}).paid?'Paid':'Payment pending';
  const amount=document.createElement('span');amount.textContent=source.querySelector('.c-amt')?.textContent||'';meta.append(payment,amount);card.append(meta);
  card.addEventListener('click',()=>openCard(inv.invnum));
  return {card,stage};
 });
 const draw=()=>{
  list.replaceChildren(...cards.filter(c=>swjJobFilter==='all'||c.stage===swjJobFilter).map(c=>c.card));
  if(!list.children.length){const empty=document.createElement('p');empty.textContent='No projects in this view.';list.append(empty);}
  filters.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.stage===swjJobFilter)));
 };
 ['all',...stages].forEach(stage=>{
  const button=document.createElement('button');button.type='button';button.dataset.stage=stage;
  const count=stage==='all'?active.length:cards.filter(c=>c.stage===stage).length;
  button.textContent=(stage==='all'?'All':STAGE_META[stage].label)+' · '+count;
  button.onclick=()=>{swjJobFilter=stage;draw();};filters.append(button);
 });draw();
}
