// Instrument the *deployed* pinned Baileys client, rather than the upstream
// TypeScript source. pnpm deploy creates a new node_modules tree from its
// content-addressed store; editing the build tree before deploy is unreliable.
//
// Baileys currently parses clearChatAction but emits no event for it.
// Emit an internal chats.update metadata envelope which our own API recognises;
// no WhatsApp mutation is sent and no message contents are logged or exposed.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TARGET = "/opt/out/api/node_modules/baileys/lib/Utils/chat-utils.js";
const ANCHOR = "else if (action?.deleteChatAction || type === 'deleteChat') {";
export function instrumentBaileysClearChat(source) {
  const i = source.indexOf(ANCHOR);
  if (i < 0 || source.indexOf(ANCHOR, i + 1) >= 0) {
    throw new Error("Unsupported Baileys build: deleteChatAction anchor absent/ambiguous");
  }
  if (source.includes("__archiveClearChat")) {
    throw new Error("Baileys clearChatAction already instrumented");
  }
  const additional = `else if (action?.clearChatAction || type === 'clearChat') {
        // Internal event only. The app checks self-chat scope, a valid cutoff,
        // initial-sync status and whether WhatsApp retains starred messages.
        const range = action?.clearChatAction?.messageRange;
        ev.emit('chats.update', [{
            id,
            __archiveClearChat: {
                lastMessageTimestamp: range?.lastMessageTimestamp == null
                    ? null : String(range.lastMessageTimestamp),
                preserveStarred: syncAction.index?.[2] !== '1',
                initial: isInitialSync
            }
        }]);
    } `;
  return source.slice(0, i) + additional + source.slice(i);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const file = fs.realpathSync(TARGET);
  const original = fs.readFileSync(file, "utf8");
  const patched = instrumentBaileysClearChat(original);
  fs.writeFileSync(file, patched);
  console.log("Pinned Baileys compiled clearChatAction handler instrumented.");
}
