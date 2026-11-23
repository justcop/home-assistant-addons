// Build-time patch to pinned upstream. Fail closed when upstream source changes.
import fs from 'node:fs';
const file='/src/packages/api/src/whatsapp/ingest.ts';
let source=fs.readFileSync(file,'utf8');
function replaceOnce(oldText,newText){
  const first=source.indexOf(oldText);
  if(first<0 || source.indexOf(oldText,first+1)>=0)throw new Error('Upstream Meta AI patch anchor missing or ambiguous');
  source=source.slice(0,first)+newText+source.slice(first+oldText.length);
}
const metaAI="'13135550002@s.whatsapp.net'";
replaceOnce(
  "      if (isStatusBroadcastJid(remoteJid)) return;\n\n      const content = normalizeMessageContent(m.message);",
  "      if (isStatusBroadcastJid(remoteJid) || canonical(remoteJid) === "+metaAI+") return;\n\n      const content = normalizeMessageContent(m.message);"
);
replaceOnce(
  "      if (rawId == null || rawId === \"\" || isStatusBroadcastJid(rawId)) return;\n      const chatId = canonical(rawId);\n      const isGroup = isGroupJid(chatId);",
  "      if (rawId == null || rawId === \"\" || isStatusBroadcastJid(rawId)) return;\n      const chatId = canonical(rawId);\n      if (chatId === "+metaAI+") return;\n      const isGroup = isGroupJid(chatId);"
);
replaceOnce(
  "      if (type !== undefined && CONTROL_CONTENT.has(type)) return;\n\n      const chatId = canonical(remoteJid);",
  "      if (type !== undefined && CONTROL_CONTENT.has(type)) return;\n" +
  "      // No human message: group/security notices or history/key transport.\n" +
  "      // Keep polls, albums, event messages and every unsupported content type.\n" +
  "      const fields = Object.keys(content ?? {}).filter(k => k !== 'messageContextInfo');\n" +
  "      const transport = new Set(['messageHistoryNotice','messageHistoryBundle','senderKeyDistributionMessage']);\n" +
  "      if ((m.messageStubType != null && fields.length === 0) ||\n" +
  "          (fields.length > 0 && fields.every(k => transport.has(k)))) return;\n\n" +
  "      const chatId = canonical(remoteJid);"
);
fs.writeFileSync(file,source);
console.log('Pinned upstream patched to skip Meta AI conversation on ingestion.');

// Extend the bundled MCP server without introducing a second public listener.
// The anchor is deliberately strict, so an upstream update cannot silently
// drop the read-only review tools.
const readToolsFile = '/src/packages/mcp/src/tools/reads.ts';
const readTools = fs.readFileSync(readToolsFile, 'utf8');
const insertionPoint = readTools.lastIndexOf('\n}');
if (!readTools.endsWith('\n}\n') || insertionPoint < 0)
  throw new Error('Upstream read-tools registration anchor changed');
const extraTools = fs.readFileSync('/src/review-mcp-register.ts', 'utf8');
fs.writeFileSync(readToolsFile, readTools.slice(0,insertionPoint) + '\n' + extraTools + readTools.slice(insertionPoint));
console.log('Pinned MCP read tool registrations extended for bounded local review.');

/**
 * Apply the per-message privacy contract to the *pinned* upstream API.
 * The upstream's messages.delete handler currently creates tombstones.
 * Baileys uses messages.delete for delete-for-me app-state actions; these
 * must instead erase the message, while revokes are sender-aware.
 *
 * Strict string anchors make an upstream bump fail its Docker build instead
 * of silently dropping deletion protection.
 */
const ingestFile = '/src/packages/api/src/whatsapp/ingest.ts';
let ingest = fs.readFileSync(ingestFile, 'utf8');
function patchIngest(before, after) {
  const at = ingest.indexOf(before);
  if (at < 0 || ingest.indexOf(before, at + 1) !== -1) {
    throw new Error('Privacy-retention patch anchor missing or ambiguous');
  }
  ingest = ingest.slice(0, at) + after + ingest.slice(at + before.length);
}
const importAnchor = 'import type { Logger } from "pino";';
patchIngest(importAnchor,
  importAnchor + '\nimport { makePrivacyRetention } from "./privacy-retention.js";');
const dependencyAnchor = '  const { db, chats, contacts, messages, reactions, logger, selfId } = deps;';
patchIngest(dependencyAnchor, dependencyAnchor +
  '\n  const privacyRetention = makePrivacyRetention(db);' +
  '\n  const removedOwnRevokes = privacyRetention.purgeExistingOwnRevokes();' +
  '\n  if (removedOwnRevokes) logger.info({ count: removedOwnRevokes }, "privacy: removed own revoked messages");');
