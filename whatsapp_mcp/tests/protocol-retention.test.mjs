import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import {isDisposableProtocolMessage,pruneProtocolNoise} from '../protocol-retention.mjs';

test('classifies only explicit transport stubs and envelopes, keeping unknown real content',()=>{
 const is=isDisposableProtocolMessage;
 assert.equal(is({messageStubType:38,message:{}}),true);
 assert.equal(is({messageStubType:28,message:{messageContextInfo:{}}}),true);
 assert.equal(is({message:{senderKeyDistributionMessage:{},messageContextInfo:{}}}),true);
 assert.equal(is({message:{messageHistoryNotice:{}}}),true);
 assert.equal(is({message:{messageHistoryBundle:{},messageContextInfo:{}}}),true);
 for(const key of ['pollCreationMessageV3','albumMessage','eventMessage','commentMessage','interactiveMessage','templateMessage','placeholderMessage','templateButtonReplyMessage']){
   assert.equal(is({message:{[key]:{},messageContextInfo:{}}}),false,key);
 }
 assert.equal(is({message:{messageContextInfo:{}}}),false);
 assert.equal(is({message:{}}),false);
 assert.equal(is({message:{documentMessage:{},senderKeyDistributionMessage:{}}}),false);
 assert.equal(is({messageStubType:38,message:{pollCreationMessageV3:{}}}),false);
 assert.equal(is(null),false);
});
test('prune removes only non-content; preserves referenced, unrecognized, and undecodable rows',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'wa-protocol-'));
 t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const db=new DatabaseSync(path.join(dir,'whatsapp.db'));
 db.exec(`
 CREATE TABLE chats(id TEXT PRIMARY KEY);
 CREATE TABLE messages(rowid INTEGER PRIMARY KEY,chat_id TEXT,id TEXT,sender_id TEXT,
 ts INTEGER,from_me INTEGER,kind TEXT,text TEXT,transcript TEXT,
 quoted_id TEXT,media_type TEXT,media_sha TEXT,raw BLOB);
 CREATE TABLE reactions(chat_id TEXT,message_id TEXT,sender_id TEXT);
 INSERT INTO chats VALUES('c@g.us');
 `);
 const put=db.prepare('INSERT INTO messages(chat_id,id,kind,text,raw,quoted_id) VALUES(?,?,?,?,?,?)');
 const raw=obj=>Buffer.from(JSON.stringify(obj));
 put.run('c@g.us','stub','other',null,raw({messageStubType:38,message:{}}),null);
 put.run('c@g.us','history','other',null,raw({message:{messageHistoryNotice:{}}}),null);
 put.run('c@g.us','poll','other',null,raw({message:{pollCreationMessageV3:{}}}),null);
 put.run('c@g.us','undecodable','other',null,Buffer.from('{bad'),null);
 put.run('c@g.us','quoted','other',null,raw({messageStubType:38,message:{}}),null);
 put.run('c@g.us','reply','text','reply',raw({message:{conversation:'reply'}}),'quoted');
 db.close();
 const decoder=b=>JSON.parse(b.toString('utf8'));
 const result=await pruneProtocolNoise(dir,{decode:decoder});
 assert.deepEqual(result,{deleted:2,retained:3,quotedProtected:1,undecodable:1});
 const verify=new DatabaseSync(path.join(dir,'whatsapp.db'),{readOnly:true});
 assert.deepEqual(verify.prepare('SELECT id FROM messages ORDER BY rowid').all().map(x=>x.id),
   ['poll','undecodable','quoted','reply']);
 verify.close();
 assert.equal((await pruneProtocolNoise(dir,{decode:decoder})).deleted,0);
});
