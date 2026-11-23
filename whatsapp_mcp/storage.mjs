import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

const INTERNAL = '/data/whatsapp';
const VOLUME_ROOTS = new Set(['/share', '/media']);

// An external location must be beneath a named HA shared storage mount.
// Never allow /data, bare /share, arbitrary host paths or traversal in this option.
export function validateStoragePath(raw) {
  const value = String(raw ?? '').trim().replace(/\/+$/, '');
  if (!value) return null;
  const parts = value.split('/');
  if (parts.length < 4 || parts[0] !== '' || !VOLUME_ROOTS.has('/' + parts[1])
      || parts.slice(2).some(p => !p || p === '.' || p === '..' || !/^[a-zA-Z0-9_.-]+$/.test(p))
      || path.posix.normalize(value) !== value) {
    throw new Error('storage_path must be /media/<mounted-volume>/.whatsapp-mcp-private or /share/<mounted-volume>/<folder>');
  }
  // Media volumes are normally browser-visible. Use a dot-prefixed,
  // owner-only private folder to avoid indexing or listing the archive.
  // This is not an access-control boundary against HA administrators.
  if (parts[1] === 'media' && parts[3] !== '.whatsapp-mcp-private')
    throw new Error('Media volume archives must use a private hidden .whatsapp-mcp-private directory');
  return { directory: value, disk: path.join('/', parts[1], parts[2]), root: '/' + parts[1] };
}

function requireMountedDisk(disk, root) {
  // A normal directory inside /share or /media is NOT an external drive. Refuse to
  // initialise a second database on the internal disk if STORAGE is absent.
  const mediaInfo = fs.statSync(root);
  const diskInfo = fs.lstatSync(disk);
  if (diskInfo.isSymbolicLink() || !diskInfo.isDirectory())
    throw new Error('External STORAGE volume is not a normal directory');
  if (fs.statSync(disk).dev === mediaInfo.dev)
    throw new Error('External STORAGE volume is not mounted as a separate filesystem: ' + disk);
}
function chownTree(p) {
  const info = fs.lstatSync(p);
  if (info.isSymbolicLink()) throw new Error('Symlinks are not permitted in private WhatsApp storage');
  fs.chownSync(p, 1000, 1000);
  fs.chmodSync(p, info.isDirectory() ? 0o700 : 0o600);
  if (info.isDirectory()) for (const entry of fs.readdirSync(p)) chownTree(path.join(p, entry));
}
function hasFiles(p) {
  return fs.existsSync(p) && fs.readdirSync(p).length > 0;
}

// Before removing a stale internal rollback copy, verify that the selected
// external database is readable and internally consistent. A lower message
// count is NOT a failure: legitimate user deletions must stay deleted.
export function verifyExternalArchive(target, requiresExistingArchive) {
  const dbFile = path.join(target, 'whatsapp.db');
  if (!fs.existsSync(dbFile)) {
    if (requiresExistingArchive)
      throw new Error('External WhatsApp archive database missing; refusing to remove internal copy');
    return false; // A genuinely fresh app has not created its database yet.
  }
  const db = new DatabaseSync(dbFile, { readOnly: true });
  try {
    const result = db.prepare('PRAGMA integrity_check').get();
    if (!result || Object.values(result)[0] !== 'ok')
      throw new Error('External WhatsApp database failed SQLite integrity check');
    db.prepare('SELECT COUNT(*) AS n FROM messages').get();
    db.prepare('SELECT COUNT(*) AS n FROM chats').get();
    return true;
  } finally {
    db.close();
  }
}

function removeStaleInternalArchive(target) {
  if (!fs.existsSync(INTERNAL)) return false;
  if (fs.lstatSync(INTERNAL).isSymbolicLink())
    throw new Error('Internal archive path is a symlink; refusing unsafe deletion');
  if (!fs.lstatSync(INTERNAL).isDirectory())
    throw new Error('Internal archive is not a directory');
  const nonempty = hasFiles(INTERNAL);
  // The active archive is on a verified distinct disk. Never compare rows
  // or copy missing rows back: intentionally deleted messages must stay gone.
  verifyExternalArchive(target, nonempty);
  fs.rmSync(INTERNAL, { recursive: true, force: false, maxRetries: 2 });
  console.log('Privacy: removed retired internal WhatsApp archive directory (no rollback copy retained).');
  return true;
}
export function configureWhatsAppStorage(storagePath) {
  const requested = validateStoragePath(storagePath);
  if (!requested) {
    fs.mkdirSync(INTERNAL, { recursive: true, mode: 0o700 });
    return { dataDir: INTERNAL, external: false };
  }

  const { directory: target, disk, root } = requested;
  // If the storage volume is disconnected, fail closed; never create an
  // empty archive on /data or restore messages from a stale fallback.
  requireMountedDisk(disk, root);
  const relative = path.relative(disk, target);
  let segment = disk;
  for (const name of relative.split(path.sep)) {
    segment = path.join(segment, name);
    if (fs.existsSync(segment) && fs.lstatSync(segment).isSymbolicLink())
      throw new Error('A component of storage_path is a symlink');
  }
  let migrated = false;
  const sourceHasFiles = hasFiles(INTERNAL);
  if (fs.existsSync(target)) {
    if (!fs.statSync(target).isDirectory()) throw new Error('storage_path is not a directory');
    if (sourceHasFiles && !fs.existsSync(path.join(target, 'whatsapp.db')))
      throw new Error('External archive folder has no WhatsApp database while local archive contains data; refusing to start');
  } else if (sourceHasFiles) {
    const temporary = path.join(disk, '.whatsapp-migration-' + process.pid + '-' + Date.now());
    console.log('Migrating WhatsApp archive to verified external storage; no permanent internal backup.');
    try {
      fs.cpSync(INTERNAL, temporary, { recursive: true, force: false, errorOnExist: true });
      chownTree(temporary);
      verifyExternalArchive(temporary, true);
      fs.renameSync(temporary, target);
      migrated = true;
    } catch (error) {
      try { if (fs.existsSync(temporary)) fs.rmSync(temporary, { recursive: true, force: true }); } catch { /* recovery remains manual */ }
      throw error;
    }
  } else {
    fs.mkdirSync(target, { recursive: true, mode: 0o700 });
    migrated = true;
  }
  if (migrated) chownTree(target);
  else { fs.chownSync(target, 1000, 1000); fs.chmodSync(target, 0o700); }

  // Confirm database integrity and that we can write on the mounted target
  // before removing anything from /data.
  verifyExternalArchive(target, sourceHasFiles);
  const probe = path.join(target, '.write-test-' + process.pid);
  fs.writeFileSync(probe, 'ok', { flag: 'wx', mode: 0o600 });
  fs.chownSync(probe, 1000, 1000);
  fs.rmSync(probe);

  // No permanently retained /data/whatsapp snapshot. This also prevents
  // full app backups from containing messages removed from the live archive.
  removeStaleInternalArchive(target);
  return { dataDir: target, external: true };
}
