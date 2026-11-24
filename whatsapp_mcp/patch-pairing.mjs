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
const PHONE_REFRESH_FILE = '/data/.whatsapp-pairing-refresh';
const PAIRING_MODE_FILE = '/data/.whatsapp-pairing-mode';
const QR_FILE = '/data/.whatsapp-link-qr';
function pairingMode(): 'qr' | 'code' {
  try {
    const mode = readFileSync(PAIRING_MODE_FILE, 'utf8').trim();
    return mode === 'qr' || mode === 'code' ? mode : 'code';
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    return process.env['WHATSAPP_PAIRING_DEFAULT_MODE'] === 'qr' ? 'qr' : 'code';
  }
}
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
  '  let retryTimer: NodeJS.Timeout | undefined;\n  let repairTimer: NodeJS.Timeout | undefined;\n  let resettingPairing = false;\n  let activePairingMode = pairingMode();');
replaceOnce('if (config.phoneNumber === undefined) {','if (pairingPhone(config.phoneNumber) === undefined) {');
replaceOnce('    if (config.qrFile !== undefined) {\n      setState("pairing");\n      return;\n    }',
  '    if (pairingMode() === "qr") {\n      setState("pairing");\n      return;\n    }');
replaceOnce('    void requestPairingCode(activeSock, config.phoneNumber);',
  '    const phone = pairingPhone(config.phoneNumber)!;\n    logger.info({ phoneNumber: phone }, "whatsapp: pairing code requested for phone number");\n    void requestPairingCode(activeSock, phone);');
replaceOnce(`      logger.info({ pairingCode }, "whatsapp: pairing code issued");
      // Deliberately unmissable in a Portainer log tail, on top of the structured log line above.
      console.log(\`\\n=== WhatsApp pairing code: \${pairingCode} ===\\n\`);`,
  `      // Treat the code as a short-lived credential. Never print it in logs.
      writeFileSync(PAIRING_CODE_FILE, JSON.stringify({ code: pairingCode, issuedAt: Date.now() }), { mode: 0o600 });
      logger.info("whatsapp: pairing code available through private Home Assistant ingress");`);
replaceOnce('    attempts = 0;\n    lastConnectedAt = nowSec();',
  '    rmSync(QR_FILE, { force: true });\n    rmSync(PAIRING_CODE_FILE, { force: true });\n    attempts = 0;\n    lastConnectedAt = nowSec();');
// Ignore connection events from an intentionally retired socket; its later
// logout/401 event must not erase the new linked session.
replaceOnce('    newSock.ev.on("creds.update", () => {\n      auth.saveCreds();',
  '    newSock.ev.on("creds.update", () => {\n      if (newSock !== sock) return;\n      auth.saveCreds();');
replaceOnce('    newSock.ev.on("connection.update", (update: Partial<BaileysConnectionState>) => {\n      touch();',
  '    newSock.ev.on("connection.update", (update: Partial<BaileysConnectionState>) => {\n      if (newSock !== sock) return;\n      touch();');
replaceOnce('  function handleClose(update: Partial<BaileysConnectionState>): void {\n    // stop() itself',
  '  function handleClose(update: Partial<BaileysConnectionState>): void {\n    // stop() itself');
replaceOnce('    if (stopped) return;\n\n    // Baileys types',
  '    if (stopped || resettingPairing) return;\n\n    // Baileys types');
const startAnchor='    stopped = false;\n    clearRetryTimer(); // don\'t let an already-scheduled retry also fire and create a second socket';
replaceOnce(startAnchor,`    stopped = false;
    if (repairTimer === undefined) repairTimer = setInterval(() => {
      if (stopped || resettingPairing) return;
      if (existsSync(PHONE_REFRESH_FILE)) {
        rmSync(PHONE_REFRESH_FILE, { force: true });
        const mode = pairingMode();
        if (mode !== activePairingMode && state === 'pairing'
            && !auth.state.creds.registered && !auth.state.creds.account) {
          activePairingMode = mode;
          const previous = sock;
          sock = undefined;
          rmSync(QR_FILE, { force: true });
          rmSync(PAIRING_CODE_FILE, { force: true });
          clearRetryTimer();
          void previous?.end(undefined).catch(() => undefined);
          // createSocket discards the half-registered unclaimed code identity.
          createSocket();
        } else if (mode === 'code') {
          const phone = pairingPhone(config.phoneNumber);
          if (phone && state === 'pairing' && sock) {
            pairingRequestedForSocket = true;
            logger.info({ phoneNumber: phone }, 'whatsapp: pairing code requested for phone number');
            void requestPairingCode(sock, phone);
          }
        }
      }
      if (!existsSync(REPAIR_SIGNAL_FILE)) return;
      resettingPairing = true;
      rmSync(REPAIR_SIGNAL_FILE, { force: true });
      rmSync(PAIRING_CODE_FILE, { force: true });
      rmSync(QR_FILE, { force: true });
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
