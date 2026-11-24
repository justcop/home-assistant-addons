import fs from 'node:fs';
import http from 'node:http';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createArchiveServer } from './archive-server.mjs';
import { ArchiveData } from './archive-data.mjs';
import { pruneMetaAI } from './prune-meta-ai.mjs';
import { restoreSelfLid } from './restore-self-lid.mjs';
import { pruneProtocolNoise } from './protocol-retention.mjs';
import { configureWhatsAppStorage } from './storage.mjs';
import { runScheduledArchiveMerge } from './archive-merge.mjs';
import { WhatsAppCloudBackup } from './cloud-backup.mjs';
import { HomeAssistantBackupAlerts } from './home-assistant-alerts.mjs';
import { WhatsAppMaintenanceMonitor } from './maintenance-monitor.mjs';

const ROOT = '/data';
const API_PORT = 8097;
const MCP_PORT = 8098;
const PUBLIC_PORT = 8099;
const ARCHIVE_PORT = 8100;
const UPSTREAM = '127.0.0.1';
const maxJwtBytes = 12_000;

function decodeBase64url(value) {
  return Buffer.from(value, 'base64url').toString('utf8');
}

// The origin MUST verify Cloudflare's signed assertion itself; a request that
// merely claims to have passed through Cloudflare is not authenticated.
export function verifyAccessJwt(token, settings, jwks, now = Date.now()) {
  if (typeof token !== 'string' || token.length > maxJwtBytes) return false;
  const parts = token.split('.');
  if (parts.length !== 3) return false;
  try {
    const header = JSON.parse(decodeBase64url(parts[0]));
    const claims = JSON.parse(decodeBase64url(parts[1]));
    if (header.alg !== 'RS256' || typeof header.kid !== 'string') return false;
    const jwk = jwks?.keys?.find(key =>
      key.kid === header.kid && key.kty === 'RSA' && key.use !== 'enc');
    if (!jwk) return false;
    const publicKey = crypto.createPublicKey({ key: jwk, format: 'jwk' });
    const validSignature = crypto.verify('RSA-SHA256',
      Buffer.from(parts[0] + '.' + parts[1]), publicKey,
      Buffer.from(parts[2], 'base64url'));
    if (!validSignature) return false;
    const team = settings.access_team_domain.toLowerCase();
    const iss = 'https://' + team;
    const aud = claims.aud;
    const audienceMatches = Array.isArray(aud)
      ? aud.includes(settings.access_audience)
      : aud === settings.access_audience;
    const seconds = Math.floor(now / 1000);
    return claims.iss === iss && audienceMatches
      && Number.isInteger(claims.exp) && claims.exp > seconds
      && Number.isInteger(claims.iat) && claims.iat <= seconds + 60
      && (!('nbf' in claims) || (Number.isInteger(claims.nbf) && claims.nbf <= seconds))
      && typeof claims.email === 'string'
      && claims.email.toLowerCase() === settings.allowed_email.toLowerCase();
  } catch {
    return false;
  }
}

