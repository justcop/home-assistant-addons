import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { pruneMetaAI, META_AI_JID } from '../prune-meta-ai.mjs';

test('pruneMetaAI removes only the explicit Meta AI JID from local SQLite', t => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'whatsapp-meta-prune-'));
  t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const db=new DatabaseSync(path.join(dir,'whatsapp.db'));
  db.exec(`
    PRAGMA foreign_keys=ON;
    CREATE TABLE chats(id TEXT PRIMARY KEY);
    CREATE TABLE messages(chat_id TEXT REFERENCES chats(id) ON DELETE CASCADE,id TEXT,kind TEXT,text TEXT);
    CREATE TABLE reactions(chat_id TEXT,message_id TEXT,sender_id TEXT);
    INSERT INTO chats VALUES('13135550002@s.whatsapp.net'),('normal@s.whatsapp.net');
    INSERT INTO messages VALUES('13135550002@s.whatsapp.net','a','text','Meta AI'),('normal@s.whatsapp.net','b','text','keep');
    INSERT INTO reactions VALUES('13135550002@s.whatsapp.net','a','x');
  `);
  db.close();
  assert.equal(pruneMetaAI(dir),1);
  assert.equal(pruneMetaAI(dir),0);
  const verify=new DatabaseSync(path.join(dir,'whatsapp.db'),{readOnly:true});
  assert.equal(verify.prepare('SELECT count(*) AS n FROM chats WHERE id = ?').get(META_AI_JID).n,0);
  assert.equal(verify.prepare('SELECT count(*) AS n FROM messages').get().n,1);
  assert.equal(verify.prepare('SELECT count(*) AS n FROM reactions').get().n,0);
  verify.close();
});
