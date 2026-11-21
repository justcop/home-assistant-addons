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

// All viewer endpoints are GET-only. The WhatsApp API's write operations are
// NEVER exposed to the viewer, even if the upstream read-only mode regresses.
export function createArchiveServer({ apiToken, port = 8100, storagePath = '/data/whatsapp', dataStore = null, allowPeer = isIngressPeer }) {
  if (!apiToken) throw new Error('Missing internal WhatsApp API token');
  return http.createServer(async (req, res) => {
    if (!allowPeer(req.socket.remoteAddress)) return send(res, 403, 'Ingress only');
    // The sole data-write route saves local display-name aliases. It cannot touch
    // WhatsApp, SQLite, media, chat state, or any upstream write endpoint.
    if (req.method === 'POST' && req.url === '/api/contact-names') {
      if (!dataStore) return send(res, 503, 'Contact name imports unavailable');
      if (req.headers['x-archive-action'] !== 'contact-import'
          || !/^application\/json(?:;|$)/i.test(req.headers['content-type'] || ''))
        return send(res, 403, 'Invalid import request');
      try {
        const chunks = [];
        let size = 0;
        for await (const chunk of req) {
          size += chunk.length;
          if (size > 3_000_000) return send(res, 413, 'Import too large');
          chunks.push(chunk);
        }
        const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        const outcome = dataStore.mergeAliases(parsed?.contacts);
        return send(res, 200, JSON.stringify(outcome), 'application/json; charset=utf-8');
      } catch (err) {
        if (err instanceof RangeError || err instanceof SyntaxError)
          return send(res, 400, 'Invalid contact data');
        console.error('Private contact name import failed:', err.message);
        return send(res, 503, 'Could not save private names');
      }
    }
    if (req.method !== 'GET' && req.method !== 'HEAD') return send(res, 405, 'Read-only archive');
    try {
      const url = new URL(req.url, 'http://archive.invalid');
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
      if (url.pathname === '/api/archive/date-range') {
        if (!dataStore) return send(res, 503, 'Archive statistics unavailable');
        if (req.method === 'HEAD') return send(res, 405, 'Method not allowed');
        return send(res, 200, JSON.stringify(dataStore.archiveDateRange()), 'application/json; charset=utf-8');
      }
      if (url.pathname === '/api/contact-names') {
        if (!dataStore) return send(res, 503, 'Private name support unavailable');
        return send(res, 200, JSON.stringify({ total: Object.keys(dataStore.aliases).length }), 'application/json; charset=utf-8');
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
      console.error('Archive viewer error:', err.message);
      if (!res.headersSent) send(res, 502, 'Archive temporarily unavailable');
      else res.destroy();
    }
  });
}
