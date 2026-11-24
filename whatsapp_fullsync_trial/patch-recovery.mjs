// Extra trial-only runtime patches applied AFTER patch-experiment.mjs.
// Each anchor must match once: unexpected upstream code fails image build.
import fs from 'node:fs';
const launcher='/opt/whatsapp-addon/run.mjs';
const ingest='/opt/whatsapp-api/dist/whatsapp/ingest.js';
const messages='/opt/whatsapp-api/dist/db/messages.js';
const worker='/opt/whatsapp-api/dist/history-backfill.js';
const server='/opt/whatsapp-addon/archive-server.mjs';
const ui='/opt/whatsapp-addon/archive-ui/index.html';
function patch(file,regex,replacement,label) {
  const input=fs.readFileSync(file,'utf8');
  const matches=[...input.matchAll(new RegExp(regex.source,regex.flags.includes('g')?regex.flags:regex.flags+'g'))];
  if(matches.length!==1) throw Error(label+': expected exactly one anchor, got '+matches.length);
  fs.writeFileSync(file,input.replace(regex,replacement));
}
patch(launcher,/WHATSAPP_HISTORY_BACKFILL:\s*'0'/,
  "WHATSAPP_HISTORY_BACKFILL: '1'",'start gated history worker');
const prior=fs.readFileSync(ingest,'utf8');
if(!prior.includes('import ')) throw Error('Expected ES module history ingester');
fs.writeFileSync(ingest,"import { observeHistoryBatch } from '/opt/whatsapp-addon/trial-observer.mjs';\n"+prior);
// Capture exact upsert results within history batches. This distinguishes
// newly inserted keys from existing rows, even when identity merges change
// the net SQLite row count. Other/live upserts are left uncounted.
patch(ingest,/const \{\s*db,\s*chats,\s*contacts,\s*messages,\s*reactions,\s*logger,\s*selfId\s*\} = deps;/,
  [
    'const { db, chats, contacts, messages, reactions, logger, selfId } = deps;',
    '  const trialBatchContext = { current:null };',
    '  const trialTrackedUpsert = input => {',
    '    const inserted = messages.upsert(input);',
    '    if (trialBatchContext.current) {',
    '      if (inserted) trialBatchContext.current.inserted++;',
    '      else trialBatchContext.current.existing++;',
    '    }',
    '    return inserted;',
    '  };'
  ].join('\n'),'trial exact upsert counting scope');
