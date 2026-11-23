import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { Readable } from 'node:stream';
import { fileURLToPath } from 'node:url';

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
export function createArchiveServer({ apiToken, port = 8100, storagePath = '/data/whatsapp', dataStore = null, cloudBackup = null, allowPeer = isIngressPeer }) {
  if (!apiToken) throw new Error('Missing internal WhatsApp API token');
  return http.createServer(async (req, res) => {
    if (!allowPeer(req.socket.remoteAddress)) return send(res, 403, 'Ingress only');
    let url;
    try { url = new URL(req.url, 'http://archive.invalid'); }
    catch { return send(res, 400, 'Invalid request URL'); }
    const isBackup = url.pathname.startsWith('/api/backup/');
    const isBackfillAction = url.pathname === '/api/archive/backfill/recheck';
    if (req.method !== 'GET' && req.method !== 'HEAD' && !(req.method === 'POST' && (isBackup || isBackfillAction)))
      return send(res, 405, 'Read-only archive');
    try {
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
        fs.writeFileSync(temp, JSON.stringify(overrides) + '\\n', { mode: 0o600 });
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
        const recent = all.filter(entry => entry.requests > 0)
          .sort((a,b) => String(b.lastAttempt || '').localeCompare(String(a.lastAttempt || '')))
          .slice(0, 60);
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
          conversations: all.length, phases: counts,
          requests: state.requests, requestsToday: state.requestsToday,
          dailyLimit: 2000, importedEstimate: state.importedEstimate, recent: chats
        }), 'application/json');
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
