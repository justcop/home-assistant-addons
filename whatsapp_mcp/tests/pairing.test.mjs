import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createArchiveServer } from '../archive-server.mjs';

test('pairing stays on admin ingress, never on WhatsApp message write routes', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'whatsapp-pairing-'));
  t.after(() => fs.rmSync(dir, { recursive:true, force:true }));
  const app = createArchiveServer({ apiToken:'test', pairingDir:dir, allowPeer:()=>true });
  await new Promise(resolve => app.listen(0, '127.0.0.1', resolve));
  t.after(() => app.close());
  const base = 'http://127.0.0.1:' + app.address().port;
  const oldFetch = globalThis.fetch;
  globalThis.fetch = (url, ...args) => String(url).includes('127.0.0.1:8097/health')
    ? Promise.resolve({ json: async () => ({connection:'pairing',needs_pairing:true,counts:{messages:0,chats:0}}) })
    : oldFetch(url,...args);
  t.after(() => { globalThis.fetch=oldFetch; });
  let reply = await oldFetch(base+'/api/pairing/status');
  assert.equal(reply.status, 200);
  assert.equal((await reply.json()).needsPairing,true);
  reply = await oldFetch(base+'/api/pairing/phone',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:'447700900123'})});
  assert.equal(reply.status,403);
  const headers={'Content-Type':'application/json','X-WhatsApp-Pairing-Action':'1'};
  reply = await oldFetch(base+'/api/pairing/phone',{method:'POST',headers,body:JSON.stringify({phone:'447700900123'})});
  assert.equal(reply.status,200);
  assert.equal(fs.readFileSync(path.join(dir,'.whatsapp-pairing-phone'),'utf8').trim(),'447700900123');
  reply = await oldFetch(base+'/api/pairing/repair',{method:'POST',headers,body:JSON.stringify({confirm:'no'})});
  assert.equal(reply.status,400);
  assert.equal(fs.existsSync(path.join(dir,'.whatsapp-repair-request')),false);
  reply = await oldFetch(base+'/api/pairing/repair',{method:'POST',headers,body:JSON.stringify({confirm:'REPAIR THIS DEVICE'})});
  assert.equal(reply.status,200);
  assert.equal(fs.existsSync(path.join(dir,'.whatsapp-repair-request')),true);
  reply = await oldFetch(base+'/api/send',{method:'POST',headers,body:'{}'});
  assert.equal(reply.status,405);
});

test('pairing code and prominent panel vanish once WhatsApp connects', () => {
  const html=fs.readFileSync(new URL('../archive-ui/index.html',import.meta.url),'utf8');
  const js=fs.readFileSync(new URL('../archive-ui/app.js',import.meta.url),'utf8');
  for(const id of ['pairing-banner','pairing-phone-form','pairing-code','pairing-repair','settings-pairing-status'])
    assert.match(html,new RegExp('id="'+id+'"'));
  assert.match(js,/pairing-banner'\)\.classList\.toggle\('hidden', !status\.needsPairing\)/);
  assert.match(js,/setInterval\(refreshPairing, 5000\)/);
  assert.match(js,/REPAIR THIS DEVICE/);
});
