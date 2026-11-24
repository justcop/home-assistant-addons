// Admin-Ingress-only recovery controller for the isolated history.
// This never sends WhatsApp messages or resets linked-device credentials.
import fs from 'node:fs';
import path from 'node:path';
import { readHistoryMetrics, isBulkRecentlyActive, RESUME_FILE } from './history-metrics.mjs';

const read = file => {
  try { return JSON.parse(fs.readFileSync(file,'utf8')); }
  catch(err) { if(err.code==='ENOENT') return null; throw err; }
};
function files(root) {
  return {control:path.join(root,RESUME_FILE),checkpoint:path.join(root,'.history-backfill-v2.json')};
}
export function historyRecoveryStatus(root,dataStore=null) {
  const {control,checkpoint}=files(root);
  const metrics=readHistoryMetrics(root);
  const saved=read(checkpoint);
  const backfillSelected=fs.existsSync(control);
  const bulkRecentlyActive=isBulkRecentlyActive(metrics);
  const recent=metrics.lastFullAt ? Date.now()-Date.parse(metrics.lastFullAt) : null;
  const phases={};
  if(saved?.schema===2) {
    for(const x of Object.values(saved.chats || {})) {
      const phase=typeof x?.phase==='string'?x.phase:'unknown';
      phases[phase]=(phases[phase]||0)+1;
    }
  }
  return {
    bulk:{
      state:bulkRecentlyActive?'receiving':metrics.fullBatches?'idle-possibly-complete':'not-observed',
      // Silence cannot prove completion or a rate-limit/lockout.
      completionConfirmed:false,
      lastFullAt:metrics.lastFullAt||null,
      secondsSinceFull:recent===null?null:Math.max(0,Math.floor(recent/1000)),
      fullBatches:metrics.fullBatches,
      fullMessagesDelivered:metrics.fullReceived
    },
    imports:{
      batches:metrics.batches,delivered:metrics.received,
      inserted:metrics.inserted||0,existing:metrics.existing||0,
      netUniqueChange:metrics.netUniqueChange,lastBatch:metrics.lastBatch||null,
      recentBatches:(metrics.history||[]).slice(-12)
    },
    backfill:{
      state:!backfillSelected?'paused':bulkRecentlyActive?'waiting-for-bulk':'enabled',
      selected:backfillSelected,
      started:!!saved,
      active:!!saved?.active,
      conversations:Object.keys(saved?.chats||{}).length,
      phases,requests:saved?.requests||0,requestsToday:saved?.requestsToday||0,
      updatedAt:saved?.updatedAt||null
    },
    archive:dataStore?dataStore.archiveDateRange().messages:null,
    note:'History completion is not verifiable from idle alone. Existing-row upserts include replays and updates, not only duplicates. Net unique change also reflects LID identity merges.'
  };
}
export function setHistoryRecovery(root,action,dataStore=null) {
  if(!['start','resume','pause'].includes(action)) throw new RangeError('Invalid recovery action');
  const {control}=files(root);
  if(action==='pause') fs.rmSync(control,{force:true});
  else {
    const metrics=readHistoryMetrics(root);
    if(isBulkRecentlyActive(metrics))
      return {conflict:true,error:'Bulk FULL history received within the last two minutes. Start per-chat requests after bulk delivery is idle.',status:historyRecoveryStatus(root,dataStore)};
    // Exclusive creation and atomic rename avoid torn control files.
    const tmp=control+'.'+process.pid+'.tmp';
    fs.writeFileSync(tmp,JSON.stringify({enabledAt:new Date().toISOString()})+'\n',{mode:0o600});
    fs.renameSync(tmp,control);
  }
  return {accepted:true,status:historyRecoveryStatus(root,dataStore)};
}
