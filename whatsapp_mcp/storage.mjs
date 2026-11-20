import fs from 'node:fs';
import path from 'node:path';

const INTERNAL = '/data/whatsapp';
const MEDIA_ROOT = '/media';

// An external location must be beneath a named HA media mount.
// Never allow /data, /share, arbitrary host paths or traversal in this option.
export function validateStoragePath(raw) {
  const value = String(raw ?? '').trim().replace(/\/+$/, '');
  if (!value) return null;
  const parts = value.split('/');
  if (parts.length < 4 || parts[0] !== '' || parts[1] !== 'media'
      || parts.slice(2).some(p => !p || p === '.' || p === '..' || !/^[a-zA-Z0-9_.-]+$/.test(p))
      || path.posix.normalize(value) !== value) {
    throw new Error('storage_path must be /media/<mounted-volume>/<folder> (no spaces or ..)');
  }
  return { directory: value, disk: path.join(MEDIA_ROOT, parts[2]) };
}

function requireMountedDisk(disk) {
  // An ordinary folder under /media is NOT an external drive. Refuse to
  // initialise a second database on the internal disk if STORAGE is absent.
  const mediaInfo = fs.statSync(MEDIA_ROOT);
  const diskInfo = fs.lstatSync(disk);
  if (diskInfo.isSymbolicLink() || !diskInfo.isDirectory())
    throw new Error('External media volume is not a normal directory');
  if (fs.statSync(disk).dev === mediaInfo.dev)
    throw new Error('External media volume is not mounted as a separate filesystem: ' + disk);
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

  const { directory: target, disk } = requested;
  requireMountedDisk(disk);
  const relative = path.relative(disk, target);
  // No part of the selected archive directory may be a symlink.
  let segment = disk;
  for (const name of relative.split(path.sep)) {
    segment = path.join(segment, name);
    if (fs.existsSync(segment) && fs.lstatSync(segment).isSymbolicLink())
      throw new Error('A component of storage_path is a symlink');
  }
  if (fs.existsSync(target)) {
    if (!fs.statSync(target).isDirectory()) throw new Error('storage_path is not a directory');
    if (hasFiles(INTERNAL) && !hasFiles(target))
      throw new Error('External archive folder is empty while local archive contains data; refusing to start');
    // A previously migrated target takes priority. The old source is kept as
    // a rollback copy; never automatically merge stale and live databases.
  } else {
    const temporary = path.join(disk, '.whatsapp-migration-' + process.pid + '-' + Date.now());
    console.log('Migrating WhatsApp archive to the verified mounted external volume (old files retained for rollback).');
    try {
      fs.cpSync(INTERNAL, temporary, { recursive: true, force: false, errorOnExist: true });
      chownTree(temporary);
      fs.renameSync(temporary, target);
    } catch (error) {
      // Do not silently revert to INTERNAL or delete the original archive.
      try { if (fs.existsSync(temporary)) fs.rmSync(temporary, { recursive: true, force: true }); } catch { /* retained for manual recovery */ }
      throw error;
    }
  }
  chownTree(target);
  // Verify that the unprivileged WhatsApp API can write here, before launch.
  const probe = path.join(target, '.write-test-' + process.pid);
  fs.writeFileSync(probe, 'ok', { flag: 'wx', mode: 0o600 });
  fs.chownSync(probe, 1000, 1000);
  fs.rmSync(probe);
  return { dataDir: target, external: true };
}
