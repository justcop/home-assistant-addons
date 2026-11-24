import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { Readable } from 'node:stream';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const UI_DIR = fileURLToPath(new URL('./archive-ui/', import.meta.url));
const API_HOST = '127.0.0.1';
const routes = Object.freeze({
  '/api/chats': { path: '/v1/chats', keys: ['query', 'isGroup', 'archived', 'unread', 'cursor', 'limit'] },
  '/api/contacts': { path: '/v1/contacts', keys: ['query', 'cursor', 'limit'] },
  '/api/messages': { path: '/v1/messages', keys: ['chat', 'cursor', 'limit', 'asc', 'after', 'before'] },
  '/api/search': { path: '/v1/messages/search', keys: ['q', 'chat', 'cursor', 'limit', 'after', 'before', 'sender', 'fromMe', 'kind', 'hasMedia'] }
});
const assets = Object.freeze({
  '/': ['index.html', 'text/html; charset=utf-8'],
  '/index.html': ['index.html', 'text/html; charset=utf-8'],
  '/app.js': ['app.js', 'text/javascript; charset=utf-8'],
  '/style.css': ['style.css', 'text/css; charset=utf-8']
});
const csp = "default-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'";

export function isIngressPeer(address) {
  return address === '172.30.32.2' || address === '::ffff:172.30.32.2';
}
export function archiveTarget(requestUrl) {
  let parsed;
  try { parsed = new URL(requestUrl, 'http://archive.invalid'); }
  catch { return null; }
  const route = routes[parsed.pathname];
  if (!route) return null;
  const query = new URLSearchParams();
  for (const name of route.keys) {
    const value = parsed.searchParams.get(name);
    if (value !== null) query.set(name, value);
  }
  if (query.has('limit')) {
    const limit = Number(query.get('limit'));
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) return null;
  } else query.set('limit', '80');
  if (parsed.pathname === '/api/search' && !query.get('q')?.trim()) return null;
  const q = query.toString();
  return route.path + (q ? '?' + q : '');
}
function send(res, status, data, type = 'text/plain; charset=utf-8') {
  res.writeHead(status, {
    'Content-Type': type, 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': csp,
    'Referrer-Policy': 'no-referrer'
  });
  res.end(data);
}

