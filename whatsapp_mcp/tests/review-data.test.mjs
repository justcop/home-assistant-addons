import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { reviewConversations, reviewContext } from '../review-data.mjs';

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'whatsapp-review-'));
  const db = new DatabaseSync(path.join(dir, 'whatsapp.db'));
  db.exec(`
    CREATE TABLE chats(id TEXT PRIMARY KEY,name TEXT,is_group INTEGER NOT NULL,unread_count INTEGER NOT NULL,archived INTEGER NOT NULL,muted_until INTEGER,participant_count INTEGER);
    CREATE TABLE messages(rowid INTEGER PRIMARY KEY,chat_id TEXT,id TEXT,sender_id TEXT,ts INTEGER,from_me INTEGER,kind TEXT,text TEXT,transcript TEXT,quoted_id TEXT,status TEXT,edited_ts INTEGER,deleted_ts INTEGER,media_type TEXT,media_sha TEXT);
    CREATE TABLE reactions(chat_id TEXT,message_id TEXT,sender_id TEXT);
    CREATE TABLE contacts(id TEXT,phone_number TEXT,lid TEXT,name TEXT,notify TEXT);
    INSERT INTO chats VALUES
      ('one@s.whatsapp.net','Alex',0,2,0,NULL,0),
      ('two@s.whatsapp.net','Blair',0,0,0,NULL,0),
      ('group@g.us','Friends',1,0,0,NULL,10),
      ('other@s.whatsapp.net','Chris',0,0,0,NULL,0);
    INSERT INTO messages(chat_id,id,sender_id,ts,from_me,kind,text) VALUES
      ('one@s.whatsapp.net','promise','one@s.whatsapp.net',1000,1,'text','I''ll sort the referral'),
      ('one@s.whatsapp.net','question','one@s.whatsapp.net',1010,0,'text','Can you confirm when it is done?'),
      ('two@s.whatsapp.net','asked','two@s.whatsapp.net',1015,0,'text','Can you book this?'),
      ('two@s.whatsapp.net','answered','two@s.whatsapp.net',1020,1,'text','Done'),
      ('group@g.us','groupgeneric','one@s.whatsapp.net',1025,0,'text','Can somebody come tomorrow?'),
      ('other@s.whatsapp.net','boring','other@s.whatsapp.net',1030,0,'text','Thanks!'),
      ('group@g.us','groupmention','two@s.whatsapp.net',1035,0,'text','Justin, could you check this?');
    INSERT INTO messages(chat_id,id,sender_id,ts,from_me,kind,text) VALUES
      ('one@s.whatsapp.net','protocol','one@s.whatsapp.net',1050,0,'system','Security code changed');
  `);
  db.close();
  t.after(() => fs.rmSync(dir, {recursive:true,force:true}));
  return dir;
}

test('review identifies unanswered question and outgoing commitment with traceable context', t => {
  const dir = fixture(t);
  const result = reviewConversations(dir,{after:900,before:1100});
  assert.equal(result.scan_truncated,false);
  assert.equal(result.scanned_messages,7); // system notice omitted
  assert.equal(result.scanned_conversations,3);
  assert.equal(result.candidate_conversations,1);
  const [item]=result.items;
  assert.equal(item.name,'Alex');
  assert.ok(item.flags.includes('possible_reply_needed'));
  assert.ok(item.flags.includes('commitment_to_verify'));
  assert.deepEqual(item.context.map(m=>m.id),['promise','question']);
  assert.equal(item.last_activity,1010);
});

test('review does not treat a completed message response as an unanswered question', t => {
  const dir=fixture(t);
  const result=reviewConversations(dir,{after:900,before:1100,only_candidates:false});
  const blair=result.items.find(i=>i.name==='Blair');
  assert.ok(blair);
  assert.ok(!blair.flags.includes('possible_reply_needed'));
  assert.equal(blair.last_from_me,true);
});

test('group review excludes generic questions but includes messages addressing the owner',t=>{
  const dir=fixture(t);
  const result=reviewConversations(dir,{after:900,before:1100,mode:'groups'});
  assert.equal(result.items.length,1);
  assert.ok(result.items[0].flags.includes('group_mention'));
  assert.equal(result.items[0].context.length,2);
});

test('batch context remains read-only and validates input and time bounds',t=>{
  const dir=fixture(t);
  const dbFile=path.join(dir,'whatsapp.db');
  const before=fs.readFileSync(dbFile);
  const result=reviewContext(dir,{chats:['one@s.whatsapp.net','two@s.whatsapp.net'],after:900,before:1100,limit_per_chat:1});
  assert.equal(result.conversations.length,2);
  assert.equal(result.conversations[0].messages.length,1);
  assert.equal(result.conversations[0].truncated,true);
  assert.equal(result.conversations[0].messages[0].id,'question');
  assert.ok(fs.readFileSync(dbFile).equals(before));
  assert.throws(()=>reviewContext(dir,{chats:[],after:900,before:1100}),RangeError);
  assert.throws(()=>reviewConversations(dir,{after:0,before:90*86400+1}),RangeError);
});
