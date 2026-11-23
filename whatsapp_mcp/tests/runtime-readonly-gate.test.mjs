import test from 'node:test';
import assert from 'node:assert/strict';
import { createArchiveServer } from '../archive-server.mjs';

test('archive viewer cannot send or mutate WhatsApp via any HTTP method', async t => {
  const app = createArchiveServer({
    apiToken: 'fake-test-token',
    allowPeer: () => true,
    storagePath: '/media/storage/.whatsapp-mcp-private'
  });
  await new Promise((resolve, reject) => {
    app.once('error', reject);
    app.listen(0, '127.0.0.1', resolve);
  });
  t.after(() => new Promise(resolve => app.close(resolve)));
  const root = 'http://127.0.0.1:' + app.address().port;
  const storage = await fetch(root + '/api/storage');
  assert.equal(storage.status, 200);
  assert.equal((await storage.json()).location, 'STORAGE drive');
  for (const [method, route] of [
    ['POST','/api/messages'], ['PUT','/api/messages'],
    ['DELETE','/api/messages'], ['PATCH','/api/chats'],
    ['POST','/api/search'], ['POST','/api/contacts']
  ]) {
    const answer = await fetch(root + route, { method });
    assert.equal(answer.status, 405, method + ' ' + route + ' must not mutate data');
  }
  const backup = await fetch(root + '/api/backup/run', {
    method: 'POST', headers: {
      'X-WhatsApp-Backup-Action': '1',
      'Content-Type': 'application/json'
    }, body: '{}'
  });
  assert.equal(backup.status, 503, 'Cloud backup management does not imply WhatsApp writes');
});
