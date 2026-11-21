import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

// These are transport/housekeeping envelopes, not conversation content.
// This intentionally does NOT classify polling, event, album, interactive,
// business template, comment, placeholder or unknown message payloads as noise.
export const TRANSPORT_FIELDS = new Set([
  'messageHistoryNotice', 'messageHistoryBundle', 'senderKeyDistributionMessage'
]);

export function isDisposableProtocolMessage(envelope) {
  if (!envelope || typeof envelope !== 'object') return false;
  const content = envelope.message ?? {};
  if (!content || typeof content !== 'object') return false;
  const fields = Object.keys(content).filter(key => key !== 'messageContextInfo');
  if (envelope.messageStubType != null && fields.length === 0) return true;
  return fields.length > 0 && fields.every(field => TRANSPORT_FIELDS.has(field));
}

export function hiddenOtherWhere() {
  return `kind='other'
     AND NULLIF(TRIM(COALESCE(text,'')),'') IS NULL
     AND NULLIF(TRIM(COALESCE(transcript,'')),'') IS NULL
     AND media_type IS NULL AND media_sha IS NULL`;
}

/**
 * Prune only proved non-conversation events. Runs before the upstream DB
 * writer starts. Fail closed if decoding cannot be performed; keep any
 * record quoted by another message or carrying reactions.
 */
export async function pruneProtocolNoise(storagePath, { decode } = {}) {
  const dbFile = path.join(storagePath, 'whatsapp.db');
  if (!fs.existsSync(dbFile)) return { deleted: 0, retained: 0, quotedProtected: 0, undecodable: 0 };
  const decoder = decode || (await import('file:///opt/whatsapp-api/node_modules/baileys/lib/index.js')).proto.WebMessageInfo.decode;
  const db = new DatabaseSync(dbFile);
  try {
    db.exec('PRAGMA busy_timeout=10000; PRAGMA foreign_keys=ON; PRAGMA secure_delete=ON;');
    const rows = db.prepare('SELECT rowid, chat_id, id, raw FROM messages WHERE ' + hiddenOtherWhere()).all();
    const quote = db.prepare('SELECT 1 FROM messages WHERE chat_id=? AND quoted_id=? LIMIT 1');
    const reaction = db.prepare('SELECT 1 FROM reactions WHERE chat_id=? AND message_id=? LIMIT 1');
    const selected = [];
    let quotedProtected=0, undecodable=0;
    for (const row of rows) {
      if (!row.raw) { undecodable++; continue; }
      let decoded;
      try { decoded = decoder(row.raw); } catch { undecodable++; continue; }
      if (!isDisposableProtocolMessage(decoded)) continue;
      if (quote.get(row.chat_id, row.id) || reaction.get(row.chat_id, row.id)) {
        quotedProtected++;
        continue;
      }
      selected.push(row.rowid);
    }
    if (selected.length) {
      const remove = db.prepare('DELETE FROM messages WHERE rowid=?');
      db.exec('BEGIN IMMEDIATE');
      try {
        for (const id of selected) remove.run(id);
        db.exec('COMMIT');
      } catch (error) {
        db.exec('ROLLBACK');
        throw error;
      }
      db.exec('PRAGMA wal_checkpoint(TRUNCATE);');
      db.exec('VACUUM;');
    }
    return {deleted: selected.length, retained: rows.length-selected.length, quotedProtected, undecodable};
  } finally {
    db.close();
  }
}
