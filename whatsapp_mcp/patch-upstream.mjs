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
