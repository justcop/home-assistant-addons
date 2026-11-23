import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { pipeline } from 'node:stream/promises';
import { DatabaseSync, backup as sqliteBackup } from 'node:sqlite';

const SCOPE = 'https://www.googleapis.com/auth/drive.file';
const DRIVE = 'https://www.googleapis.com/drive/v3';
const OAUTH = 'https://oauth2.googleapis.com';
const PREFIX = 'whatsapp-db-';
const FOLDER = 'WhatsApp Private Archive (encrypted)';
const MAGIC = Buffer.from('WACLOUD1', 'ascii');
const timeZone = 'Europe/London';

function readJson(file, fallback = {}) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return fallback; throw error; }
}
function savePrivate(file, value) {
  const tmp = file + '.' + process.pid + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(value), { mode: 0o600, flag: 'wx' });
  try { fs.renameSync(tmp, file); } finally {
    if (fs.existsSync(tmp)) fs.unlinkSync(tmp);
  }
}
function readKey(file) {
  const encoded = fs.readFileSync(file, 'utf8').trim();
  const bytes = Buffer.from(encoded, 'base64url');
  if (bytes.length !== 32) throw new Error('Invalid backup recovery key');
  return bytes;
}
export function ukDateAndTime(date = new Date()) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).formatToParts(date);
  const part = type => parts.find(p => p.type === type)?.value;
  return { date: part('year') + '-' + part('month') + '-' + part('day'),
    time: part('hour') + ':' + part('minute') };
}
export function validateBackupTime(time) {
  const value = String(time || '').trim();
  if (!/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(value))
    throw new Error('Backup time must be HH:MM');
  return value;
}
async function fileDigest(file, algorithm = 'md5') {
  const hash = crypto.createHash(algorithm);
  for await (const bytes of fs.createReadStream(file)) hash.update(bytes);
  return hash.digest('hex');
}
export async function encryptSnapshot(input, output, key, random = crypto.randomBytes) {
  if (!Buffer.isBuffer(key) || key.length !== 32) throw new Error('Expected 32-byte recovery key');
  const nonce = random(12);
  fs.writeFileSync(output, Buffer.concat([MAGIC, nonce]), { flag: 'wx', mode: 0o600 });
  const cipher = crypto.createCipheriv('aes-256-gcm', key, nonce);
  await pipeline(fs.createReadStream(input), cipher, fs.createWriteStream(output, { flags: 'a', mode: 0o600 }));
  fs.appendFileSync(output, cipher.getAuthTag());
}
export async function decryptSnapshot(input, output, key) {
  if (!Buffer.isBuffer(key) || key.length !== 32) throw new Error('Expected 32-byte recovery key');
  const info = fs.statSync(input);
  if (info.size < 8 + 12 + 16) throw new Error('Invalid encrypted backup length');
  const fd = fs.openSync(input, 'r');
  const header = Buffer.alloc(20), tag = Buffer.alloc(16);
  try {
    fs.readSync(fd, header, 0, 20, 0);
    fs.readSync(fd, tag, 0, 16, info.size - 16);
  } finally { fs.closeSync(fd); }
  if (!header.subarray(0, 8).equals(MAGIC)) throw new Error('Unknown encrypted backup format');
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, header.subarray(8, 20));
  decipher.setAuthTag(tag);
  try {
    await pipeline(fs.createReadStream(input, { start: 20, end: info.size - 17 }), decipher,
      fs.createWriteStream(output, { flags: 'wx', mode: 0o600 }));
  } catch {
    try { fs.unlinkSync(output); } catch { /* nonexistent or already removed */ }
    throw new Error('Encrypted backup authentication failed or recovery key is incorrect');
  }
  checkSqlite(output);
}
export function checkSqlite(file) {
  const db = new DatabaseSync(file, { readOnly: true });
  try {
    const result = db.prepare('PRAGMA integrity_check').get();
    if (!result || Object.values(result)[0] !== 'ok') throw new Error('Backup SQLite integrity check failed');
    db.prepare('SELECT COUNT(*) FROM chats').get();
    db.prepare('SELECT COUNT(*) FROM messages').get();
  } finally { db.close(); }
}
async function jsonBody(response) {
  const body = await response.json().catch(() => ({}));
  return body;
}
function safeProviderError(type, response, body = {}) {
  const code = typeof body.error === 'string' ? body.error : body.error?.status;
  return new Error(type + ' failed (HTTP ' + response.status + (code ? ', ' + String(code).slice(0, 50) : '') + ')');
}
function immutableName(date = new Date()) {
  return PREFIX + date.toISOString().replace(/[:.]/g, '-') + '.wacloud';
}
export class WhatsAppCloudBackup {
  constructor({ dataDir, stateDir = '/data/.whatsapp-cloud-backup',
    clientId = '', clientSecret = '', time = '05:30', request = fetch, clock = () => new Date(), alerts = null }) {
    this.dataDir = dataDir;
    this.stateDir = stateDir;
    this.request = request;
    this.clock = clock;
    this.alerts = alerts;
    fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
    this.stateFile = path.join(stateDir, 'status.json');
    this.tokenFile = path.join(stateDir, 'google-token.json');
    this.clientFile = path.join(stateDir, 'google-client.json');
    this.keyFile = path.join(stateDir, 'recovery-key');
    this.state = readJson(this.stateFile, { enabled: false, recoveryConfirmed: false });
    this.backupTime = validateBackupTime(this.state.backupTime || time);
    const client = readJson(this.clientFile, {});
    this.clientId = client.clientId || String(clientId).trim();
    this.clientSecret = client.clientSecret || String(clientSecret).trim();
    this.tokens = readJson(this.tokenFile, {});
    this.pendingAuth = null;
    this.access = null;
    this.running = false;
    this.timer = null;
    this.attemptToday = null;
    // A forced restart during backup could leave a plaintext SQLite snapshot
    // in the external staging directory. Clear only our private named staging
    // directories before starting the scheduler; never recover old messages.
    for (const entry of fs.readdirSync(this.dataDir, { withFileTypes: true })) {
      if (entry.isDirectory() && entry.name.startsWith('.cloud-snapshot-')) {
        fs.rmSync(path.join(this.dataDir, entry.name), { recursive: true, force: true });
      }
    }
  }
  _save() { savePrivate(this.stateFile, this.state); }
  _alertFailure(error) {
    if (!this.alerts) return;
    void this.alerts.failure(error).catch(e => {
      console.error('WhatsApp cloud backup notification failed:', e.message);
    });
  }
  _alertRecovered() {
    if (!this.alerts) return;
    void this.alerts.recovered().catch(e => {
      console.error('WhatsApp cloud backup notification dismissal failed:', e.message);
    });
  }

