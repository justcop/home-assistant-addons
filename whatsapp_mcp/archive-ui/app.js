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
  $('backfill-view').classList.toggle('hidden', name !== 'backfill');
  $('read-only-pill').classList.toggle('hidden', name === 'backup');
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
function renderBackupAlert(status) {
  const failed = !!status?.lastError;
  const retention = !!status?.retentionPending;
  const banner = $('backup-alert');
  banner.classList.toggle('hidden', !failed && !retention);
  $('backup-alert-text').textContent = retention
    ? 'WhatsApp cloud backup: previous cloud copy cleanup has not completed. ' +
      (status.lastError || 'More than one cloud copy may remain.')
    : failed ? 'WhatsApp cloud backup failed: ' + status.lastError : '';
}
async function refreshBackupAlert() {
  try { renderBackupAlert(await get('api/backup/status')); }
  catch { /* transient UI connectivity is not itself a cloud backup failure */ }
}
$('backup-alert-open').addEventListener('click', showBackup);
setInterval(() => { void refreshBackupAlert(); }, 60000);
void refreshBackupAlert();
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
  const p = $('backup-feedback');
  p.textContent = message;
  p.classList.toggle('error', error);
  p.classList.toggle('visible', !!message);
}
async function refreshCloudBackup() {
  if (state.view !== 'backup') return;
  try {
    cloudStatus = await get('api/backup/status');
    const s = cloudStatus;
    renderBackupAlert(s);
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
    // The OAuth client ID is an identifier (not a password), so keep it
    // visible. Never return or populate the saved secret.
    const clientIdField = $('backup-client-id');
    if (s.clientId && !clientIdField.value.trim() && document.activeElement !== clientIdField)
      clientIdField.value = s.clientId;
    const secretField = $('backup-client-secret');
    secretField.required = !s.clientSecretStored;
    secretField.placeholder = s.clientSecretStored
      ? 'Saved securely — leave blank to keep current secret'
      : 'Enter Google client secret';
    $('backup-client-status').textContent = s.clientConfigured
      ? '✓ Google client ID and secret saved. The secret box stays empty for security.'
      : 'No Google client has been saved yet.';
    const connectError = s.lastAuthError || '';
    const connectNotice = $('backup-connect-feedback');
    if (connectError && !connectNotice.textContent)
      connectNotice.textContent = connectError;
    connectNotice.classList.toggle('error', !!connectError);
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
  cloudFeedback(action === 'authorize' ? 'Contacting Google for a device authorisation code…' : 'Saving…');
  if (action === 'authorize' || action === 'configure') {
    $('backup-connect-feedback').textContent = '';
    $('backup-connect-feedback').classList.remove('error');
  }
  try {
    const result = await cloudPost(action, payload);
    cloudFeedback(message);
    if (action === 'authorize') {
      $('backup-connect-feedback').textContent = 'Google authorisation started. Enter the code displayed below.';
      $('backup-connect-feedback').classList.remove('error');
    }
    await refreshCloudBackup();
    return result;
  } catch (error) {
    cloudFeedback(error.message, true);
    if (action === 'authorize' || action === 'configure') {
      $('backup-connect-feedback').textContent = error.message;
      $('backup-connect-feedback').classList.add('error');
    }
    return null;
  }
}
$('show-backup').addEventListener('click', showBackup);
$('backup-client-form').addEventListener('submit', async event => {
  event.preventDefault();
  const saved = await cloudAction('configure', {
    clientId: $('backup-client-id').value.trim(),
    clientSecret: $('backup-client-secret').value.trim()
  }, 'Google OAuth client saved. Select Connect Google Drive to authorise it.');
  // A failed save leaves the typed secret intact so it can be corrected.
  // After a successful save, clear only the password input, not the ID.
  if (saved) $('backup-client-secret').value = '';
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

async function showBackfill() {
  setHeading('History recovery', 'Resumable background retrieval from WhatsApp');
  view('backfill');
  $('shell').classList.add('chat-open');
  await updateBackfill();
}
async function updateBackfill() {
  if (state.view !== 'backfill') return;
  try {
    const result = await get('api/archive/backfill', { view: $('backfill-filter').value });
    if (result.state === 'not_started') {
      $('backfill-summary').textContent = 'No history recovery has started yet.';
      $('backfill-chats').replaceChildren();
      return;
    }
    const phases = result.phases || {};
    const summary = (result.state === 'running' ? 'Running' : 'Paused') +
      ' · ' + result.requests + ' requests (' + result.requestsToday +
      '/' + result.dailyLimit + ' today) · ' + result.conversations + ' conversations' +
      ' · ' + (phases.stalled || 0) + ' stalled' +
      ' · ' + (phases.no_anchor || 0) + ' without a usable starting message';
    $('backfill-summary').textContent = summary;
    const list = $('backfill-chats');
    list.replaceChildren();
    for (const chat of result.recent || []) {
      // Red means no progress is possible without further investigation.
      // Yellow means in progress or awaiting a retry. Green is provisional:
      // WhatsApp hasn't certified that the conversation is fully recovered.
      const severity = chat.phase === 'likely_complete' ? 'green' :
        (chat.phase === 'stalled' || chat.phase === 'no_anchor') ? 'red' : 'yellow';
      const item = el('div','backfill-item backfill-' + severity);
      const heading = el('strong','',chat.name);
      const phaseLabel = chat.phase === 'likely_complete' ? 'Probably complete (empty follow-up)' :
        chat.phase === 'retry' ? 'Awaiting another attempt' :
        chat.phase === 'no_anchor' ? 'No usable starting message' :
        chat.phase === 'stalled' ? 'Stalled — needs attention' :
        chat.phase === 'recheck_requested' ? 'Recheck requested' :
        chat.phase === 'requesting' ? 'Fetching older messages' : 'Queued';
      const status = el('span','backfill-status',phaseLabel);
      const total = chat.archivedMessages == null ? 'Count unavailable' :
        Number(chat.archivedMessages).toLocaleString() + ' messages archived';
      const detail = el('p','',total + ' · ' + chat.requests + ' history requests' +
        ' · approx. ' + chat.importedEstimate + ' messages received during backfill' +
        (chat.oldestTimestamp ? ' · oldest ' +
          stamp(chat.oldestTimestamp, {day:'numeric',month:'short',year:'numeric'}) : ''));
      item.append(heading,status,detail);
      if (chat.phase === 'likely_complete' && !chat.manualRecheck) {
        const button = el('button','backfill-recheck','Not complete — keep searching');
        button.type = 'button';
        button.addEventListener('click',async () => {
          button.disabled = true;
          button.textContent = 'Requesting recheck…';
          try {
            const response = await fetch('api/archive/backfill/recheck',{
              method:'POST',cache:'no-store',
              headers:{'Content-Type':'application/json','X-WhatsApp-Backfill-Action':'1'},
              body:JSON.stringify({action:'recheck',chatId:chat.id})
            });
            if (!response.ok) throw new Error('Unable to request further history (' + response.status + ')');
            button.textContent = 'Recheck queued';
            await updateBackfill();
          } catch(error) {
            button.textContent = 'Retry recheck';
            button.disabled = false;
            $('backfill-summary').textContent = error.message;
          }
        });
        item.append(button);
      }
      list.append(item);
    }
    if (!list.childElementCount) list.append(el('p','placeholder','No conversations processed yet.'));
  } catch(err) {
    $('backfill-summary').textContent = err.message;
  }
}
$('show-backfill').addEventListener('click',showBackfill);
$('backfill-filter').addEventListener('change',updateBackfill);
setInterval(updateBackfill, 15_000);

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
// Private Home Assistant ingress-only pairing. Never expose codes through the
// public Cloudflare gateway or persist them in browser storage.
let pairingStatus = null;
async function pairingAction(route, payload) {
  const response = await fetch('api/pairing/' + route, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-WhatsApp-Pairing-Action': '1' },
    body: JSON.stringify(payload)
  });
  let data = {};
  try { data = await response.json(); } catch { /* fail with a generic status */ }
  if (!response.ok) throw new Error(data.error || 'Pairing action failed (' + response.status + ')');
  return data;
}
async function refreshPairing() {
  try {
    const status = await get('api/pairing/status');
    pairingStatus = status;
    $('connection-status').textContent = 'WhatsApp: ' + (
      status.connected ? 'Connected' : status.connection === 'pairing' ? 'Awaiting pairing'
        : status.connection === 'logged_out' ? 'Needs re-pairing'
        : status.connection === 'disconnected' ? 'Reconnecting…' : status.connection);
    $('settings-pairing-status').textContent = status.connected
      ? 'Connected — your message archive is synchronising.'
      : status.needsPairing ? 'This add-on needs to be linked to WhatsApp.'
        : 'WhatsApp connection: ' + status.connection;
    // When connected, the prominent pairing panel disappears automatically,
    // while a deliberate re-pair button remains available in Settings.
    $('pairing-banner').classList.toggle('hidden', !status.needsPairing);
    $('pairing-code-panel').classList.toggle('hidden', !status.needsPairing || !status.code);
    $('pairing-phone-form').classList.toggle('hidden', !status.needsPairing || !!status.code);
    if (status.code) {
      $('pairing-code').textContent = status.code;
      $('pairing-description').textContent = 'Enter the pairing code below on your phone. This page will hide the code once linked.';
    } else {
      $('pairing-code').textContent = '';
      $('pairing-description').textContent = status.phoneConfigured
        ? 'Your phone number is saved. A new code should appear shortly. If not, request one again below.'
        : 'Enter your number below to request a fresh WhatsApp pairing code.';
    }
  } catch {
    $('connection-status').textContent = 'WhatsApp connection status unavailable';
    $('settings-pairing-status').textContent = 'Could not read WhatsApp connection status.';
    // Do not display a stale pairing code after connectivity is lost.
    $('pairing-code').textContent = '';
    $('pairing-code-panel').classList.add('hidden');
  }
}
$('pairing-phone-form').addEventListener('submit', async event => {
  event.preventDefault();
  const feedback = $('pairing-feedback');
  feedback.textContent = 'Requesting pairing code…';
  try {
    await pairingAction('phone', { phone: $('pairing-phone').value.trim() });
    $('pairing-phone').value = '';
    feedback.textContent = 'Phone number saved. The pairing code should appear here shortly.';
    await refreshPairing();
  } catch (err) { feedback.textContent = err.message; }
});
$('pairing-copy').addEventListener('click', () => {
  const code = $('pairing-code').textContent.trim();
  if (code) navigator.clipboard?.writeText(code).catch(() => {
    $('pairing-feedback').textContent = 'Copy unavailable. Enter the code manually.';
  });
});
$('pairing-repair').addEventListener('click', async () => {
  const confirmation = window.prompt(
    'This resets the WhatsApp linked credentials for THIS add-on, but keeps the existing archive. ' +
    'Your other linked devices are not changed. Type REPAIR THIS DEVICE to continue:');
  if (confirmation !== 'REPAIR THIS DEVICE') return;
  $('settings-pairing-feedback').textContent = 'Re-pair requested…';
  try {
    await pairingAction('repair', { confirm: confirmation });
    $('settings-pairing-feedback').textContent =
      'Credentials will reset shortly. Return to the main page to enter your number and new pairing code.';
    await refreshPairing();
  } catch (err) { $('settings-pairing-feedback').textContent = err.message; }
});
setInterval(refreshPairing, 5000);
void refreshPairing();

get('api/storage').then(data=>$('storage-status').textContent='Storage: '+data.location).catch(()=>$('storage-status').textContent='Storage status unavailable');
loadChats(true);