function loadOptions() {
  const raw = JSON.parse(fs.readFileSync(path.join(ROOT, 'options.json'), 'utf8'));
  const options = Object.fromEntries(['phone_number', 'access_team_domain', 'access_audience', 'allowed_email', 'storage_path', 'backup_google_client_id', 'backup_google_client_secret', 'backup_time_uk']
    .map(key => [key, String(raw[key] ?? '').trim()]));
  options.access_team_domain = options.access_team_domain.replace(/^https?:\/\//i, '').replace(/\/$/, '').toLowerCase();
  options.allowed_email = options.allowed_email.toLowerCase();
  if (options.access_team_domain && !/^[a-z0-9.-]+\.cloudflareaccess\.com$/.test(options.access_team_domain))
    throw new Error('access_team_domain must be your <team>.cloudflareaccess.com hostname');
  if (options.phone_number && !/^[1-9][0-9]{7,14}$/.test(options.phone_number))
    throw new Error('phone_number must be 8-15 international digits without + or leading 0');
  return options;
}

function loadToken(name) {
  const dir = path.join(ROOT, '.whatsapp-mcp-secrets');
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  fs.chmodSync(dir, 0o700);
  const filename = path.join(dir, name);
  try {
    return fs.readFileSync(filename, 'utf8').trim();
  } catch (err) {
    if (err.code !== 'ENOENT') throw err;
    const token = crypto.randomBytes(32).toString('hex');
    fs.writeFileSync(filename, token + '\n', { mode: 0o600, flag: 'wx' });
    return token;
  }
}

export function childEnvironment(env, parent = process.env) {
  // SUPERVISOR_TOKEN is an unrestricted Home Assistant admin credential.
  // Never grant it to the third-party WhatsApp API or MCP children.
  const safe = { ...parent, ...env };
  for (const key of ['SUPERVISOR_TOKEN', 'HASSIO_TOKEN', 'SUPERVISOR_API', 'HASSIO_API']) delete safe[key];
  return safe;
}
function startChild(label, executable, env) {
  const child = spawn(process.execPath, [executable], {
    cwd: path.dirname(path.dirname(executable)),
    env: childEnvironment(env),
    stdio: 'inherit',
    uid: process.getuid(),
    gid: process.getgid()
  });
  child.on('error', err => { console.error(label + ' failed:', err.message); process.exitCode = 1; shutdown(); });
  child.on('exit', (code, signal) => {
    if (!stopping) { console.error(label + ' stopped unexpectedly:', code, signal); process.exitCode = 1; shutdown(); }
  });
  return child;
}

let api, mcp, gateway, archive, archiveData, cloudBackup, maintenanceMonitor;
let stopping = false;
function shutdown() {
  if (stopping) return;
  stopping = true;
  if (gateway) gateway.close();
  if (archive) archive.close();
  if (cloudBackup) cloudBackup.close();
  if (maintenanceMonitor) maintenanceMonitor.close();
  if (archiveData) archiveData.close();
  if (mcp) mcp.kill('SIGTERM');
  if (api) api.kill('SIGTERM');
  setTimeout(() => process.exit(process.exitCode || 0), 2000).unref();
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

async function waitForServer(port, child) {
  for (let i = 0; i < 60; i++) {
    if (child.exitCode !== null || child.signalCode !== null) throw new Error('child exited');
    try {
      const res = await fetch('http://' + UPSTREAM + ':' + port + '/health',
        { signal: AbortSignal.timeout(1000) });
      // HTTP is listening even when the account has not yet paired.
      // An MCP health response can be unhealthy (503) until WhatsApp pairs.
      if (res.status === 200 || res.status === 503) return;
    } catch { /* not listening yet */ }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error('port ' + port + ' did not become healthy');
}

async function main() {
  const opts = loadOptions();
  const DEFAULT_PAIRING_MODE = 'code';
  const storage = configureWhatsAppStorage(opts.storage_path);
  const apiToken = loadToken('api-token');
  const mcpToken = loadToken('mcp-token');
  const backupStateDir = path.join(ROOT, '.whatsapp-cloud-backup');
  fs.mkdirSync(backupStateDir, { recursive: true, mode: 0o700 });
  fs.chmodSync(backupStateDir, 0o700);
  // The app starts as root on Supervisor; the WhatsApp database and listeners
  // run as the unprivileged Node user after initial data directory setup.
  // The external archive must not have a recreated /data/whatsapp rollback
  // copy. Only chown it when the user explicitly selected internal storage.
  for (const dir of [ROOT, ...(storage.external ? [] : [storage.dataDir]), path.join(ROOT, '.whatsapp-mcp-secrets'), backupStateDir]) {
    fs.chownSync(dir, 1000, 1000);
  }
  if (process.getuid?.() === 0) { process.setgid(1000); process.setuid(1000); }
  // Verify write access as the actual API user, not only as root, before
  // allowing an external SQLite database to be opened.
  const writeProbe = path.join(storage.dataDir, '.permission-test-' + process.pid);
  fs.writeFileSync(writeProbe, 'ok', { flag: 'wx', mode: 0o600 });
  fs.unlinkSync(writeProbe);
  // Execute an explicitly scheduled content-only trial merge before WhatsApp opens the DB.
  // Failures leave production untouched and are recorded for the private admin UI.
  const consolidation = runScheduledArchiveMerge(storage.dataDir);
  if (consolidation) console.log('WhatsApp archive merge outcome: '+JSON.stringify(consolidation));
  // One-time, non-destructive reversal of the redundant self-LID quarantine.
  const selfLidRestore = await restoreSelfLid(storage.dataDir, opts.phone_number);
  console.log('Self-LID chat restore: '+JSON.stringify(selfLidRestore));
  // Prune only this verified Meta AI JID before the WhatsApp writer starts.
  // The verified legacy quarantine cleanup ran once in 0.3.9.
  // Do not purge ambiguous own-LID messages on future restarts.
  const pruned = pruneMetaAI(storage.dataDir);
  if (pruned) console.log('Removed ' + pruned + ' Meta AI messages from the local archive.');
  const protocol = await pruneProtocolNoise(storage.dataDir);
  console.log('WhatsApp protocol retention cleanup: '+JSON.stringify(protocol));

  console.log('WhatsApp configured pairing phone number (HA options): ' + (opts.phone_number || 'not set; may be entered in private pairing UI'));
  api = startChild('WhatsApp API', '/opt/whatsapp-api/dist/main.js', {
    PORT: String(API_PORT),
    WHATSAPP_DATA_DIR: storage.dataDir,
    WHATSAPP_API_TOKEN: apiToken,
    WHATSAPP_PHONE_NUMBER: opts.phone_number,
    WHATSAPP_QR_FILE: '/data/.whatsapp-link-qr',
    WHATSAPP_PAIRING_DEFAULT_MODE: DEFAULT_PAIRING_MODE,
    WHATSAPP_MCP_READONLY: 'true',
    // Resumable backfill: up to 2,000 requests/day (our safety ceiling), 50 messages/request.
    WHATSAPP_HISTORY_BACKFILL: '1',
    LOG_LEVEL: 'info'
  });
  await waitForServer(API_PORT, api);
  mcp = startChild('WhatsApp MCP', '/opt/whatsapp-mcp/dist/main.js', {
    PORT: String(MCP_PORT),
    WHATSAPP_API_URL: 'http://' + UPSTREAM + ':' + API_PORT,
    WHATSAPP_API_TOKEN: apiToken,
    WHATSAPP_MCP_TOKEN: mcpToken,
    WHATSAPP_DATA_DIR: storage.dataDir,
    WHATSAPP_REVIEW_MODULE: '/opt/whatsapp-addon/review-data.mjs',
    LOG_LEVEL: 'info'
  });
  await waitForServer(MCP_PORT, mcp);

  // Only Supervisor's ingress proxy can connect to this separate listener.
  // The public Cloudflare /mcp gateway remains entirely separate.
  archiveData = new ArchiveData(storage.dataDir);
  cloudBackup = new WhatsAppCloudBackup({
    dataDir: storage.dataDir, stateDir: backupStateDir,
    clientId: opts.backup_google_client_id,
    clientSecret: opts.backup_google_client_secret,
    time: opts.backup_time_uk || '05:30',
    alerts: new HomeAssistantBackupAlerts()
  });
  archive = createArchiveServer({ apiToken, port: ARCHIVE_PORT,
    storagePath: storage.dataDir, dataStore: archiveData, cloudBackup, configuredPhone: !!opts.phone_number, configuredPhoneNumber: opts.phone_number, defaultPairingMode: DEFAULT_PAIRING_MODE });
  cloudBackup.start();
  maintenanceMonitor = new WhatsAppMaintenanceMonitor();
  maintenanceMonitor.start();
  archive.listen(ARCHIVE_PORT, '0.0.0.0', () => {
    console.log('Private WhatsApp Archive UI ready through Home Assistant Ingress (port ' + ARCHIVE_PORT + ').');
    console.log('WhatsApp data location: ' + (storage.external ? 'verified external shared-storage volume' : 'internal app storage') + '.');
    if (!storage.external) {
      let volumes = [];
      try { volumes = ['share', 'media'].flatMap(root => fs.readdirSync('/' + root, { withFileTypes: true }).filter(d=>d.isDirectory()).map(d=>root + '/' + d.name)); }
      catch { /* the share map may be unavailable until the app definition is refreshed */ }
      console.log('Available storage folders: ' + (volumes.join(', ') || '(none)') + '.');
    }
  });

  let cachedKeys, fetchedAt = 0;
  async function getKeys(force = false) {
    if (!force && cachedKeys && Date.now() - fetchedAt < 3600000) return cachedKeys;
    const response = await fetch('https://' + opts.access_team_domain + '/cdn-cgi/access/certs',
      { signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error('Cloudflare certificate lookup failed');
    const next = await response.json();
    if (!Array.isArray(next.keys) || next.keys.length === 0)
      throw new Error('Cloudflare certificates missing');
    cachedKeys = next;
    fetchedAt = Date.now();
    return cachedKeys;
  }

  gateway = http.createServer(async (req, res) => {
    // Diagnostic metadata only: never log headers, JWTs, URLs with queries,
    // client addresses, message bodies, or WhatsApp identifiers.
    const started = Date.now();
    const requestPath = (() => {
      try { return new URL(req.url, 'http://localhost').pathname === '/mcp' ? '/mcp' : '(other)'; }
      catch { return '(invalid)'; }
    })();
    let stage = 'received';
    res.once('finish', () => {
      console.log('MCP gateway diagnostic: ' + JSON.stringify({
        method: ['GET', 'POST', 'DELETE', 'OPTIONS'].includes(req.method) ? req.method : 'OTHER',
        path: requestPath, status: res.statusCode, stage,
        duration_ms: Date.now() - started
      }));
    });
    try {
      if (!opts.access_team_domain || !opts.access_audience || !opts.allowed_email) {
        stage = 'configuration_incomplete';
        res.writeHead(503).end('Cloudflare Access configuration incomplete');
        return;
      }
      const token = req.headers['cf-access-jwt-assertion'];
      if (typeof token !== 'string') { stage = 'assertion_missing'; res.writeHead(401).end('Access assertion required'); return; }
      let good = verifyAccessJwt(token, opts, await getKeys());
      if (!good) good = verifyAccessJwt(token, opts, await getKeys(true));
      if (!good) { stage = 'assertion_invalid'; res.writeHead(403).end('Access assertion rejected'); return; }
      stage = 'authenticated';
      if (new URL(req.url, 'http://localhost').pathname !== '/mcp') {
        stage = 'path_not_found';
        res.writeHead(404).end('Not found'); return;
      }
      const headers = { ...req.headers };
      for (const name of Object.keys(headers)) {
        if (name === 'authorization' || name === 'host' || name === 'cookie'
          || name.startsWith('cf-') || name.startsWith('x-forwarded-'))
          delete headers[name];
      }
      headers.authorization = 'Bearer ' + mcpToken;
      headers.host = UPSTREAM + ':' + MCP_PORT;
      stage = 'upstream_connecting';
      const upstream = http.request({
        hostname: UPSTREAM, port: MCP_PORT, path: req.url,
        method: req.method, headers
      }, upstreamRes => {
        const responseHeaders = { ...upstreamRes.headers };
        delete responseHeaders['connection'];
        stage = 'upstream_response';
        res.writeHead(upstreamRes.statusCode || 502, responseHeaders);
        upstreamRes.pipe(res);
      });
      upstream.on('error', () => {
        stage = 'upstream_error';
        if (!res.headersSent) res.writeHead(502);
        res.end('MCP backend unavailable');
      });
      req.pipe(upstream);
    } catch (err) {
      // Avoid leaking exception strings containing URLs or upstream metadata.
      stage = 'gateway_exception';
      console.error('MCP gateway exception at stage: ' + stage);
      if (!res.headersSent) res.writeHead(503);
      res.end('Access validation unavailable');
    }
  });
  gateway.requestTimeout = 300000;
  gateway.listen(PUBLIC_PORT, '0.0.0.0', () => {
    console.log('Read-only MCP gateway ready on port ' + PUBLIC_PORT + ' (/mcp).');
    console.log('Raw WhatsApp API and MCP backend are not published outside this container.');
    if (!opts.phone_number) console.log('Configure phone_number to begin WhatsApp pairing.');
    if (!opts.access_audience || !opts.access_team_domain || !opts.allowed_email)
      console.log('Cloudflare Access settings are incomplete: all external requests are denied.');
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(err => { console.error('WhatsApp MCP startup failed:', err.message); process.exitCode = 1; shutdown(); });
}