patchIngest(
  '      const chatId = canonical(remoteJid);\n      const isGroup = isGroupJid(chatId);\n      chats.ensure(chatId, isGroup);',
  '      const chatId = canonical(remoteJid);\n' +
  '      // Replayed sync history must never restore content explicitly erased locally.\n' +
  '      if (privacyRetention.isSuppressed(chatId, messageId)) return;\n' +
  '      const isGroup = isGroupJid(chatId);\n      chats.ensure(chatId, isGroup);'
);
patchIngest(
  '    if (update.messageStubType === WAMessageStubType.REVOKE) {\n' +
  '      messages.markDeleted(chatId, id, nowSec());\n      return;\n    }',
  '    if (update.messageStubType === WAMessageStubType.REVOKE) {\n' +
  '      const stored = messages.get(chatId, id);\n' +
  '      if (stored?.fromMe === true || (stored === undefined && key.fromMe === true)) {\n' +
  '        privacyRetention.purge(chatId, id, "own_revoke");\n' +
  '      } else {\n' +
  '        // Other people\'s delete-for-everyone actions retain a tombstone.\n' +
  '        messages.markDeleted(chatId, id, nowSec());\n' +
  '      }\n      return;\n    }'
);
patchIngest(
  '    const ts = nowSec();\n' +
  '    for (const key of payload.keys) {\n' +
  '      const target = targetOf(key);\n' +
  '      if (target !== undefined) messages.markDeleted(target.chatId, target.id, ts);\n' +
  '    }',
  '    // Baileys emits message-key deletions from the local app-state\n' +
  '    // deleteMessageForMeAction. Unlike revokes, these erase data.\n' +
  '    for (const key of payload.keys) {\n' +
  '      const target = targetOf(key);\n' +
  '      if (target !== undefined) privacyRetention.purge(target.chatId, target.id, "delete_for_me");\n' +
  '    }'
);
// A clearChatAction is converted by our Baileys runtime patch into an
// internal chats.update record. During the first deployment only the user's
// own Messages to Yourself thread is eligible for destructive clearing.
patchIngest(
  '      const chatId = canonical(rawId);\n      if (chatId === \'13135550002@s.whatsapp.net\') return;\n',
  '      const chatId = canonical(rawId);\n      if (chatId === \'13135550002@s.whatsapp.net\') return;\n' +
  '      const archiveClear = (c as Partial<Chat> & { __archiveClearChat?: {' +
  ' lastMessageTimestamp?: string | null; preserveStarred?: boolean; initial?: boolean' +
  ' } }).__archiveClearChat;\n' +
  '      if (archiveClear) {\n' +
  '        if (archiveClear.initial) {\n' +
  '          logger.info({ outcome: "initial_sync_ignored" }, "privacy: clear-chat action observed");\n' +
  '        } else if (!self() || chatId !== self()) {\n' +
  '          logger.info({ outcome: "outside_self_test_scope" }, "privacy: clear-chat action observed");\n' +
  '        } else if (archiveClear.preserveStarred !== false) {\n' +
  '          logger.info({ outcome: "starred_preserved_unsupported" }, "privacy: clear-chat action observed");\n' +
  '        } else {\n' +
  '          const rawCutoff = Number(archiveClear.lastMessageTimestamp);\n' +
  '          const cutoff = Number.isSafeInteger(rawCutoff) && rawCutoff > 0\n' +
  '            ? Math.floor(rawCutoff >= 1e11 ? rawCutoff / 1000 : rawCutoff) : null;\n' +
  '          if (cutoff === null) {\n' +
  '            logger.warn({ outcome: "no_valid_boundary" }, "privacy: clear-chat ignored");\n' +
  '          } else {\n' +
  '            const result = privacyRetention.clearChatThrough(chatId, cutoff);\n' +
  '            logger.info({ outcome: "purged", ...result }, "privacy: self-chat clear applied");\n' +
  '          }\n' +
  '        }\n' +
  '      }\n' +
  ''
);
// The account's own LID is not interchangeable with the Messages to
// Yourself phone JID for archive purposes. Meta AI prompts can arrive
// using this same unsuffixed LID, so preserve that provenance as its own
// unclassified conversation instead of asserting they were self-messages.
patchIngest(
  'canonicalId, isGroupJid, isStatusBroadcastJid, lidFromJid, phoneFromJid, userJid',
  'canonicalId, normalizeJid, isGroupJid, isStatusBroadcastJid, lidFromJid, phoneFromJid, userJid'
);
patchIngest(
  '  function canonical(jid: string): string {\n    return canonicalId(jid, lookup);\n  }',
  '  function canonical(jid: string): string {\n    return canonicalId(jid, lookup);\n  }\n' +
  '  // Keep the self LID separate when it resolves to our own phone account.\n' +
  '  function canonicalChat(jid: string): string {\n' +
  '    const raw = normalizeJid(jid);\n' +
  '    if (raw.endsWith("@lid") && self() !== undefined && canonical(raw) === self()) return raw;\n' +
  '    return canonical(jid);\n' +
  '  }'
);
patchIngest('return { chatId: canonical(remoteJid), id };','return { chatId: canonicalChat(remoteJid), id };');
patchIngest(
  '      const chatId = canonical(remoteJid);\n      // Replayed sync history',
  '      const chatId = canonicalChat(remoteJid);\n      // Replayed sync history'
);
patchIngest(
  '      const chatId = canonical(rawId);\n      if (chatId === \'13135550002@s.whatsapp.net\')',
  '      const chatId = canonicalChat(rawId);\n      if (chatId === \'13135550002@s.whatsapp.net\')'
);
patchIngest(
  '    contacts.linkIdentity(lid, pn);',
  '    // Identity merging would fold the self LID back into Messages to Yourself,\n' +
  '    // losing the original destination ambiguity. All other LID mappings work as before.\n' +
  '    if (self() !== undefined && canonical(pn) === self()) return;\n' +
  '    contacts.linkIdentity(lid, pn);'
);
fs.writeFileSync(ingestFile, ingest);
fs.copyFileSync('/src/privacy-retention.ts', '/src/packages/api/src/whatsapp/privacy-retention.ts');
console.log('Pinned upstream patched with sender-aware revocations and Delete-for-me purges.');
