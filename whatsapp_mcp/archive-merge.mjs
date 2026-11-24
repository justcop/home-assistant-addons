// One-shot, fail-closed consolidation of a trial content-only export into the
// production archive. Never imports auth_creds/auth_keys or overwrites existing
// production messages, session state, local settings or message read states.
// Called at start-up before either WhatsApp child process opens SQLite.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';

export const TRANSFER_FILE='/share/whatsapp-history-transfer/trial-messages.sqlite';
const REQUEST='/data/.whatsapp-archive-merge-request';
const REPORT='/data/.whatsapp-archive-merge-report.json';
const BACKUP='.whatsapp-before-trial-merge.sqlite';
const STAGE='.whatsapp-merge-staging.sqlite';
const quote=p=>"'"+String(p).replaceAll("'","''")+"'";
const getCount=(db,table)=>db.prepare('SELECT COUNT(*) AS n FROM '+table).get().n;
const digest=(chat,id)=>createHash('sha256').update(chat).update('\0').update(id).digest('hex');
const check=db=>Object.values(db.prepare('PRAGMA integrity_check').get())[0]==='ok';
const atomicJSON=(file,data)=>{
  const temp=file+'.'+process.pid+'.tmp';
  fs.writeFileSync(temp,JSON.stringify(data)+'\n',{mode:0o600});
  fs.renameSync(temp,file);
};
const readReport=(file=REPORT)=>{try{return JSON.parse(fs.readFileSync(file,'utf8'));}catch{return null;}};

