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
// Desktop sub-platform registration is rejected by WhatsApp in this setup,
// including after successful Safari pairing. Keep a consistent companion
// identity across QR registration and subsequent authenticated connections.
// The already paired trial auth/database is preserved across this image update.
patchOnce(connectionPath,/Browsers\.macOS\((["'])Chrome\1\)/,
  'Browsers.macOS("Safari")',
  'keep stable Safari identity for QR and authenticated connections');
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
console.log('Trial image: stable Safari session through QR and post-pair reconnect; full history requested, isolated /data.');
