/**
 * One-shot, bounded proof of Baileys' on-demand history retrieval.
 *
 * Never unlinks WhatsApp, resets auth, clears chat data, or fabricates read
 * states. We send at most three peer history requests, 50 messages each,
 * and record aggregate outcomes with no chat IDs or message content.
 * A durable marker prevents reconnect/restart request storms.
 */
import fs from "node:fs";
import path from "node:path";
import { proto } from "baileys";
import type { Logger } from "pino";
import type { WhatsAppConnection } from "./whatsapp/connection.js";
import type { ChatsRepo } from "./db/chats.js";
import type { MessagesRepo } from "./db/messages.js";

export type TrialDeps = {
  conn: WhatsAppConnection;
  chats: ChatsRepo;
  messages: MessagesRepo;
  dataDir: string;
  logger: Logger;
};

const REQUEST_LIMIT = 3;
const MESSAGES_PER_REQUEST = 50;
const WAIT_MS = 20_000;
const START_DELAY_MS = 10_000;

type Attempt = {
  oldestTimestamp: number;
  status: "requested" | "failed" | "no_reply" | "received";
  returnedMessages: number;
};
type Trial = { startedAt: string; completedAt?: string; attempts: Attempt[] };

const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));

function writeCheckpoint(file: string, state: Trial): void {
  const temp = file + "." + process.pid + ".tmp";
  fs.writeFileSync(temp, JSON.stringify(state) + "\n", { mode: 0o600 });
  fs.renameSync(temp, file);
}

export function startHistoryBackfillTrial({ conn, chats, messages, dataDir, logger }: TrialDeps): void {
  const stateFile = path.join(dataDir, ".history-backfill-trial-v1.json");
  let started = false;
  conn.onStateChange(state => {
    if (state !== "connected" || started) return;
    started = true;
    const timer = setTimeout(() => {
      void run().catch(() => {
        // Do not log exception objects: network failures may contain identifiers.
        logger.warn("history-backfill: trial stopped; no archive or auth state was reset");
      });
    }, START_DELAY_MS);
    timer.unref();
  });

  async function run(): Promise<void> {
    // Conservative one-shot gate: failures should not trigger an automatic retry storm.
    try {
      fs.writeFileSync(stateFile, JSON.stringify({ startedAt: new Date().toISOString(), attempts: [] }) + "\n",
        { flag: "wx", mode: 0o600 });
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "EEXIST") {
        logger.info("history-backfill: prior trial recorded; no automatic repeat");
        return;
      }
      throw err;
    }

    const trial: Trial = { startedAt: new Date().toISOString(), attempts: [] };
    // Choose up to three chats with an existing stored message, prioritising
    // oldest local anchors. No chat identifiers are written to the checkpoint.
    const anchors: { key: NonNullable<proto.IWebMessageInfo["key"]>; ts: number }[] = [];
    for (const chat of chats.list({}, 500, 0)) {
      const oldest = messages.list({ chatId: chat.id, asc: true }, 1, 0)[0];
      if (!oldest || oldest.ts <= 0 || oldest.fromMe === undefined) continue;
      const raw = messages.getRaw(chat.id, oldest.id);
      if (!raw) continue;
      try {
        const envelope = proto.WebMessageInfo.decode(raw);
        if (!envelope.key?.id) continue;
        // Use the stored envelope's actual JID, rather than rewriting a group
        // participant or potentially changing a phone/LID identity.
        if (!envelope.key.remoteJid) continue;
        anchors.push({ key: envelope.key, ts: oldest.ts });
      } catch {
        // Invalid saved envelope: not a usable anchor. Never log its contents.
      }
    }
    anchors.sort((a, b) => a.ts - b.ts);
    const selected = anchors.slice(0, REQUEST_LIMIT);
    logger.info({ candidates: anchors.length, selected: selected.length, perRequest: MESSAGES_PER_REQUEST },
      "history-backfill: bounded trial starting");

    for (const anchor of selected) {
      let socket;
      try { socket = conn.requireSocket(); }
      catch { logger.warn("history-backfill: connection unavailable; remaining attempts skipped"); break; }

      const attempt: Attempt = { oldestTimestamp: anchor.ts, status: "requested", returnedMessages: 0 };
      trial.attempts.push(attempt);
      writeCheckpoint(stateFile, trial);

      const onHistory = (batch: { messages: readonly unknown[] }) => {
        attempt.returnedMessages += batch.messages.length;
      };
      socket.ev.on("messaging-history.set", onHistory);
      try {
        // Baileys stores this as oldestMsgTimestampMs; local archive timestamps
        // are Unix seconds, so multiply by 1000.
        await socket.fetchMessageHistory(MESSAGES_PER_REQUEST, anchor.key, anchor.ts * 1000);
        await sleep(WAIT_MS);
        attempt.status = attempt.returnedMessages > 0 ? "received" : "no_reply";
      } catch {
        attempt.status = "failed";
      } finally {
        socket.ev.off("messaging-history.set", onHistory);
        writeCheckpoint(stateFile, trial);
      }
      logger.info({ outcome: attempt.status, received: attempt.returnedMessages },
        "history-backfill: bounded request finished");
    }
    trial.completedAt = new Date().toISOString();
    writeCheckpoint(stateFile, trial);
    logger.info({ attempts: trial.attempts.length, received: trial.attempts.reduce((n, a) => n + a.returnedMessages, 0) },
      "history-backfill: trial completed");
  }
}
