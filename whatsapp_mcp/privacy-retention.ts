/**
 * Local privacy policy for the linked WhatsApp archive.
 *
 * A delete-for-me action is NOT the same as a message revoke. The former is
 * an account-local erasure request; the latter is visible to the conversation.
 * Never infer either from a missing row in incomplete WhatsApp history sync.
 *
 * The suppression key deliberately contains no chat ID, message ID, text or
 * media bytes. It survives history replays without retaining message content.
 */
import { createHash } from "node:crypto";
import { unlinkSync } from "node:fs";
import { join } from "node:path";
import type { Db } from "../db/client.js";

export type PrivacyPurgeReason = "delete_for_me" | "own_revoke" | "clear_chat";

type StoredMedia = { media_sha: string | null };
type RevokedMessage = { chat_id: string; id: string };
type Count = { n: number };
const HASH = /^[a-f0-9]{64}$/;

export function makePrivacyRetention(db: Db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS privacy_suppressed_messages (
      message_digest TEXT PRIMARY KEY,
      reason TEXT NOT NULL,
      observed_ts INTEGER NOT NULL
    ) STRICT;
  `);
  // Deleted SQLite cells are overwritten on ordinary deletions. The WAL and
  // existing independent backups still require separate retention controls.
  db.exec("PRAGMA secure_delete=ON");

  const known = db.prepare("SELECT 1 FROM privacy_suppressed_messages WHERE message_digest = ?");
  const suppress = db.prepare(
    "INSERT OR IGNORE INTO privacy_suppressed_messages(message_digest, reason, observed_ts) VALUES (?, ?, ?)",
  );
  const original = db.prepare("SELECT media_sha FROM messages WHERE chat_id=? AND id=?");
  const dropReactions = db.prepare("DELETE FROM reactions WHERE chat_id=? AND message_id=?");
  const clearQuoteLinks = db.prepare("UPDATE messages SET quoted_id=NULL WHERE chat_id=? AND quoted_id=?");
  const dropMessage = db.prepare("DELETE FROM messages WHERE chat_id=? AND id=?");
  const sharedMedia = db.prepare("SELECT COUNT(*) AS n FROM messages WHERE media_sha=?");

  function digest(chatId: string, id: string): string {
    // Message IDs are random-looking WhatsApp identifiers; hashing also
    // prevents ordinary SQL inspection from revealing conversation IDs.
    return createHash("sha256").update(chatId).update("\0").update(id).digest("hex");
  }

  function isSuppressed(chatId: string, id: string): boolean {
    return known.get(digest(chatId, id)) !== undefined;
  }

  /**
   * Transactional removal, even when a caller is already in a SQLite
   * transaction. A persistent digest prevents re-ingestion on reconnect.
   */
  function purge(chatId: string, id: string, reason: PrivacyPurgeReason): boolean {
    const media = original.get(chatId, id) as StoredMedia | undefined;
    const existed = media !== undefined;
    const hash = digest(chatId, id);
    db.exec("SAVEPOINT privacy_purge");
    try {
      suppress.run(hash, reason, Math.floor(Date.now() / 1000));
      // This archive has no revision table yet. When the revisions feature is
      // added, it must use this same purge path and delete its own rows too.
      const historyTable = db.prepare(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_revisions'",
      ).get();
      if (historyTable !== undefined) {
        db.prepare("DELETE FROM message_revisions WHERE chat_id=? AND id=?").run(chatId, id);
      }
      dropReactions.run(chatId, id);
      clearQuoteLinks.run(chatId, id);
      dropMessage.run(chatId, id); // Also removes current text from FTS triggers.
      db.exec("RELEASE SAVEPOINT privacy_purge");
    } catch (err) {
      db.exec("ROLLBACK TO SAVEPOINT privacy_purge");
      db.exec("RELEASE SAVEPOINT privacy_purge");
      throw err;
    }
    // Cached files are content addressed and might belong to another message.
    // Only remove files with verified digests and no remaining DB reference.
    const sha = media?.media_sha;
    if (sha && HASH.test(sha) &&
        (sharedMedia.get(sha) as Count).n === 0) {
      const mediaDir = process.env['WHATSAPP_MEDIA_DIR'] ||
        join(process.env['WHATSAPP_DATA_DIR'] || "/data/whatsapp", "media");
      try {
        unlinkSync(join(mediaDir, sha));
      } catch (err) {
        if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
          // A file cleanup failure must not roll back the privacy tombstone:
          // the database already refuses to expose or replay this message.
          // The error should be investigated rather than silently ignored.
          throw new Error("Purged database row, but cached media cleanup failed", { cause: err });
        }
      }
    }
    return existed;
  }

  /**
   * Clear only messages present at or before the last message timestamp from
   * an explicit WhatsApp clear-chat action. Never infer a clear from missing
   * messages in a partial history sync. The caller must validate account,
   * sender, chat identity, and the action's preserve-starred setting.
   *
   * Individual purges have independent transactions and suppress replay.
   * If a later purge fails, preceding purges remain durable; retry is safe.
   */
  function clearChatThrough(chatId: string, lastMessageTs: number): { purged: number; skipped: number } {
    if (!chatId || !Number.isSafeInteger(lastMessageTs) ||
        lastMessageTs < 1 || lastMessageTs > Math.floor(Date.now() / 1000) + 300) {
      throw new RangeError("Invalid confirmed clear-chat boundary");
    }
    // Hard cap stops a malformed event wiping unexpectedly large archives.
    const limit = 20000;
    const old = db.prepare(
      "SELECT id FROM messages WHERE chat_id=? AND ts<=? ORDER BY ts, rowid LIMIT ?",
    ).all(chatId, lastMessageTs, limit + 1) as { id: string }[];
    if (old.length > limit) throw new RangeError("Clear-chat batch exceeds safety limit");
    let purged = 0;
    for (const row of old) {
      if (purge(chatId, row.id, "clear_chat")) purged++;
    }
    const skipped = (db.prepare(
      "SELECT COUNT(*) AS n FROM messages WHERE chat_id=? AND ts>?",
    ).get(chatId,lastMessageTs) as Count).n;
    return { purged, skipped };
  }

  /** Safe startup migration: only *known* user-authored revocations. */
  function purgeExistingOwnRevokes(): number {
    const old = db.prepare(
      "SELECT chat_id, id FROM messages WHERE from_me=1 AND deleted_ts IS NOT NULL",
    ).all() as RevokedMessage[];
    for (const row of old) purge(row.chat_id, row.id, "own_revoke");
    return old.length;
  }

  return { isSuppressed, purge, clearChatThrough, purgeExistingOwnRevokes };
}
