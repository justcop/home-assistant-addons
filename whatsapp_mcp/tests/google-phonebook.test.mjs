import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { ArchiveData } from '../archive-data.mjs';
import { GooglePhonebookBridge } from '../google-phonebook-sync.mjs';
import { childEnvironment } from '../run.mjs';

function fixture(t) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'google-phonebook-bridge-'));
  const db=new DatabaseSync(path.join(dir,'whatsapp.db'));
  db.exec(`
    CREATE TABLE contacts(id TEXT,phone_number TEXT,lid TEXT,name TEXT,notify TEXT);
    CREATE TABLE chats(id TEXT PRIMARY KEY,name TEXT,is_group INTEGER NOT NULL,unread_count INTEGER NOT NULL,archived INTEGER NOT NULL,muted_until INTEGER,participant_count INTEGER);
    CREATE TABLE messages(rowid INTEGER PRIMARY KEY,chat_id TEXT,id TEXT,sender_id TEXT,ts INTEGER,from_me INTEGER,kind TEXT,text TEXT,transcript TEXT,quoted_id TEXT,status TEXT,edited_ts INTEGER,deleted_ts INTEGER,media_type TEXT,media_sha TEXT);
    CREATE TABLE reactions(chat_id TEXT,message_id TEXT,sender_id TEXT);
    INSERT INTO contacts VALUES('447700900123@s.whatsapp.net','447700900123',NULL,'WhatsApp Alias',NULL);
    INSERT INTO chats VALUES('447700900123@s.whatsapp.net',NULL,0,0,0,NULL,NULL);
    INSERT INTO messages(chat_id,id,sender_id,ts,from_me,kind,text) VALUES('447700900123@s.whatsapp.net','a','447700900123@s.whatsapp.net',1,0,'text','Hello');
  `);
  db.close();
  const store=new ArchiveData(dir);
  t.after(()=>{store.close();fs.rmSync(dir,{recursive:true,force:true});});
  return store;
}
test('third-party WhatsApp API and MCP children never receive Home Assistant admin token',()=>{
  const child=childEnvironment({PORT:'8097'},{PATH:'/usr/bin',SUPERVISOR_TOKEN:'super-secret',HASSIO_TOKEN:'secret2',HASSIO_API:'secret3',LOG_LEVEL:'info'});
  assert.equal(child.PORT,'8097');
  assert.equal(child.SUPERVISOR_TOKEN,undefined);
  assert.equal(child.HASSIO_TOKEN,undefined);
  assert.equal(child.HASSIO_API,undefined);
});
test('bridge uses Google Phonebook only and persists read-only name results',async t=>{
  const store=fixture(t);
  const requests=[];
  const bridge=new GooglePhonebookBridge(store,{token:'test-secret',fetchImpl:async(url,init)=>{
    requests.push({url,method:init.method,auth:init.headers.Authorization,body:init.body});
    if(url.endsWith('/services')){
      return {ok:true,json:async()=>[{domain:'google_phonebook',services:{lookup:{}}}]};
    }
    if(url.includes('/services/google_phonebook/lookup?return_response')){
      return {ok:true,json:async()=>({service_response:{found:true,name:'Jane Google'}})};
    }
    return {ok:false,status:404};
  }});
  t.after(()=>bridge.stop());
  assert.equal(await bridge.sync(),true);
  assert.equal(bridge.status().matched,1);
  assert.equal(store.listChats(new URLSearchParams()).items[0].name,'Jane Google');
  assert.equal(requests[1].method,'POST');
  assert.deepEqual(JSON.parse(requests[1].body),{phone_number:'+447700900123'});
  assert.ok(requests.every(r=>r.auth==='Bearer test-secret'));
  assert.ok(requests.every(r=>r.url.includes('/services')));
  assert.equal(store.loadAliases(store.googleFile)['447700900123'],'Jane Google');
  store.mergeAliases([{phone:'447700900123',name:'My Manual Label'}]);
  assert.equal(store.displayName('447700900123@s.whatsapp.net'),'My Manual Label');
});
test('missing OAuth service never clears existing contact names',async t=>{
  const store=fixture(t);
  store.setGoogleNames({'447700900123':'Old Name'});
  const bridge=new GooglePhonebookBridge(store,{token:'test-secret',fetchImpl:async()=>({ok:true,json:async()=>[]})});
  t.after(()=>bridge.stop());
  assert.equal(await bridge.sync(),false);
  assert.equal(bridge.status().status,'setup_required');
  assert.equal(store.displayName('447700900123@s.whatsapp.net'),'Old Name');
});
test('unknown/not-authorized service failures preserve local Google contact cache',async t=>{
  const store=fixture(t);
  store.setGoogleNames({'447700900123':'Old Name'});
  const bridge=new GooglePhonebookBridge(store,{token:'test-secret',fetchImpl:async(url)=>{
    if(url.endsWith('/services'))return {ok:true,json:async()=>[{domain:'google_phonebook',services:{lookup:{}}}]};
    return {ok:false,status:401};
  }});
  t.after(()=>bridge.stop());
  assert.equal(await bridge.sync(),false);
  assert.equal(store.displayName('447700900123@s.whatsapp.net'),'Old Name');
});
