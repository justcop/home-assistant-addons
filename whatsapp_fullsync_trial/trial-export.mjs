// A one-time export of historical WhatsApp *content*, not Baileys credentials.
// The trial keeps its internal database; output is under a fixed HA /share
// exchange directory, accessible only through local Supervisor admin ingress.
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export const TRANSFER_DIR='/share/whatsapp-history-transfer';
export const EXPORT_FILE=path.join(TRANSFER_DIR,'trial-messages.sqlite');
const tables=['chats','contacts','messages','reactions','privacy_suppressed_messages'];
const quote=p=>"'"+String(p).replaceAll("'","''")+"'";

export function prepareTrialExportDirectory(directory=TRANSFER_DIR) {
  const parent=path.dirname(directory);
  if(!fs.statSync(parent).isDirectory())throw Error('Shared transfer volume not mounted');
  if(fs.existsSync(directory)) {
    if(fs.lstatSync(directory).isSymbolicLink()||!fs.statSync(directory).isDirectory())
      throw Error('Unsafe WhatsApp export directory');
  } else fs.mkdirSync(directory,{mode:0o700});
  fs.chownSync(directory,1000,1000);
  fs.chmodSync(directory,0o700);
}

export function trialExportStatus(directory=TRANSFER_DIR) {
  const file=path.join(directory,'trial-messages.sqlite');
  if(!fs.existsSync(file))return {ready:false};
  if(fs.lstatSync(file).isSymbolicLink())throw Error('Unsafe transfer file');
  const db=new DatabaseSync(file,{readOnly:true});
  try {
    const info=db.prepare('SELECT schema_version AS schemaVersion,created_at AS createdAt FROM export_info').get();
    const messageCount=db.prepare('SELECT COUNT(*) AS n FROM messages').get().n;
    const chatCount=db.prepare('SELECT COUNT(*) AS n FROM chats').get().n;
    return {ready:true,schemaVersion:info.schemaVersion,createdAt:info.createdAt,
      messages:messageCount,chats:chatCount,sizeBytes:fs.statSync(file).size};
  } finally {db.close();}
}

export function exportTrialSnapshot(dataDir, directory=TRANSFER_DIR) {
  prepareTrialExportDirectory(directory);
  const source=path.join(dataDir,'whatsapp.db');
  if(!fs.existsSync(source))throw Error('Trial database unavailable');
  if(fs.lstatSync(source).isSymbolicLink())throw Error('Unsafe trial database');
  const output=path.join(directory,'trial-messages.sqlite');
  const temp=path.join(directory,'.trial-messages.'+process.pid+'.tmp');
  let db;
  try {
    if(fs.existsSync(temp))throw Error('Another export may be in progress');
    db=new DatabaseSync(temp);
    fs.chmodSync(temp,0o600);
    db.exec('PRAGMA journal_mode=DELETE; PRAGMA secure_delete=ON');
    db.exec('ATTACH DATABASE '+quote(source)+' AS src');
    db.exec('BEGIN');
    for(const table of tables) {
      const exists=db.prepare(
        "SELECT name FROM src.sqlite_master WHERE type='table' AND name=?"
      ).get(table);
      if(!exists)throw Error('Trial schema missing required table '+table);
      db.exec('CREATE TABLE '+table+' AS SELECT * FROM src.'+table);
    }
    db.exec('CREATE TABLE export_info (schema_version INTEGER NOT NULL,created_at TEXT NOT NULL)');
    db.prepare('INSERT INTO export_info VALUES (?,?)').run(1,new Date().toISOString());
    db.exec('COMMIT');
    db.exec('DETACH DATABASE src');
    const health=db.prepare('PRAGMA integrity_check').get();
    if(Object.values(health)[0]!=='ok')throw Error('Trial export integrity failed');
    const expected=tables.map(t=>db.prepare('SELECT COUNT(*) AS n FROM '+t).get().n);
    db.close();db=null;
    // Atomic publication: a reader cannot see a half-written snapshot.
    fs.renameSync(temp,output);
    fs.chmodSync(output,0o600);
    return {exported:true,tableCounts:Object.fromEntries(tables.map((t,i)=>[t,expected[i]])),
      ...trialExportStatus(directory)};
  } catch(err) {
    try{db?.exec('ROLLBACK');}catch{}
    try{db?.close();}catch{}
    try{fs.rmSync(temp,{force:true});}catch{}
    throw err;
  }
}