patch(ingest,/const inserted = messages\.upsert\(\{/,
  'const inserted = trialTrackedUpsert({',
  'count inserted and existing historical message keys');
patch(ingest,/applyHistory\(h\);/,
  [
    'const beforeTrialRows = messages.count();',
    '        const trialBatchCounters = {inserted:0,existing:0};',
    '        trialBatchContext.current = trialBatchCounters;',
    '        try { applyHistory(h); } finally { trialBatchContext.current = null; }',
    '        const trialType = Object.entries(proto.HistorySync.HistorySyncType).find(([,value]) => value === h.syncType)?.[0] || String(h.syncType);',
    '        observeHistoryBatch({type:trialType,received:h.messages.length,inserted:trialBatchCounters.inserted,existing:trialBatchCounters.existing,netUniqueChange:messages.count()-beforeTrialRows,isLatest:h.isLatest});'
  ].join('\n'),'reconcile exact history upserts');
patch(messages,/text = COALESCE\(excluded\.text, messages\.text\),/,
  'text = CASE WHEN messages.deleted_ts IS NOT NULL THEN NULL ELSE COALESCE(excluded.text, messages.text) END,',
  'preserve revoked text tombstone on replay');
patch(worker,/while \(connected && !halted\) \{/,
  [
    'while (connected && !halted) {',
    "        let bulkRecentlyActive = false;",
    "        try {",
    "          const metric = JSON.parse(fs.readFileSync(path.join(dataDir,'.trial-history-metrics.json'),'utf8'));",
    "          bulkRecentlyActive = !!metric.lastFullAt && Date.now() - Date.parse(metric.lastFullAt) < 120000;",
    "        } catch { /* not observed */ }",
    "        if (!fs.existsSync(path.join(dataDir,'.trial-recovery-resume')) || bulkRecentlyActive) {",
    "          await sleep(5000);",
    "          continue;",
    "        }"
  ].join('\n'),'gate one-chat-at-a-time backfill');
// Observe real per-request HTTP/Boom codes (without message content, JIDs or
// authentication details). Silence is not a server-generated 429.
patch(worker,/socket\.fetchMessageHistory\(\s*BATCH\s*,\s*old\.key\s*,\s*old\.ts\s*\*\s*1000\s*\)/,
  [
    'socket.fetchMessageHistory(BATCH,old.key,old.ts*1000).catch(err => {',
    '            const code = Number(err?.output?.statusCode ?? err?.status ?? err?.response?.status);',
    "            logger.warn({status:Number.isFinite(code)?code:null},'trial: history API request rejected');",
    '            throw err;',
    '          })'
  ].join('\n'),'request-level protocol error classification');
// Delay across all chats after a failed history request. The response may be
// silent rather than a real 429: do not invent a server-provided retry time.
patch(worker,/const newer = anchor\(c\.id\),/,
  [
    "if (failed) {",
    "          const delaySeconds = Math.min(120, 5 * 2 ** Math.min(c.failures, 5));",
    "          logger.warn({delaySeconds},'trial: failed history request backed off');",
    "          await sleep(delaySeconds*1000);",
    "        }",
    "        const newer = anchor(c.id),"
  ].join('\n'),'back off on failed history requests');
patch(server,/import fs from 'node:fs';/,
  "import fs from 'node:fs';\nimport { trialRecoveryStatus, setTrialRecovery } from './trial-recovery.mjs';",
  'private recovery controller import');
patch(server,/'\/style\.css': \['style\.css', 'text\/css; charset=utf-8'\]/,
  "'/style.css': ['style.css', 'text/css; charset=utf-8'],\n  '/trial-recovery-ui.js': ['trial-recovery-ui.js', 'text/javascript; charset=utf-8']",
  'trial recovery UI asset');
patch(server,/const isBackfillAction = url.pathname === '\/api\/archive\/backfill\/recheck';/,
  "const isBackfillAction = url.pathname === '/api/archive/backfill/recheck';\n    const isTrialRecovery = url.pathname === '/api/trial/recovery';",
  'trial recovery route');
patch(server,/\(isBackup \|\| isBackfillAction \|\| isPairing\)/,
  '(isBackup || isBackfillAction || isPairing || isTrialRecovery)',
  'trial recovery HTTP method guard');
patch(server,/if \(isPairing\) \{/,
  [
    'if (isTrialRecovery) {',
    "        if(req.method === 'GET') return send(res,200,JSON.stringify(trialRecoveryStatus(storagePath,dataStore)),'application/json');",
    "        if(req.method !== 'POST' || req.headers['x-whatsapp-recovery-action'] !== '1' ||",
    "          !String(req.headers['content-type'] || '').startsWith('application/json'))",
    "          return send(res,403,'Invalid recovery request');",
    "        let body='';",
    "        for await (const chunk of req) { body += chunk.toString('utf8'); if(body.length>256) return send(res,413,'Request too large'); }",
    "        let params;",
    "        try {params=JSON.parse(body);} catch {return send(res,400,'Invalid JSON');}",
    "        if(!params || typeof params!=='object' || Array.isArray(params) || !['start','pause','resume'].includes(params.action))",
    "          return send(res,400,'Invalid action');",
    "        const result=setTrialRecovery(storagePath,params.action,dataStore);",
    "        return send(res,result.conflict?409:200,JSON.stringify(result),'application/json');",
    "      }",
    "      if (isPairing) {"
  ].join('\n'),'private history recovery handler');
patch(ui,/<\/body>/,
  '<script defer src="trial-recovery-ui.js"></script>\n</body>',
  'trial controls UI');
// A reconnect which arrives while the old worker is still draining its
// previous request must clear the halted flag. Without this, the prior
// worker can exit after the new connected callback was missed by 'running'.
patch(worker,/if \(!connected\)\s*halted = true;/,
  'if (!connected) halted = true;\n    else halted = false;',
  'resume worker on transient reconnect');
// A manual recheck must work repeatedly, including for stalled conversations.
// Use a per-action nonce (timestamp), not an irrevocable boolean. The worker
// acknowledges the nonce in its checkpoint before the next request.
patch(worker,/if \(selected !== true\)\s*continue;/,
  'if (selected !== true && !Number.isSafeInteger(selected)) continue;',
  'accept timestamped recheck commands');
patch(worker,/if \(!item\.manualRecheck\) \{/,
  [
    'if (!item.manualRecheck || (Number.isSafeInteger(selected) && item.recheckToken !== selected)) {',
    '          if (Number.isSafeInteger(selected)) item.recheckToken = selected;'
  ].join('\n'),'repeatable manual retries');
patch(worker,/if \(item\.phase === "likely_complete"\) \{/,
  'if (item.phase === "likely_complete" || item.phase === "stalled") {',
  'reopen stalled conversations manually');
patch(server,/if \(entry\.phase !== 'likely_complete' && !entry\.manualRecheck\)/,
  "if (entry.phase !== 'likely_complete' && entry.phase !== 'stalled' && !entry.manualRecheck)",
  'permit stalled-chat retry');
patch(server,/value: true, enumerable: true, configurable: true, writable: true/,
  'value: Date.now(), enumerable: true, configurable: true, writable: true',
  'timestamp manual retry override');
patch(server,/const manualRecheck = entry\.manualRecheck === true \|\| overrides\[entry\.id\] === true;/,
  "const manualRecheck = Number.isSafeInteger(overrides[entry.id]) ? entry.recheckToken !== overrides[entry.id] : (overrides[entry.id] === true && entry.manualRecheck !== true);",
  'show only pending manual retries');
const appJs='/opt/whatsapp-addon/archive-ui/app.js';
patch(appJs,/if \(chat\.phase === 'likely_complete' && !chat\.manualRecheck\) \{/,
  "if (chat.phase === 'likely_complete' || chat.phase === 'stalled') {",
  'show manual retry for stalled chat');
patch(appJs,/const button = el\('button','backfill-recheck','Not complete — keep searching'\);/,
  "const button = el('button','backfill-recheck',chat.phase === 'stalled' ? 'Retry stalled conversation' : 'Not complete — keep searching');",
  'label retry accurately');
// Native HA alert when the opted-in worker stops or has no checkpoint.
// No notification is emitted merely because FULL history becomes idle.
const runner='/opt/whatsapp-addon/run.mjs';
patch(runner,/import \{ createArchiveServer \} from '\.\/archive-server\.mjs';/,
  "import { createArchiveServer } from './archive-server.mjs';\nimport { startTrialRecoveryMonitor } from './trial-alerts.mjs';",
  'trial alert monitor import');
patch(runner,/archive\.listen\(ARCHIVE_PORT, '0\.0\.0\.0', \(\) => \{/,
  "startTrialRecoveryMonitor({root:storage.dataDir});\n  archive.listen(ARCHIVE_PORT, '0.0.0.0', () => {",
  'launch private alert monitor');
console.log('Trial recovery controls, dedup tombstone safeguard and telemetry installed.');