function validate(db) {
  const info=db.prepare('SELECT schema_version AS version,created_at AS created FROM incoming.export_info').get();
  if(info?.version!==1)throw Error('Unrecognised trial export format');
  for(const table of ['chats','contacts','messages','reactions','privacy_suppressed_messages']) {
    if(!db.prepare("SELECT name FROM incoming.sqlite_master WHERE type='table' AND name=?").get(table))
      throw Error('Trial export missing '+table);
  }
  for(const forbidden of ['auth_creds','auth_keys']) {
    if(db.prepare("SELECT name FROM incoming.sqlite_master WHERE type='table' AND name=?").get(forbidden))
      throw Error('Trial export contains device credentials; refusing merge');
  }
  if(!check(db))throw Error('Production database integrity check failed');
  const prod=getCount(db,'main.messages');
  const trial=getCount(db,'incoming.messages');
  const overlap=db.prepare('SELECT COUNT(*) AS n FROM incoming.messages i JOIN main.messages m ON m.chat_id=i.chat_id AND m.id=i.id').get().n;
  if(prod>1000 && trial>1000 && overlap<500)
    throw Error('Archives do not have enough overlapping message keys to confirm same account');
  return {ready:true,production:prod,trial,overlap,trialOnly:trial-overlap,
    productionOnly:prod-overlap,expectedWithoutSuppression:prod+trial-overlap,
    trialChats:getCount(db,'incoming.chats'),createdAt:info.created};
}
export function mergeStatus(storagePath,transferFile=TRANSFER_FILE,reportFile=REPORT,requestFile=REQUEST) {
  const file=path.join(storagePath,'whatsapp.db');
  const rollback=path.join(storagePath,BACKUP);
  const report=readReport(reportFile);
  const base={requested:fs.existsSync(requestFile),report,
    rollbackPresent:fs.existsSync(rollback)};
  if(!fs.existsSync(file))return {...base,ready:false,reason:'Production database unavailable'};
  if(!fs.existsSync(transferFile))return {...base,ready:false,reason:'Trial export has not been created'};
  if(fs.lstatSync(transferFile).isSymbolicLink())throw Error('Unsafe trial export file');
  const db=new DatabaseSync(file,{readOnly:true});
  try {
    db.exec('ATTACH DATABASE '+quote(transferFile)+' AS incoming');
    const status=validate(db);
    return {...base,...status};
  } finally {db.close();}
}
export function scheduleMerge(storagePath,transferFile=TRANSFER_FILE,requestFile=REQUEST) {
  const check=mergeStatus(storagePath,transferFile,REPORT,requestFile);
  if(!check.ready)throw Error(check.reason||'Trial archive not ready');
  if(check.rollbackPresent)throw Error('Previous merge rollback still exists; finalise or inspect first');
  if(check.trialOnly<1)throw Error('No additional trial messages to import');
  const file=path.join(storagePath,'whatsapp.db');
  if(fs.lstatSync(file).isSymbolicLink())throw Error('Unsafe production archive');
  fs.writeFileSync(requestFile,JSON.stringify({createdAt:new Date().toISOString()})+'\n',
    {mode:0o600,flag:'wx'});
  return {accepted:true,restartRequired:true,
    production:check.production,trial:check.trial,
    overlap:check.overlap,additionalCandidates:check.trialOnly};
}
function execute(storagePath,transferFile=TRANSFER_FILE,reportFile=REPORT,requestFile=REQUEST) {
  const live=path.join(storagePath,'whatsapp.db');
  const staging=path.join(storagePath,STAGE);
  const backup=path.join(storagePath,BACKUP);
  if(fs.existsSync(staging)||fs.existsSync(backup))
    throw Error('Unresolved previous merge staging or rollback file');
  const preflight=mergeStatus(storagePath,transferFile,reportFile,requestFile);
  if(!preflight.ready || preflight.trialOnly<1)throw Error('Preflight failed at restart');

  // The WhatsApp child processes are not yet running. Checkpoint all WAL
  // data before renaming the live DB; old -wal bytes must never be attached
  // to the newly staged SQLite file after cutover.
  const original=new DatabaseSync(live);
  try {
    original.exec('PRAGMA wal_checkpoint(TRUNCATE)');
    original.exec('PRAGMA journal_mode=DELETE');
    original.exec('VACUUM main INTO '+quote(staging));
  } finally {original.close();}
  for(const suffix of ['-wal','-shm']) {
    const f=live+suffix;
    if(fs.existsSync(f)&&fs.statSync(f).size>0)throw Error('Production WAL not clean; refusing database swap');
    if(fs.existsSync(f))fs.rmSync(f);
  }

  let working;
  try {
    working=new DatabaseSync(staging);
    working.exec('PRAGMA secure_delete=ON; PRAGMA journal_mode=DELETE');
    working.exec('ATTACH DATABASE '+quote(transferFile)+' AS incoming');
    const snapshot=validate(working);
    const suppressed=new Set(working.prepare(
      'SELECT message_digest FROM privacy_suppressed_messages'
    ).all().map(x=>x.message_digest));
    const incomingSuppression=working.prepare(
      'SELECT message_digest,reason,observed_ts FROM incoming.privacy_suppressed_messages'
    ).all();
    for(const row of incomingSuppression)suppressed.add(row.message_digest);
    const clash=working.prepare('SELECT chat_id,id FROM main.messages').iterate();
    for(const row of clash) {
      if(suppressed.has(digest(row.chat_id,row.id)))
        throw Error('Existing production message conflicts with an explicit deletion record; merge stopped');
    }

    working.exec('BEGIN IMMEDIATE');
    working.exec('INSERT OR IGNORE INTO privacy_suppressed_messages(message_digest,reason,observed_ts) SELECT message_digest,reason,observed_ts FROM incoming.privacy_suppressed_messages');
    // Keep all existing production metadata and unread/read state.
    working.exec('INSERT OR IGNORE INTO contacts (id,phone_number,lid,name,notify,raw) SELECT id,phone_number,lid,name,notify,raw FROM incoming.contacts');
    working.exec('INSERT OR IGNORE INTO chats (id,name,is_group,last_message_ts,unread_count,archived,muted_until,participant_count,raw) SELECT id,name,is_group,last_message_ts,0,archived,muted_until,participant_count,raw FROM incoming.chats');

    const insert=working.prepare(
      'INSERT OR IGNORE INTO messages(chat_id,id,sender_id,ts,from_me,kind,text,transcript,quoted_id,status,edited_ts,deleted_ts,media_type,media_sha,raw,ptt,duration_s,transcript_model,transcript_language) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
    );
    let added=0,overlap=0,skippedSuppressed=0;
    for(const row of working.prepare('SELECT * FROM incoming.messages').iterate()) {
      if(suppressed.has(digest(row.chat_id,row.id))){skippedSuppressed++;continue;}
      const deleted=row.deleted_ts!==null;
      const result=insert.run(
        row.chat_id,row.id,row.sender_id,row.ts,row.from_me,row.kind,
        deleted?null:row.text,deleted?null:row.transcript,row.quoted_id,
        row.status,row.edited_ts,row.deleted_ts,row.media_type,
        null, // No media files are transferred; never falsely mark them cached.
        row.raw,row.ptt,row.duration_s,
        deleted?null:row.transcript_model,deleted?null:row.transcript_language
      );
      if(result.changes)added++;else overlap++;
    }
    working.exec(
      'INSERT OR IGNORE INTO reactions(chat_id,message_id,sender_id,emoji,ts) '+
      'SELECT r.chat_id,r.message_id,r.sender_id,r.emoji,r.ts FROM incoming.reactions r '+
      'WHERE EXISTS(SELECT 1 FROM main.messages m WHERE m.chat_id=r.chat_id AND m.id=r.message_id)'
    );
    working.exec('INSERT INTO messages_fts(messages_fts) VALUES (\'rebuild\')');
    const total=getCount(working,'messages');
    if(total!==snapshot.production+added)
      throw Error('Merged message count does not match inserted unique keys');
    working.exec('COMMIT');
    working.exec('DETACH DATABASE incoming');
    if(!check(working))throw Error('Staged SQLite database failed integrity check');
    working.close();working=null;

    // Atomic same-filesystem replacement. Old production DB is retained for
    // explicit post-verification finalisation, not overwritten.
    fs.renameSync(live,backup);
    try {fs.renameSync(staging,live);}
    catch(err){fs.renameSync(backup,live);throw err;}
    const result={success:true,at:new Date().toISOString(),before:snapshot.production,
      trial:snapshot.trial,overlap,added,skippedSuppressed,after:total,
      rollbackPresent:true};
    atomicJSON(reportFile,result);
    return result;
  } catch(err) {
    try {working?.exec('ROLLBACK');}catch{}
    try {working?.close();}catch{}
    try {fs.rmSync(staging,{force:true});}catch{}
    throw err;
  }
}
export function runScheduledArchiveMerge(storagePath,{requestFile=REQUEST,transferFile=TRANSFER_FILE,reportFile=REPORT}={}) {
  if(!fs.existsSync(requestFile))return null;
  let result;
  try {result=execute(storagePath,transferFile,reportFile,requestFile);}
  catch(err){result={success:false,at:new Date().toISOString(),error:String(err.message||err).slice(0,230)};atomicJSON(reportFile,result);}
  fs.rmSync(requestFile,{force:true});
  return result;
}
export function finaliseMerge(storagePath,{transferFile=TRANSFER_FILE,reportFile=REPORT}={}) {
  const report=readReport(reportFile);
  if(!report?.success)throw Error('No successfully staged merge to finalise');
  const dbPath=path.join(storagePath,'whatsapp.db');
  const checkDb=new DatabaseSync(dbPath,{readOnly:true});
  try {
    if(!check(checkDb))throw Error('Merged database integrity check failed');
    if(getCount(checkDb,'messages')<report.after)throw Error('Merged message count decreased; refusing cleanup');
  } finally{checkDb.close();}
  const rollback=path.join(storagePath,BACKUP);
  if(!fs.existsSync(rollback))throw Error('Rollback database is absent');
  fs.rmSync(rollback);
  if(fs.existsSync(transferFile))fs.rmSync(transferFile);
  const updated={...report,rollbackPresent:false,finalisedAt:new Date().toISOString()};
  atomicJSON(reportFile,updated);
  return {finalised:true,after:report.after,rollbackRemoved:true,exchangeRemoved:true};
}
