// Actionable recovery alerts. Uses Home Assistant's native persistent
// notifications, never external ports or message data.
import fs from 'node:fs';
import path from 'node:path';
const ID='whatsapp_history_recovery_attention';
function read(file) {
  try { return JSON.parse(fs.readFileSync(file,'utf8')); }
  catch(err) { if(err.code==='ENOENT') return null; throw err; }
}
export function startHistoryRecoveryMonitor({
  root, token=process.env.SUPERVISOR_TOKEN, request=fetch, intervalMs=60_000
}) {
  let current=null, busy=false;
  async function send(action, data) {
    if(!token) return;
    const res=await request('http://supervisor/core/api/services/persistent_notification/'+action,{
      method:'POST',
      headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},
      body:JSON.stringify(data),signal:AbortSignal.timeout(10000)
    });
    if(!res.ok)throw Error('Home Assistant notification API HTTP '+res.status);
  }
  async function tick() {
    if(busy)return;
    busy=true;
    try {
      const control=read(path.join(root,'.history-recovery-resume'));
      const cp=read(path.join(root,'.history-backfill-v2.json'));
      let reason=null;
      if(control) {
        const since=Date.now()-Date.parse(control.enabledAt||'');
        if(since>300_000 && (!cp || cp.schema!==2))
          reason='Recovery was enabled but its persistent checkpoint is unavailable.';
        else if(cp && cp.schema===2 && cp.active===false && since>600_000 &&
          (Date.now()-Date.parse(cp.updatedAt||''))>600_000 &&
          cp.requestsToday<2000)
          reason='Older-message recovery stopped while enabled. Check the experimental app logs.';
      }
      // A quiet FULL history stream is normal: it is not treated as failure.
      if(reason && current!==reason){
        await send('create',{notification_id:ID,
          title:'WhatsApp Archive: history recovery needs attention',
          message:reason+' Open the WhatsApp Archive Web UI → History sync to inspect or pause. Your archive remains available.'});
        current=reason;
      } else if(!reason && current!==null) {
        await send('dismiss',{notification_id:ID});
        current=null;
      }
    } catch(err) {
      console.warn('history recovery monitor: '+String(err?.message||'check failed').slice(0,160));
    } finally {busy=false;}
  }
  const timer=setInterval(()=>{void tick();},intervalMs);
  timer.unref?.();
  void tick();
  return {tick,stop(){clearInterval(timer);}};
}
