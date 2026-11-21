import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createArchiveServer, archiveTarget, isIngressPeer } from '../archive-server.mjs';
import { validateStoragePath } from '../storage.mjs';

test('desktop archive layout keeps both panes independently scrollable', () => {
  const css = readFileSync(new URL('../archive-ui/style.css', import.meta.url), 'utf8');
  assert.match(css, /\\.shell\\{[^}]*grid-template-rows:minmax\\(0,1fr\\)/);
  assert.match(css, /\\.sidebar\\{[^}]*min-height:0;overflow:hidden/);
  assert.match(css, /\\.main\\{[^}]*min-height:0;overflow:hidden/);
  assert.match(css, /\\.chat-list\\{[^}]*min-height:0;overflow-y:auto/);
  assert.match(css, /\\.messages-view\\{[^}]*min-height:0;overflow-y:auto/);
  assert.match(css, /\\.sidebar-footer\\{[^}]*flex-shrink:0/);
});

test('only Home Assistant Supervisor ingress peer can enter the archive', () => {
  assert.equal(isIngressPeer('172.30.32.2'), true);
  assert.equal(isIngressPeer('::ffff:172.30.32.2'), true);
  for (const ip of ['127.0.0.1', '172.30.32.1', '192.168.1.10', '::1', null])
    assert.equal(isIngressPeer(ip), false);
});

test('archive API can only address upstream read routes with approved filters', () => {
  assert.equal(archiveTarget('/api/messages?chat=x%40s.whatsapp.net&limit=100&ignored=evil'),
    '/v1/messages?chat=x%40s.whatsapp.net&limit=100');
  assert.equal(archiveTarget('/api/search?q=hello'), '/v1/messages/search?q=hello&limit=80');
  for (const url of ['/api/send', '/v1/messages', '/api/messages/../../v1/messages',
    '/api/search', '/api/search?q=', '/api/messages?limit=999', '/api/messages?limit=-2',
    '/api/messages?limit=NaN']) assert.equal(archiveTarget(url), null, url);
});

test('external storage path must identify a named HA shared-storage volume and a folder', () => {
  assert.equal(validateStoragePath(''), null);
  assert.deepEqual(validateStoragePath('/share/STORAGE/whatsapp-mcp'),
    { directory:'/share/STORAGE/whatsapp-mcp', disk:'/share/STORAGE', root:'/share' });
  assert.deepEqual(validateStoragePath('/media/storage/.whatsapp-mcp-private'),
    { directory:'/media/storage/.whatsapp-mcp-private', disk:'/media/storage', root:'/media' });
  for (const value of ['/data/whatsapp','/share/STORAGE','/share/STORAGE/..',
    '/share/STORAGE/../../etc','/share/STORAGE/','/share//archive','/media/STORAGE/wa',
    '/share/STORAGE/my folder','/share/STORAGE/foo/../bar',
    '/media/storage/whatsapp-mcp', '/media/storage/public', '/media/storage/.other'])
    assert.throws(() => validateStoragePath(value), value);
});

test('archive requires ingress even for the HTML document', async t => {
  const app = createArchiveServer({ apiToken: 'test-api-token', port: 0 });
  await new Promise(resolve => app.listen(0,'127.0.0.1',resolve));
  t.after(() => app.close());
  const url = 'http://127.0.0.1:' + app.address().port;
  const res = await fetch(url);
  assert.equal(res.status,403);
  assert.equal(await res.text(),'Ingress only');
});

test('the private HTML viewer has CSP and rejects every write method', async t => {
  const app = createArchiveServer({ apiToken: 'test-api-token', allowPeer: () => true });
  await new Promise(resolve => app.listen(0,'127.0.0.1',resolve));
  t.after(() => app.close());
  const url = 'http://127.0.0.1:' + app.address().port;
  const page = await fetch(url);
  assert.equal(page.status,200);
  assert.match(await page.text(),/WhatsApp Archive/);
  assert.match(page.headers.get('content-security-policy'),/script-src 'self'/);
  assert.equal(page.headers.get('cache-control'),'no-store');
  for (const method of ['POST','PATCH','DELETE']) {
    const res = await fetch(url+'/api/messages',{ method });
    assert.equal(res.status,405,method);
  }
  const denied = await fetch(url+'/api/send');
  assert.equal(denied.status,404);
});
