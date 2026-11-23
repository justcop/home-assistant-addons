import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { DatabaseSync } from 'node:sqlite';
import { validateStoragePath, verifyExternalArchive } from '../storage.mjs';

test('private STORAGE path remains confined to a named external volume', () => {
  assert.deepEqual(validateStoragePath('/media/storage/.whatsapp-mcp-private'), {
    directory: '/media/storage/.whatsapp-mcp-private',
    disk: '/media/storage', root: '/media'
  });
  for (const bad of ['/data/whatsapp', '/media/storage/public', '/media/storage/../other',
    '/media/storage', '/media/storage/.whatsapp-mcp-private/../../data']) {
    assert.throws(() => validateStoragePath(bad));
  }
});

test('external database must be present, readable and structurally intact before deleting a copy', t => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-storage-privacy-'));
  t.after(() => fs.rmSync(tmp, { recursive: true, force: true }));
  assert.throws(() => verifyExternalArchive(tmp, true), /missing/);
  assert.equal(verifyExternalArchive(tmp, false), false);
  const file = path.join(tmp, 'whatsapp.db');
  const db = new DatabaseSync(file);
  db.exec('CREATE TABLE messages (id TEXT PRIMARY KEY); CREATE TABLE chats (id TEXT PRIMARY KEY);');
  db.prepare('INSERT INTO messages(id) VALUES(?)').run('surviving_message');
  db.close();
  assert.equal(verifyExternalArchive(tmp, true), true);
  fs.writeFileSync(file, 'not a SQLite file');
  assert.throws(() => verifyExternalArchive(tmp, true), /database|SQLite|malformed/i);
});

class FakeElement {
  constructor(tag = 'div') {
    this.tag = tag;
    this.value = '';
    this.textContent = '';
    this.children = [];
    this.attributes = {};
    this.handlers = new Map();
    const values = new Set();
    this.classList = {
      toggle: (key, on) => { if (on) values.add(key); else values.delete(key); },
      add: key => values.add(key),
      remove: key => values.delete(key),
      contains: key => values.has(key)
    };
  }
  addEventListener(name, cb) { this.handlers.set(name, cb); }
  setAttribute(name, value) { this.attributes[name] = value; }
  replaceChildren(...children) { this.children = [...children]; }
  append(...children) { this.children.push(...children); }
  click() { this.handlers.get('click')?.(); }
}
test('Direct and Groups tabs filter browsing, but searching remains global', async () => {
  const elements = new Map();
  const getEl = id => {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  };
  const requests = [];
  const ctx = vm.createContext({
    document: {
      getElementById: getEl,
      createElement: tag => new FakeElement(tag)
    },
    window: {},
    URLSearchParams,
    Date,
    Map,
    Set,
    Number,
    String,
    console,
    setTimeout,
    clearTimeout,
    fetch: async url => {
      requests.push(String(url));
      return { ok: true, json: async () => url.startsWith('api/storage')
        ? { location: 'STORAGE drive' }
        : { items: [], nextCursor: null } };
    },
    requestAnimationFrame: fn => fn()
  });
  const source = fs.readFileSync(new URL('../archive-ui/app.js', import.meta.url), 'utf8');
  vm.runInContext(source, ctx);
  const flush = () => new Promise(resolve => setImmediate(resolve));
  await flush();
  const chats = () => requests.filter(r => r.startsWith('api/chats'));
  assert.match(chats().at(-1), /isGroup=false/);
  assert.equal(getEl('tab-direct').attributes['aria-selected'], undefined);
  getEl('tab-groups').click();
  await flush();
  assert.match(chats().at(-1), /isGroup=true/);
  assert.equal(getEl('tab-groups').attributes['aria-selected'], 'true');
  assert.equal(getEl('tab-direct').attributes['aria-selected'], 'false');
  getEl('chat-filter').value = 'Gina';
  await vm.runInContext('loadChats(true)',ctx);
  assert.match(chats().at(-1), /query=Gina/);
  assert.doesNotMatch(chats().at(-1), /isGroup=/);
  getEl('chat-filter').value = '';
  getEl('tab-direct').click();
  await flush();
  assert.match(chats().at(-1), /isGroup=false/);
});

test('backup exclusions cover internal message data but do not exclude app credentials', () => {
  const yaml = fs.readFileSync(new URL('../config.yaml', import.meta.url), 'utf8');
  assert.match(yaml, /backup_exclude:\s*\n\s*- whatsapp\s*\n\s*- whatsapp\/\*\*/);
  assert.doesNotMatch(yaml, /backup_exclude:[^]*- \.whatsapp-mcp-secrets/);
});
