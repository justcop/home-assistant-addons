/**
 * One-shot, read-only diagnostic for mixed Meta AI / Messages to Yourself
 * routing. No content or JIDs are logged. Only metadata and message timestamp.
 *
 * Executes before WhatsApp API takes SQLite writer ownership.
 */
import { DatabaseSync } from 'node:sqlite';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

export async function diagnoseSelfRouting(dataDir, phoneNumber) {
  const dbPath=join(dataDir,'whatsapp.db');
  if (!existsSync(dbPath) || !/^[1-9][0-9]{7,14}$/.test(phoneNumber)) return;
  const { proto }=await import('/opt/whatsapp-api/node_modules/baileys/lib/index.js');
  const db=new DatabaseSync(dbPath,{readOnly:true});
  const self=phoneNumber+'@s.whatsapp.net';
  try {
    const rows=db.prepare(
      'SELECT id,ts,raw,chat_id FROM messages WHERE chat_id=? AND ts>=? ORDER BY ts DESC LIMIT 100'
    ).all(self, 1790000000);
    const classify=(jid)=> {
      if (jid==null) return 'missing';
      if (jid===self || jid.startsWith(phoneNumber+':') && jid.endsWith('@s.whatsapp.net')) return 'self_phone';
      if (jid==='13135550002@s.whatsapp.net') return 'known_meta_ai';
      if (jid.endsWith('@lid')) return 'lid';
      if (jid.endsWith('@s.whatsapp.net')) return 'other_phone';
      return 'other_type';
    };
    const knownSelfLids=db.prepare('SELECT lid FROM contacts WHERE id=? OR phone_number=?').all(self,phoneNumber)
      .map(c=>String(c.lid??'').replace(/@lid$/,'').replace(/:\\d+$/,'').replace(/_\\d+$/,''))
      .filter(Boolean);
    const observedLids=new Map();
    const keyLid=(jid)=> {
      if (!jid || !String(jid).endsWith('@lid')) return null;
      const v=String(jid).replace(/@lid$/,'').replace(/:\\d+$/,'').replace(/_\\d+$/,'');
      if(!observedLids.has(v)) observedLids.set(v,'lid_'+(observedLids.size+1));
      return { distinctLabel:observedLids.get(v), matchesSelfContactLid:knownSelfLids.includes(v) };
    };
    const diagnostics=[];
    for(const row of rows){
      try {
        const m=proto.WebMessageInfo.decode(new Uint8Array(row.raw));
        const k=m.key ?? {};
        const allKeys=Object.keys(k).sort();
        const ci=m.message?.extendedTextMessage?.contextInfo ??
          m.message?.imageMessage?.contextInfo ??
          m.message?.conversation?.contextInfo ??
          null;
        diagnostics.push({
          ts:Number(row.ts),
          remote:classify(k.remoteJid),
          remoteLid:keyLid(k.remoteJid),
          participant:classify(k.participant),
          keyFlags:allKeys.filter(x=>!['id','remoteJid','participant','fromMe'].includes(x)),
          fromMe:k.fromMe===true,
          messageFields:Object.keys(m.message??{}).filter(x=>x!=='messageContextInfo').sort(),
          contextFields:ci?Object.keys(ci).filter(x=>!['quotedMessage','quotedParticipant','quotedStanzaID'].includes(x)).sort():[],
          stub:m.messageStubType??null,
          // Do not log message text or original WhatsApp identifiers.
        });
      }catch{diagnostics.push({ts:Number(row.ts),unreadable:true});}
    }
    const contacts=db.prepare('SELECT id,lid,phone_number FROM contacts WHERE id=? OR phone_number=? LIMIT 10')
      .all(self,phoneNumber);
    console.log('Self-chat message routing diagnostic: '+JSON.stringify({
      inspected:rows.length,
      contacts:contacts.map(c=>({
        selfId:c.id===self,
        lidPresent:!!c.lid,
        phoneMatches:c.phone_number===phoneNumber
      })),
      messages:diagnostics
    }));
  } finally {db.close();}
}
