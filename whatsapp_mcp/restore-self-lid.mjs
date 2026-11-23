/**
 * Roll back the temporary own-LID quarantine now that Meta AI is recognised
 * at the raw @bot recipient boundary. Restore genuine self-messages under
 * the phone JID without deleting content or the replay-suppression ledger.
 *
 * Runs before the WhatsApp API writer starts. A legacy chat is eligible only
 * when it has our own known LID and the exact quarantine label. Every moved
 * row must contain an original own-LID WAMessage envelope and from_me=1.
 */
import { DatabaseSync } from 'node:sqlite';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

const QUARANTINE_NAME = 'Unclassified own-LID messages';

function normalizeLid(jid) {
  if (typeof jid !== 'string') return null;
  const raw = jid.replace(/@lid$/, '').replace(/:\d+$/, '').replace(/_\d+$/, '');
  return /^\d+$/.test(raw) ? raw + '@lid' : null;
}

export async function restoreSelfLid(dataDir, phoneNumber, decoder = null) {
  if (!/^[1-9][0-9]{7,14}$/.test(phoneNumber ?? ''))
    return { restored: 0, reason: 'invalid_phone' };
  const file = join(dataDir, 'whatsapp.db');
  if (!existsSync(file)) return { restored: 0, reason: 'no_database' };
  const db = new DatabaseSync(file);
  try {
    db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=10000;');
    const self = phoneNumber + '@s.whatsapp.net';
    const contact = db.prepare('SELECT lid FROM contacts WHERE id=? AND phone_number=?')
      .get(self, phoneNumber);
    const lid = normalizeLid(contact?.lid);
    if (!lid) return { restored: 0, reason: 'self_lid_unknown' };
    const chat = db.prepare('SELECT name FROM chats WHERE id=?').get(lid);
    if (chat?.name !== QUARANTINE_NAME)
      return { restored: 0, reason: 'no_legacy_quarantine' };

    if (!decoder) {
      const { proto } = await import('/opt/whatsapp-api/node_modules/baileys/lib/index.js');
      decoder = bytes => proto.WebMessageInfo.decode(bytes);
    }
    const rows = db.prepare('SELECT id, from_me, raw FROM messages WHERE chat_id=?').all(lid);
    if (rows.length > 20000) throw new Error('Legacy self-LID restore exceeds safety cap');

    const eligible = [];
    for (const row of rows) {
      if (row.from_me !== 1 || row.raw == null) continue;
      try {
        const msg = decoder(new Uint8Array(row.raw));
        if (normalizeLid(msg.key?.remoteJid) === lid && msg.key?.fromMe === true)
          eligible.push(row.id);
      } catch {
        // Malformed or absent original envelope is never silently reclassified.
      }
    }
    db.exec('BEGIN IMMEDIATE');
    let remaining;
    try {
      const exists = db.prepare('SELECT 1 FROM messages WHERE chat_id=? AND id=?');
      const moveReactions = db.prepare(
        'UPDATE reactions SET chat_id=? WHERE chat_id=? AND message_id=?');
      const moveMessage = db.prepare(
        'UPDATE messages SET chat_id=? WHERE chat_id=? AND id=?');
      for (const id of eligible) {
        if (exists.get(self, id))
          throw new Error('Self-message ID collision; quarantine left unchanged');
        moveReactions.run(self, lid, id);
        if (moveMessage.run(self, lid, id).changes !== 1)
          throw new Error('Expected exactly one self-LID message to move');
      }
      remaining = db.prepare('SELECT COUNT(*) AS n FROM messages WHERE chat_id=?').get(lid).n;
      if (remaining === 0) {
        const leftoverReactions = db.prepare('SELECT 1 FROM reactions WHERE chat_id=? LIMIT 1').get(lid);
        if (!leftoverReactions) db.prepare('DELETE FROM chats WHERE id=?').run(lid);
      }
      db.prepare('UPDATE chats SET last_message_ts=(SELECT MAX(ts) FROM messages WHERE chat_id=?) WHERE id=?')
        .run(self, self);
      db.exec('COMMIT');
    } catch (error) {
      db.exec('ROLLBACK');
      throw error;
    }
    return { restored: eligible.length, remaining, reason: 'restored_to_self_chat' };
  } finally {
    db.close();
  }
}
