
import test from 'node:test';
import assert from 'node:assert/strict';
import { instrumentMetaAiStanzas } from '../patch-baileys-meta-ai.mjs';

const snippet='const handleMessage = async (node) => { events.push("decrypted"); };';
function run(node, me={id:"447700111222@s.whatsapp.net",lid:"123456789@lid"}) {
  const source=instrumentMetaAiStanzas(snippet);
  const events=[];
  const receipts=[];
  const logs=[];
  const areJidsSameUser=(a,b)=>!!a && !!b && a.split('@')[0].split(':')[0]===b.split('@')[0].split(':')[0];
  const authState={creds:{me}};
  const sendNode=async receipt=>{receipts.push(receipt)};
  const logger={info:line=>logs.push(line)};
  const handleMessage=Function('events','areJidsSameUser','authState','sendNode','logger',source+';return handleMessage;')(
    events,areJidsSameUser,authState,sendNode,logger
  );
  return handleMessage(node).then(()=>({events,receipts,logs}));
}

test('exclude real @bot recipient from own LID and send correctly addressed receipt',async()=>{
  const r=await run({attrs:{from:'123456789@lid',recipient:'867051314767696@bot',id:'random-id'}});
  assert.deepEqual(r.events,[]);
  assert.deepEqual(r.receipts,[{tag:'receipt',attrs:{id:'random-id',to:'123456789@lid',recipient:'867051314767696@bot',type:'sender'}}]);
  assert.deepEqual(r.logs,['privacy: excluded outgoing Meta AI stanza']);
});
test('genuine self-chat is not excluded',async()=>{
  const r=await run({attrs:{from:'123456789@lid',id:'normal-self-msg'}});
  assert.deepEqual(r.events,['decrypted']);
  assert.deepEqual(r.receipts,[]);
});
test('non-bot recipients and third-party source must not be discarded',async()=>{
  for(const attrs of [
    {from:'123456789@lid',recipient:'447700999888@s.whatsapp.net',id:'human'},
    {from:'999999@lid',recipient:'867051314767696@bot',id:'not-us'},
    {from:'447700111222:2@s.whatsapp.net',recipient:'447700111222@s.whatsapp.net',id:'self'},
  ]) {
    const r=await run({attrs});
    assert.deepEqual(r.events,['decrypted']);
  }
});
test('patch refuses changed or duplicate upstream anchors',()=>{
  assert.throws(()=>instrumentMetaAiStanzas('nothing'),/anchor/);
  assert.throws(()=>instrumentMetaAiStanzas(snippet+snippet),/ambiguous/);
});
