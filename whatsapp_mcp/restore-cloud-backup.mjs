// Offline recovery tool. Run on a trusted machine with Node 24+.
// NEVER overwrite an active /media/storage/.whatsapp-mcp-private/whatsapp.db.
// Usage: node restore-cloud-backup.mjs FILE.wacloud OUTPUT.sqlite RECOVERY-KEY-FILE
import fs from 'node:fs';
import path from 'node:path';
import { decryptSnapshot } from './cloud-backup.mjs';

const args = process.argv.slice(2);
if (args.length !== 3) {
  console.error('Usage: node restore-cloud-backup.mjs encrypted.wacloud recovered.sqlite recovery-key.txt');
  process.exit(2);
}
const [source, output, keyFile] = args;
if (!fs.existsSync(source) || !source.endsWith('.wacloud')) {
  console.error('Provide an existing .wacloud file');
  process.exit(2);
}
if (fs.existsSync(output)) {
  console.error('Refusing to overwrite an existing database');
  process.exit(2);
}
if (path.resolve(source) === path.resolve(output)) {
  console.error('Source and destination must differ');
  process.exit(2);
}
const key = Buffer.from(fs.readFileSync(keyFile, 'utf8').trim(), 'base64url');
if (key.length !== 32) {
  console.error('The recovery key file must contain a 32-byte base64url key');
  process.exit(2);
}
try {
  await decryptSnapshot(source, output, key);
  console.log('Backup authenticated and SQLite integrity verified. Recovered database at: ' + path.resolve(output));
  console.log('Keep the WhatsApp app stopped before replacing any live archive; linked-device credentials and media are not included.');
} catch (error) {
  console.error('Restore failed: ' + error.message);
  process.exitCode = 1;
}
