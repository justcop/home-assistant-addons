import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export const META_AI_JID = '13135550002@s.whatsapp.net';

/** Remove only the confirmed Meta AI chat from our local mirror. Runs before API start. */
export function pruneMetaAI(dataDir) {
  const file = path.join(dataDir, 'whatsapp.db');
  if (!fs.existsSync(file)) return 0;
  const db = new DatabaseSync(file);
  try {
    db.exec('PRAGMA busy_timeout = 10000; PRAGMA foreign_keys = ON; PRAGMA secure_delete = ON;');
    const messages = db.prepare('SELECT COUNT(*) AS n FROM messages WHERE chat_id = ?').get(META_AI_JID).n;
    const chat = db.prepare('SELECT COUNT(*) AS n FROM chats WHERE id = ?').get(META_AI_JID).n;
    if (!messages && !chat) return 0;
    db.exec('BEGIN IMMEDIATE');
    try {
      db.prepare('DELETE FROM reactions WHERE chat_id = ?').run(META_AI_JID);
      db.prepare('DELETE FROM messages WHERE chat_id = ?').run(META_AI_JID);
      db.prepare('DELETE FROM chats WHERE id = ?').run(META_AI_JID);
      db.exec('COMMIT');
    } catch (error) {
      db.exec('ROLLBACK');
      throw error;
    }
    // With no concurrent API writer, discard the WAL and compact deleted SQLite pages.
    db.exec('PRAGMA wal_checkpoint(TRUNCATE);');
    db.exec('VACUUM;');
    return messages;
  } finally { db.close(); }
}
