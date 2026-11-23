/**
 * Reversible, content-preserving startup migration.
 * WhatsApp may deliver outgoing Meta AI prompts under the account's own
 * unsuffixed LID. Since this alone does not prove an AI recipient, keep those
 * messages separate as "Unclassified own-LID messages". Never discard bytes.
 *
 * Run while the WhatsApp API writer is stopped. No global LID changes.
 */
import { DatabaseSync } from 'node:sqlite';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

export const QUARANTINE_NAME='Unclassified own-LID messages';

function normalizeLid(jid) {
  if (typeof jid!=='string') return null;
  let raw=jid.replace(/@lid$/,'');
  raw=raw.replace(/:\d+$/,'').replace(/_\d+$/,'');
  return /^\d+$/.test(raw) ? raw+'@lid' : null;
}

export async function separateSelfLid(dataDir, phoneNumber, decoder = null) {
  if (!/^[1-9][0-9]{7,14}$/.test(phoneNumber??'')) return { moved:0,reason:'no_valid_phone' };
  const file=join(dataDir,'whatsapp.db');
  if(!existsSync(file)) return { moved:0,reason:'no_database' };
  if(!decoder) {
    const {proto}=await import('/opt/whatsapp-api/node_modules/baileys/lib/index.js');
    decoder=(bytes)=>proto.WebMessageInfo.decode(bytes);
  }
  const self=phoneNumber+'@s.whatsapp.net';
  const db=new DatabaseSync(file);
  try {
    db.exec('PRAGMA busy_timeout=10000; PRAGMA foreign_keys=ON;');
    const c=db.prepare('SELECT lid FROM contacts WHERE id=? OR phone_number=? AND lid IS NOT NULL LIMIT 1').get(self,phoneNumber);
    const lid=normalizeLid(c?.lid);
    if (!lid) return { moved:0,reason:'self_lid_unknown' };
    const selected=[];
    const rows=db.prepare('SELECT id, raw FROM messages WHERE chat_id=? AND raw IS NOT NULL').all(self);
    for (const row of rows) {
      try {
        const msg=decoder(new Uint8Array(row.raw));
        if(normalizeLid(msg.key?.remoteJid)===lid) selected.push(row.id);
      } catch {
        // Unreadable provenance is not licence to move/delete the message.
      }
    }
    if(selected.length===0) return {moved:0,reason:'none'};
    if(selected.length>20000) throw new Error('Self-LID migration exceeds 20,000-row safety cap');
    db.exec('BEGIN IMMEDIATE');
    try {
      db.prepare('INSERT INTO chats (id,name,is_group) VALUES (?,?,0) ON CONFLICT(id) DO UPDATE SET name=excluded.name')
        .run(lid,QUARANTINE_NAME);
      const duplicate=db.prepare('SELECT 1 FROM messages WHERE chat_id=? AND id=?');
      const move=db.prepare('UPDATE messages SET chat_id=? WHERE chat_id=? AND id=?');
      const reaction=db.prepare('UPDATE OR IGNORE reactions SET chat_id=? WHERE chat_id=? AND message_id=?');
      const redundant=db.prepare('DELETE FROM reactions WHERE chat_id=? AND message_id=?');
      for(const id of selected){
        if(duplicate.get(lid,id)) throw new Error('Self-LID message ID collision: refusing migration');
        reaction.run(lid,self,id);
        redundant.run(self,id);
        const n=move.run(lid,self,id).changes;
        if(n!==1) throw new Error('Self-LID row moved unexpectedly');
      }
      db.prepare('UPDATE chats SET last_message_ts=(SELECT MAX(ts) FROM messages WHERE chat_id=?) WHERE id=?')
        .run(lid,lid);
      db.prepare('UPDATE chats SET last_message_ts=(SELECT MAX(ts) FROM messages WHERE chat_id=?) WHERE id=?')
        .run(self,self);
      db.exec('COMMIT');
    } catch(e){db.exec('ROLLBACK');throw e;}
    return {moved:selected.length,reason:'quarantined'};
  } finally {db.close();}
}
