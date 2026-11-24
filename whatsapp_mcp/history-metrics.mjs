// Privacy-safe aggregate history telemetry. Never record message contents or identifiers.
import fs from 'node:fs';
import path from 'node:path';

export const METRICS_FILE = '.history-metrics.json';
export const RESUME_FILE = '.history-recovery-resume';
const read = file => { try { return JSON.parse(fs.readFileSync(file,'utf8')); } catch { return null; } };
export function observeHistoryBatch({type,received,inserted=0,existing=0,netUniqueChange,isLatest}) {
  const root = process.env.WHATSAPP_DATA_DIR;
  if (!root || !Number.isFinite(received) || !Number.isFinite(netUniqueChange)) return;
  const file = path.join(root,METRICS_FILE);
  const current = read(file) || {schema:1,batches:0,received:0,inserted:0,existing:0,netUniqueChange:0,fullBatches:0,fullReceived:0,history:[]};
  const now = new Date().toISOString();
  const entry = {time:now,type:String(type).slice(0,40),received,inserted,existing,netUniqueChange,isLatest:isLatest===true};
  current.batches++;
  current.received += received;
  current.inserted = (current.inserted||0) + inserted;
  current.existing = (current.existing||0) + existing;
  current.netUniqueChange += netUniqueChange;
  current.lastBatch = entry;
  current.history = [...(current.history || []).slice(-39),entry];
  if (type === 'FULL') {
    current.fullBatches++;
    current.fullReceived+=received;
    current.lastFullAt=now;
  }
  // Exact upsert counters distinguish inserted keys from existing-row replays.
  // The independent net row change still accounts for LID/PN consolidation.
  const temp=file+'.'+process.pid+'.tmp';
  fs.writeFileSync(temp,JSON.stringify(current)+'\n',{mode:0o600});
  fs.renameSync(temp,file);
}
export function readHistoryMetrics(root) {
  const s=read(path.join(root,METRICS_FILE));
  return s?.schema===1 ? s : {schema:1,batches:0,received:0,inserted:0,existing:0,netUniqueChange:0,fullBatches:0,fullReceived:0,history:[]};
}
export function isBulkRecentlyActive(metrics, now=Date.now()) {
  const last=Date.parse(metrics.lastFullAt || '');
  return Number.isFinite(last) && now-last < 120_000;
}
