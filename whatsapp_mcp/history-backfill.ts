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
type Phase = "queued" | "requesting" | "retry" | "stalled" | "no_anchor";
type ChatState = { id: string; phase: Phase; requests: number; importedEstimate: number;
  failures: number; earliest: number | null; lastAttempt?: string };
type State = { schema: 2; startedAt: string; updatedAt: string; day: string;
  requestsToday: number; requests: number; importedEstimate: number; active: boolean;
  chats: Record<string, ChatState> };
const FILE = ".history-backfill-v2.json";
const PER_DAY = 60, PER_CHAT = 400, GAP_MS = 45_000, WINDOW_MS = 35_000, BATCH = 50;
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
      for(const c of page) if(!s.chats[c.id]) s.chats[c.id]={
        id:c.id,phase:"queued",requests:0,importedEstimate:0,failures:0,earliest:null
      };
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
        eligible.sort((a,b)=>a.requests-b.requests||
          (a.lastAttempt||"").localeCompare(b.lastAttempt||""));
        const c=eligible[0];if(!c) break;
        const old=anchor(c.id);
        if(!old){c.phase="no_anchor";save(file,s);continue;}
        c.earliest=old.ts;c.phase="requesting";c.requests++;
        c.lastAttempt=new Date().toISOString();s.requests++;s.requestsToday++;
        save(file,s); // before network request; no repeated storm after crash
        let returned=0, failed=false;
        try {
          const socket=conn.requireSocket();
          const onHistory=(batch:{syncType?:proto.HistorySync.HistorySyncType|null;
            messages:readonly unknown[]})=>{
            if(batch.syncType===proto.HistorySync.HistorySyncType.ON_DEMAND)
              returned+=batch.messages.length;
          };
          socket.ev.on("messaging-history.set",onHistory);
          try {
            await socket.fetchMessageHistory(BATCH,old.key,old.ts*1000);
            await sleep(WINDOW_MS);
          } finally {socket.ev.off("messaging-history.set",onHistory);}
        } catch {failed=true;}
        const newer=anchor(c.id),advanced=!!newer&&newer.ts<old.ts;
        if(advanced&&newer){
          c.earliest=newer.ts;
          // Estimate: provider batches may contain duplicate rows, and
          // an earlier anchor doesn't guarantee every record is unique.
          const estimate=Math.min(BATCH,returned||BATCH);
          c.importedEstimate+=estimate;s.importedEstimate+=estimate;
          c.failures=0;c.phase="queued";
        } else {
          c.failures++;c.phase=c.failures>=2?"stalled":"retry";
        }
        if(c.requests>=PER_CHAT)c.phase="stalled";
        save(file,s);
        logger.info({advanced,responseMessages:returned,failed,
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
