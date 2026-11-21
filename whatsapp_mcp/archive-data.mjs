import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

// These are WhatsApp protocol/control events, not human conversation activity.
// Preserve the raw rows for audit and to avoid changing the upstream MCP API.
export const VISIBLE_MESSAGE = `m.kind != 'system' AND (
  m.kind != 'other'
  OR NULLIF(TRIM(COALESCE(m.text, '')), '') IS NOT NULL
  OR NULLIF(TRIM(COALESCE(m.transcript, '')), '') IS NOT NULL
  OR m.media_type IS NOT NULL OR m.media_sha IS NOT NULL
)`;
const MAX_PAGE = 200;
// Only this confirmed WhatsApp account, not a chat named "Meta AI" by a user.
export const META_AI_JID = '13135550002@s.whatsapp.net';
const normalize = value => String(value || '').trim();
function integer(value, fallback, min, max) {
  if (value === null || value === undefined || value === '') return fallback;
  if (!/^[0-9]+$/.test(String(value))) throw new RangeError('Invalid pagination value');
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < min || number > max)
    throw new RangeError('Invalid pagination value');
  return number;
}
function numberFromJid(jid) {
  if (typeof jid !== 'string' || !jid.endsWith('@s.whatsapp.net')) return null;
  return jid.split('@')[0].split(':')[0];
}
export function normalisePhone(value) {
  let digits = String(value ?? '').replace(/[^0-9]/g, '');
  if (digits.startsWith('00')) digits = digits.slice(2);
  // Some exported UK address books include an optional domestic trunk zero.
  if (/^4407\d{9}$/.test(digits)) digits = '44' + digits.slice(3);
  if (/^07\d{9}$/.test(digits)) digits = '44' + digits.slice(1);
  return /^[1-9][0-9]{6,14}$/.test(digits) ? digits : null;
}
export function isHumanName(value) {
  const text = normalize(value);
  return !!text && !/^\+?[0-9 ()-]{7,}$/.test(text) && !/^\d+@(?:lid|s\.whatsapp\.net)$/.test(text);
}
function page(items, offset, limit) {
  return { items: items.slice(offset,offset+limit), nextCursor:offset+limit<items.length?String(offset+limit):null };
}

