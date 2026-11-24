// Strict build-time patches for a private, ingress-only pairing controller.
// Fail closed when the pinned upstream changes.
import fs from 'node:fs';
const file = '/src/packages/api/src/whatsapp/connection.ts';
let s=fs.readFileSync(file,'utf8');
function replaceOnce(before,after) {
  const first=s.indexOf(before);
  if(first<0||s.indexOf(before,first+1)>=0)throw Error('Pairing source anchor missing/ambiguous: '+before.slice(0,70));
  s=s.slice(0,first)+after+s.slice(first+before.length);
}
replaceOnce('import { rmSync, writeFileSync } from "node:fs";',
  'import { rmSync, writeFileSync, existsSync, readFileSync } from "node:fs";');
const header='export function makeConnection(deps: ConnectionDeps): WhatsAppConnection {';
replaceOnce(header,`// The add-on's private pairing UI writes only the phone number, never auth keys.
const PAIRING_PHONE_FILE = '/data/.whatsapp-pairing-phone';
const PAIRING_CODE_FILE = '/data/.whatsapp-pairing-code.json';
const REPAIR_SIGNAL_FILE = '/data/.whatsapp-repair-request';
function pairingPhone(fallback: string | undefined): string | undefined {
  try {
    const value = readFileSync(PAIRING_PHONE_FILE, 'utf8').trim();
    return /^[1-9][0-9]{7,14}$/.test(value) ? value : undefined;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    return fallback;
  }
}

${header}`);
replaceOnce('  let retryTimer: NodeJS.Timeout | undefined;',
  '  let retryTimer: NodeJS.Timeout | undefined;\n  let repairTimer: NodeJS.Timeout | undefined;\n  let resettingPairing = false;');
replaceOnce('if (config.phoneNumber === undefined) {','if (pairingPhone(config.phoneNumber) === undefined) {');
replaceOnce('    void requestPairingCode(activeSock, config.phoneNumber);',
  '    void requestPairingCode(activeSock, pairingPhone(config.phoneNumber)!);');
replaceOnce(`      logger.info({ pairingCode }, "whatsapp: pairing code issued");
      // Deliberately unmissable in a Portainer log tail, on top of the structured log line above.
      console.log(\`\\n=== WhatsApp pairing code: \${pairingCode} ===\\n\`);`,
  `      // Treat the code as a short-lived credential. Never print it in logs.
      writeFileSync(PAIRING_CODE_FILE, JSON.stringify({ code: pairingCode, issuedAt: Date.now() }), { mode: 0o600 });
      logger.info("whatsapp: pairing code available through private Home Assistant ingress");`);
replaceOnce('    attempts = 0;\n    lastConnectedAt = nowSec();',
  '    rmSync(PAIRING_CODE_FILE, { force: true });\n    attempts = 0;\n    lastConnectedAt = nowSec();');
replaceOnce('  function handleClose(update: Partial<BaileysConnectionState>): void {\n    // stop() itself',
  '  function handleClose(update: Partial<BaileysConnectionState>): void {\n    // stop() itself');
replaceOnce('    if (stopped) return;\n\n    // Baileys types',
  '    if (stopped || resettingPairing) return;\n\n    // Baileys types');
const startAnchor='    stopped = false;\n    clearRetryTimer(); // don\'t let an already-scheduled retry also fire and create a second socket';
replaceOnce(startAnchor,`    stopped = false;
    if (repairTimer === undefined) repairTimer = setInterval(() => {
      if (stopped || resettingPairing || !existsSync(REPAIR_SIGNAL_FILE)) return;
      resettingPairing = true;
      rmSync(REPAIR_SIGNAL_FILE, { force: true });
      rmSync(PAIRING_CODE_FILE, { force: true });
      void (async () => {
        // This is a deliberate LOCAL credential reset. Never delete messages,
        // archive storage, tombstones or deletion-suppression records.
        const previous = sock;
        sock = undefined;
        clearRetryTimer();
        try { await previous?.end(undefined); } catch { /* best effort */ }
        auth.clear();
        attempts = 0;
        pairingRequestedForSocket = false;
        resettingPairing = false;
        createSocket();
      })().catch(err => {
        resettingPairing = false;
        logger.error({ err }, 'whatsapp: requested local re-pair failed');
        scheduleRetry();
      });
    }, 1000);
    clearRetryTimer(); // don't let an already-scheduled retry also fire and create a second socket`);
replaceOnce('    stopped = true;\n    clearRetryTimer();',
  '    stopped = true;\n    if (repairTimer !== undefined) { clearInterval(repairTimer); repairTimer = undefined; }\n    clearRetryTimer();');
fs.writeFileSync(file,s);
console.log('Private WhatsApp pairing file controller patched into pinned upstream.');
