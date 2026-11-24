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
if(!prior.startsWith('import ')) throw Error('Expected ES module history ingester');
fs.writeFileSync(ingest,"import { observeHistoryBatch } from '/opt/whatsapp-addon/trial-observer.mjs';\n"+prior);
patch(ingest,/applyHistory\(h\);/,
  [
    'const beforeTrialRows = messages.count();',
    '        applyHistory(h);',
    '        const trialType = Object.entries(proto.HistorySync.HistorySyncType).find(([,value]) => value === h.syncType)?.[0] || String(h.syncType);',
    '        observeHistoryBatch({type:trialType,received:h.messages.length,netUniqueChange:messages.count()-beforeTrialRows,isLatest:h.isLatest});'
  ].join('\n'),'reconcile imported history');
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
patch(worker,/} catch \{\s*failed = true;\s*}/,
  [
    '} catch (err) {',
    '          failed = true;',
    '          const status = Number(err?.output?.statusCode ?? err?.status ?? err?.response?.status);',
    "          if (status === 429 || status === 503) {",
    "            const delaySeconds = Math.min(1800,Math.max(30,Number(err?.response?.headers?.get?.('retry-after')) || 120));",
    "            logger.warn({status,delaySeconds},'trial: upstream history request backed off');",
    '            await sleep(delaySeconds*1000);',
    '          }',
    '        }'
  ].join('\n'),'back off on real rate-limit or unavailable responses');
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
console.log('Trial recovery controls, dedup tombstone safeguard and telemetry installed.');
