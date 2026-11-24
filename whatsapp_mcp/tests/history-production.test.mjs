import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { observeHistoryBatch,readHistoryMetrics } from '../history-metrics.mjs';
import { historyRecoveryStatus,setHistoryRecovery } from '../history-recovery.mjs';

test('history metrics track only aggregate records, and recovery is paused until explicitly started',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'whatsapp-history-status-'));
  const original=process.env.WHATSAPP_DATA_DIR;
  try{
    process.env.WHATSAPP_DATA_DIR=root;
    const fakeArchive={archiveDateRange:()=>({messages:{count:4321,oldest:100}})};
    assert.equal(historyRecoveryStatus(root,fakeArchive).backfill.state,'paused');
    const enable=setHistoryRecovery(root,'resume',fakeArchive);
    assert.equal(enable.accepted,true);
    assert.equal(enable.status.backfill.state,'enabled');
    assert.equal(fs.existsSync(path.join(root,'.history-recovery-resume')),true);
    assert.equal(setHistoryRecovery(root,'pause',fakeArchive).accepted,true);
    assert.equal(fs.existsSync(path.join(root,'.history-recovery-resume')),false);
    observeHistoryBatch({type:'FULL',received:100,inserted:70,existing:8,netUniqueChange:65,isLatest:false});
    const metrics=readHistoryMetrics(root);
    assert.equal(metrics.fullBatches,1);
    assert.equal(metrics.inserted,70);
    assert.equal(metrics.existing,8);
    assert.equal(metrics.netUniqueChange,65);
    const status=historyRecoveryStatus(root,fakeArchive);
    assert.equal(status.bulk.state,'receiving');
    assert.equal(status.archive.count,4321);
    const blocked=setHistoryRecovery(root,'resume',fakeArchive);
    assert.equal(blocked.conflict,true);
    assert.equal(fs.existsSync(path.join(root,'.history-recovery-resume')),false);
    assert.ok(!fs.readFileSync(path.join(root,'.history-metrics.json'),'utf8').includes('chat_id'));
    const file=path.join(root,'.history-metrics.json');
    const data=JSON.parse(fs.readFileSync(file,'utf8'));
    data.lastFullAt=new Date(Date.now()-190000).toISOString();
    fs.writeFileSync(file,JSON.stringify(data));
    assert.equal(setHistoryRecovery(root,'start').accepted,true);
    assert.equal(setHistoryRecovery(root,'pause').accepted,true);
  } finally {
    if(original===undefined)delete process.env.WHATSAPP_DATA_DIR;
    else process.env.WHATSAPP_DATA_DIR=original;
    fs.rmSync(root,{recursive:true,force:true});
  }
});
