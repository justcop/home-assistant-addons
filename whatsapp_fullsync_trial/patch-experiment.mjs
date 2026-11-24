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
// Baileys rc14 fails before QR with WIN_HYBRID on an unlinked session.
// Bootstrap with a Safari QR, then advertise WIN_HYBRID after the trial
// has linked. This is not a native Desktop pairing experiment.
patchOnce(connectionPath,/Browsers\.macOS\((["'])Chrome\1\)/,
  '(auth.state.creds.registered || auth.state.creds.account ? Browsers.windows("Desktop") : Browsers.macOS("Safari"))',
  'web QR bootstrap then authenticated Windows Desktop');
if(!fs.readFileSync(connectionPath,'utf8').includes('syncFullHistory: true'))
  throw Error('Full-history negotiation is not enabled in the pinned base image');
// Baileys rc14 defaults to filtering FULL history notifications even when
// syncFullHistory=true causes WhatsApp to send them. Override the filter only
// in this isolated experiment. The existing upstream provenance/fromMe checks
// and deletion-suppression mechanisms remain intact.
patchOnce(connectionPath,/syncFullHistory: true,\s*generateHighQualityLinkPreview:/,
  'syncFullHistory: true,\n        shouldSyncHistoryMessage: () => true,\n        generateHighQualityLinkPreview:',
  'accept full-history sync notifications');

patchOnce(launcherPath,/const DEFAULT_PAIRING_MODE = 'code';/,
  "const DEFAULT_PAIRING_MODE = 'qr';",'trial uses QR by default');
patchOnce(launcherPath,/WHATSAPP_HISTORY_BACKFILL:\s*'1'/,
  "WHATSAPP_HISTORY_BACKFILL: '0'",'pause competing on-demand history requests');
patchOnce(launcherPath,/configureWhatsAppStorage\(opts\.storage_path\)/,
  "configureWhatsAppStorage('')",'force independent internal archive');
// Never allow a production shared volume to be reached, even if modified options
// later try to supply a storage_path. The experimental slug has its own /data.
console.log('Trial image: Safari QR bootstrap -> Windows Desktop (WIN_HYBRID) after registration; full history requested, isolated /data.');
