import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createArchiveServer } from '../archive-server.mjs';

test('admin ingress can requeue only probably complete chats and show exact archived count', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(),'whatsapp-backfill-review-'));
  t.after(() => fs.rmSync(dir,{recursive:true,force:true}));
  const checkpoint = {
    schema:2,active:true,requests:8,requestsToday:8,importedEstimate:50,
    chats:{
      'friend@s.whatsapp.net':{
        id:'friend@s.whatsapp.net',phase:'likely_complete',
        completionBasis:'empty_followup',requests:5,importedEstimate:40,earliest:100
      },
      'group@g.us':{id:'group@g.us',phase:'queued',requests:3,importedEstimate:10,earliest:150}
    }
  };
  const cpFile = path.join(dir,'.history-backfill-v2.json');
  fs.writeFileSync(cpFile,JSON.stringify(checkpoint));
  const app = createArchiveServer({
    apiToken:'test',storagePath:dir,allowPeer:()=>true,
    dataStore:{
      chatNameStmt:{ get:() => ({ name:'Friend',isGroup:0 }) },
      chatMessageCountStmt:{ get:() => ({total:203}) },
      displayName:()=> 'Friend'
    }
  });
  await new Promise(resolve => app.listen(0,'127.0.0.1',resolve));
  t.after(() => new Promise(resolve=>app.close(resolve)));
  const base = 'http://127.0.0.1:' + app.address().port;
  const endpoint = base+'/api/archive/backfill/recheck';
  async function recheck(chatId,headers={
    'X-WhatsApp-Backfill-Action':'1','Content-Type':'application/json'
  }){
    return fetch(endpoint,{method:'POST',headers,
      body:JSON.stringify({action:'recheck',chatId})});
  }
  assert.equal((await recheck('friend@s.whatsapp.net',{'Content-Type':'application/json'})).status,403);
  assert.equal((await recheck('group@g.us')).status,409);
  assert.equal((await recheck('unknown@s.whatsapp.net')).status,404);
  assert.equal((await recheck('friend@s.whatsapp.net')).status,200);
  const overrides=JSON.parse(fs.readFileSync(path.join(dir,'.history-backfill-overrides-v1.json'),'utf8'));
  assert.ok(Number.isSafeInteger(overrides['friend@s.whatsapp.net']) && overrides['friend@s.whatsapp.net'] > 0,
    'Manual recheck uses a nonce so the same chat can be requeued repeatedly');
  assert.deepEqual(JSON.parse(fs.readFileSync(cpFile,'utf8')),checkpoint,
    'The active backfill checkpoint must not be overwritten by a manual action');
  const response=await fetch(base+'/api/archive/backfill');
  assert.equal(response.status,200);
  const recent=(await response.json()).recent;
  const friend=recent.find(c=>c.name==='Friend'&&c.id==='friend@s.whatsapp.net');
  assert.equal(friend.archivedMessages,203);
  assert.equal(friend.manualRecheck,true);
  assert.equal(friend.phase,'recheck_requested');
  assert.equal((await recheck('friend@s.whatsapp.net')).status,200,
    'Repeating a recheck must be idempotent');
});

test('non-ingress clients cannot submit manual recheck actions',async t=>{
  const app=createArchiveServer({apiToken:'test'});
  await new Promise(resolve=>app.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>app.close(resolve)));
  const response=await fetch('http://127.0.0.1:'+app.address().port+
    '/api/archive/backfill/recheck',{method:'POST',headers:{
      'X-WhatsApp-Backfill-Action':'1','Content-Type':'application/json'
    },body:'{}'});
  assert.equal(response.status,403);
});
