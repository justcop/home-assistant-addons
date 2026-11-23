import test from "node:test";
import assert from "node:assert/strict";
import { instrumentBaileysClearChat } from "../patch-baileys-clear-chat.mjs";

// Exercises the exact runtime JavaScript insertion point. The full Docker
// build separately verifies that the pinned deployed Baileys package matches.
const source = `
function process(action, type, id, syncAction, isInitialSync, ev) {
  if (false) {}
  else if (action?.deleteChatAction || type === 'deleteChat') {
    ev.emit('chats.delete', [id]);
  }
}
`;
function instrumentedAction(action,index,initial=false) {
  const patched=instrumentBaileysClearChat(source);
  const fn=Function(patched+'; return process;')();
  const events=[];
  fn(action,index[0],index[1],{index},initial,{emit:(name,items)=>events.push({name,items})});
  return events;
}
test("Baileys emits metadata-only clear-chat signal with explicit cutoff and delete-starred flag",()=>{
  const events=instrumentedAction({clearChatAction:{messageRange:{lastMessageTimestamp:1795431234}}},["clearChat","self@s.whatsapp.net","1","0"]);
  assert.deepEqual(events,[{name:"chats.update",items:[{id:"self@s.whatsapp.net",__archiveClearChat:{
    lastMessageTimestamp:"1795431234",preserveStarred:false,initial:false
  }}]}]);
});
test("keep-starred flag, initial sync and missing cutoff are kept distinct",()=>{
  const [keep] = instrumentedAction({clearChatAction:{messageRange:{lastMessageTimestamp:345}}},["clearChat","self@s.whatsapp.net","0","0"]);
  assert.equal(keep.items[0].__archiveClearChat.preserveStarred,true);
  const [initial] = instrumentedAction({clearChatAction:{messageRange:{lastMessageTimestamp:345}}},["clearChat","self@s.whatsapp.net","1","0"],true);
  assert.equal(initial.items[0].__archiveClearChat.initial,true);
  const [none] = instrumentedAction({clearChatAction:{}},["clearChat","self@s.whatsapp.net","1","0"]);
  assert.equal(none.items[0].__archiveClearChat.lastMessageTimestamp,null);
});
test("do not conflate delete chat and clear chat",()=>{
  assert.deepEqual(instrumentedAction({deleteChatAction:{}},["deleteChat","self@s.whatsapp.net","1"]),[
    {name:"chats.delete",items:["self@s.whatsapp.net"]}
  ]);
  assert.throws(()=>instrumentBaileysClearChat("const a = 3"),/anchor/);
  assert.throws(()=>instrumentBaileysClearChat(source+source),/ambiguous/);
});