export class ArchiveData {
  constructor(storagePath, options = {}) {
    const dbPath = options.dbPath || path.join(storagePath, 'whatsapp.db');
    this.db = new DatabaseSync(dbPath, { readOnly: true });
    this.db.exec('PRAGMA query_only = ON; PRAGMA busy_timeout = 2000;');
    this.aliasFile = options.aliasFile || path.join(storagePath, '.archive-contact-names.json');
    this.aliases = this.loadAliases();
    this.refreshContacts();
    this.chatsStmt = this.db.prepare(`
      SELECT c.id, c.name, c.is_group AS isGroup, c.unread_count AS unreadCount,
             c.archived, c.muted_until AS mutedUntil, c.participant_count AS participantCount,
             (SELECT m.ts FROM messages m WHERE m.chat_id = c.id AND ${VISIBLE_MESSAGE}
              ORDER BY m.ts DESC, m.rowid DESC LIMIT 1) AS lastMessageTs
      FROM chats c
    `);
    this.chatNameStmt = this.db.prepare('SELECT name, is_group AS isGroup FROM chats WHERE id = ?');
    this.messagesStmt = this.db.prepare(`
      SELECT m.id, m.chat_id AS chat, m.sender_id AS senderId, m.ts,
             m.from_me AS fromMe, m.kind, m.text, m.transcript,
             m.quoted_id AS quotedId, m.status, m.edited_ts AS editedTs,
             m.deleted_ts AS deletedTs, m.media_type AS mediaType,
             m.media_sha AS mediaSha,
             (SELECT count(*) FROM reactions r WHERE r.chat_id = m.chat_id AND r.message_id = m.id) AS reactionCount
      FROM messages m
      WHERE m.chat_id = :chat AND ${VISIBLE_MESSAGE}
        AND (:before IS NULL OR m.ts <= :before)
        AND (:after IS NULL OR m.ts >= :after)
      ORDER BY m.ts DESC, m.rowid DESC LIMIT :limit OFFSET :offset
    `);
  }
  close() { this.db.close(); }
  refreshContacts() {
    const all=this.db.prepare('SELECT id, phone_number, lid, name, notify FROM contacts').all();
    this.contacts=new Map(all.map(x=>[x.id,x]));
    this.lidContacts=new Map();
    for(const c of all) if(c.lid) this.lidContacts.set(String(c.lid),c);
  }
  loadAliases(file = this.aliasFile) {
    try {
      const data=JSON.parse(fs.readFileSync(file,'utf8'));
      if (!data || typeof data!=='object' || Array.isArray(data)) return {};
      return Object.fromEntries(Object.entries(data).filter(([phone,name])=>
        normalisePhone(phone)===phone && typeof name==='string' && name.length<=160 && isHumanName(name)));
    } catch (e) {
      if (e.code==='ENOENT') return {};
      throw e;
    }
  }
  contactFor(id) {
    return this.contacts.get(id) || (id?.endsWith('@lid')?this.lidContacts.get(id.split('@')[0]):null);
  }
  displayName(id, currentName = null, isGroup = false) {
    if(isGroup) return isHumanName(currentName)?currentName:'Unnamed group';
    const contact=this.contactFor(id);
    const phone=normalisePhone(numberFromJid(id)||contact?.phone_number||numberFromJid(contact?.id));
    if(phone && this.aliases[phone]) return this.aliases[phone];
    for(const n of [contact?.name, currentName, contact?.notify])
      if (isHumanName(n)) return n.trim();
    if(phone) return '+'+phone;
    if(isHumanName(currentName)) return currentName;
    return 'Unknown contact';
  }
  listChats(searchParams) {
    this.refreshContacts();
    const limit=integer(searchParams.get('limit'),80,1,MAX_PAGE);
    const offset=integer(searchParams.get('cursor'),0,0,1000000);
    const q=normalize(searchParams.get('query')).toLocaleLowerCase();
    const group=searchParams.get('isGroup'), archived=searchParams.get('archived'), unread=searchParams.get('unread');
    const rows=this.chatsStmt.all().map(c=>({
      id:c.id, name:this.displayName(c.id,c.name,!!c.isGroup), isGroup:!!c.isGroup,
      lastMessageTs:c.lastMessageTs,unreadCount:c.unreadCount,archived:!!c.archived,
      mutedUntil:c.mutedUntil,participantCount:c.participantCount
    })).filter(c=>{
      if(c.id===META_AI_JID)return false;
      if(q && !(c.name.toLocaleLowerCase().includes(q)||c.id.toLocaleLowerCase().includes(q)))return false;
      if(group!==null && group!==String(c.isGroup))return false;
      if(archived!==null && archived!==String(c.archived))return false;
      if(unread==='true' && c.unreadCount<=0)return false;
      if(unread==='false' && c.unreadCount>0)return false;
      return true;
    }).sort((a,b)=>(b.lastMessageTs??-1)-(a.lastMessageTs??-1)||a.id.localeCompare(b.id));
    return page(rows,offset,limit);
  }
  listMessages(searchParams) {
    const chat=searchParams.get('chat');
    if(!chat || chat.length>300)throw new RangeError('Invalid chat');
    if(chat===META_AI_JID)return {items:[],nextCursor:null};
    this.refreshContacts();
    const limit=integer(searchParams.get('limit'),100,1,MAX_PAGE);
    const offset=integer(searchParams.get('cursor'),0,0,1000000);
    const before=integer(searchParams.get('before'),null,0,9999999999);
    const after=integer(searchParams.get('after'),null,0,9999999999);
    const rows=this.messagesStmt.all({chat,before,after,limit:limit+1,offset});
    return {
      items:rows.slice(0,limit).map(m=>({
        id:m.id,chat:m.chat,ts:m.ts,fromMe:!!m.fromMe,
        sender:{id:m.senderId,name:m.fromMe?'You':this.displayName(m.senderId)},
        kind:m.kind,text:m.text,transcript:m.transcript,quotedId:m.quotedId,
        status:m.status,edited:m.editedTs!==null,deleted:m.deletedTs!==null,
        media:m.mediaType===null&&m.mediaSha===null?null:{type:m.mediaType,cached:m.mediaSha!==null},
        reactionCount:m.reactionCount
      })),
      nextCursor:rows.length>limit?String(offset+limit):null
    };
  }
  renameHit(hit) {
    if(!hit || typeof hit!=='object' || hit.chat===META_AI_JID)return null;
    const chat=this.chatNameStmt.get(hit.chat);
    return {...hit,
      chatName:this.displayName(hit.chat,chat?.name,!!chat?.isGroup),
      sender:hit.sender?{...hit.sender,name:hit.fromMe?'You':this.displayName(hit.sender.id,hit.sender.name)}:hit.sender};
  }
  archiveDateRange() {
    // Count both raw WhatsApp events and actual user-visible messages.
    // No message text, contact names or identifiers are returned.
    const raw = this.db.prepare(
      'SELECT COUNT(*) AS count, MIN(ts) AS oldest, MAX(ts) AS newest FROM messages'
    ).get();
    const meaningful = this.db.prepare(`
      SELECT COUNT(*) AS count, MIN(m.ts) AS oldest, MAX(m.ts) AS newest
      FROM messages m WHERE ${VISIBLE_MESSAGE}
    `).get();
    const chats = this.db.prepare(
      'SELECT COUNT(*) AS count FROM chats'
    ).get();
    return {
      raw: { count:raw.count, oldest:raw.oldest, newest:raw.newest },
      messages: { count:meaningful.count, oldest:meaningful.oldest, newest:meaningful.newest },
      chatCount: chats.count,
      kindCounts: Object.fromEntries(this.db.prepare('SELECT kind, COUNT(*) AS n FROM messages GROUP BY kind').all().map(r=>[r.kind,r.n])),
      emptyOtherCount: this.db.prepare("SELECT COUNT(*) AS n FROM messages WHERE kind='other' AND NULLIF(TRIM(COALESCE(text,'')),'') IS NULL AND NULLIF(TRIM(COALESCE(transcript,'')),'') IS NULL AND media_type IS NULL AND media_sha IS NULL").get().n
    };
  }
  mergeAliases(entries) {
    if(!Array.isArray(entries)||entries.length>20000)throw new RangeError('Invalid contact import');
    const next={...this.aliases};
    let applied=0;
    for (const entry of entries) {
      if(!entry || typeof entry!=='object')continue;
      const phone=normalisePhone(entry.phone);
      const name=normalize(entry.name);
      if(!phone || !isHumanName(name) || name.length>160)continue;
      if(next[phone]!==name){next[phone]=name;applied++;}
    }
    const temp=this.aliasFile+'.tmp-'+process.pid;
    try {
      fs.writeFileSync(temp,JSON.stringify(next),{mode:0o600,flag:'wx'});
      fs.renameSync(temp,this.aliasFile);
      this.aliases=next;
    }catch(e){try{fs.unlinkSync(temp)}catch{}throw e;}
    return {imported:applied,total:Object.keys(next).length};
  }
}
