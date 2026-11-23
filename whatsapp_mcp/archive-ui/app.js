'use strict';
const $ = id => document.getElementById(id);
const state = { chats: [], chatCursor: null, chatQuery: '', chatType: 'direct', chatGeneration: 0, activeChat: null,
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
  $('backup-view').classList.toggle('hidden', name !== 'backup');
  document.querySelectorAll('.read-only-pill').forEach(p => p.classList.toggle('hidden', name === 'backup'));
}
function setHeading(title, description) {
  $('conversation-name').textContent = title;
  $('conversation-description').textContent = description;
}
function setChatType(type) {
  if (type !== 'direct' && type !== 'groups') return;
  state.chatType = type;
  for (const choice of ['direct', 'groups']) {
    const selected = choice === type;
    $('tab-' + choice).classList.toggle('active', selected);
    $('tab-' + choice).setAttribute('aria-selected', String(selected));
  }
  loadChats(true);
}
async function loadChats(reset = false) {
  const query = $('chat-filter').value.trim();
  // Browsing is strictly per tab; searching names can span both types.
  const groupFilter = query ? null : state.chatType === 'groups';
  if (reset) {
    state.chatCursor = null;
    state.chats = [];
    state.chatQuery = query;
    state.chatGeneration++;
  }
  const generation = state.chatGeneration;
  const expected = state.chatQuery;
  const cursor = state.chatCursor;
  const list = $('chat-list');
  if (reset) list.replaceChildren(el('p','placeholder','Loading conversations…'));
  try {
    const page = await get('api/chats', {
      query: expected, isGroup: groupFilter, limit: 80, cursor
    });
    if (generation !== state.chatGeneration || expected !== state.chatQuery) return;
    const known = new Set(state.chats.map(c => c.id));
    state.chats.push(...page.items.filter(c => !known.has(c.id)));
    for (const c of page.items) chatById.set(c.id, c);
    state.chatCursor = page.nextCursor;
    renderChats();
  } catch (err) {
    if (generation === state.chatGeneration)
      list.replaceChildren(el('p','placeholder',err.message));
  }
}
function renderChats() {
  const list = $('chat-list');
  list.replaceChildren();
  if (!state.chats.length) list.append(el('p','placeholder', 'No ' + (state.chatQuery ? 'matching' : state.chatType === 'groups' ? 'group' : 'direct') + ' conversations found.'));
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
  if (attachment) return text ? text + '\n[' + attachment + ']' : '[' + attachment + ']';
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
  // A global search result may open a chat from the other tab.
  // Make the sidebar show the selected conversation's own category.
  const type = chat.isGroup ? 'groups' : 'direct';
  if (type !== state.chatType || $('chat-filter').value.trim()) {
    $('chat-filter').value = '';
    setChatType(type);
  }
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
    attachment ? (preview ? preview + '\n[' + attachment + ']' : '[' + attachment + ']') :
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

let cloudStatus = null, recoveryKeyOnScreen = null, cloudAuthPolling = false;
async function cloudPost(action, payload = {}) {
  const res = await fetch('api/backup/' + action, {
    method: 'POST', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-WhatsApp-Backup-Action': '1' },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Cloud backup action failed (' + res.status + ')');
  return data;
}
function cloudFeedback(message, error = false) {
  $('backup-feedback').textContent = message;
  $('backup-feedback').classList.toggle('error', error);
}
async function refreshCloudBackup() {
  if (state.view !== 'backup') return;
  try {
    cloudStatus = await get('api/backup/status');
    const s = cloudStatus;
    const pieces = [
      s.connected ? 'Google Drive connected' : s.clientConfigured ? 'Google Drive needs authorisation' : 'Google OAuth client not configured',
      s.recoveryConfirmed ? 'Recovery key saved' : 'Recovery key not yet confirmed',
      s.enabled ? 'Daily backup enabled' : 'Daily backup disabled'
    ];
    if (s.lastSuccess) pieces.push('Last successful backup: ' + new Date(s.lastSuccess).toLocaleString());
    if (s.lastFileName) pieces.push('Cloud file: ' + s.lastFileName);
    if (s.running) pieces.push('Backup is running…');
    if (s.retentionPending) pieces.push('Cloud cleanup pending — more than one copy may remain temporarily');
    if (s.lastError) pieces.push('ERROR: ' + s.lastError);
    $('backup-state').textContent = pieces.join('\n');
    $('backup-state').classList.toggle('error', !!s.lastError);
    $('backup-time').value = s.time || '05:30';
    $('backup-authorize').disabled = !s.clientConfigured;
    const ready = s.connected && s.recoveryConfirmed && !s.running;
    $('backup-enable').disabled = !ready && !s.enabled;
    $('backup-enable').textContent = s.enabled ? 'Disable daily backup' : 'Enable daily backup';
    $('backup-run').disabled = !ready;
    $('backup-download').classList.toggle('hidden', !s.lastFileName);
    const auth = s.pendingAuthorisation;
    $('backup-code-panel').classList.toggle('hidden', !auth);
    if (auth) {
      $('backup-user-code').textContent = auth.userCode || '';
      $('backup-verify-link').href = /^https:\/\/(www\.)?google\.com\//.test(auth.verificationUrl || '')
        ? auth.verificationUrl : 'https://www.google.com/device';
      $('backup-poll-state').textContent = 'Waiting for Google authorisation…';
    }
  } catch (error) { cloudFeedback(error.message, true); }
}
function showBackup() {
  view('backup');
  setHeading('Cloud backup & recovery', 'Encrypted · separate from Home Assistant backups');
  $('shell').classList.add('chat-open');
  cloudFeedback('');
  void refreshCloudBackup();
}
async function cloudAction(action, payload = {}, message = 'Updated.') {
  cloudFeedback('Working…');
  try {
    const result = await cloudPost(action, payload);
    cloudFeedback(message);
    await refreshCloudBackup();
    return result;
  } catch (error) {
    cloudFeedback(error.message, true);
    return null;
  }
}
$('show-backup').addEventListener('click', showBackup);
$('backup-client-form').addEventListener('submit', async event => {
  event.preventDefault();
  await cloudAction('configure', {
    clientId: $('backup-client-id').value.trim(),
    clientSecret: $('backup-client-secret').value.trim()
  }, 'Google client saved locally. Now select Connect Google Drive.');
  $('backup-client-secret').value = '';
});
$('backup-authorize').addEventListener('click', () =>
  cloudAction('authorize', {}, 'Open the Google link and enter the code below.'));
$('backup-generate-key').addEventListener('click', async () => {
  const result = await cloudAction('recovery-key', {}, 'Copy this key into a password manager outside Home Assistant.');
  if (result?.key) {
    recoveryKeyOnScreen = result.key;
    $('backup-key-value').textContent = result.key;
    $('backup-key-panel').classList.remove('hidden');
    $('backup-key-saved').checked = false;
    $('backup-confirm-key').disabled = true;
  }
});
$('backup-copy-key').addEventListener('click', () => {
  if (!recoveryKeyOnScreen) return;
  navigator.clipboard.writeText(recoveryKeyOnScreen)
    .then(() => cloudFeedback('Recovery key copied. Save it outside Home Assistant.'))
    .catch(() => cloudFeedback('Copy failed; select and copy the key manually.', true));
});
$('backup-key-saved').addEventListener('change', () => {
  $('backup-confirm-key').disabled = !$('backup-key-saved').checked;
});
$('backup-confirm-key').addEventListener('click', async () => {
  if (!$('backup-key-saved').checked || !recoveryKeyOnScreen) return;
  const result = await cloudAction('confirm-key', {}, 'Recovery key confirmed.');
  if (result) {
    $('backup-key-panel').classList.add('hidden');
    $('backup-key-value').textContent = '';
    recoveryKeyOnScreen = null;
  }
});
$('backup-save-time').addEventListener('click', () =>
  cloudAction('time', {time: $('backup-time').value}, 'Daily UK time saved.'));
$('backup-enable').addEventListener('click', () =>
  cloudAction('enable', {enabled: !cloudStatus?.enabled},
    cloudStatus?.enabled ? 'Daily backups disabled.' : 'Daily backups enabled.'));
$('backup-run').addEventListener('click', () =>
  cloudAction('run', {}, 'Backup started. Status will update automatically.'));
setInterval(async () => {
  if (state.view !== 'backup') return;
  if (cloudStatus?.pendingAuthorisation && !cloudAuthPolling) {
    cloudAuthPolling = true;
    try {
      const data = await cloudPost('poll');
      if (data.state === 'connected') cloudFeedback('Google Drive connected.');
    } catch (err) {
      cloudFeedback(err.message, true);
    } finally { cloudAuthPolling = false; }
  }
  await refreshCloudBackup();
}, 7000);

$('tab-direct').addEventListener('click', () => setChatType('direct'));
$('tab-groups').addEventListener('click', () => setChatType('groups'));
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