  configureGoogleClient(clientId, clientSecret) {
    const id = String(clientId || '').trim();
    // Never return the saved client secret to the browser. An empty secret
    // means "keep saved" ONLY when the client ID itself is unchanged.
    const suppliedSecret = String(clientSecret || '').trim();
    const secret = suppliedSecret || (id === this.clientId ? this.clientSecret : '');
    if (!/^[0-9]+-[a-z0-9-]+\.apps\.googleusercontent\.com$/.test(id) ||
        !secret || secret.length > 300)
      throw new Error('Enter a valid Google OAuth client ID and its secret. To change client IDs you must also supply the new secret.');
    // A new OAuth client can be unable to see/delete files owned by the old
    // Drive client due to drive.file scoping: never silently orphan a copy.
    if (this.state.latestFileId && this.clientId && id !== this.clientId)
      throw new Error('Changing OAuth client could orphan the prior encrypted Drive backup. Delete it in Google Drive first or reconnect using the existing client.');
    // A different OAuth client must never silently use the old client's token.
    const changed = id !== this.clientId || secret !== this.clientSecret;
    savePrivate(this.clientFile, { clientId: id, clientSecret: secret });
    this.clientId = id;
    this.clientSecret = secret;
    if (changed) {
      this.tokens = {};
      this.access = null;
      this.pendingAuth = null;
      if (fs.existsSync(this.tokenFile)) fs.unlinkSync(this.tokenFile);
      this.state.enabled = false;
      this.state.folderId = null;
      this.state.latestFileId = null;
      this.state.lastFileName = null;
      this.state.lastSuccess = null;
      this.state.lastSuccessLocalDate = null;
      this.state.retentionPending = false;
      this._save();
    }
    this.state.lastAuthError = null;
    this._save();
    return this.status();
  }
  setBackupTime(time) {
    this.backupTime = validateBackupTime(time);
    this.state.backupTime = this.backupTime;
    this._save();
    return this.status();
  }
  status() {
    const now = ukDateAndTime(this.clock());
    return {
      clientConfigured: !!(this.clientId && this.clientSecret),
      // Client IDs are public identifiers, not secrets; showing the saved ID
      // makes it possible to verify the exact Google Console entry.
      clientId: this.clientId || null,
      clientSecretStored: !!this.clientSecret,
      connected: !!this.tokens.refresh_token,
      recoveryKeyCreated: fs.existsSync(this.keyFile),
      recoveryConfirmed: !!this.state.recoveryConfirmed,
      enabled: !!this.state.enabled,
      running: this.running,
      time: this.backupTime,
      timeZone, localDate: now.date, localTime: now.time,
      lastSuccess: this.state.lastSuccess || null,
      lastFileName: this.state.lastFileName || null,
      lastError: this.state.lastError || null,
      lastAuthError: this.state.lastAuthError || null,
      retentionPending: !!this.state.retentionPending,
      pendingAuthorisation: this.pendingAuth ? {
        verificationUrl: this.pendingAuth.verification_url,
        userCode: this.pendingAuth.user_code,
        expiresAt: new Date(this.pendingAuth.expiresAt).toISOString()
      } : null
    };
  }
  recoveryKey() {
    if (!fs.existsSync(this.keyFile)) {
      const key = crypto.randomBytes(32).toString('base64url');
      fs.writeFileSync(this.keyFile, key, { mode: 0o600, flag: 'wx' });
      this.state.recoveryConfirmed = false;
      this._save();
    }
    return { key: fs.readFileSync(this.keyFile, 'utf8').trim() };
  }
  confirmRecoveryKey() {
    if (!fs.existsSync(this.keyFile)) throw new Error('Generate and save a recovery key first');
    this.state.recoveryConfirmed = true;
    this._save();
    return this.status();
  }
  setEnabled(enabled) {
    if (enabled && (!this.clientId || !this.clientSecret || !this.tokens.refresh_token ||
        !this.state.recoveryConfirmed || !fs.existsSync(this.keyFile)))
      throw new Error('Connect Google Drive and save the recovery key before enabling backups');
    this.state.enabled = !!enabled;
    this._save();
    return this.status();
  }
  async _oauthRequest(endpoint, params) {
    const res = await this.request(OAUTH + endpoint, {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(params).toString(), signal: AbortSignal.timeout(30000)
    });
    return { res, body: await jsonBody(res) };
  }
  async beginAuthorisation() {
    if (!this.clientId || !this.clientSecret) throw new Error('Enter Google device OAuth client ID and secret in app configuration first');
    const { res, body } = await this._oauthRequest('/device/code', {
      client_id: this.clientId, scope: SCOPE
    });
    if (!res.ok || !body.device_code || !body.user_code) {
      const error = res.status === 401 && body.error === 'invalid_client'
        ? new Error('Google rejected this OAuth client (invalid_client). In Google Cloud Console, verify that the saved client ID exists and its application type is "TVs and Limited Input devices", not "Web application" or "Desktop". If necessary create a new TV client and save its matching ID and secret.')
        : safeProviderError('Google device authorisation', res, body);
      this.state.lastAuthError = error.message;
      this._save();
      throw error;
    }
    this.state.lastAuthError = null;
    this._save();
    const interval = Math.max(5, Number(body.interval) || 5);
    this.pendingAuth = {
      device_code: body.device_code, user_code: body.user_code,
      verification_url: body.verification_url,
      expiresAt: Date.now() + Math.min(Number(body.expires_in) || 900, 1800) * 1000,
      interval, nextPollAt: Date.now() + interval * 1000
    };
    return this.status();
  }
  async pollAuthorisation() {
    const p = this.pendingAuth;
    if (!p) throw new Error('No Google authorisation is in progress');
    if (Date.now() >= p.expiresAt) {
      this.pendingAuth = null;
      throw new Error('Google authorisation code expired; start again');
    }
    if (Date.now() < p.nextPollAt)
      return { state: 'pending', retryAfterSeconds: Math.ceil((p.nextPollAt - Date.now()) / 1000) };
    p.nextPollAt = Date.now() + p.interval * 1000;
    const { res, body } = await this._oauthRequest('/token', {
      client_id: this.clientId, client_secret: this.clientSecret,
      device_code: p.device_code, grant_type: 'urn:ietf:params:oauth:grant-type:device_code'
    });
    if (body.error === 'authorization_pending') return { state: 'pending', retryAfterSeconds: p.interval };
    if (body.error === 'slow_down') {
      p.interval += 5;
      p.nextPollAt = Date.now() + p.interval * 1000;
      return { state: 'pending', retryAfterSeconds: p.interval };
    }
    if (!res.ok || !body.refresh_token) {
      if (body.error === 'expired_token' || body.error === 'access_denied') this.pendingAuth = null;
      throw safeProviderError('Google device token request', res, body);
    }
    this.tokens = { refresh_token: body.refresh_token };
    savePrivate(this.tokenFile, this.tokens);
    this.access = { token: body.access_token, expires: Date.now() + (Number(body.expires_in) || 3600) * 1000 - 60000 };
    this.pendingAuth = null;
    return { state: 'connected' };
  }
  async _accessToken() {
    if (this.access?.expires > Date.now()) return this.access.token;
    if (!this.tokens.refresh_token || !this.clientId) throw new Error('Google Drive is not connected');
    const { res, body } = await this._oauthRequest('/token', {
      client_id: this.clientId, client_secret: this.clientSecret,
      refresh_token: this.tokens.refresh_token, grant_type: 'refresh_token'
    });
    if (!res.ok || !body.access_token) throw safeProviderError('Google token refresh', res, body);
    if (body.refresh_token && body.refresh_token !== this.tokens.refresh_token) {
      this.tokens.refresh_token = body.refresh_token;
      savePrivate(this.tokenFile, this.tokens);
    }
    this.access = { token: body.access_token, expires: Date.now() + (Number(body.expires_in) || 3600) * 1000 - 60000 };
    return this.access.token;
  }
  async _drive(endpoint, { method = 'GET', data = null, headers = {} } = {}) {
    const token = await this._accessToken();
    const res = await this.request(DRIVE + endpoint, {
      method,
      headers: { Authorization: 'Bearer ' + token,
        ...(data === null ? {} : { 'Content-Type': 'application/json' }), ...headers },
      ...(data === null ? {} : { body: JSON.stringify(data) }),
      signal: AbortSignal.timeout(30000)
    });
    if (!res.ok) throw safeProviderError('Google Drive API', res, await jsonBody(res));
    return res.status === 204 ? null : await jsonBody(res);
  }
  async _folderId() {
    if (this.state.folderId) {
      try {
        const f = await this._drive('/files/' + encodeURIComponent(this.state.folderId) + '?fields=id,mimeType,trashed');
        if (f.id && f.mimeType === 'application/vnd.google-apps.folder' && !f.trashed) return f.id;
      } catch { /* missing or inaccessible folder; try app-created folder lookup */ }
    }
    const q = "name = '" + FOLDER.replace(/'/g, "\\'") +
      "' and mimeType = 'application/vnd.google-apps.folder' and trashed = false";
    const listing = await this._drive('/files?q=' + encodeURIComponent(q) +
      '&fields=' + encodeURIComponent('files(id,name),nextPageToken') + '&page_size=100');
    let folderId = listing.files?.[0]?.id;
    if (!folderId) {
      const folder = await this._drive('/files?fields=id,name', { method: 'POST',
        data: { name: FOLDER, mimeType: 'application/vnd.google-apps.folder' } });
      folderId = folder.id;
    }
    if (!folderId) throw new Error('Google Drive folder could not be created');
    this.state.folderId = folderId;
    this._save();
    return folderId;
  }
  async _listBackups(folderId) {
    const q = "'" + folderId + "' in parents and trashed = false";
    const files = [];
    let pageToken;
    do {
      const query = new URLSearchParams({ q, pageSize: '1000',
        fields: 'nextPageToken,files(id,name,size,md5Checksum,trashed)' });
      if (pageToken) query.set('pageToken', pageToken);
      const page = await this._drive('/files?' + query.toString());
      files.push(...(page.files || []).filter(f => f.name?.startsWith(PREFIX) && f.name.endsWith('.wacloud')));
      pageToken = page.nextPageToken;
    } while (pageToken);
    return files;
  }
  async _upload(encrypted, folderId, name) {
    const size = fs.statSync(encrypted).size;
    const token = await this._accessToken();
    const metadata = { name, mimeType: 'application/octet-stream', parents: [folderId],
      appProperties: { createdBy: 'whatsapp-private-backup-v1' } };
    const start = await this.request('https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,name,size,md5Checksum', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json; charset=UTF-8',
        'X-Upload-Content-Type': 'application/octet-stream', 'X-Upload-Content-Length': String(size) },
      body: JSON.stringify(metadata), signal: AbortSignal.timeout(30000)
    });
    if (!start.ok) throw safeProviderError('Google Drive upload initiation', start, await jsonBody(start));
    const location = start.headers.get('location');
    if (!location || !location.startsWith('https://www.googleapis.com/')) throw new Error('Invalid Google Drive resumable upload URL');
    // The provider may reject an upload without reading the request body.
    // Destroy the stream on any response before removing the temporary file;
    // otherwise a late open can emit ENOENT after the staging folder is gone.
    const uploadBody = fs.createReadStream(encrypted);
    let streamError = null;
    uploadBody.on('error', error => { streamError = error; });
    let uploaded;
    try {
      uploaded = await this.request(location, {
        method: 'PUT', headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': String(size) },
        body: uploadBody, duplex: 'half', signal: AbortSignal.timeout(30 * 60 * 1000)
      });
    } finally { uploadBody.destroy(); }
    if (streamError) throw new Error('Could not read encrypted upload file');
    if (!uploaded.ok) throw safeProviderError('Google Drive encrypted upload', uploaded, await jsonBody(uploaded));
    const meta = await jsonBody(uploaded);
    if (!meta.id) throw new Error('Google Drive upload returned no file identifier');
    const verified = await this._drive('/files/' + encodeURIComponent(meta.id) +
      '?fields=id,name,size,md5Checksum,trashed');
    const md5 = await fileDigest(encrypted);
    if (verified.trashed || String(verified.size) !== String(size) || verified.md5Checksum !== md5)
      throw new Error('Google Drive encrypted backup failed checksum or size verification');
    return verified;
  }
  async _pruneOtherBackups(folderId, keepId) {
    const files = await this._listBackups(folderId);
    for (const file of files) {
      if (file.id !== keepId)
        await this._drive('/files/' + encodeURIComponent(file.id), { method: 'DELETE' });
    }
    const remaining = await this._listBackups(folderId);
    if (remaining.length !== 1 || remaining[0].id !== keepId)
      throw new Error('Cloud retention could not be verified as exactly one backup');
  }
  _ready() {
    if (!this.clientId || !this.clientSecret || !this.tokens.refresh_token ||
        !fs.existsSync(this.keyFile) || !this.state.recoveryConfirmed)
      throw new Error('Connect Google Drive and save your recovery key first');
    if (!fs.existsSync(path.join(this.dataDir, 'whatsapp.db')))
      throw new Error('Live WhatsApp database is missing');
  }
  runNow() {
    if (this.running) return { started: false, reason: 'already_running' };
    this._ready();
    this.running = true;
    this.state.lastError = null;
    this._save();
    void this._perform().catch(error => {
      // Never log message content, tokens, identifiers, backup keys or file paths.
      this.state.lastError = String(error?.message || 'Cloud backup failed').slice(0, 230);
      this._save();
      this._alertFailure(this.state.lastError);
      console.error('WhatsApp cloud backup failed:', this.state.lastError);
    }).finally(() => { this.running = false; });
    return { started: true };
  }
  async _perform() {
    const staging = fs.mkdtempSync(path.join(this.dataDir, '.cloud-snapshot-'));
    fs.chmodSync(staging, 0o700);
    const snapshot = path.join(staging, 'database.sqlite');
    const encrypted = path.join(staging, 'encrypted.wacloud');
    try {
      const db = new DatabaseSync(path.join(this.dataDir, 'whatsapp.db'), { readOnly: true });
      try { await sqliteBackup(db, snapshot); }
      finally { db.close(); }
      checkSqlite(snapshot);
      await encryptSnapshot(snapshot, encrypted, readKey(this.keyFile));
      // Erase transient plaintext before the upload begins.
      fs.unlinkSync(snapshot);
      const folderId = await this._folderId();
      const name = immutableName(this.clock());
      const file = await this._upload(encrypted, folderId, name);
      // Persist the new verified ID before attempting to prune. A failed prune
      // will be resumed rather than claiming single-copy retention succeeded.
      this.state.retentionPending = true;
      this.state.latestFileId = file.id;
      this.state.lastFileName = file.name;
      this._save();
      await this._pruneOtherBackups(folderId, file.id);
      this.state.retentionPending = false;
      this.state.lastError = null;
      this.state.lastSuccess = this.clock().toISOString();
      this.state.lastSuccessLocalDate = ukDateAndTime(this.clock()).date;
      this._save();
      this._alertRecovered();
      console.log('WhatsApp encrypted Google Drive backup verified; one cloud copy retained.');
    } finally {
      fs.rmSync(staging, { recursive: true, force: true });
    }
  }
  async _resumePrune() {
    if (!this.state.retentionPending || this.running || !this.tokens.refresh_token || !this.state.latestFileId) return;
    this.running = true;
    try {
      const folderId = await this._folderId();
      const verified = await this._drive('/files/' + encodeURIComponent(this.state.latestFileId) +
        '?fields=id,trashed');
      if (!verified.id || verified.trashed) throw new Error('Latest cloud backup is missing');
      await this._pruneOtherBackups(folderId, verified.id);
      this.state.retentionPending = false;
      this.state.lastError = null;
      this.state.lastSuccess = this.clock().toISOString();
      this.state.lastSuccessLocalDate = ukDateAndTime(this.clock()).date;
      this._save();
      this._alertRecovered();
    } catch (error) {
      this.state.lastError = String(error?.message || 'Cloud backup cleanup failed').slice(0, 230);
      this._save();
      this._alertFailure(this.state.lastError);
    } finally { this.running = false; }
  }
  async encryptedDownload() {
    if (!this.state.latestFileId) throw new Error('No cloud backup is available to download');
    const token = await this._accessToken();
    const res = await this.request(DRIVE + '/files/' + encodeURIComponent(this.state.latestFileId) + '?alt=media', {
      headers: { Authorization: 'Bearer ' + token },
      signal: AbortSignal.timeout(30 * 60 * 1000)
    });
    if (!res.ok) throw safeProviderError('Google Drive backup download', res);
    return { response: res, filename: this.state.lastFileName || 'whatsapp-private.wacloud' };
  }
  async tick() {
    const now = ukDateAndTime(this.clock());
    if (this.running) return;
    if (this.state.retentionPending) {
      await this._resumePrune();
      return;
    }
    if (!this.state.enabled || now.time < this.backupTime ||
        now.date === this.state.lastSuccessLocalDate || now.date === this.attemptToday) return;
    this.attemptToday = now.date;
    try { this.runNow(); }
    catch (error) {
      this.state.lastError = String(error.message).slice(0, 230);
      this._save();
      this._alertFailure(this.state.lastError);
    }
  }
  start() {
    if (this.timer) return;
    // Re-post a pre-existing failure after restart so HA notifications are
    // restored even if Home Assistant was restarted or a user dismissed it.
    if (this.state.lastError) this._alertFailure(this.state.lastError);
    this.timer = setInterval(() => { void this.tick().catch(() => {}); }, 60000);
    void this.tick().catch(() => {});
  }
  close() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}
