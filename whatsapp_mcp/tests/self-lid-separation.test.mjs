import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { separateSelfLid } from '../self-lid-separation.mjs';

const self='447700900111@s.whatsapp.net';
const lid='123456789012345@lid';
const decode=bytes=>JSON.parse(Buffer.from(bytes).toString('utf8'));
function fixture(t) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'self-lid-test-'));
  const db=new DatabaseSync(path.join(dir,'whatsapp.db'));
  db.exec([
    'PRAGMA foreign_keys=ON;',
    'CREATE TABLE contacts(id TEXT,phone_number TEXT,lid TEXT);',
    'CREATE TABLE chats(id TEXT PRIMARY KEY,name TEXT,is_group INTEGER DEFAULT 0,last_message_ts INTEGER);',
    'CREATE TABLE messages(rowid INTEGER PRIMARY KEY,chat_id TEXT NOT NULL REFERENCES chats(id),id TEXT,ts INTEGER,text TEXT,raw BLOB,UNIQUE(chat_id,id));',
    'CREATE TABLE reactions(chat_id TEXT,message_id TEXT,sender_id TEXT,emoji TEXT,ts INTEGER,PRIMARY KEY(chat_id,message_id,sender_id));',
    "CREATE VIRTUAL TABLE messages_fts USING fts5(text,content='messages',content_rowid='rowid');",
    'CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN INSERT INTO messages_fts(rowid,text) VALUES(new.rowid,new.text); END;',
    "CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN INSERT INTO messages_fts(messages_fts,rowid,text) VALUES('delete',old.rowid,old.text); END;",
    "CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN INSERT INTO messages_fts(messages_fts,rowid,text) VALUES('delete',old.rowid,old.text); INSERT INTO messages_fts(rowid,text) VALUES(new.rowid,new.text); END;"
  ].join('\n'));
  db.prepare('INSERT INTO contacts(id,phone_number,lid) VALUES(?,?,?)').run(self,'447700900111','123456789012345');
  db.prepare('INSERT INTO chats(id,name) VALUES(?,?)').run(self,'Yourself');
  const put=db.prepare('INSERT INTO messages(chat_id,id,ts,text,raw) VALUES(?,?,?,?,?)');
  function add(id,remote,ts,text){put.run(self,id,ts,text,Buffer.from(JSON.stringify({key:{remoteJid:remote}})))}
  t.after(()=>{db.close();fs.rmSync(dir,{recursive:true,force:true})});
  return {dir,db,add};
}
test('self LID provenance is separated, not deleted; phone JID and reactions remain',async t=>{
  const {db,dir,add}=fixture(t);
  add('phone',self,100,'genuine self');
  add('agent',lid,200,'ambiguous prompt');
  add('agent2','123456789012345:77@lid',300,'another prompt');
  db.prepare('INSERT INTO reactions VALUES(?,?,?,?,?)').run(self,'agent','someone','thumbs-up',200);
  assert.deepEqual(await separateSelfLid(dir,'447700900111',decode),{moved:2,reason:'quarantined'});
  const rows=db.prepare('SELECT chat_id,id,text,raw FROM messages ORDER BY id').all().map(r=>({chat_id:r.chat_id,id:r.id,text:r.text,original:decode(r.raw).key.remoteJid}));
  assert.deepEqual(rows,[
    {chat_id:lid,id:'agent',text:'ambiguous prompt',original:lid},
    {chat_id:lid,id:'agent2',text:'another prompt',original:'123456789012345:77@lid'},
    {chat_id:self,id:'phone',text:'genuine self',original:self}
  ]);
  assert.equal(db.prepare('SELECT chat_id FROM reactions').get().chat_id,lid);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM messages_fts WHERE messages_fts MATCH 'prompt'").get().n,2);
  assert.equal(db.prepare('SELECT name FROM chats WHERE id=?').get(lid).name,'Unclassified own-LID messages');
  assert.deepEqual(await separateSelfLid(dir,'447700900111',decode),{moved:0,reason:'none'});
});
test('malformed envelopes stay untouched; other LIDs are not moved',async t=>{
  const {db,dir,add}=fixture(t);
  add('unrelated','9999999999@lid',100,'unrelated');
  db.prepare('INSERT INTO messages(chat_id,id,ts,text,raw) VALUES(?,?,?,?,?)').run(self,'bad',200,'untouched',Buffer.from('corrupt'));
  assert.deepEqual(await separateSelfLid(dir,'447700900111',decode),{moved:0,reason:'none'});
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM messages WHERE chat_id=?').get(self).n,2);
});
