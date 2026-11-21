import { ArchiveData, VISIBLE_MESSAGE, META_AI_JID } from './archive-data.mjs';

// Local-only, read-only review of the same SQLite database as the archive viewer.
// These flags select candidates for an LLM to assess; they are NOT completion judgements.
const DAY = 86400;
const MAX_WINDOW = 90 * DAY;
const MAX_SCAN = 50000;
const actionWords = /(?:\b(?:could|can|would|will) you\b|\bplease (?:send|check|call|book|pay|confirm|let me know)\b|\b(?:let me know|remind me|are you free|what time|when can|when are)\b|\?)/i;
const promiseWords = /(?:\bi['’]?ll\s+(?:send|check|book|pay|call|sort|ask|organis|arrang|bring|pick|refer|follow|confirm|transfer|look|do|get)|\bi will\s+(?:send|check|book|pay|call|sort|ask|organis|arrang|bring|pick|refer|follow|confirm|transfer|look|do|get)|\bi (?:need|have) to\s+(?:send|check|book|pay|call|sort|ask|organis|arrang|refer|follow|confirm)|\bwill send\b)/i;
const arrangements = /(?:\b(?:tomorrow|tonight|next (?:week|month|sunday|monday|tuesday|wednesday|thursday|friday|saturday)|this (?:weekend|sunday|monday|tuesday|wednesday|thursday|friday|saturday))\b|\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b)/i;
function groupMention(text, hint) {
  const body = normalize(text);
  if (/@[0-9A-Za-z_]{3,}/.test(body)) return true;
  if (typeof hint !== 'string' || !hint.trim() || hint.length > 80) return false;
  const safe = hint.trim().toLowerCase().replace(/[^a-z0-9 ]/g, '');
  return safe.length >= 3 && body.toLowerCase().includes(safe);
}
const normalize = value => String(value ?? '').replace(/\s+/g, ' ').trim();
function int(value, fallback, min, max, name) {
  if (value === undefined || value === null) return fallback;
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new RangeError('Invalid ' + name);
  return value;
}
function period(options) {
  const now = Math.floor(Date.now() / 1000);
  const days = int(options.days, 21, 1, 90, 'days');
  const before = int(options.before, now, 0, 9999999999, 'before');
  const after = int(options.after, before - days * DAY, 0, 9999999999, 'after');
  if (after > before || before - after > MAX_WINDOW) throw new RangeError('Review window must be at most 90 days');
  return { after, before };
}
function brief(row, store, textLimit = 320) {
  const text = normalize(row.text || row.transcript);
  return {
    id: row.id, ts: row.ts, from_me: !!row.from_me, kind: row.kind,
    sender: row.from_me ? 'You' : store.displayName(row.sender_id),
    text: text ? text.slice(0, textLimit) : '[' + row.kind + ']',
    ...(row.transcript && !row.text ? { transcript: true } : {})
  };
}
function loadRows(store, { after, before, mode }) {
  // A bounded scan prevents an unexpectedly huge history import exhausting the MCP process.
  const rows = store.db.prepare(`
    SELECT m.chat_id, m.id, m.sender_id, m.ts, m.from_me, m.kind, m.text, m.transcript,
           c.name AS chat_name, c.is_group, c.unread_count
    FROM messages m JOIN chats c ON c.id = m.chat_id
    WHERE m.ts BETWEEN :after AND :before
      AND m.deleted_ts IS NULL
      AND m.chat_id != :meta
      AND ${VISIBLE_MESSAGE}
      AND (:mode = 'all'
        OR (:mode = 'direct' AND c.is_group = 0)
        OR (:mode = 'groups' AND c.is_group = 1))
    ORDER BY m.ts DESC, m.rowid DESC LIMIT :scanLimit
  `).all({ after, before, meta: META_AI_JID, mode, scanLimit: MAX_SCAN + 1 });
  return { rows: rows.slice(0, MAX_SCAN), truncated: rows.length > MAX_SCAN };
}
export function reviewConversations(dataDir, options = {}) {
  const { after, before } = period(options);
  const mode = options.mode ?? 'direct';
  if (!['direct', 'groups', 'all'].includes(mode)) throw new RangeError('Invalid mode');
  const limit = int(options.limit, 35, 1, 80, 'limit');
  const offset = int(options.offset, 0, 0, 5000, 'offset');
  const context = int(options.context, 6, 2, 12, 'context');
  const onlyCandidates = options.only_candidates !== false;
  const store = new ArchiveData(dataDir);
  try {
    const { rows, truncated } = loadRows(store, { after, before, mode });
    const byChat = new Map();
    for (const r of rows) {
      if (!byChat.has(r.chat_id)) {
        byChat.set(r.chat_id, {
          chat: r.chat_id, name: store.displayName(r.chat_id, r.chat_name, !!r.is_group),
          is_group: !!r.is_group, unread_count: r.unread_count, messages: []
        });
      }
      byChat.get(r.chat_id).messages.push(r); // descending by timestamp
    }
    const summaries = [];
    for (const c of byChat.values()) {
      const ms = c.messages;
      const last = ms[0];
      const lastInbound = ms.find(m => !m.from_me);
      const lastOutbound = ms.find(m => m.from_me);
      const pending = !last.from_me && (!c.is_group || groupMention(last.text, options.name_hint))
        && actionWords.test(normalize(last.text || last.transcript));
      const commitments = ms.filter(m => m.from_me && promiseWords.test(normalize(m.text))).slice(0, 3);
      const planned = ms.filter(m => arrangements.test(normalize(m.text || m.transcript))).slice(0, 2);
      // Generic group questions are not addressed to the account owner.
      const mentions = c.is_group && ms.some(m => !m.from_me && groupMention(m.text, options.name_hint));
      const isCandidate = pending || commitments.length > 0 || (!c.is_group && planned.length > 0) || mentions;
      if (onlyCandidates && !isCandidate) continue;
      const flags = [];
      if (pending) flags.push('possible_reply_needed');
      if (commitments.length) flags.push('commitment_to_verify');
      if (planned.length && !c.is_group) flags.push('arrangement_to_verify');
      if (mentions) flags.push('group_mention');
      summaries.push({
        chat: c.chat, name: c.name, is_group: c.is_group, unread_count: c.unread_count,
        message_count: ms.length,
        received_count: ms.filter(m => !m.from_me).length,
        sent_count: ms.filter(m => !!m.from_me).length,
        last_activity: last.ts, last_from_me: !!last.from_me,
        flags,
        last_incoming: lastInbound ? brief(lastInbound, store, 240) : null,
        last_outgoing: lastOutbound ? brief(lastOutbound, store, 240) : null,
        commitments: commitments.map(m => brief(m, store)),
        upcoming_or_dated: planned.map(m => brief(m, store)),
        context: ms.slice(0, context).reverse().map(m => brief(m, store))
      });
    }
    summaries.sort((a,b) => {
      const score = s => (s.flags.includes('possible_reply_needed') ? 8 : 0) +
        (s.flags.includes('commitment_to_verify') ? 5 : 0) +
        (s.flags.includes('arrangement_to_verify') ? 2 : 0) +
        (s.flags.includes('group_mention') ? 1 : 0);
      return score(b) - score(a) || b.last_activity - a.last_activity;
    });
    const items = summaries.slice(offset, offset + limit);
    return {
      from: after, to: before, mode, only_candidates: onlyCandidates,
      scanned_messages: rows.length, scanned_conversations: byChat.size,
      scan_truncated: truncated, candidate_conversations: summaries.length,
      next_offset: offset + limit < summaries.length ? offset + limit : null,
      caveat: 'Flags are heuristic leads, not verified outstanding tasks. A later reply, action elsewhere, or untranscribed media may resolve them.',
      items
    };
  } finally { store.close(); }
}

export function reviewContext(dataDir, options = {}) {
  const chats = options.chats;
  if (!Array.isArray(chats) || chats.length < 1 || chats.length > 20 ||
      chats.some(c => typeof c !== 'string' || !c || c.length > 300))
    throw new RangeError('chats must contain 1–20 chat IDs');
  const maxMessages = int(options.limit_per_chat, 40, 1, 100, 'limit_per_chat');
  const { after, before } = period(options);
  const store = new ArchiveData(dataDir);
  try {
    const stmt = store.db.prepare(`
      SELECT m.id, m.chat_id, m.sender_id, m.ts, m.from_me, m.kind, m.text, m.transcript
      FROM messages m
      WHERE m.chat_id = :chat AND m.ts BETWEEN :after AND :before AND m.deleted_ts IS NULL
        AND ${VISIBLE_MESSAGE}
      ORDER BY m.ts DESC, m.rowid DESC LIMIT :limit
    `);
    const chatStmt = store.db.prepare('SELECT name, is_group FROM chats WHERE id = ?');
    return {
      from: after, to: before,
      conversations: chats.map(chat => {
        if (chat === META_AI_JID) return { chat, messages: [], truncated: false };
        const c = chatStmt.get(chat);
        if (!c) return { chat, error: 'Chat not found' };
        const rows = stmt.all({ chat, after, before, limit: maxMessages + 1 });
        return {
          chat, name: store.displayName(chat, c.name, !!c.is_group),
          truncated: rows.length > maxMessages,
          messages: rows.slice(0, maxMessages).reverse().map(m => brief(m, store, 1800))
        };
      })
    };
  } finally { store.close(); }
}
