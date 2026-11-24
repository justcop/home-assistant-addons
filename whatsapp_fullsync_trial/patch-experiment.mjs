// Isolated fresh-pairing experiment. Never modify the original WhatsApp app.
import fs from 'node:fs';
const connectionPath='/opt/whatsapp-api/dist/whatsapp/connection.js';
const launcherPath='/opt/whatsapp-addon/run.mjs';
function patchOnce(file,pattern,replacement,label) {
  const text=fs.readFileSync(file,'utf8');
  const hits=[...text.matchAll(new RegExp(pattern.source,pattern.flags.includes('g')?pattern.flags:pattern.flags+'g'))];
  if(hits.length!==1)throw Error(label+': expected one anchor; found '+hits.length);
  fs.writeFileSync(file,text.replace(pattern,replacement));
}
patchOnce(connectionPath,/macOS\((["'])Chrome\1\)/,
  'macOS("Safari")','fresh-pairing companion profile');
if(!fs.readFileSync(connectionPath,'utf8').includes('syncFullHistory: true'))
  throw Error('Full-history negotiation is not enabled in the pinned base image');
patchOnce(launcherPath,/const DEFAULT_PAIRING_MODE = 'code';/,
  "const DEFAULT_PAIRING_MODE = 'qr';",'trial uses QR by default');
patchOnce(launcherPath,/WHATSAPP_HISTORY_BACKFILL:\s*'1'/,
  "WHATSAPP_HISTORY_BACKFILL: '0'",'pause competing on-demand history requests');
patchOnce(launcherPath,/configureWhatsAppStorage\(opts\.storage_path\)/,
  "configureWhatsAppStorage('')",'force independent internal archive');
// Never allow a production shared volume to be reached, even if modified options
// later try to supply a storage_path. The experimental slug has its own /data.
console.log('Trial image: macOS Safari + syncFullHistory, backfill paused, isolated /data.');
