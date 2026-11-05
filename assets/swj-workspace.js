/* UI only: no network, storage or business calculations. */
(()=>{
 const nav=document.querySelector('.swj-bottom-nav');
 const measure=()=>{
  const h=nav?.getBoundingClientRect().height||0;
  document.documentElement.style.setProperty('--swj-nav-height',h+'px');
  const action=document.querySelector('.gen-sticky-bar');
  if(action)document.documentElement.style.setProperty('--swj-action-height',action.getBoundingClientRect().height+'px');
 };
 if(nav){new ResizeObserver(measure).observe(nav);nav.setAttribute('aria-label','Primary navigation');}
 const action=document.querySelector('.gen-sticky-bar');if(action)new ResizeObserver(measure).observe(action);measure();
 const sheet=document.querySelector('.swj-more-sheet'),card=sheet?.querySelector('.swj-more-card');
 if(card){
  const heading=document.createElement('h2');heading.className='swj-more-title';heading.id='swj-more-heading';heading.textContent='More';card.prepend(heading);
  const close=document.createElement('button');close.type='button';close.className='swj-more-close';close.setAttribute('aria-label','Close More menu');close.textContent='×';heading.prepend(close);close.onclick=()=>window.swjToggleMore();
  sheet.setAttribute('role','dialog');sheet.setAttribute('aria-modal','true');sheet.setAttribute('aria-labelledby',heading.id);
  let lastGroup='';
  card.querySelectorAll('.swj-more-link').forEach(link=>{
   const href=link.getAttribute('href')||'';
   const group=link.tagName==='BUTTON'?'Account':/hours/.test(href)?'Team':/luxury|gs-invoice/.test(href)?'Specialized invoices':'Business tools';
   if(group!==lastGroup){const label=document.createElement('div');label.className='swj-more-group';label.textContent=group;link.before(label);lastGroup=group;}
   link.textContent=link.textContent.replace(/^[^\p{L}\p{N}]+/u,'');
  });
  let returnFocus;
  new MutationObserver(()=>{const open=sheet.classList.contains('open');if(open){returnFocus=document.activeElement;close.focus();}else if(returnFocus?.isConnected)returnFocus.focus();}).observe(sheet,{attributes:true,attributeFilter:['class']});
  sheet.addEventListener('keydown',e=>{if(e.key!=='Tab')return;const nodes=[...card.querySelectorAll('a[href],button')].filter(n=>n.getClientRects().length);const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}});
 }
 // Keep all editor nodes mounted: collapsing never discards unsaved input values.
 const sectionSummaries=[];
 document.querySelectorAll('#overlay .m-sec').forEach((section,i)=>{
  const title=section.querySelector(':scope > .m-sec-ttl');if(!title)return;
  const body=document.createElement('div');body.id='swj-job-section-'+i;
  [...section.childNodes].forEach(node=>{if(node!==title)body.append(node);});
  const button=document.createElement('button');button.type='button';button.className='swj-section-toggle';button.setAttribute('aria-controls',body.id);button.setAttribute('aria-expanded','true');
  const text=document.createElement('span');text.textContent=title.textContent.replace(/^[^\p{L}\p{N}]+/u,'');button.append(text);
  title.replaceWith(button);section.append(body);
  const summary=document.createElement('small');summary.className='swj-section-summary';text.append(summary);
  const summarize=()=>{const fields=[...body.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(el=>!el.disabled&&el.value);summary.textContent=fields.slice(0,2).map(el=>el.tagName==='SELECT'?el.selectedOptions[0]?.textContent:el.value).join(' · ').slice(0,100);};
  sectionSummaries.push(summarize);
  if(matchMedia('(max-width:767px)').matches&&i>1){button.setAttribute('aria-expanded','false');body.hidden=true;}
  button.onclick=()=>{const open=button.getAttribute('aria-expanded')==='true';if(open)summarize();button.setAttribute('aria-expanded',String(!open));body.hidden=open;};
 });
 const editor=document.getElementById('overlay');
 if(editor){
  editor.setAttribute('role','dialog');editor.setAttribute('aria-modal','true');editor.setAttribute('aria-labelledby','m-name');
  const name=document.getElementById('m-name');name.setAttribute('tabindex','-1');let previousFocus;
  new MutationObserver(()=>{if(editor.classList.contains('on')){sectionSummaries.forEach(update=>update());previousFocus=document.activeElement;name.focus({preventScroll:true});}else if(previousFocus?.isConnected)previousFocus.focus({preventScroll:true});}).observe(editor,{attributes:true,attributeFilter:['class']});
  editor.addEventListener('keydown',event=>{if(event.key!=='Tab')return;const focusable=[...editor.querySelectorAll('button,a[href],input,select,textarea,[tabindex="0"]')].filter(el=>!el.disabled&&el.getClientRects().length);const first=focusable[0],last=focusable.at(-1);if(event.shiftKey&&(document.activeElement===first||document.activeElement===name)){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}});
 }
 const actions=document.querySelector('#overlay .m-btns');
 if(actions){const footer=document.createElement('div');footer.className='swj-job-save-footer';actions.querySelectorAll('button').forEach(b=>{if(b.getAttribute('onclick')==='closeModal()'||b.getAttribute('onclick')==='saveCard()')footer.append(b);});actions.after(footer);}
})();
