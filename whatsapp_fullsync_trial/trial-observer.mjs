// Trial-only aggregate history telemetry. Never record message contents or identifiers.
import fs from 'node:fs';
import path from 'node:path';

export const METRICS_FILE = '.trial-history-metrics.json';
export const RESUME_FILE = '.trial-recovery-resume';
const read = file => { try { return JSON.parse(fs.readFileSync(file,'utf8')); } catch { return null; } };
export function observeHistoryBatch({type,received,netUniqueChange,isLatest}) {
  const root = process.env.WHATSAPP_DATA_DIR;
  if (!root || !Number.isFinite(received) || !Number.isFinite(netUniqueChange)) return;
  const file = path.join(root,METRICS_FILE);
  const current = read(file) || {schema:1,batches:0,received:0,netUniqueChange:0,fullBatches:0,fullReceived:0,history:[]};
  const now = new Date().toISOString();
  const entry = {time:now,type:String(type).slice(0,40),received,netUniqueChange,isLatest:isLatest===true};
  current.batches++;
  current.received += received;
  current.netUniqueChange += netUniqueChange;
  current.lastBatch = entry;
  current.history = [...(current.history || []).slice(-39),entry];
  if (type === 'FULL') {
    current.fullBatches++;
    current.fullReceived+=received;
    current.lastFullAt=now;
  }
  // A database snapshot before and after ingest yields the *net* change, not
  // an exact inserted-vs-duplicate split: LID/PN consolidation can remove rows.
  const temp=file+'.'+process.pid+'.tmp';
  fs.writeFileSync(temp,JSON.stringify(current)+'\n',{mode:0o600});
  fs.renameSync(temp,file);
}
export function readTrialMetrics(root) {
  const s=read(path.join(root,METRICS_FILE));
  return s?.schema===1 ? s : {schema:1,batches:0,received:0,netUniqueChange:0,fullBatches:0,fullReceived:0,history:[]};
}
export function isBulkRecentlyActive(metrics, now=Date.now()) {
  const last=Date.parse(metrics.lastFullAt || '');
  return Number.isFinite(last) && now-last < 120_000;
}
