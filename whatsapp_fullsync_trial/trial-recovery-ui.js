// Private Ingress view — never exposes messages, addresses, contact IDs or auth.
(() => {
  const host=document.getElementById('backfill-view');
  if(!host)return;
  const panel=document.createElement('section');
  panel.className='backup-card';
  panel.setAttribute('aria-label','Full history and recovery controls');
  const title=document.createElement('h3');
  title.textContent='Full history recovery — experimental trial';
  const state=document.createElement('p');
  state.setAttribute('role','status');
  const info=document.createElement('p');
  const buttons=document.createElement('div');
  buttons.style.display='flex';
  buttons.style.flexWrap='wrap';
  buttons.style.gap='10px';
  const start=document.createElement('button'),pause=document.createElement('button'),refresh=document.createElement('button');
  for(const button of [start,pause,refresh])button.className='backup-button';
  start.textContent='Start / resume older-message requests';
  pause.textContent='Pause requests';
  refresh.textContent='Refresh progress';
  buttons.append(start,pause,refresh);
  const detail=document.createElement('p');
  detail.textContent='Initial FULL history is pushed by WhatsApp, not polled one chat at a time. Reconnecting does not reliably replay it. Older-message recovery requests one conversation at a time, saves progress across restarts, prioritises direct chats and never sends messages. Silence does not prove completion.';
  const feedback=document.createElement('p');
  feedback.setAttribute('role','alert');
  panel.append(title,state,info,buttons,detail,feedback);
  host.insertBefore(panel,host.querySelector('#backfill-summary'));
  const integer=n=>Number(n||0).toLocaleString();
  const date=s=>s?new Date(s).toLocaleString():'not yet observed';
  let busy=false;
  async function load() {
    if(busy)return;
    try{
      const res=await fetch('api/trial/recovery',{cache:'no-store'});
      if(!res.ok)throw Error('Status HTTP '+res.status);
      const data=await res.json();
      state.textContent='Bulk history: '+data.bulk.state.replaceAll('-',' ') +
        ' · Last FULL batch: '+date(data.bulk.lastFullAt)+
        ' · Individual requests: '+data.backfill.state.replaceAll('-',' ');
      info.textContent='Unique readable messages stored: '+integer(data.archive?.count)+
        ' · FULL batches: '+integer(data.bulk.fullBatches)+
        ' · FULL records delivered: '+integer(data.bulk.fullMessagesDelivered)+
        ' · Net new unique rows across observed batches: '+integer(data.imports.netUniqueChange)+
        ' · Per-chat requests: '+integer(data.backfill.requests)+
        ' · Conversations checkpointed: '+integer(data.backfill.conversations)+
        '. Delivered records can overlap; net row changes are not an exact duplicate count.';
      start.disabled=data.backfill.selected || data.bulk.state==='receiving';
      pause.disabled=!data.backfill.selected;
    }catch(e){state.textContent='Recovery status unavailable: '+String(e.message||e);}
  }
  async function command(action){
    if(busy)return;
    if(action!=='pause' && !confirm('Start one-at-a-time WhatsApp requests for older messages? Bulk FULL delivery must be idle. The trial will save checkpoints and respect pacing; no messages will be sent.'))return;
    busy=true;feedback.textContent='Applying request…';
    try{
      const res=await fetch('api/trial/recovery',{method:'POST',headers:{'Content-Type':'application/json','X-WhatsApp-Recovery-Action':'1'},body:JSON.stringify({action})});
      const answer=await res.json();
      if(!res.ok)throw Error(answer.error||'HTTP '+res.status);
      feedback.textContent=action==='pause'?'Requests paused (an in-flight request may finish).':'Recovery enabled; existing checkpoints preserved.';
    }catch(e){feedback.textContent='Request not applied: '+String(e.message||e);}
    finally{busy=false;await load();}
  }
  start.addEventListener('click',()=>void command('resume'));
  pause.addEventListener('click',()=>void command('pause'));
  refresh.addEventListener('click',()=>void load());
  void load();
  setInterval(()=>{if(!host.classList.contains('hidden'))void load();},15000);
})();
