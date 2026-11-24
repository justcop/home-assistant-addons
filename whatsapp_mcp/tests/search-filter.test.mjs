import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { ArchiveData } from '../archive-data.mjs';

const root=fs.mkdtempSync(path.join(os.tmpdir(),'whatsapp-search-test-'));
const dbfile=path.join(root,'whatsapp.db');
const db=new DatabaseSync(dbfile);
db.exec(
  'CREATE TABLE chats (id TEXT PRIMARY KEY,name TEXT,is_group INTEGER,unread_count INTEGER,archived INTEGER,muted_until INTEGER,participant_count INTEGER);'+
  'CREATE TABLE contacts (id TEXT PRIMARY KEY,phone_number TEXT,lid TEXT,name TEXT,notify TEXT);'+
  'CREATE TABLE messages (rowid INTEGER PRIMARY KEY,chat_id TEXT,id TEXT,sender_id TEXT,ts INTEGER,from_me INTEGER,kind TEXT,text TEXT,transcript TEXT,quoted_id TEXT,status TEXT,edited_ts INTEGER,deleted_ts INTEGER,media_type TEXT,media_sha TEXT);'+
  'CREATE TABLE reactions (chat_id TEXT,message_id TEXT,sender_id TEXT);'
);
const chat=db.prepare('INSERT INTO chats VALUES (?,?,?,0,0,NULL,NULL)');
chat.run('111@s.whatsapp.net','111',0);
chat.run('team@g.us','A very long Group of Friends With Unique Names and Special Characters',1);
chat.run('13135550002@s.whatsapp.net','Meta AI',0);
db.prepare('INSERT INTO contacts VALUES (?,?,?,?,?)').run('111@s.whatsapp.net','111',null,'Alice Johnson','Alice');
const ins=db.prepare('INSERT INTO messages(chat_id,id,sender_id,ts,from_me,kind,text,transcript,deleted_ts) VALUES (?,?,?,?,?,?,?,?,?)');
ins.run('111@s.whatsapp.net','a','111@s.whatsapp.net',1761955200,0,'text','let us meet for coffee',null,null);
ins.run('111@s.whatsapp.net','b','111@s.whatsapp.net',1762041600,0,'text','Budget approved',null,null);
ins.run('team@g.us','c','111@s.whatsapp.net',1762128000,0,'text','Planning trip',null,null);
ins.run('team@g.us','d','111@s.whatsapp.net',1762214400,0,'audio',null,'The budget meeting recording',null);
ins.run('team@g.us','deleted','111@s.whatsapp.net',1762300800,0,'text','Secret revoked budget',null,1762300900);
ins.run('13135550002@s.whatsapp.net','ai','111@s.whatsapp.net',1762387200,0,'text','meta budget',null,null);
db.close();

test('filtered archive matches resolved contact names and message contents, with safe pagination',()=>{
  const store=new ArchiveData(root);
  try {
    const params=q=>new URLSearchParams(q);
    let hit=store.searchArchive(params({q:'alice',scope:'name'}));
    assert.equal(hit.items.length,2);
    assert.ok(hit.items.every(x=>x.chatName==='Alice Johnson'));
    hit=store.searchArchive(params({q:'BUDGET',scope:'message'}));
    assert.deepEqual(new Set(hit.items.map(x=>x.id)),new Set(['b','d']));
    hit=store.searchArchive(params({q:'Planning',scope:'any'}));
    assert.equal(hit.items.length,1);
    assert.equal(hit.items[0].id,'c');
    hit=store.searchArchive(params({q:'group of friends',scope:'name'}));
    assert.deepEqual(new Set(hit.items.map(x=>x.id)),new Set(['c','d']));
    hit=store.searchArchive(params({after:'1762041600',before:'1762128000'}));
    assert.deepEqual(hit.items.map(x=>x.id),['c','b']);
    hit=store.searchArchive(params({q:'budget',scope:'any',isGroup:'true'}));
    assert.equal(hit.items.length,1);
    assert.equal(hit.items[0].id,'d');
    const first=store.searchArchive(params({after:'1761955200',limit:'2'}));
    assert.equal(first.items.length,2);
    assert.equal(first.nextCursor,'2');
    const second=store.searchArchive(params({after:'1761955200',limit:'2',cursor:first.nextCursor}));
    assert.equal(second.items.length,2);
    assert.equal(second.nextCursor,null);
    assert.ok(![...first.items,...second.items].some(x=>x.deleted || x.chat==='13135550002@s.whatsapp.net'));
    assert.throws(()=>store.searchArchive(params({})),RangeError);
    assert.throws(()=>store.searchArchive(params({scope:'oops',q:'Alice'})),RangeError);
    assert.throws(()=>store.searchArchive(params({after:'1762128000',before:'1761955200'})),RangeError);
    assert.throws(()=>store.searchArchive(params({q:'x'.repeat(161)})),RangeError);
  } finally { store.close(); }
});
test.after(()=>fs.rmSync(root,{recursive:true,force:true}));
