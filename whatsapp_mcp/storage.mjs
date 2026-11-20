import fs from 'node:fs';
import path from 'node:path';

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
export function configureWhatsAppStorage(storagePath) {
  const requested = validateStoragePath(storagePath);
  fs.mkdirSync(INTERNAL, { recursive: true, mode: 0o700 });
  if (!requested) return { dataDir: INTERNAL, external: false };

  const { directory: target, disk, root } = requested;
  requireMountedDisk(disk, root);
  const relative = path.relative(disk, target);
  // No part of the selected archive directory may be a symlink.
  let segment = disk;
  for (const name of relative.split(path.sep)) {
    segment = path.join(segment, name);
    if (fs.existsSync(segment) && fs.lstatSync(segment).isSymbolicLink())
      throw new Error('A component of storage_path is a symlink');
  }
  let migrated = false;
  if (fs.existsSync(target)) {
    if (!fs.statSync(target).isDirectory()) throw new Error('storage_path is not a directory');
    if (hasFiles(INTERNAL) && !fs.existsSync(path.join(target, 'whatsapp.db')))
      throw new Error('External archive folder has no WhatsApp database while local archive contains data; refusing to start');
    // A previously migrated target takes priority. The old source is kept as
    // a rollback copy; never automatically merge stale and live databases.
  } else {
    const temporary = path.join(disk, '.whatsapp-migration-' + process.pid + '-' + Date.now());
    console.log('Migrating WhatsApp archive to the verified mounted external volume (old files retained for rollback).');
    try {
      fs.cpSync(INTERNAL, temporary, { recursive: true, force: false, errorOnExist: true });
      chownTree(temporary);
      fs.renameSync(temporary, target);
      migrated = true;
    } catch (error) {
      // Do not silently revert to INTERNAL or delete the original archive.
      try { if (fs.existsSync(temporary)) fs.rmSync(temporary, { recursive: true, force: true }); } catch { /* retained for manual recovery */ }
      throw error;
    }
  }
  // Full recursive chown is only necessary after migration. Repeating it on
  // every startup would traverse every cached attachment and slow boot badly.
  if (!migrated) { fs.chownSync(target, 1000, 1000); fs.chmodSync(target, 0o700); }
  // Verify that the unprivileged WhatsApp API can write here, before launch.
  const probe = path.join(target, '.write-test-' + process.pid);
  fs.writeFileSync(probe, 'ok', { flag: 'wx', mode: 0o600 });
  fs.chownSync(probe, 1000, 1000);
  fs.rmSync(probe);
  return { dataDir: target, external: true };
}
