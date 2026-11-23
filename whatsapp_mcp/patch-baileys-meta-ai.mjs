
/**
 * Meta AI recipient is on the raw stanza (@bot) but is discarded by the pinned
 * Baileys message decoder. Filter BEFORE decrypt/upsert and send correct sender
 * receipt to avoid perpetual redelivery (upstream Baileys issue #2822).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const FILE='/opt/out/api/node_modules/baileys/lib/Socket/messages-recv.js';

export function instrumentMetaAiStanzas(source) {
  const anchor='const handleMessage = async (node) => {';
  const at=source.indexOf(anchor);
  if(at<0 || source.indexOf(anchor,at+1)!==-1)
    throw new Error('Pinned Baileys handleMessage anchor missing or ambiguous');
  if(source.includes('privacy: excluded outgoing Meta AI stanza'))
    throw new Error('Meta AI stanza filter already installed');
  const lines=[
    '// Original recipient survives here, unlike decoded message key.',
    'if (typeof node.attrs.recipient === "string" && node.attrs.recipient.endsWith("@bot") &&',
    '    typeof node.attrs.from === "string" &&',
    '    (areJidsSameUser(node.attrs.from, authState.creds.me?.id) ||',
    '     areJidsSameUser(node.attrs.from, authState.creds.me?.lid))) {',
    '  if (!node.attrs.id) throw new Error("Meta AI stanza missing message id");',
    '  await sendNode({tag:"receipt", attrs: {id: node.attrs.id, to:node.attrs.from,',
    '    recipient:node.attrs.recipient, type:"sender"}});',
    '  logger.info("privacy: excluded outgoing Meta AI stanza");',
    '  return;',
    '}'
  ];
  const addition='\n'+lines.map(line=>'\t\t'+line).join('\n')+'\n';
  return source.slice(0,at+anchor.length)+addition+source.slice(at+anchor.length);
}

if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const file=fs.realpathSync(FILE);
  fs.writeFileSync(file,instrumentMetaAiStanzas(fs.readFileSync(file,'utf8')));
  console.log('Pinned Baileys Meta AI stanza exclusion installed');
}
