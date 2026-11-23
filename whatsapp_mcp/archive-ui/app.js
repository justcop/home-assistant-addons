'use strict';
const $ = id => document.getElementById(id);
const state = { chats: [], chatCursor: null, chatQuery: '', activeChat: null,
  messages: [], messageCursor: null, messageBefore: null, messageGeneration: 0,
  searchCursor: null, searchTerm: '', searchGeneration: 0, view: 'empty' };
const chatById = new Map();
function el(tag, cls, value) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (value !== undefined) node.textContent = String(value);
  return node;
}
function stamp(ts, opts = { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) {
  if (!Number.isFinite(Number(ts)) || Number(ts) <= 0) return '';
  return new Date(Number(ts) * 1000).toLocaleString(undefined, opts);
}
function chatName(chat) {
  if (!chat) return 'Unknown conversation';
  return chat.name || (chat.isGroup ? 'Unnamed group' : chat.id?.split('@')[0] || 'Unnamed chat');
}
function shortDate(ts) {
  return stamp(ts, { month: 'short', day: 'numeric' });
}
async function get(route, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') query.set(key, String(value));
  }
  const res = await fetch(route + (query.size ? '?' + query : ''), { cache: 'no-store' });
  if (!res.ok) throw new Error('Archive request failed (' + res.status + ')');
  return res.json();
}
function view(name) {
  state.view = name;
  $('empty-state').classList.toggle('hidden', name !== 'empty');
  $('messages-view').classList.toggle('hidden', name !== 'messages');
  $('search-view').classList.toggle('hidden', name !== 'search');
}
function setHeading(title, description) {
  $('conversation-name').textContent = title;
  $('conversation-description').textContent = description;
}
async function loadChats(reset = false) {
  const query = $('chat-filter').value.trim();
  if (reset) { state.chatCursor = null; state.chats = []; state.chatQuery = query; }
  const expected = state.chatQuery;
  const list = $('chat-list');
  if (reset) list.replaceChildren(el('p','placeholder','Loading conversations…'));
  try {
    const page = await get('api/chats', { query: expected, limit: 80, cursor: state.chatCursor });
    if (expected !== state.chatQuery) return;
    state.chats.push(...page.items);
    for (const c of page.items) chatById.set(c.id, c);
    state.chatCursor = page.nextCursor;
    renderChats();
  } catch (err) {
    list.replaceChildren(el('p','placeholder',err.message));
  }
}
function renderChats() {
  const list = $('chat-list');
  list.replaceChildren();
  if (!state.chats.length) list.append(el('p','placeholder','No conversations found.'));
  for (const chat of state.chats) {
    const btn = el('button', 'chat-row' + (state.activeChat?.id === chat.id ? ' active' : ''));
    btn.type = 'button';
    btn.setAttribute('role','listitem');
    const avatar = el('span','avatar',chat.isGroup ? '👥' : chatName(chat).slice(0,1).toUpperCase());
    const middle = el('span','chat-meta');
    middle.append(el('span','chat-name',chatName(chat)),el('br'),el('span','chat-detail',chat.isGroup ? 'Group conversation' : 'Direct message'));
    const right = el('span','chat-date');
    right.append(el('span','',shortDate(chat.lastMessageTs)));
    if (chat.unreadCount) right.append(el('br'),el('span','unread',chat.unreadCount));
    btn.append(avatar,middle,right);
    btn.addEventListener('click',()=>openChat(chat));
    list.append(btn);
  }
  if (state.chatCursor) {
    const more = el('button','older','Load more conversations');
    more.addEventListener('click',()=>loadChats(false));
    list.append(more);
  }
}
function mediaLabel(m) {
  // A captioned image/video/document is still an attachment even when the
  // upstream archive has no cached media metadata (m.media === null).
  const kind = String(m.kind || '').toLowerCase();
  const labels = { image: 'Image', video: 'Video', audio: 'Audio',
    document: 'Document', sticker: 'Sticker' };
  return labels[kind] || (m.media ? 'Attachment' : null);
}
function messageBody(m) {
  if (m.deleted) return 'Message deleted';
  const text = m.text || m.transcript || '';
  const attachment = mediaLabel(m);
  if (attachment) return text ? text + '\\n[' + attachment + ']' : '[' + attachment + ']';
  if (text) return text;
  if (m.kind && m.kind !== 'text') return '[' + m.kind + ']';
  return '(No text)';
}
function renderMessages({ preserveTop = false } = {}) {
  const scroller = $('messages-view');
  const previousHeight = scroller.scrollHeight, previousTop = scroller.scrollTop;
  const container = $('messages');
  container.replaceChildren();
  let lastDay = null;
  for (const msg of [...state.messages].sort((a,b)=>Number(a.ts)-Number(b.ts))) {
    const day = stamp(msg.ts, { year: 'numeric', month: 'long', day: 'numeric' });
    if (day && day !== lastDay) { container.append(el('div','date-divider',day)); lastDay = day; }
    const row = el('div','message-row' + (msg.fromMe ? ' mine' : ''));
    row.id = 'message-' + String(msg.id).replace(/[^a-zA-Z0-9_-]/g,'_');
    const bubble = el('div','bubble');
    if (!msg.fromMe && state.activeChat?.isGroup) bubble.append(el('span','sender',msg.sender?.name || msg.sender?.id || 'Unknown sender'));
    bubble.append(el('span','body',messageBody(msg)));
    if (msg.media) {
      const a = el('a','attachment','Download attachment (' + (msg.media.type || msg.kind || 'media') + ')');
      a.href = 'api/media?' + new URLSearchParams({chat:msg.chat,id:msg.id}).toString();
      a.rel = 'noopener noreferrer';
      a.download = 'whatsapp-attachment';
      bubble.append(a);
    }
    const meta = el('div','meta');
    if (msg.edited) meta.append(el('span','', 'Edited'));
    if (msg.reactionCount) meta.append(el('span','','♥ ' + msg.reactionCount));
    meta.append(el('span','',stamp(msg.ts,{hour:'2-digit',minute:'2-digit'})));
    bubble.append(meta);
    row.append(bubble);container.append(row);
  }
  $('older').classList.toggle('hidden', !state.messageCursor);
  $('message-status').textContent = state.messages.length ? state.messages.length + ' messages loaded' : 'No messages in the selected range';
  requestAnimationFrame(()=>{
    if (preserveTop) scroller.scrollTop = scroller.scrollHeight - previousHeight + previousTop;
    else scroller.scrollTop = scroller.scrollHeight;
  });
}
async function openChat(chat, nearTs = null) {
  state.activeChat = chat;
  state.messages = [];
  state.messageCursor = null;
  state.messageBefore = nearTs === null ? null : Number(nearTs)+60;
  const current = ++state.messageGeneration;
  setHeading(chatName(chat),chat.isGroup ? 'Group conversation · read-only history' : 'Private conversation · read-only history');
  view('messages');
  $('shell').classList.add('chat-open');
  $('messages').replaceChildren();
  $('message-status').textContent = 'Loading messages…';
  renderChats();
  await loadMessages(false,current);
}
async function loadMessages(appendOlder = true, generation = state.messageGeneration) {
  if (!state.activeChat) return;
  const chat = state.activeChat;
  const btn = $('older'); btn.disabled = true;
  try {
    const page = await get('api/messages', { chat: chat.id, limit: 100,
      cursor: state.messageCursor, before: state.messageBefore });
    if (generation !== state.messageGeneration) return;
    const known = new Set(state.messages.map(m=>m.id));
    state.messages.push(...page.items.filter(m=>!known.has(m.id)));
    state.messageCursor = page.nextCursor;
    renderMessages({preserveTop: appendOlder});
  } catch (err) {
    if(generation === state.messageGeneration) $('message-status').textContent = err.message;
  } finally { btn.disabled = false; }
}
function showSearch() {
  state.searchGeneration++;
  view('search');
  setHeading('Search messages','Search your indexed WhatsApp archive');
  $('shell').classList.add('chat-open');
  $('message-search').focus();
}
function searchHitNode(hit) {
  const chat = chatById.get(hit.chat) || {id:hit.chat,name:hit.chatName || null,isGroup:hit.chat?.endsWith('@g.us')};
  const btn = el('button','search-hit');
  btn.type = 'button';
  const title = el('div','hit-title');
  title.append(el('span','hit-chat',chatName(chat)),el('span','hit-time',stamp(hit.ts)));
  btn.append(title);
  if (hit.sender?.name) btn.append(el('div','hit-sender',hit.fromMe ? 'You' : hit.sender.name));
  const preview = hit.snippet || hit.text || hit.transcript || '';
  const attachment = mediaLabel(hit);
  btn.append(el('div','hit-body',hit.deleted ? 'Message deleted' :
    attachment ? (preview ? preview + '\\n[' + attachment + ']' : '[' + attachment + ']') :
    (preview || messageBody(hit))));
  btn.addEventListener('click',()=>openChat(chat,hit.ts));
  return btn;
}
async function searchMessages(reset=true) {
  const query = $('message-search').value.trim();
  if (!query) return;
  if(reset) {state.searchTerm=query; state.searchCursor=null; $('search-results').replaceChildren();}
  const gen = ++state.searchGeneration;
  $('search-summary').textContent = 'Searching…';
  $('more-results').classList.add('hidden');
  try {
    const page = await get('api/search',{q:state.searchTerm,limit:60,cursor:state.searchCursor});
    if(gen!==state.searchGeneration) return;
    const list = $('search-results');
    for(const hit of page.items) list.append(searchHitNode(hit));
    state.searchCursor=page.nextCursor;
    $('more-results').classList.toggle('hidden',!page.nextCursor);
    $('search-summary').textContent= list.childElementCount ? list.childElementCount+' results shown'+(page.nextCursor?' · more available':'') : 'No matching messages found';
  } catch(err) {
    if(gen===state.searchGeneration) $('search-summary').textContent=err.message;
  }
}
$('chat-filter').addEventListener('input',()=>{
  clearTimeout(window.__chatSearch);
  window.__chatSearch=setTimeout(()=>loadChats(true),260);
});
$('all-search').addEventListener('click',showSearch);
$('message-search-form').addEventListener('submit',e=>{e.preventDefault();searchMessages(true);});
$('more-results').addEventListener('click',()=>searchMessages(false));
$('older').addEventListener('click',()=>loadMessages(true));
$('show-chats').addEventListener('click',()=>$('shell').classList.remove('chat-open'));
get('api/storage').then(data=>$('storage-status').textContent='Storage: '+data.location).catch(()=>$('storage-status').textContent='Storage status unavailable');
loadChats(true);