// WhatsApp content endpoints stay GET-only. A small, separate configuration
// controller is POST-only, protected by HA admin Ingress and a custom header;
// no path can send, edit or delete a WhatsApp message.
export function createArchiveServer({ apiToken, port = 8100, storagePath = '/data/whatsapp', dataStore = null, cloudBackup = null, configuredPhone = false, configuredPhoneNumber = '', pairingDir = '/data', defaultPairingMode = 'code', allowPeer = isIngressPeer }) {
  if (!apiToken) throw new Error('Missing internal WhatsApp API token');
  return http.createServer(async (req, res) => {
    if (!allowPeer(req.socket.remoteAddress)) return send(res, 403, 'Ingress only');
    let url;
    try { url = new URL(req.url, 'http://archive.invalid'); }
    catch { return send(res, 400, 'Invalid request URL'); }
    const isPairing = url.pathname.startsWith('/api/pairing/');
    const isBackup = url.pathname.startsWith('/api/backup/');
    const isBackfillAction = url.pathname === '/api/archive/backfill/recheck';
    if (req.method !== 'GET' && req.method !== 'HEAD' && !(req.method === 'POST' && (isBackup || isBackfillAction || isPairing)))
      return send(res, 405, 'Read-only archive');
    try {
      if (isPairing) {
        const phoneFile = path.join(pairingDir, '.whatsapp-pairing-phone');
        const codeFile = path.join(pairingDir, '.whatsapp-pairing-code.json');
        const signalFile = path.join(pairingDir, '.whatsapp-repair-request');
        const modeFile = path.join(pairingDir, '.whatsapp-pairing-mode');
        const qrFile = path.join(pairingDir, '.whatsapp-link-qr');
        let pairingMode = defaultPairingMode === 'qr' ? 'qr' : 'code';
        try { const chosen = fs.readFileSync(modeFile, 'utf8').trim(); if (['qr','code'].includes(chosen)) pairingMode = chosen; }
        catch (err) { if (err.code !== 'ENOENT') throw err; }
        if (req.method === 'GET' && url.pathname === '/api/pairing/qr') {
          // A QR is a live linked-device credential. It must never enter logs,
          // Cloudflare, a cache or unauthenticated HTTP routes.
          const healthResponse = await fetch('http://' + API_HOST + ':8097/health',
            { signal: AbortSignal.timeout(3000) });
          if (!healthResponse.ok) return send(res, 503, 'WhatsApp unavailable');
          const health = await healthResponse.json();
          if (!health.needs_pairing || pairingMode !== 'qr')
            return send(res, 404, 'QR pairing unavailable');
          let qr;
          try {
            const stat = fs.statSync(qrFile);
            if (stat.size < 20 || stat.size > 4096 || Date.now() - stat.mtimeMs > 35000)
              return send(res, 404, 'Waiting for refreshed QR');
            qr = fs.readFileSync(qrFile, 'utf8').trim();
          } catch (err) {
            if (err.code === 'ENOENT') return send(res, 404, 'QR not ready');
            throw err;
          }
          if (!qr || qr.length > 4096) return send(res, 404, 'QR unavailable');
          // No command interpolation: qrencode receives a single argv argument.
          const svg = execFileSync('qrencode', ['--type=SVG', '--output=-', '--margin=2', '--size=6', qr],
            { timeout: 4000, maxBuffer: 160000 });
          return send(res, 200, svg, 'image/svg+xml');
        }
        if (req.method === 'GET' && url.pathname === '/api/pairing/status') {
          let health;
          try {
            const result = await fetch('http://' + API_HOST + ':8097/health',
              { signal: AbortSignal.timeout(3000) });
            health = await result.json();
          } catch {
            return send(res, 503, JSON.stringify({ error: 'WhatsApp connection status unavailable' }),
              'application/json; charset=utf-8');
          }
          const connection = String(health.connection || 'unknown');
          const needsPairing = health.needs_pairing === true || connection === 'pairing' || connection === 'logged_out';
          let code = null, issuedAt = null;
          if (needsPairing) {
            try {
              const issued = JSON.parse(fs.readFileSync(codeFile, 'utf8'));
              if (typeof issued.code === 'string' && /^[A-Z0-9]{6,12}$/.test(issued.code)
                  && Number.isFinite(issued.issuedAt) && Date.now() - issued.issuedAt < 120000
                  && Date.now() >= issued.issuedAt) {
                code = issued.code;
                issuedAt = issued.issuedAt;
              }
            } catch { /* not yet issued or expired */ }
          }
          let phoneConfigured = configuredPhone;
          try { phoneConfigured = /^[1-9][0-9]{7,14}$/.test(fs.readFileSync(phoneFile, 'utf8').trim()); }
          catch { /* HA options may provide the number instead */ }
          return send(res, 200, JSON.stringify({
            connection, needsPairing, connected: connection === 'connected',
            code: pairingMode === 'code' ? code : null, issuedAt,
            pairingMode, qrAvailable: needsPairing && pairingMode === 'qr'
              && fs.existsSync(qrFile),
            phoneNumber: (() => {
              try {
                const value = fs.readFileSync(phoneFile, 'utf8').trim();
                if (/^[1-9][0-9]{7,14}$/.test(value)) return value;
              } catch { /* use HA configured number */ }
              return configuredPhoneNumber || null;
            })(), phoneConfigured,
            messages: Number(health.counts?.messages || 0),
            chats: Number(health.counts?.chats || 0)
          }), 'application/json; charset=utf-8');
        }
        if (req.method !== 'POST' ||
            !['/api/pairing/phone', '/api/pairing/repair', '/api/pairing/mode'].includes(url.pathname))
          return send(res, 405, 'Method not allowed');
        if (req.headers['x-whatsapp-pairing-action'] !== '1' ||
            !String(req.headers['content-type'] || '').startsWith('application/json'))
          return send(res, 403, 'Invalid pairing request');
        let body = '';
        for await (const chunk of req) {
          body += chunk.toString('utf8');
          if (body.length > 256) return send(res, 413, 'Request too large');
        }
        let params;
        try { params = JSON.parse(body); }
        catch { return send(res, 400, 'Invalid JSON'); }
        if (!params || typeof params !== 'object' || Array.isArray(params))
          return send(res, 400, 'Invalid pairing request');
        if (url.pathname === '/api/pairing/mode') {
          if (params.mode !== 'qr' && params.mode !== 'code')
            return send(res, 400, 'Invalid pairing mode');
          const healthResponse = await fetch('http://' + API_HOST + ':8097/health', { signal: AbortSignal.timeout(3000) });
          const health = await healthResponse.json();
          if (!health.needs_pairing)
            return send(res, 409, 'Cannot change pairing mode while already linked');
          fs.writeFileSync(modeFile, params.mode + '\n', { mode: 0o600 });
          fs.rmSync(codeFile, { force: true });
          fs.rmSync(qrFile, { force: true });
          fs.writeFileSync(path.join(pairingDir,'.whatsapp-pairing-refresh'), String(Date.now()), { mode: 0o600 });
          return send(res, 200, JSON.stringify({ mode: params.mode }), 'application/json');
        }
        if (url.pathname === '/api/pairing/phone') {
          if (typeof params.phone !== 'string' || !/^[1-9][0-9]{7,14}$/.test(params.phone))
            return send(res, 400, JSON.stringify({ error: 'Use international digits without + or spaces' }), 'application/json');
          const current = await fetch('http://' + API_HOST + ':8097/health', { signal: AbortSignal.timeout(3000) });
          const health = await current.json();
          if (health.connection === 'connected' || health.connection === 'connecting' && !health.needs_pairing)
            return send(res, 409, JSON.stringify({ error: 'Re-pair from Settings before changing a linked number' }), 'application/json');
          fs.writeFileSync(phoneFile, params.phone + '\n', { mode: 0o600 });
          console.log('WhatsApp pairing phone number configured in private web UI: ' + params.phone);
          fs.rmSync(codeFile, { force: true });
          fs.writeFileSync(path.join(pairingDir, '.whatsapp-pairing-refresh'), String(Date.now()), { mode: 0o600 });
          return send(res, 200, JSON.stringify({ saved: true }), 'application/json');
        }
        if (params.confirm !== 'REPAIR THIS DEVICE')
          return send(res, 400, JSON.stringify({ error: 'Explicit confirmation required' }), 'application/json');
        // Reset only this app's linked credentials; never touch archive messages.
        fs.writeFileSync(signalFile, String(Date.now()), { mode: 0o600, flag: 'w' });
        fs.rmSync(codeFile, { force: true });
        return send(res, 200, JSON.stringify({ requested: true }), 'application/json');
      }
      if (isBackfillAction) {
        if (req.method !== 'POST') return send(res, 405, 'Method not allowed');
        // Admin-only ingress with a custom JSON header; no public API or
        // cross-origin form can invoke this. Only the backfill checkpoint is
        // affected; WhatsApp messages, read receipts and auth are untouched.
        if (req.headers['x-whatsapp-backfill-action'] !== '1' ||
            !String(req.headers['content-type'] || '').startsWith('application/json'))
          return send(res, 403, 'Invalid history recovery request');
        let body = '';
        for await (const chunk of req) {
          body += chunk.toString('utf8');
          if (body.length > 512) return send(res, 413, 'Request too large');
        }
        let params;
        try { params = JSON.parse(body); }
        catch { return send(res, 400, 'Invalid JSON'); }
        if (!params || typeof params !== 'object' || Array.isArray(params) ||
            typeof params.chatId !== 'string' || params.chatId.length > 300 ||
            params.chatId.length === 0 || params.action !== 'recheck')
          return send(res, 400, 'Invalid request');
        const checkpointPath = path.join(storagePath, '.history-backfill-v2.json');
        let state;
        try { state = JSON.parse(fs.readFileSync(checkpointPath, 'utf8')); }
        catch { return send(res, 503, 'History recovery unavailable'); }
        if (state.schema !== 2 || !state.chats || !Object.hasOwn(state.chats, params.chatId))
          return send(res, 404, 'Conversation not found');
        const entry = state.chats[params.chatId];
        if (entry.phase !== 'likely_complete' && !entry.manualRecheck)
          return send(res, 409, 'Only probably complete conversations can be reopened');
        const overrideFile = path.join(storagePath, '.history-backfill-overrides-v1.json');
        let overrides = {};
        try { overrides = JSON.parse(fs.readFileSync(overrideFile, 'utf8')); }
        catch (err) { if (err.code !== 'ENOENT') return send(res, 503, 'Manual review state unavailable'); }
        if (!overrides || typeof overrides !== 'object' || Array.isArray(overrides))
          return send(res, 503, 'Invalid manual review state');
        Object.defineProperty(overrides, params.chatId, {
          value: true, enumerable: true, configurable: true, writable: true
        });
        const temp = overrideFile + '.' + process.pid + '.tmp';
        fs.writeFileSync(temp, JSON.stringify(overrides) + '\n', { mode: 0o600 });
        fs.renameSync(temp, overrideFile);
        return send(res, 200, JSON.stringify({ accepted: true, state: 'recheck_requested' }), 'application/json');
      }
      if (isBackup) {
        if (!cloudBackup) return send(res, 503, 'Cloud backup is unavailable');
        if (req.method === 'POST') {
          // A cross-origin HTML form cannot send this custom JSON header; no
          // permissive CORS/preflight route exists. Ingress itself checks HA admin.
          if (req.headers['x-whatsapp-backup-action'] !== '1' ||
              !String(req.headers['content-type'] || '').startsWith('application/json'))
            return send(res, 403, 'Invalid backup request');
          let body = '';
          for await (const chunk of req) {
            body += chunk.toString('utf8');
            if (body.length > 1024) return send(res, 413, 'Request too large');
          }
          const params = body ? JSON.parse(body) : {};
          if (!params || typeof params !== 'object' || Array.isArray(params))
            return send(res, 400, 'Invalid backup request body');
          let result;
          switch (url.pathname) {
            case '/api/backup/configure':
              result = cloudBackup.configureGoogleClient(params.clientId, params.clientSecret); break;
            case '/api/backup/time':
              result = cloudBackup.setBackupTime(params.time); break;
            case '/api/backup/authorize': result = await cloudBackup.beginAuthorisation(); break;
            case '/api/backup/poll': result = await cloudBackup.pollAuthorisation(); break;
            case '/api/backup/recovery-key': result = cloudBackup.recoveryKey(); break;
            case '/api/backup/confirm-key': result = cloudBackup.confirmRecoveryKey(); break;
            case '/api/backup/enable':
              if (typeof params.enabled !== 'boolean') return send(res, 400, 'Expected enabled boolean');
              result = cloudBackup.setEnabled(params.enabled); break;
            case '/api/backup/run': result = cloudBackup.runNow(); break;
            default: return send(res, 404, 'Not found');
          }
          return send(res, 200, JSON.stringify(result), 'application/json; charset=utf-8');
        }
        if (req.method !== 'GET') return send(res, 405, 'Method not allowed');
        if (url.pathname === '/api/backup/status')
          return send(res, 200, JSON.stringify(cloudBackup.status()), 'application/json; charset=utf-8');
        if (url.pathname === '/api/backup/download') {
          const { response, filename } = await cloudBackup.encryptedDownload();
          res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Cache-Control': 'no-store',
            'Content-Disposition': 'attachment; filename="' + filename.replace(/[^a-zA-Z0-9_.-]/g, '') + '"',
            'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': csp,
            'Referrer-Policy': 'no-referrer' });
          if (response.body) Readable.fromWeb(response.body).pipe(res);
          else res.end();
          return;
        }
        return send(res, 404, 'Not found');
      }
      const asset = assets[url.pathname];
      if (asset) {
        const [filename, mime] = asset;
        const bytes = fs.readFileSync(path.join(UI_DIR, filename));
        res.writeHead(200, { 'Content-Type': mime, 'Content-Length': bytes.length,
          'Cache-Control': 'no-store', 'Content-Security-Policy': csp,
          'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' });
        return res.end(req.method === 'HEAD' ? undefined : bytes);
      }
      if (url.pathname === '/api/storage') {
        return send(res, 200, JSON.stringify({ location: (storagePath.startsWith('/share/') || storagePath.startsWith('/media/')) ? 'STORAGE drive' : 'Home Assistant app storage' }), 'application/json');
      }
      // Private read-only progress: the checkpoint contains internal chat IDs,
      // but this ingress response exposes only display names and aggregates.
      if (url.pathname === '/api/archive/backfill') {
        if (req.method !== 'GET') return send(res, 405, 'Method not allowed');
        const file = path.join(storagePath, '.history-backfill-v2.json');
        let state;
        try { state = JSON.parse(fs.readFileSync(file, 'utf8')); }
        catch (err) {
          if (err.code === 'ENOENT')
            return send(res, 200, JSON.stringify({state:'not_started'}), 'application/json');
          return send(res, 503, 'History progress unavailable');
        }
        if (state.schema !== 2 || !state.chats || typeof state.chats !== 'object')
          return send(res, 503, 'History progress invalid');
        let overrides = {};
        try { overrides = JSON.parse(fs.readFileSync(path.join(storagePath, '.history-backfill-overrides-v1.json'), 'utf8')); }
        catch (err) { if (err.code !== 'ENOENT') return send(res, 503, 'Manual review state unavailable'); }
        const all = Object.values(state.chats);
        const counts = {};
        for (const entry of all) counts[entry.phase] = (counts[entry.phase] || 0) + 1;
        const view = url.searchParams.get('view') === 'probably_complete'
          ? 'probably_complete' : 'recent';
        const recent = all.filter(entry => view === 'probably_complete'
          ? entry.phase === 'likely_complete' && overrides[entry.id] !== true && entry.manualRecheck !== true
          : entry.requests > 0)
          .sort((a,b) => String(b.lastAttempt || '').localeCompare(String(a.lastAttempt || '')))
          .slice(0, view === 'probably_complete' ? 1000 : 60);
        const chats = recent.map(entry => {
          const metadata = dataStore?.chatNameStmt?.get(entry.id);
          const name = dataStore?.displayName?.(entry.id, metadata?.name, !!metadata?.isGroup);
          const manualRecheck = entry.manualRecheck === true || overrides[entry.id] === true;
          const phase = manualRecheck && entry.phase === 'likely_complete' ? 'recheck_requested' : entry.phase;
          const count = dataStore?.chatMessageCountStmt?.get(entry.id)?.total ?? null;
          return { id: entry.id, name: String(name || (metadata?.isGroup ? 'Group' : 'Conversation')),
            phase, manualRecheck, completionBasis: entry.completionBasis || null, requests: entry.requests,
            archivedMessages: count, importedEstimate: entry.importedEstimate,
            oldestTimestamp: entry.earliest, lastAttempt: entry.lastAttempt || null };
        });
        return send(res, 200, JSON.stringify({
          state: state.active ? 'running' : 'paused',
          updatedAt: state.updatedAt, startedAt: state.startedAt,
          conversations: all.length, phases: counts, view,
          requests: state.requests, requestsToday: state.requestsToday,
          dailyLimit: 2000, importedEstimate: state.importedEstimate, recent: chats
        }), 'application/json');
      }
      if (url.pathname === '/api/archive/filter') {
        if (!dataStore) return send(res,503,'Archive search unavailable');
        if (req.method !== 'GET') return send(res,405,'Method not allowed');
        try {
          return send(res,200,JSON.stringify(dataStore.searchArchive(url.searchParams)),'application/json; charset=utf-8');
        } catch(err) {
          if(err instanceof RangeError) return send(res,400,'Invalid search filters');
          throw err;
        }
      }
      if (url.pathname === '/api/archive/date-range') {
        if (!dataStore) return send(res, 503, 'Archive statistics unavailable');
        if (req.method === 'HEAD') return send(res, 405, 'Method not allowed');
        return send(res, 200, JSON.stringify(dataStore.archiveDateRange()), 'application/json; charset=utf-8');
      }
      if (url.pathname === '/api/archive/unknown-types') {
        if (!dataStore) return send(res, 503, 'Archive diagnostics unavailable');
        if (req.method === 'HEAD') return send(res, 405, 'Method not allowed');
        const counts = await dataStore.classifyEmptyOther();
        return send(res, 200, JSON.stringify(counts), 'application/json; charset=utf-8');
      }
      if (dataStore && (url.pathname === '/api/chats' || url.pathname === '/api/messages')) {
        if (req.method === 'HEAD') return send(res, 405, 'Method not allowed');
        try {
          const answer = url.pathname === '/api/chats' ? dataStore.listChats(url.searchParams)
            : dataStore.listMessages(url.searchParams);
          return send(res, 200, JSON.stringify(answer), 'application/json; charset=utf-8');
        } catch (err) {
          if (err instanceof RangeError) return send(res, 400, 'Invalid archive query');
          throw err;
        }
      }
      if (req.method === 'HEAD') return send(res, 405, 'Method not allowed');
      let target = archiveTarget(req.url);
      let media = false;
      if (url.pathname === '/api/media') {
        const chat = url.searchParams.get('chat');
        const id = url.searchParams.get('id');
        if (!chat || !id || chat.length > 300 || id.length > 300) return send(res, 400, 'Invalid attachment reference');
        target = '/v1/media/' + encodeURIComponent(chat) + '/' + encodeURIComponent(id);
        media = true;
      }
      if (!target) return send(res, 404, 'Not found');
      const response = await fetch('http://' + API_HOST + ':8097' + target, {
        headers: { Authorization: 'Bearer ' + apiToken },
        signal: AbortSignal.timeout(media ? 120000 : 20000)
      });
      if (!response.ok) {
        return send(res, response.status >= 500 ? 502 : response.status, 'Archive request unavailable');
      }
      if (!media && url.pathname === '/api/search' && dataStore) {
        const page = await response.json();
        page.items = (page.items || [])
          .filter(hit => hit.chat !== '13135550002@s.whatsapp.net'
            && hit.kind !== 'system' && !(hit.kind === 'other'
            && !hit.text && !hit.transcript && !hit.media))
          .map(hit => dataStore.renameHit(hit));
        return send(res, 200, JSON.stringify(page), 'application/json; charset=utf-8');
      }
      const headers = {
        'Content-Type': media ? 'application/octet-stream' : 'application/json; charset=utf-8',
        'Content-Security-Policy': csp, 'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'
      };
      if (media) headers['Content-Disposition'] = 'attachment; filename="whatsapp-attachment"';
      res.writeHead(200, headers);
      if (response.body) Readable.fromWeb(response.body).pipe(res);
      else res.end();
    } catch (err) {
      // No message data, OAuth tokens, encryption keys or file identifiers
      // are included in backup error responses or routine access logs.
      if (isBackup) {
        if (!res.headersSent) return send(res, 400, JSON.stringify({ error: String(err.message || 'Backup action failed').slice(0, 230) }), 'application/json; charset=utf-8');
        return res.destroy();
      }
      console.error('Archive viewer error:', err.message);
      if (!res.headersSent) send(res, 502, 'Archive temporarily unavailable');
      else res.destroy();
    }
  });
}
