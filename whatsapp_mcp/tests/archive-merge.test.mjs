import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { exportTrialSnapshot } from '../../whatsapp_fullsync_trial/trial-export.mjs';
import { mergeStatus,scheduleMerge,runScheduledArchiveMerge,finaliseMerge } from '../archive-merge.mjs';

function createDb(file) {
  const db=new DatabaseSync(file);
  db.exec(
    'CREATE TABLE chats (id TEXT PRIMARY KEY,name TEXT,is_group INTEGER NOT NULL,last_message_ts INTEGER,unread_count INTEGER NOT NULL,archived INTEGER NOT NULL,muted_until INTEGER,participant_count INTEGER,raw TEXT) STRICT;'+
    'CREATE TABLE contacts (id TEXT PRIMARY KEY,phone_number TEXT,lid TEXT,name TEXT,notify TEXT,raw TEXT) STRICT;'+
    'CREATE TABLE messages (rowid INTEGER PRIMARY KEY,chat_id TEXT NOT NULL,id TEXT NOT NULL,sender_id TEXT NOT NULL,ts INTEGER NOT NULL,from_me INTEGER NOT NULL,kind TEXT NOT NULL,text TEXT,transcript TEXT,quoted_id TEXT,status TEXT,edited_ts INTEGER,deleted_ts INTEGER,media_type TEXT,media_sha TEXT,raw BLOB,ptt INTEGER,duration_s INTEGER,transcript_model TEXT,transcript_language TEXT) STRICT;'+
    'CREATE UNIQUE INDEX messages_chat_id_id ON messages(chat_id,id);'+
    'CREATE TABLE reactions (chat_id TEXT,message_id TEXT,sender_id TEXT,emoji TEXT,ts INTEGER,PRIMARY KEY(chat_id,message_id,sender_id)) STRICT;'+
    'CREATE TABLE privacy_suppressed_messages (message_digest TEXT PRIMARY KEY,reason TEXT NOT NULL,observed_ts INTEGER NOT NULL) STRICT;'+
    'CREATE TABLE auth_creds (key TEXT PRIMARY KEY,value TEXT NOT NULL) STRICT;'+
    'CREATE TABLE auth_keys (type TEXT NOT NULL,id TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(type,id)) STRICT;'+
    "CREATE VIRTUAL TABLE messages_fts USING fts5(text,transcript,content='messages',content_rowid='rowid');"+
    'CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN INSERT INTO messages_fts(rowid,text,transcript) VALUES(new.rowid,new.text,new.transcript); END;'+
    "CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN INSERT INTO messages_fts(messages_fts,rowid,text,transcript) VALUES('delete',old.rowid,old.text,old.transcript); END;"+
    "CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN INSERT INTO messages_fts(messages_fts,rowid,text,transcript) VALUES('delete',old.rowid,old.text,old.transcript); INSERT INTO messages_fts(rowid,text,transcript) VALUES(new.rowid,new.text,new.transcript); END;"
  );
  return db;
}
function chat(db,id,name) {
  db.prepare('INSERT INTO chats VALUES (?,?,0,100,5,0,NULL,0,NULL)').run(id,name);
}
function message(db,chatId,id,text,mediaSha=null) {
  db.prepare('INSERT INTO messages(chat_id,id,sender_id,ts,from_me,kind,text,media_sha,raw) VALUES (?,?,?,100,0,?,?,?,?,?)')
    .run(chatId,id,'self@s.whatsapp.net','text',text,mediaSha,Buffer.from('raw'));
}
test('one-time merge imports only missing keys, never device auth, preserves production messages and rebuilds search',()=>{
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'wa-consolidation-test-'));
  try {
    const liveDir=path.join(temp,'live'),trialDir=path.join(temp,'trial'),transfer=path.join(temp,'transfer');
    for(const p of [liveDir,trialDir,transfer])fs.mkdirSync(p);
    const prod=createDb(path.join(liveDir,'whatsapp.db'));
    chat(prod,'me@s.whatsapp.net','Production');
    message(prod,'me@s.whatsapp.net','overlap','Keep authoritative production body');
    message(prod,'me@s.whatsapp.net','live-only','Keep unique live message');
    prod.prepare("INSERT INTO auth_creds VALUES('device','PRODUCTION-AUTH')").run();
    prod.close();
    const trial=createDb(path.join(trialDir,'whatsapp.db'));
    chat(trial,'me@s.whatsapp.net','Conflicting trial label');
    chat(trial,'new@g.us','Trial Only Group');
    message(trial,'me@s.whatsapp.net','overlap','DO NOT OVERWRITE production');
    message(trial,'me@s.whatsapp.net','trial-only','Unique history recovered by trial');
    message(trial,'new@g.us','group-only','Unique group record');
    message(trial,'new@g.us','suppressed','Should not be resurrected');
    const hash=createHash('sha256').update('new@g.us').update('\0').update('suppressed').digest('hex');
    trial.prepare("INSERT INTO privacy_suppressed_messages VALUES (?,'delete_for_me',100)").run(hash);
    trial.prepare("INSERT INTO reactions VALUES ('new@g.us','group-only','sender@s.whatsapp.net','👍',100)").run();
    trial.prepare("INSERT INTO auth_creds VALUES('device','TRIAL-AUTH-DO-NOT-COPY')").run();
    trial.close();

    const exported=exportTrialSnapshot(trialDir,transfer);
    assert.equal(exported.messages,4);
    const source=path.join(transfer,'trial-messages.sqlite');
    const exportDB=new DatabaseSync(source,{readOnly:true});
    assert.equal(exportDB.prepare("SELECT COUNT(*) AS n FROM sqlite_master WHERE name IN ('auth_creds','auth_keys')").get().n,0);
    exportDB.close();
    const request=path.join(temp,'request'),report=path.join(temp,'report');
    const before=mergeStatus(liveDir,source,report,request);
    assert.equal(before.production,2);
    assert.equal(before.trial,4);
    assert.equal(before.overlap,1);
    assert.equal(before.trialOnly,3);
    assert.equal(scheduleMerge(liveDir,source,request).accepted,true);
    const finished=runScheduledArchiveMerge(liveDir,{requestFile:request,transferFile:source,reportFile:report});
    assert.equal(finished.success,true,finished.error);
    assert.equal(finished.added,2);
    assert.equal(finished.after,4);
    assert.equal(finished.skippedSuppressed,1);
    assert.ok(fs.existsSync(path.join(liveDir,'.whatsapp-before-trial-merge.sqlite')));
    const merged=new DatabaseSync(path.join(liveDir,'whatsapp.db'),{readOnly:true});
    assert.equal(merged.prepare("SELECT value FROM auth_creds WHERE key='device'").get().value,'PRODUCTION-AUTH');
    assert.equal(merged.prepare("SELECT text FROM messages WHERE id='overlap'").get().text,'Keep authoritative production body');
    assert.equal(merged.prepare("SELECT COUNT(*) AS n FROM messages WHERE id='suppressed'").get().n,0);
    assert.equal(merged.prepare("SELECT COUNT(*) AS n FROM messages_fts WHERE messages_fts MATCH 'Unique'").get().n,2);
    assert.equal(merged.prepare("SELECT COUNT(*) AS n FROM reactions").get().n,1);
    assert.equal(merged.prepare("SELECT unread_count FROM chats WHERE id='me@s.whatsapp.net'").get().unread_count,5);
    merged.close();
    const fin=finaliseMerge(liveDir,{transferFile:source,reportFile:report});
    assert.equal(fin.finalised,true);
    assert.equal(fs.existsSync(source),false);
    assert.equal(fs.existsSync(path.join(liveDir,'.whatsapp-before-trial-merge.sqlite')),false);
  } finally {fs.rmSync(temp,{recursive:true,force:true});}
});
