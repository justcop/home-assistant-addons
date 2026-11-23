import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { makePrivacyRetention } from "../privacy-retention.ts";

function fixture(t) {
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),"wa-private-delete-"));
  const mediaDir=path.join(directory,"media");
  fs.mkdirSync(mediaDir);
  const oldMedia=process.env.WHATSAPP_MEDIA_DIR;
  process.env.WHATSAPP_MEDIA_DIR=mediaDir;
  const db=new DatabaseSync(path.join(directory,"wa.db"));
  db.exec(`
    PRAGMA journal_mode=WAL;
    CREATE TABLE messages (
      chat_id TEXT NOT NULL, id TEXT NOT NULL, from_me INTEGER NOT NULL,
      deleted_ts INTEGER, quoted_id TEXT, text TEXT, media_sha TEXT,
      PRIMARY KEY(chat_id,id)
    );
    CREATE TABLE reactions(chat_id TEXT NOT NULL,message_id TEXT NOT NULL,sender_id TEXT);
    CREATE VIRTUAL TABLE messages_fts USING fts5(text,content='messages',content_rowid='rowid');
    CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
      INSERT INTO messages_fts(rowid,text) VALUES (new.rowid,new.text);
    END;
    CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
      INSERT INTO messages_fts(messages_fts,rowid,text) VALUES('delete',old.rowid,old.text);
    END;
    CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
      INSERT INTO messages_fts(messages_fts,rowid,text) VALUES('delete',old.rowid,old.text);
      INSERT INTO messages_fts(rowid,text) VALUES(new.rowid,new.text);
    END;
  `);
  const put=db.prepare("INSERT INTO messages(chat_id,id,from_me,deleted_ts,quoted_id,text,media_sha) VALUES (?,?,?,?,?,?,?)");
  t.after(()=>{
    db.close();
    if(oldMedia===undefined) delete process.env.WHATSAPP_MEDIA_DIR;
    else process.env.WHATSAPP_MEDIA_DIR=oldMedia;
    fs.rmSync(directory,{recursive:true,force:true});
  });
  return {db,dir:directory,mediaDir,put,retention:makePrivacyRetention(db)};
}
const count=(db,table)=>db.prepare("SELECT COUNT(*) AS n FROM "+table).get().n;

test("Delete-for-me really erases either author's message, revisions and FTS, not just a tombstone",t=>{
  const {db,put,retention}=fixture(t);
  put.run("chat","incoming",0,null,null,"incoming private content",null);
  put.run("chat","outgoing",1,null,null,"outgoing private content",null);
  put.run("chat","other",0,null,"incoming","unrelated reply",null);
  db.exec("CREATE TABLE message_revisions(chat_id TEXT,id TEXT,rev INTEGER,text TEXT)");
  db.prepare("INSERT INTO message_revisions VALUES(?,?,?,?)").run("chat","incoming",1,"previous secret");
  db.prepare("INSERT INTO reactions VALUES(?,?,?)").run("chat","incoming","reactor");

  assert.equal(retention.purge("chat","incoming","delete_for_me"),true);
  assert.equal(retention.purge("chat","outgoing","delete_for_me"),true);
  assert.equal(count(db,"messages"),1);
  assert.equal(count(db,"reactions"),0);
  assert.equal(count(db,"message_revisions"),0);
  assert.equal(db.prepare("SELECT quoted_id FROM messages WHERE id='other'").get().quoted_id,null);
  assert.equal(db.prepare("SELECT count(*) AS n FROM messages_fts WHERE messages_fts MATCH 'private'").get().n,0);
  assert.equal(retention.isSuppressed("chat","incoming"),true);
  assert.equal(retention.isSuppressed("chat","outgoing"),true);
  const suppression=db.prepare("SELECT message_digest FROM privacy_suppressed_messages").all();
  assert.equal(suppression.length,2);
  assert.ok(suppression.every(row=>/^[0-9a-f]{64}$/.test(row.message_digest)));
});

test("A replay is suppressed across SQLite reopen and repeat delete is idempotent",t=>{
  const {db,put,retention}=fixture(t);
  put.run("chat","id",0,null,null,"secret",null);
  assert.equal(retention.purge("chat","id","delete_for_me"),true);
  assert.equal(retention.purge("chat","id","delete_for_me"),false);
  assert.equal(count(db,"privacy_suppressed_messages"),1);
  assert.equal(makePrivacyRetention(db).isSuppressed("chat","id"),true);
  assert.equal(retention.isSuppressed("other chat","id"),false);
});

test("Only existing own revoked messages are eligible for startup cleanup",t=>{
  const {db,put,retention}=fixture(t);
  put.run("c","own-revoked",1,123,null,"previously revoked",null);
  put.run("c","others-revoked",0,123,null,"other's deleted text",null);
  put.run("c","own-live",1,null,null,"still live",null);
  assert.equal(retention.purgeExistingOwnRevokes(),1);
  assert.deepEqual(db.prepare("SELECT id FROM messages ORDER BY id").all().map(x=>x.id),["others-revoked","own-live"]);
  assert.equal(retention.isSuppressed("c","own-revoked"),true);
  assert.equal(retention.isSuppressed("c","others-revoked"),false);
  assert.equal(retention.purgeExistingOwnRevokes(),0);
});

test("Only unreferenced cached media bytes are removed",t=>{
  const {db,put,retention,mediaDir}=fixture(t);
  const sha="a".repeat(64);
  const target=path.join(mediaDir,sha);
  fs.writeFileSync(target,"private media data");
  put.run("c","a",0,null,null,"caption",sha);
  put.run("c","b",0,null,null,"other caption",sha);
  retention.purge("c","a","delete_for_me");
  assert.equal(fs.existsSync(target),true);
  retention.purge("c","b","delete_for_me");
  assert.equal(fs.existsSync(target),false);
});
