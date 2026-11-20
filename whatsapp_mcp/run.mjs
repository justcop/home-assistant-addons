import fs from 'node:fs';
import http from 'node:http';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const ROOT = '/data';
const API_PORT = 8097;
const MCP_PORT = 8098;
const PUBLIC_PORT = 8099;
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
  const options = Object.fromEntries(['phone_number', 'access_team_domain', 'access_audience', 'allowed_email']
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

function startChild(label, executable, env) {
  const child = spawn(process.execPath, [executable], {
    cwd: path.dirname(path.dirname(executable)),
    env: { ...process.env, ...env },
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

let api, mcp, gateway;
let stopping = false;
function shutdown() {
  if (stopping) return;
  stopping = true;
  if (gateway) gateway.close();
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
  fs.mkdirSync(path.join(ROOT, 'whatsapp'), { recursive: true });
  const opts = loadOptions();
  const apiToken = loadToken('api-token');
  const mcpToken = loadToken('mcp-token');
  // The app starts as root on Supervisor; the WhatsApp database and listeners
  // run as the unprivileged Node user after initial data directory setup.
  for (const dir of [ROOT, path.join(ROOT, 'whatsapp'), path.join(ROOT, '.whatsapp-mcp-secrets')]) {
    fs.chownSync(dir, 1000, 1000);
  }
  if (process.getuid?.() === 0) { process.setgid(1000); process.setuid(1000); }

  api = startChild('WhatsApp API', '/opt/whatsapp-api/dist/main.js', {
    PORT: String(API_PORT),
    WHATSAPP_DATA_DIR: path.join(ROOT, 'whatsapp'),
    WHATSAPP_API_TOKEN: apiToken,
    WHATSAPP_PHONE_NUMBER: opts.phone_number,
    WHATSAPP_MCP_READONLY: 'true',
    LOG_LEVEL: 'info'
  });
  await waitForServer(API_PORT, api);
  mcp = startChild('WhatsApp MCP', '/opt/whatsapp-mcp/dist/main.js', {
    PORT: String(MCP_PORT),
    WHATSAPP_API_URL: 'http://' + UPSTREAM + ':' + API_PORT,
    WHATSAPP_API_TOKEN: apiToken,
    WHATSAPP_MCP_TOKEN: mcpToken,
    LOG_LEVEL: 'info'
  });
  await waitForServer(MCP_PORT, mcp);

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
    try {
      if (!opts.access_team_domain || !opts.access_audience || !opts.allowed_email) {
        res.writeHead(503).end('Cloudflare Access configuration incomplete');
        return;
      }
      const token = req.headers['cf-access-jwt-assertion'];
      if (typeof token !== 'string') { res.writeHead(401).end('Access assertion required'); return; }
      let good = verifyAccessJwt(token, opts, await getKeys());
      if (!good) good = verifyAccessJwt(token, opts, await getKeys(true));
      if (!good) { res.writeHead(403).end('Access assertion rejected'); return; }
      if (new URL(req.url, 'http://localhost').pathname !== '/mcp') {
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
      const upstream = http.request({
        hostname: UPSTREAM, port: MCP_PORT, path: req.url,
        method: req.method, headers
      }, upstreamRes => {
        const responseHeaders = { ...upstreamRes.headers };
        delete responseHeaders['connection'];
        res.writeHead(upstreamRes.statusCode || 502, responseHeaders);
        upstreamRes.pipe(res);
      });
      upstream.on('error', () => {
        if (!res.headersSent) res.writeHead(502);
        res.end('MCP backend unavailable');
      });
      req.pipe(upstream);
    } catch (err) {
      console.error('MCP gateway error:', err.message);
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
