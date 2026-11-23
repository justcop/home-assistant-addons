import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { restoreSelfLid } from '../restore-self-lid.mjs';

const number = '447700900111';
const self = number + '@s.whatsapp.net';
const lid = '123456789012345@lid';
const decode = bytes => JSON.parse(Buffer.from(bytes).toString('utf8'));

function fixture(t, label = 'Unclassified own-LID messages') {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'whatsapp-restore-'));
  const db = new DatabaseSync(path.join(dir, 'whatsapp.db'));
  db.exec([
    'PRAGMA foreign_keys=ON;',
    'CREATE TABLE contacts(id TEXT PRIMARY KEY,phone_number TEXT,lid TEXT);',
    'CREATE TABLE chats(id TEXT PRIMARY KEY,name TEXT,is_group INTEGER NOT NULL DEFAULT 0,last_message_ts INTEGER);',
    'CREATE TABLE messages(rowid INTEGER PRIMARY KEY,chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,id TEXT NOT NULL,ts INTEGER NOT NULL,from_me INTEGER NOT NULL,text TEXT,raw BLOB,UNIQUE(chat_id,id));',
    'CREATE TABLE reactions(chat_id TEXT NOT NULL,message_id TEXT NOT NULL,sender_id TEXT NOT NULL,PRIMARY KEY(chat_id,message_id,sender_id));',
    'CREATE TABLE privacy_suppressed_messages(message_digest TEXT PRIMARY KEY,reason TEXT,observed_ts INTEGER);',
    "CREATE VIRTUAL TABLE messages_fts USING fts5(text,content='messages',content_rowid='rowid');",
    'CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN INSERT INTO messages_fts(rowid,text) VALUES(new.rowid,new.text); END;',
    "CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN INSERT INTO messages_fts(messages_fts,rowid,text) VALUES('delete',old.rowid,old.text); END;",
    "CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN INSERT INTO messages_fts(messages_fts,rowid,text) VALUES('delete',old.rowid,old.text); INSERT INTO messages_fts(rowid,text) VALUES(new.rowid,new.text); END;"
  ].join('\n'));
  db.prepare('INSERT INTO contacts VALUES(?,?,?)').run(self, number, lid.slice(0, -4));
  db.prepare('INSERT INTO chats(id,name) VALUES(?,?)').run(self, 'Messages to Yourself');
  db.prepare('INSERT INTO chats(id,name) VALUES(?,?)').run(lid, label);
  const put = db.prepare('INSERT INTO messages(chat_id,id,ts,from_me,text,raw) VALUES(?,?,?,?,?,?)');
  const add = (chat, id, remote, ts, text, fromMe = true) =>
    put.run(chat,id,ts,fromMe ? 1:0,text,Buffer.from(JSON.stringify({key:{remoteJid:remote,fromMe}})));
  db.prepare('INSERT INTO privacy_suppressed_messages VALUES(?,?,?)')
    .run('previously-purged-meta-ai-digest','excluded_own_lid',1000);
  t.after(() => { db.close(); fs.rmSync(dir,{recursive:true,force:true}); });
  return { db, dir, add };
}

test('misrouted self-LID message moves to real self-chat with FTS and reactions intact', async t => {
  const { db, dir, add } = fixture(t);
  add(self, 'old', self, 100, 'old genuine self');
  add(lid, 'new', lid, 200, 'Test genuine self');
  db.prepare('INSERT INTO reactions VALUES(?,?,?)').run(lid,'new',self);
  const result = await restoreSelfLid(dir, number, decode);
  assert.deepEqual(result,{restored:1,remaining:0,reason:'restored_to_self_chat'});
  const messages=db.prepare('SELECT chat_id,id,text,raw FROM messages ORDER BY id').all();
  assert.deepEqual(messages.map(m=>[m.chat_id,m.id,m.text]),[
    [self,'new','Test genuine self'],
    [self,'old','old genuine self']
  ]);
  assert.equal(decode(messages[0].raw).key.remoteJid,lid);
  assert.equal(db.prepare('SELECT chat_id FROM reactions').get().chat_id,self);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM messages_fts WHERE messages_fts MATCH 'genuine'").get().n,2);
  assert.equal(db.prepare('SELECT last_message_ts FROM chats WHERE id=?').get(self).last_message_ts,200);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM chats WHERE id=?').get(lid).n,0);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM privacy_suppressed_messages').get().n,1);
  assert.deepEqual(await restoreSelfLid(dir, number, decode),{restored:0,reason:'no_legacy_quarantine'});
});

test('unknown envelopes and messages not sent by account remain quarantined', async t => {
  const { db, dir, add } = fixture(t);
  add(lid,'not-ours','987654321@lid',200,'not self');
  add(lid,'inbound',lid,201,'not sent by us',false);
  const result=await restoreSelfLid(dir,number,decode);
  assert.deepEqual(result,{restored:0,remaining:2,reason:'restored_to_self_chat'});
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM messages WHERE chat_id=?').get(lid).n,2);
});

test('never move ordinary chat named differently',async t=>{
  const {db,dir,add}=fixture(t,'A real contact');
  add(lid,'new',lid,200,'Keep');
  assert.deepEqual(await restoreSelfLid(dir,number,decode),{restored:0,reason:'no_legacy_quarantine'});
  assert.equal(db.prepare('SELECT text FROM messages WHERE chat_id=?').get(lid).text,'Keep');
});

test('duplicate message IDs abort without destroying either chat',async t=>{
  const {db,dir,add}=fixture(t);
  add(self,'same',self,100,'original');
  add(lid,'same',lid,200,'duplicate');
  await assert.rejects(restoreSelfLid(dir,number,decode),/collision/);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM messages').get().n,2);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM privacy_suppressed_messages').get().n,1);
});
