/**
 * Exclude the account's own LID-addressed traffic from this archive.
 * This policy is intentionally broader than "Meta AI": the wire does not
 * distinguish Meta AI prompts from any other own-LID-addressed messages.
 * Genuine self-chat under the phone JID is retained.
 *
 * Called before the WhatsApp writer starts. Not a WhatsApp-side deletion.
 */
import { DatabaseSync } from 'node:sqlite';
import { existsSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';

const HEX_SHA=/^[a-f0-9]{64}$/;

export function pruneOwnLid(dataDir, phoneNumber) {
  if (!/^[1-9][0-9]{7,14}$/.test(phoneNumber??'')) return {removed:0,reason:'invalid_phone'};
  const dbPath=join(dataDir,'whatsapp.db');
  if(!existsSync(dbPath)) return {removed:0,reason:'no_database'};
  const db=new DatabaseSync(dbPath);
  try {
    db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=10000; PRAGMA secure_delete=ON;');
    const phoneJid=phoneNumber+'@s.whatsapp.net';
    const c=db.prepare('SELECT lid FROM contacts WHERE id=? AND phone_number=? LIMIT 1').get(phoneJid,phoneNumber);
    const bare=String(c?.lid??'').replace(/@lid$/,'');
    if(!/^\d+$/.test(bare)) return {removed:0,reason:'self_lid_unknown'};
    const lid=bare+'@lid';
    const chat=db.prepare('SELECT name FROM chats WHERE id=?').get(lid);
    // Do not delete a normal chat which happens to carry the same LID; demand
    // the exact quarantine label produced by the audited migration.
    if(!chat || chat.name!=='Unclassified own-LID messages')
      return {removed:0,reason:'no_confirmed_quarantine'};
    const media=db.prepare('SELECT DISTINCT media_sha AS sha FROM messages WHERE chat_id=? AND media_sha IS NOT NULL').all(lid);
    const ids=db.prepare('SELECT id FROM messages WHERE chat_id=?').all(lid);
    db.exec('BEGIN IMMEDIATE');
    try {
      const hasSuppress=!!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='privacy_suppressed_messages'").get();
      if(hasSuppress){
        const add=db.prepare("INSERT OR IGNORE INTO privacy_suppressed_messages(message_digest,reason,observed_ts) VALUES(?,?,?)");
        const now=Math.floor(Date.now()/1000);
        for(const {id} of ids) {
          const digest=createHash('sha256').update(lid).update('\0').update(id).digest('hex');
          add.run(digest,'excluded_own_lid',now);
        }
      }
      db.prepare('DELETE FROM reactions WHERE chat_id=?').run(lid);
      db.prepare('DELETE FROM messages WHERE chat_id=?').run(lid);
      db.prepare('DELETE FROM chats WHERE id=?').run(lid);
      db.exec('COMMIT');
    }catch(e){db.exec('ROLLBACK');throw e;}
    let mediaRemoved=0;
    for(const {sha} of media){
      if(!HEX_SHA.test(String(sha))) continue;
      const inUse=db.prepare('SELECT 1 FROM messages WHERE media_sha=? LIMIT 1').get(sha);
      if(inUse)continue;
      try{unlinkSync(join(dataDir,'media',sha));mediaRemoved++;}
      catch(e){if(e.code!=='ENOENT')throw e;}
    }
    db.exec('PRAGMA wal_checkpoint(TRUNCATE);');
    return {removed:ids.length,mediaRemoved,reason:'excluded_own_lid'};
  }finally{db.close();}
}
