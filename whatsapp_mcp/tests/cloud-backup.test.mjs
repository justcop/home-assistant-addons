import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { encryptSnapshot, decryptSnapshot, checkSqlite, ukDateAndTime, validateBackupTime,
  WhatsAppCloudBackup } from '../cloud-backup.mjs';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-cloud-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const db = new DatabaseSync(path.join(root, 'whatsapp.db'));
  db.exec('CREATE TABLE chats(id TEXT PRIMARY KEY); CREATE TABLE messages(id TEXT PRIMARY KEY);');
  db.exec("INSERT INTO chats VALUES('direct'); INSERT INTO messages VALUES('original');");
  db.close();
  return root;
}
test('AES-256-GCM roundtrip detects incorrect keys and modified ciphertext', async t => {
  const root = fixture(t), key = crypto.randomBytes(32), encrypted = path.join(root, 'enc');
  const snapshot = path.join(root, 'whatsapp.db');
  await encryptSnapshot(snapshot, encrypted, key);
  const recovered = path.join(root, 'restored.db');
  await decryptSnapshot(encrypted, recovered, key);
  checkSqlite(recovered);
  assert.equal(fs.readFileSync(recovered).equals(fs.readFileSync(snapshot)), true);
  await assert.rejects(decryptSnapshot(encrypted, path.join(root, 'wrong.db'), crypto.randomBytes(32)), /authentication failed/);
  const corrupted = fs.readFileSync(encrypted);
  corrupted[25] ^= 2;
  fs.writeFileSync(encrypted, corrupted);
  await assert.rejects(decryptSnapshot(encrypted, path.join(root, 'modified.db'), key), /authentication failed/);
  assert.equal(fs.existsSync(path.join(root, 'modified.db')), false);
});
test('backup schedule tracks London DST without advancing a UTC backup into the wrong day', () => {
  assert.deepEqual(ukDateAndTime(new Date('2026-07-01T04:30:00Z')),
    { date: '2026-07-01', time: '05:30' });
  assert.deepEqual(ukDateAndTime(new Date('2026-11-23T05:30:00Z')),
    { date: '2026-11-23', time: '05:30' });
  assert.equal(validateBackupTime('05:30'), '05:30');
  assert.throws(() => validateBackupTime('25:61'));
});
function mockDrive() {
  const files = new Map(), uploadSessions = new Map();
  let seq = 0, failNextUpload = false;
  const json = (data, status = 200, headers = {}) => new Response(JSON.stringify(data), {
    status, headers: { 'Content-Type': 'application/json', ...headers }
  });
  return {
    files,
    setFailNext: () => { failNextUpload = true; },
    async request(uri, options = {}) {
      const url = new URL(uri), endpoint = url.pathname, method = options.method || 'GET';
      if (endpoint === '/token')
        return json({ access_token: 'test-access-token', expires_in: 3600 });
      if (endpoint === '/drive/v3/files' && method === 'GET') {
        if ((url.searchParams.get('q') || '').includes('mimeType')) {
          return json({ files: [...files.values()].filter(f => f.mimeType === 'application/vnd.google-apps.folder')
            .map(f => ({ id: f.id, name: f.name })) });
        }
        return json({ files: [...files.values()].filter(f => f.mimeType !== 'application/vnd.google-apps.folder')
          .map(f => ({ id: f.id, name: f.name, size: f.size, md5Checksum: f.md5Checksum })) });
      }
      if (endpoint === '/drive/v3/files' && method === 'POST') {
        const data = JSON.parse(options.body);
        const id = 'folder-' + (++seq);
        files.set(id, { id, name: data.name, mimeType: data.mimeType, trashed: false });
        return json({ id, name: data.name });
      }
      if (endpoint === '/upload/drive/v3/files' && method === 'POST') {
        const data = JSON.parse(options.body), id = 'file-' + (++seq);
        uploadSessions.set(id, data);
        return json({}, 200, { Location: 'https://www.googleapis.com/upload/session/' + id });
      }
      if (endpoint.startsWith('/upload/session/') && method === 'PUT') {
        if (failNextUpload) {
          failNextUpload = false;
          return json({ error: 'server_error' }, 503);
        }
        const id = endpoint.split('/').at(-1);
        const bytes = Buffer.from(await new Response(options.body).arrayBuffer());
        const data = uploadSessions.get(id);
        const file = { id, name: data.name, size: String(bytes.length), trashed: false,
          md5Checksum: crypto.createHash('md5').update(bytes).digest('hex'), bytes,
          mimeType: 'application/octet-stream' };
        files.set(id, file);
        return json(file, 200);
      }
      if (endpoint.startsWith('/drive/v3/files/')) {
        const id = endpoint.split('/').at(-1);
        const item = files.get(id);
        if (!item) return json({ error: 'not_found' }, 404);
        if (method === 'DELETE') {
          files.delete(id);
          return new Response(null, { status: 204 });
        }
        if (url.searchParams.get('alt') === 'media')
          return new Response(item.bytes, { status: 200 });
        return json({ id: item.id, name: item.name, size: item.size,
          md5Checksum: item.md5Checksum, trashed: item.trashed, mimeType: item.mimeType });
      }
      throw new Error('Unexpected mock request: ' + method + ' ' + uri);
    }
  };
}
async function waitForCompletion(service) {
  for (let i = 0; i < 300 && service.running; i++)
    await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(service.running, false, 'backup must finish');
}
test('only one verified encrypted Google Drive file remains; failed replacements keep the previous copy', async t => {
  const root = fixture(t), drive = mockDrive();
  const service = new WhatsAppCloudBackup({ dataDir: root, stateDir: path.join(root, 'private'),
    clientId: '123-test.apps.googleusercontent.com', clientSecret: 'private-secret',
    request: (...args) => drive.request(...args) });
  assert.equal(service.status().enabled, false);
  assert.throws(() => service.setEnabled(true), /Connect Google Drive/);
  service.tokens = { refresh_token: 'mock-refresh-token' };
  const { key } = service.recoveryKey();
  assert.equal(Buffer.from(key, 'base64url').length, 32);
  service.confirmRecoveryKey();
  service.setEnabled(true);
  assert.deepEqual(service.runNow(), { started: true });
  await waitForCompletion(service);
  assert.equal(service.status().lastError, null);
  let backups = [...drive.files.values()].filter(f => f.name.startsWith('whatsapp-db-'));
  assert.equal(backups.length, 1);
  const originalId = backups[0].id;
  const db = new DatabaseSync(path.join(root, 'whatsapp.db'));
  db.exec("DELETE FROM messages WHERE id='original'");
  db.close();
  drive.setFailNext();
  service.runNow();
  await waitForCompletion(service);
  backups = [...drive.files.values()].filter(f => f.name.startsWith('whatsapp-db-'));
  assert.deepEqual(backups.map(f => f.id), [originalId], 'a failed backup must not delete the preceding good copy');
  service.runNow();
  await waitForCompletion(service);
  assert.equal(service.status().lastError, null);
  backups = [...drive.files.values()].filter(f => f.name.startsWith('whatsapp-db-'));
  assert.equal(backups.length, 1);
  assert.notEqual(backups[0].id, originalId);
  const encrypted = path.join(root, 'download.wacloud');
  fs.writeFileSync(encrypted, backups[0].bytes);
  const restored = path.join(root, 'restored.db');
  await decryptSnapshot(encrypted, restored, Buffer.from(key, 'base64url'));
  const recoveredDb = new DatabaseSync(restored, { readOnly: true });
  assert.equal(recoveredDb.prepare('SELECT COUNT(*) as n FROM messages').get().n, 0,
    'deleted messages must not be resurrected from older cloud backups');
  recoveredDb.close();
  assert.equal(fs.readdirSync(root).some(p => p.startsWith('.cloud-snapshot-')), false,
    'no temporary plaintext snapshot may remain after a backup');
});
test('Google client reset disables backup and invalidates the old OAuth token', t => {
  const root = fixture(t);
  const service = new WhatsAppCloudBackup({ dataDir: root, stateDir: path.join(root, 'private') });
  const status = service.configureGoogleClient('123-test.apps.googleusercontent.com', 'secret');
  assert.equal(status.clientConfigured, true);
  assert.equal(status.enabled, false);
  assert.equal(service.clientSecret, 'secret');
  assert.equal(service.status().connected, false);
});
