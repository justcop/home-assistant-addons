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
// Normalize original LID keys when checking already-erased message IDs:
// historical Meta AI prompts must not reappear under the restored phone chat.
patchIngest(
  'canonicalId, isGroupJid, isStatusBroadcastJid, lidFromJid, phoneFromJid, userJid',
  'canonicalId, normalizeJid, isGroupJid, isStatusBroadcastJid, lidFromJid, phoneFromJid, userJid'
);
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
  '      if (privacyRetention.isSuppressed(chatId, messageId) ||\n' +
  '          privacyRetention.isSuppressed(normalizeJid(remoteJid), messageId)) return;\n' +
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
fs.writeFileSync(ingestFile, ingest);
fs.copyFileSync('/src/privacy-retention.ts', '/src/packages/api/src/whatsapp/privacy-retention.ts');
console.log('Pinned upstream patched with sender-aware revocations and Delete-for-me purges.');



// Fresh-pairing full-history experiment. Kept on an isolated Git branch;
// DO NOT merge/deploy to an existing authenticated installation as a substitute
// for a fresh pairing. Never clear /data or the existing archive to run this.
//
// Baileys documents Browsers.macOS("Desktop") + syncFullHistory: true, but
// the pinned pairing-code implementation/server rejects the name "Desktop"
// in companion_platform_display with HTTP/IQ 400. Safari is a recognized
// companion label while preserving the macOS platform. This tests whether
// the full-history negotiation works on a NEW pairing with phone-number code.
// It is an experiment, not proof that Safari receives the same history as
// Baileys' documented Desktop profile.
const connectionFile = '/src/packages/api/src/whatsapp/connection.ts';
let connection = fs.readFileSync(connectionFile, 'utf8');
function patchConnection(before, after) {
  const at = connection.indexOf(before);
  if (at < 0 || connection.indexOf(before, at + 1) !== -1) {
    throw new Error('Extended history patch anchor missing or ambiguous');
  }
  connection = connection.slice(0, at) + after + connection.slice(at + before.length);
}
const oldBrowserNote = `        // 🔴 \`Chrome\`, never \`Desktop\`, and this is load-bearing rather than cosmetic. Baileys
        // sends \`companion_platform_display\` as \`\${browser[1]} (\${browser[0]})\`, and WhatsApp
        // validates that string strictly for the pairing-code IQ — it does not for QR
        // registration, which is why only this deployment's login path is affected. "Desktop
        // (Mac OS)" is answered \`<iq type='error'><error code='400' text='bad-request'/></iq>\`,
        // and \`requestPairingCode\` never awaits that reply: it returns a locally generated code
        // regardless, so the server prints a perfectly plausible 8 characters that were never
        // registered and the phone answers "couldn't link device". Upstream issue #2560; its fix,
        // PR #2559, is unmerged as of 7.0.0-rc14, so supplying a canonical label is ours to do.
        // Only the six names in Baileys' \`BROWSER_TO_COMPANION_WEB_CLIENT\` map — Chrome, Edge,
        // Firefox, IE, Opera, Safari — are accepted; anything else, "Desktop" included, is not.
        browser: Browsers.macOS("Chrome"), // also the ban-risk mitigation from the spec`;
patchConnection(oldBrowserNote,
  '        // Experimental fresh pairing: Safari is a canonical pairing-code label.\\n' +
  '        // macOS platform plus full-history negotiation; do not assume full recovery.\\n' +
  '        browser: Browsers.macOS("Safari"),');
patchConnection('syncFullHistory: false,','syncFullHistory: true,');
fs.writeFileSync(connectionFile, connection);
console.log('Experimental Safari/macOS pairing-code profile with full-history sync enabled.');

// Persistent, rate-limited on-demand history. Keep it out of the public REST
// and MCP surfaces, and fail the image build if upstream bootstrap shifts.
const mainPath = '/src/packages/api/src/main.ts';
let mainSource = fs.readFileSync(mainPath, 'utf8');
const historyImportAnchor = 'import { makeSender } from "./whatsapp/send.js";';
const historyStartAnchor = '  live = conn;\n';
if (mainSource.split(historyImportAnchor).length !== 2 || mainSource.split(historyStartAnchor).length !== 2) {
  throw new Error('History backfill bootstrap anchors changed');
}
mainSource = mainSource.replace(historyImportAnchor,
  historyImportAnchor + '\nimport { startResumableHistoryBackfill } from "./history-backfill.js";');
mainSource = mainSource.replace(historyStartAnchor,
  historyStartAnchor + '\n  if (process.env[\"WHATSAPP_HISTORY_BACKFILL\"] === "1") {\n' +
  '    startResumableHistoryBackfill({ conn, chats, messages, dataDir: config.dataDir, logger });\n' +
  '  }\n');
fs.writeFileSync(mainPath, mainSource);
fs.copyFileSync('/src/history-backfill.ts', '/src/packages/api/src/history-backfill.ts');
console.log('Resumable history worker included; requires explicit opt-in environment flag.');

// Privacy-safe read-state diagnostics: count only server read-zero updates,
// without logging message data, group/contact IDs or chat names. These do not
// read or write WhatsApp state; they only identify why unread badges stay stale.
const readSyncAnchor = '    sock.ev.on("chats.update", (cs) => {\n      guard("chats.update", () => {\n        runChunked(cs, "chats", applyChat);';
if (ingest.split(readSyncAnchor).length !== 2) {
  throw new Error('Read-state diagnostics anchor changed');
}
ingest = ingest.replace(readSyncAnchor,
  '    sock.ev.on("chats.update", (cs) => {\n' +
  '      guard("chats.update", () => {\n' +
  '        const zeroUpdates = cs.filter(c => c.unreadCount === 0).length;\n' +
  '        if (zeroUpdates > 0) logger.info({ count: zeroUpdates }, "unread-sync: read-zero updates received");\n' +
  '        runChunked(cs, "chats", applyChat);');
// ON_DEMAND history carries an old chat snapshot. It must not overwrite
// the current local unread count with historical state.
patchIngest(
  '    runChunked(h.chats, "chats", applyChat);',
  '    runChunked(h.chats, "chats", c => {\n' +
  '      if (h.syncType === proto.HistorySync.HistorySyncType.ON_DEMAND) {\n' +
  '        const metadata: Partial<Chat> = { ...c };\n' +
  '        delete metadata.unreadCount;\n' +
  '        applyChat(metadata);\n' +
  '      } else applyChat(c);\n' +
  '    });'
);
fs.writeFileSync(ingestFile, ingest);
console.log('Privacy-safe read-state diagnostics and on-demand unread protection included.');
