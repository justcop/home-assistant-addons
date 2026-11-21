import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { ArchiveData, normalisePhone, isHumanName } from '../archive-data.mjs';

test('phone numbers are normalised without inventing contact names',()=>{
  assert.equal(normalisePhone('+44 (0) 7700 900123'),'447700900123');
  assert.equal(normalisePhone('07700 900123'),'447700900123');
  assert.equal(normalisePhone('+44 7700 900123'),'447700900123');
  assert.equal(normalisePhone('0044 7700 900123'),'447700900123');
  assert.equal(isHumanName('+447700900123'),false);
  assert.equal(isHumanName('Jane Doe'),true);
});
function fixture(t) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'archive-test-'));
  const db=new DatabaseSync(path.join(dir,'whatsapp.db'));
  db.exec(`
    CREATE TABLE chats(id TEXT PRIMARY KEY,name TEXT,is_group INTEGER NOT NULL,unread_count INTEGER NOT NULL,archived INTEGER NOT NULL,muted_until INTEGER,participant_count INTEGER);
    CREATE TABLE messages(rowid INTEGER PRIMARY KEY,chat_id TEXT,id TEXT,sender_id TEXT,ts INTEGER,from_me INTEGER,kind TEXT,text TEXT,transcript TEXT,quoted_id TEXT,status TEXT,edited_ts INTEGER,deleted_ts INTEGER,media_type TEXT,media_sha TEXT);
    CREATE TABLE reactions(chat_id TEXT,message_id TEXT,sender_id TEXT);
    CREATE TABLE contacts(id TEXT,phone_number TEXT,lid TEXT,name TEXT,notify TEXT);
    CREATE INDEX messages_chat_ts ON messages(chat_id,ts DESC);
    INSERT INTO chats VALUES
     ('old@g.us','Leaving BMA',1,0,0,NULL,20),
     ('447700900123@s.whatsapp.net',NULL,0,0,0,NULL,NULL),
     ('empty@g.us','Empty',1,0,0,NULL,10);
    INSERT INTO contacts VALUES('447700900123@s.whatsapp.net','447700900123',NULL,NULL,'WhatsApp nickname');
    INSERT INTO messages(chat_id,id,sender_id,ts,from_me,kind,text) VALUES
      ('old@g.us','real','447700900123@s.whatsapp.net',100,0,'text','Old real conversation'),
      ('old@g.us','sys1','447700900123@s.whatsapp.net',500,1,'system','Security code changed'),
      ('old@g.us','sys2','447700900123@s.whatsapp.net',510,1,'other',NULL),
      ('old@g.us','sys3','447700900123@s.whatsapp.net',520,1,'other',''),
      ('447700900123@s.whatsapp.net','active','447700900123@s.whatsapp.net',400,0,'text','Hello world');
  `);
  db.close();
  const store=new ArchiveData(dir);
  t.after(()=>{store.close();fs.rmSync(dir,{recursive:true,force:true});});
  return store;
}

test('protocol security notices are excluded from activity and messages but preserved in SQLite', t=>{
  const store=fixture(t);
  const chats=store.listChats(new URLSearchParams('limit=20'));
  assert.deepEqual(chats.items.map(x=>x.id),[
    '447700900123@s.whatsapp.net','old@g.us','empty@g.us'
  ]);
  assert.equal(chats.items[1].lastMessageTs,100);
  assert.equal(chats.items[2].lastMessageTs,null);
  const visible=store.listMessages(new URLSearchParams('chat=old%40g.us&limit=10'));
  assert.deepEqual(visible.items.map(x=>x.id),['real']);
  assert.equal(store.db.prepare("SELECT COUNT(*) AS n FROM messages WHERE chat_id='old@g.us'").get().n,4);
});
test('synchronised WhatsApp contact names appear without an imported phonebook', t=>{
  const store=fixture(t);
  const chats=store.listChats(new URLSearchParams());
  assert.equal(chats.items[1].name,'WhatsApp nickname');
  assert.equal(store.listMessages(new URLSearchParams('chat=old%40g.us')).items[0].sender.name,'WhatsApp nickname');
});
test('visible message pagination does not leave pages of protocol notices',t=>{
  const store=fixture(t);
  const only=store.listMessages(new URLSearchParams('chat=old%40g.us&limit=1'));
  assert.equal(only.items.length,1);
  assert.equal(only.nextCursor,null);
  const first=store.listChats(new URLSearchParams('limit=1'));
  assert.equal(first.items.length,1);
  assert.equal(first.nextCursor,'1');
  const second=store.listChats(new URLSearchParams('limit=1&cursor=1'));
  assert.equal(second.items.length,1);
  assert.equal(second.items[0].id,'old@g.us');
  assert.throws(()=>store.listMessages(new URLSearchParams('chat=old%40g.us&cursor=-1')),RangeError);
});

test('archive date range includes raw WhatsApp events separately from readable messages', t => {
  const store=fixture(t);
  const stats=store.archiveDateRange();
  assert.deepEqual(stats,{
    raw:{count:5,oldest:100,newest:520},
    messages:{count:2,oldest:100,newest:400},
    chatCount:3,
    kindCounts:{other:2,system:1,text:2},
    emptyOtherCount:2
  });
});
