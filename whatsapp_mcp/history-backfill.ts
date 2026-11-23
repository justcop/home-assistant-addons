/** Persistent, paced, archive-only WhatsApp history backfill. */
import fs from "node:fs";
import path from "node:path";
import { proto } from "baileys";
import type { Logger } from "pino";
import type { WhatsAppConnection } from "./whatsapp/connection.js";
import type { ChatsRepo } from "./db/chats.js";
import type { MessagesRepo } from "./db/messages.js";
export type BackfillDeps = { conn: WhatsAppConnection; chats: ChatsRepo;
  messages: MessagesRepo; dataDir: string; logger: Logger };
type Phase = "queued" | "requesting" | "retry" | "stalled" | "no_anchor" | "likely_complete";
type ChatState = { id: string; isGroup?: boolean; phase: Phase; requests: number; importedEstimate: number;
  failures: number; earliest: number | null; lastAttempt?: string;
  completionBasis?: "short_page" };
type State = { schema: 2; startedAt: string; updatedAt: string; day: string;
  requestsToday: number; requests: number; importedEstimate: number; active: boolean;
  chats: Record<string, ChatState> };
const FILE = ".history-backfill-v2.json";
// Operational circuit-breaker, NOT a documented WhatsApp or Baileys quota.
// One in-flight request at a time; on failures the existing per-chat stall
// mechanism avoids retry storms. Most throughput is bounded by the reply wait.
const PER_DAY = 2000, PER_CHAT = 400, GAP_MS = 3_000, WINDOW_MS = 25_000, BATCH = 50;
const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));
const today = () => new Date().toISOString().slice(0,10);
function save(file: string, s: State): void {
  s.updatedAt = new Date().toISOString();
  const temp = file+"."+process.pid+".tmp";
  fs.writeFileSync(temp, JSON.stringify(s)+"\n", { mode: 0o600 });
  fs.renameSync(temp,file);
}
function load(file: string): State {
  try {
    const s = JSON.parse(fs.readFileSync(file,"utf8")) as State;
    if (s.schema !== 2 || !s.chats || typeof s.chats !== "object") throw Error("Invalid checkpoint");
    s.active=false;
    for (const c of Object.values(s.chats)) if (c.phase === "requesting") c.phase="retry";
    return s;
  } catch(e) {
    if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
    return { schema:2,startedAt:new Date().toISOString(),updatedAt:new Date().toISOString(),
      day:"",requestsToday:0,requests:0,importedEstimate:0,active:false,chats:{} };
  }
}
export function startResumableHistoryBackfill({conn,chats,messages,dataDir,logger}: BackfillDeps): void {
  const file=path.join(dataDir, FILE);
  let running=false, connected=false, halted=false;
  function anchor(chatId:string): {key:NonNullable<proto.IWebMessageInfo["key"]>;ts:number}|undefined {
    for(const row of messages.list({chatId,asc:true},12,0)) {
      if(row.ts<=0) continue;
      const raw=messages.getRaw(chatId,row.id); if(!raw) continue;
      try {
        const msg=proto.WebMessageInfo.decode(raw);
        if(msg.key?.remoteJid && msg.key.id) return {key:msg.key,ts:row.ts};
      } catch { /* invalid envelope, try next */ }
    }
    return undefined;
  }
  function discover(s:State):void {
    for(let offset=0;offset<20_000;offset+=200) {
      const page=chats.list({},200,offset);
      for(const c of page) {
        const existing=s.chats[c.id];
        if(!existing) s.chats[c.id]={
          id:c.id,isGroup:c.isGroup,phase:"queued",requests:0,
          importedEstimate:0,failures:0,earliest:null
        };
        // Refresh the type for existing checkpoints, too: a previously
        // discovered chat must not be mistaken for a direct conversation.
        else existing.isGroup=c.isGroup;
      }
      if(page.length<200) break;
    }
  }
  async function worker():Promise<void> {
    let s:State;
    try {s=load(file);discover(s);s.active=true;save(file,s);}
    catch {logger.error("history-backfill: checkpoint unavailable; cannot run");return;}
    logger.info({conversations:Object.keys(s.chats).length,requestsPerDay:PER_DAY},
      "history-backfill: resumable worker started");
    try {
      while(connected && !halted) {
        if(today()!==s.day) {s.day=today();s.requestsToday=0;discover(s);save(file,s);}
        if(s.requestsToday>=PER_DAY) {
          const midnight=Date.parse(today()+"T00:00:00Z")+86_400_000;
          await sleep(Math.max(30_000,midnight-Date.now()+60_000));continue;
        }
        const eligible=Object.values(s.chats).filter(c=>
          (c.phase==="queued"||c.phase==="retry")&&c.requests<PER_CHAT);
        if(!eligible.length) break;
        // Within each tier keep the existing fair, least-requested-first
        // ordering. Direct chats take precedence over groups, without
        // discarding earlier progress or preventing groups from eventually
        // running once no eligible direct conversations remain.
        eligible.sort((a,b)=>Number(a.isGroup===true)-Number(b.isGroup===true)||
          a.requests-b.requests||
          (a.lastAttempt||"").localeCompare(b.lastAttempt||""));
        const c=eligible[0];if(!c) break;
        const old=anchor(c.id);
        if(!old){c.phase="no_anchor";save(file,s);continue;}
        c.earliest=old.ts;c.phase="requesting";c.requests++;
        c.lastAttempt=new Date().toISOString();s.requests++;s.requestsToday++;
        save(file,s); // before network request; no repeated storm after crash
        let returned=0, failed=false, correlated=false;
        try {
          const socket=conn.requireSocket();
          // fetchMessageHistory returns a request session ID. Correlate the
          // resulting ON_DEMAND chunks by that ID so a delayed reply to a
          // different request cannot make this thread look complete.
          const batches=new Map<string,number>();
          const onHistory=(batch:{syncType?:proto.HistorySync.HistorySyncType|null;
            peerDataRequestSessionId?:string|null; messages:readonly unknown[]})=>{
            if(batch.syncType!==proto.HistorySync.HistorySyncType.ON_DEMAND ||
              !batch.peerDataRequestSessionId) return;
            const id=batch.peerDataRequestSessionId;
            batches.set(id,(batches.get(id)||0)+batch.messages.length);
          };
          socket.ev.on("messaging-history.set",onHistory);
          try {
            const sessionId=await socket.fetchMessageHistory(BATCH,old.key,old.ts*1000);
            await sleep(WINDOW_MS);
            correlated=batches.has(sessionId);
            returned=batches.get(sessionId)||0;
          } finally {socket.ev.off("messaging-history.set",onHistory);}
        } catch {failed=true;}
        const newer=anchor(c.id),advanced=!!newer&&newer.ts<old.ts;
        if(advanced&&newer){
          c.earliest=newer.ts;
          // Estimated protocol records, not guaranteed unique DB inserts.
          const estimate=Math.min(BATCH,returned||BATCH);
          c.importedEstimate+=estimate;s.importedEstimate+=estimate;
        }
        if(!failed && correlated && returned>0 && returned<BATCH){
          // A short *correlated* page is evidence of reaching the start, but
          // not a definitive WhatsApp end-of-history marker. Stop automatic
          // requests and label it as probable completion for review.
          c.phase="likely_complete";
          c.completionBasis="short_page";
          c.failures=0;
        } else if(advanced){
          c.phase="queued";c.failures=0;
        } else {
          // Missing response, or a full page with no earlier anchor:
          // neither proves that WhatsApp has exhausted the conversation.
          c.failures++;c.phase=c.failures>=2?"stalled":"retry";
        }
        if(c.requests>=PER_CHAT && c.phase==="queued") c.phase="stalled";
        save(file,s);
        logger.info({advanced,responseMessages:returned,correlated,failed,
          outcome:c.phase,attempts:c.requests,requestsToday:s.requestsToday},
          "history-backfill: request checkpointed");
        if(connected&&!halted) await sleep(GAP_MS);
      }
    } catch {logger.error("history-backfill: worker paused; checkpoint preserved");}
    finally {
      try{s.active=false;save(file,s);}catch{/* fail closed */}
      logger.info({requests:s.requests,requestsToday:s.requestsToday},
        "history-backfill: worker stopped");
    }
  }
  conn.onStateChange(state=>{
    connected=state==="connected";
    if(!connected)halted=true;
    if(connected&&!running){
      halted=false;running=true;
      const timer=setTimeout(()=>{
        void worker().finally(()=>{running=false;});
      },10_000);
      timer.unref();
    }
  });
}
