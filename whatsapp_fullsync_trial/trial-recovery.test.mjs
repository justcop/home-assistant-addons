import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { observeHistoryBatch, readTrialMetrics } from './trial-observer.mjs';
import { trialRecoveryStatus, setTrialRecovery } from './trial-recovery.mjs';

function isolated(fn) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'wa-trial-test-'));
  try { return fn(dir); } finally { fs.rmSync(dir,{recursive:true,force:true}); }
}
test('history metrics are aggregate-only and survive process state reinitialisation',()=>{
  isolated(dir=>{
    const old=process.env.WHATSAPP_DATA_DIR;
    process.env.WHATSAPP_DATA_DIR=dir;
    try{
      observeHistoryBatch({type:'FULL',received:5000,netUniqueChange:4973,isLatest:false});
      observeHistoryBatch({type:'FULL',received:5000,netUniqueChange:0,isLatest:false});
      const metrics=readTrialMetrics(dir);
      assert.equal(metrics.fullBatches,2);
      assert.equal(metrics.fullReceived,10000);
      assert.equal(metrics.netUniqueChange,4973);
      assert.equal(metrics.history.length,2);
      assert.ok(!fs.readFileSync(path.join(dir,'.trial-history-metrics.json'),'utf8').includes('some-private-contact'));
      assert.equal(trialRecoveryStatus(dir).bulk.state,'receiving');
    } finally {if(old===undefined)delete process.env.WHATSAPP_DATA_DIR;else process.env.WHATSAPP_DATA_DIR=old;}
  });
});
test('trial recovery is opt-in, checkpoint-preserving and pauses without deleting messages',()=>{
  isolated(dir=>{
    const cp=path.join(dir,'.history-backfill-v2.json');
    const snapshot={schema:2,active:false,requests:12,chats:{'anonymous-chat':{phase:'retry',requests:12}}};
    fs.writeFileSync(cp,JSON.stringify(snapshot));
    assert.equal(trialRecoveryStatus(dir).backfill.state,'paused');
    assert.equal(setTrialRecovery(dir,'start').accepted,true);
    assert.equal(trialRecoveryStatus(dir).backfill.state,'enabled');
    assert.equal(setTrialRecovery(dir,'pause').accepted,true);
    assert.equal(trialRecoveryStatus(dir).backfill.state,'paused');
    assert.deepEqual(JSON.parse(fs.readFileSync(cp,'utf8')),snapshot);
  });
});
test('active FULL bulk sync prevents competing per-chat requests',()=>{
  isolated(dir=>{
    fs.writeFileSync(path.join(dir,'.trial-history-metrics.json'),
      JSON.stringify({schema:1,fullBatches:1,lastFullAt:new Date().toISOString()}));
    assert.equal(setTrialRecovery(dir,'start').conflict,true);
    assert.equal(trialRecoveryStatus(dir).backfill.state,'paused');
  });
});
test('unique chat/message ID survives repeated and overlapping imports, even after reopen',()=>{
  isolated(dir=>{
    const filename=path.join(dir,'messages.sqlite');
    const sql="INSERT INTO messages(chat_id,id,text,deleted_ts) VALUES(?,?,?,NULL) ON CONFLICT(chat_id,id) DO UPDATE SET text=CASE WHEN messages.deleted_ts IS NOT NULL THEN NULL ELSE COALESCE(excluded.text,messages.text) END";
    let db=new DatabaseSync(filename);
    db.exec('CREATE TABLE messages(chat_id TEXT,id TEXT,text TEXT,deleted_ts INTEGER,UNIQUE(chat_id,id))');
    db.prepare(sql).run('chat','1','first');
    db.prepare(sql).run('chat','1','first'); // repeat
    db.prepare(sql).run('chat','2','second'); // overlap
    assert.equal(db.prepare('SELECT COUNT(*) AS n FROM messages').get().n,2);
    db.prepare('UPDATE messages SET text=NULL,deleted_ts=123 WHERE id=?').run('1');
    db.close();
    db=new DatabaseSync(filename);
    db.prepare(sql).run('chat','1','deleted-content-should-stay-deleted');
    db.prepare(sql).run('chat','2',null); // incomplete replay cannot erase second row
    assert.equal(db.prepare('SELECT text FROM messages WHERE id=?').get('1').text,null);
    assert.equal(db.prepare('SELECT text FROM messages WHERE id=?').get('2').text,'second');
    assert.equal(db.prepare('SELECT COUNT(*) AS n FROM messages').get().n,2);
    db.close();
  });
});
test('invalid recovery action cannot alter control file',()=>{
  isolated(dir=>{
    assert.throws(()=>setTrialRecovery(dir,'repair'),RangeError);
    assert.equal(fs.existsSync(path.join(dir,'.trial-recovery-resume')),false);
  });
});
