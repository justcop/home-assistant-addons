// Archive admin-only pairing view. The production UI is unchanged.
// Keep an actionable panel visible before a QR is issued or if registration fails.
(() => {
  const panel=document.getElementById('pairing-banner');
  if(!panel)return;
  const feedback=document.getElementById('pairing-feedback');
  const status=document.createElement('p');
  status.id='trial-pairing-state';
  status.setAttribute('role','status');
  status.textContent='Preparing a QR code for this linked device…';
  const retry=document.createElement('button');
  retry.type='button';
  retry.className='backup-button';
  retry.id='trial-pairing-retry';
  retry.textContent='Restart pairing';
  const hint=document.createElement('p');
  hint.className='pairing-help';
  hint.textContent='The archive uses its existing stable WhatsApp companion identity. Re-pairing resets only linked-device credentials, not stored messages.';
  panel.insertBefore(status,feedback);
  panel.insertBefore(retry,feedback);
  panel.insertBefore(hint,feedback);
  let busy=false;
  async function refresh() {
    if(busy)return;
    try {
      const res=await fetch('api/pairing/status',{cache:'no-store'});
      if(!res.ok)throw Error('connection status HTTP '+res.status);
      const s=await res.json();
      panel.classList.toggle('hidden',s.connected);
      if(s.connected) {
        status.textContent='Linked and connected. The trial is collecting the history offered by WhatsApp.';
        retry.disabled=true;
      } else if(s.qrAvailable) {
        status.textContent='QR code available. Scan it in WhatsApp → Linked devices → Link a device.';
        retry.disabled=false;
      } else if(s.connection==='pairing') {
        status.textContent='Waiting for WhatsApp to issue a fresh QR. If it remains blank, use Restart pairing.';
        retry.disabled=false;
      } else {
        status.textContent='WhatsApp has not issued a QR (connection: '+String(s.connection||'unknown')+
          '). Registration may have failed; you can restart this trial pairing without deleting archived messages.';
        retry.disabled=false;
      }
    } catch(error) {
      panel.classList.remove('hidden');
      status.textContent='Pairing status unavailable: '+String(error.message||error);
      retry.disabled=true;
    }
  }
  retry.addEventListener('click',async()=>{
    if(busy)return;
    if(!window.confirm('Restart the linked-device pairing? This resets credentials, but preserves its archive and leaves the message archive untouched.'))return;
    busy=true;retry.disabled=true;
    feedback.textContent='Restarting trial pairing…';
    try {
      const res=await fetch('api/pairing/repair',{
        method:'POST',cache:'no-store',
        headers:{'Content-Type':'application/json','X-WhatsApp-Pairing-Action':'1'},
        body:JSON.stringify({confirm:'REPAIR THIS DEVICE'})
      });
      if(!res.ok)throw Error('Pairing restart failed (HTTP '+res.status+')');
      feedback.textContent='Pairing restart requested. A QR will appear here if WhatsApp accepts the registration.';
    } catch(error){feedback.textContent=String(error.message||error);}
    finally{busy=false;void refresh();}
  });
  setInterval(refresh,5000);
  void refresh();
})();
