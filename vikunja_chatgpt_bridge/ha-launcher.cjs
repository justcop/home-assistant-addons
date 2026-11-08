'use strict';
const fs = require('node:fs');
const { spawn } = require('node:child_process');

let options;
try {
  options = JSON.parse(fs.readFileSync('/data/options.json', 'utf8'));
} catch (err) {
  console.error('Unable to read Home Assistant options:', err.message);
  process.exit(1);
}
const required = ['vikunja_url', 'mcp_base_url', 'vikunja_token', 'mcp_auth_password', 'jwt_secret'];
const missing = required.filter(key => typeof options[key] !== 'string' || !options[key].trim());
if (missing.length) {
  console.error('Configure these add-on options:', missing.join(', '));
  process.exit(1);
}
if (options.jwt_secret.length < 32) {
  console.error('jwt_secret must be at least 32 characters.');
  process.exit(1);
}
for (const key of ['vikunja_url', 'mcp_base_url']) {
  try {
    const url = new URL(options[key]);
    if (url.protocol !== 'https:') throw new Error('HTTPS required');
    if (url.pathname !== '/' || url.search || url.hash) throw new Error('Use domain origin without a path');
  } catch (e) {
    console.error(`${key} must be an HTTPS URL like https://subdomain.example.com without a path: ${e.message}`);
    process.exit(1);
  }
}
const env = {
  ...process.env,
  VIKUNJA_API_URL: options.vikunja_url.replace(/\/$/, ''),
  VIKUNJA_API_TOKEN: options.vikunja_token,
  MCP_AUTH_PASSWORD: options.mcp_auth_password,
  JWT_SECRET: options.jwt_secret,
  BASE_URL: options.mcp_base_url.replace(/\/$/, ''),
  PORT: '3000',
  AUTH_DB_PATH: '/data/auth.db',
};
fs.mkdirSync('/data', { recursive: true });
fs.chownSync('/data', 1001, 0);
fs.chmodSync('/data', 0o700);
console.log('Starting MCP bridge on port 3000. OAuth database stored at /data/auth.db.');
const child = spawn('node', ['dist/index.js'], {
  cwd: '/app', env, stdio: 'inherit', uid: 1001, gid: 0,
});
child.on('error', e => { console.error('Unable to launch MCP:', e.message); process.exit(1); });
child.on('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => child.kill(signal));
