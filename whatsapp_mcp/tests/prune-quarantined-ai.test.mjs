
import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pruneQuarantinedAi } from '../prune-quarantined-ai.mjs';
const pn='447700900111';
const self=pn+'@s.whatsapp.net';
const lid='1234567890@lid';
function fixture(t, label='Unclassified own-LID messages') {
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'prune-ai-'));
 const db=new DatabaseSync(path.join(dir,'whatsapp.db'));
 db.exec([
 'PRAGMA foreign_keys=ON;',
 'CREATE TABLE contacts(id TEXT PRIMARY KEY, phone_number TEXT,lid TEXT);',
 'CREATE TABLE chats(id TEXT PRIMARY KEY,name TEXT);',
 'CREATE TABLE messages(chat_id TEXT NOT NULL REFERENCES chats(id),id TEXT,media_sha TEXT,text TEXT,PRIMARY KEY(chat_id,id));',
 'CREATE TABLE reactions(chat_id TEXT,message_id TEXT,sender_id TEXT,PRIMARY KEY(chat_id,message_id,sender_id));',
 'CREATE TABLE privacy_suppressed_messages(message_digest TEXT PRIMARY KEY,reason TEXT,observed_ts INTEGER);'
 ].join('\n'));
 db.prepare('INSERT INTO contacts VALUES(?,?,?)').run(self,pn,'1234567890');
 db.prepare('INSERT INTO chats VALUES(?,?)').run(self,'Messages to Yourself');
 db.prepare('INSERT INTO chats VALUES(?,?)').run(lid,label);
 db.prepare('INSERT INTO messages VALUES(?,?,?,?)').run(self,'genuine',null,'Keep me');
 db.prepare('INSERT INTO messages VALUES(?,?,?,?)').run(lid,'ai1',null,'Meta prompt');
 db.prepare('INSERT INTO reactions VALUES(?,?,?)').run(lid,'ai1',self);
 t.after(()=>{db.close();fs.rmSync(dir,{recursive:true,force:true})});
 return {db,dir};
}
test('purges only exact quarantined LID chat, retaining real self message',t=>{
 const {db,dir}=fixture(t);
 const r=pruneQuarantinedAi(dir,pn);
 assert.equal(r.removed,1);
 assert.equal(db.prepare('SELECT text FROM messages WHERE chat_id=?').get(self).text,'Keep me');
 assert.equal(db.prepare('SELECT COUNT(*) AS n FROM messages WHERE chat_id=?').get(lid).n,0);
 assert.equal(db.prepare('SELECT COUNT(*) AS n FROM reactions').get().n,0);
 assert.equal(db.prepare('SELECT COUNT(*) AS n FROM privacy_suppressed_messages').get().n,1);
 assert.equal(pruneQuarantinedAi(dir,pn).removed,0);
});
test('wrong quarantine label prevents deletion',t=>{
 const {db,dir}=fixture(t,'Another conversation');
 assert.equal(pruneQuarantinedAi(dir,pn).removed,0);
 assert.equal(db.prepare('SELECT COUNT(*) AS n FROM messages').get().n,2);
});
