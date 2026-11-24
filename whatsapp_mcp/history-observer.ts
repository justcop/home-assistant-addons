// Aggregate history telemetry in the private archive; never persist JIDs,
// message bodies or contact names. Called only after FULL/ON_DEMAND ingestion.
import fs from "node:fs";
import path from "node:path";
type Metrics = {
  schema:1;batches:number;received:number;inserted:number;existing:number;
  netUniqueChange:number;fullBatches:number;fullReceived:number;
  lastFullAt?:string;lastBatch?:unknown;history:unknown[];
};
export function observeHistoryBatch(input:{
  type:string;received:number;inserted:number;existing:number;
  netUniqueChange:number;isLatest?:boolean|null;
}):void {
  const root=process.env["WHATSAPP_DATA_DIR"];
  if(!root || !Number.isFinite(input.received)||!Number.isFinite(input.netUniqueChange))return;
  const file=path.join(root,".history-metrics.json");
  let m:Metrics;
  try {m=JSON.parse(fs.readFileSync(file,"utf8")) as Metrics;}
  catch {m={schema:1,batches:0,received:0,inserted:0,existing:0,
    netUniqueChange:0,fullBatches:0,fullReceived:0,history:[]};}
  const now=new Date().toISOString();
  const entry={time:now,type:String(input.type).slice(0,40),received:input.received,
    inserted:input.inserted,existing:input.existing,netUniqueChange:input.netUniqueChange,
    isLatest:input.isLatest===true};
  m.batches++;m.received+=input.received;m.inserted+=input.inserted;
  m.existing+=input.existing;m.netUniqueChange+=input.netUniqueChange;
  m.lastBatch=entry;m.history=[...m.history.slice(-39),entry];
  if(input.type==="FULL"){m.fullBatches++;m.fullReceived+=input.received;m.lastFullAt=now;}
  const temp=file+"."+process.pid+".tmp";
  fs.writeFileSync(temp,JSON.stringify(m)+"\n",{mode:0o600});
  fs.renameSync(temp,file);
}
