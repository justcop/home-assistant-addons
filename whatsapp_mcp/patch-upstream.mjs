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
fs.writeFileSync(file,source);
console.log('Pinned upstream patched to skip Meta AI conversation on ingestion.');
