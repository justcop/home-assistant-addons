/* Govee Lighting Designer — experimental Lovelace card.
 * Palettes are generated locally. No raw frames are sent without explicit target configuration.
 */
const LIGHTING = [
 ["Rhythm",0x00,true],["Sprouting",0x01,true],["Shiny",0x02,true],
 ["Beat",0x03,true],["Waves",0x04,false],["Spectrum",0x05,false],
 ["Rolling",0x06,false],["Hopping",0x0b,false],["Starlight",0x07,false],
 ["Piano Keys",0x08,false],["Jumping",0x09,false],["Luminous",0x0a,false]
];
const CARNIVAL = ["Brilliant","Pulsate","Dazzle","Fascination","Aspiring","Cadence","Revel","Fluctuate","Funny","Shimmer"];
const EFFECTS = [
 ...LIGHTING.map(([name,id,variants])=>({name,id,group:"Lighting Show",variants,verified:true})),
 ...CARNIVAL.map((name,i)=>({name,id:0x21+i,group:"Carnival",variants:true,verified:false}))
];
// A captured single frame supports 20 total bytes. Byte 6 distinguishes Dynamic (0) from Soft (1).
function effectFrame(effect,variant) {
  if (!effect.verified) throw new Error("Effect ID has not yet been verified");
  const data = [0x33,0x60,0x02,0x01,effect.id,0x00,variant==="Soft"?1:0,0x64,...Array(11).fill(0)];
  const sum = data.reduce((a,b)=>a^b,0);
  return [...data,sum].map(x=>x.toString(16).padStart(2,"0")).join(" ");
}
function rand(seed){let s=(seed>>>0)||1;return ()=>{s=(Math.imul(1664525,s)+1013904223)>>>0;return s/4294967296;};}
const mod=(v,n)=>((v%n)+n)%n;
const presets={
 Aurora:{base:195,s:0.8,l:0.65,scheme:"analogous"},
 Sunset:{base:22,s:0.88,l:0.69,scheme:"analogous"},
 Ocean:{base:205,s:0.85,l:0.52,scheme:"analogous"},
 "Jewel Tones":{base:265,s:0.82,l:0.54,scheme:"triadic"},
 Neon:{base:325,s:1,l:0.72,scheme:"triadic"},
 Forest:{base:135,s:0.66,l:0.48,scheme:"analogous"},
 Pastel:{base:290,s:0.42,l:0.83,scheme:"analogous"}
};
function palette(count,options={},seed=1,locks={}) {
 const r=rand(seed),result=[];const scheme=options.scheme||"analogous";
 for(let i=0;i<count;i++){
  if(locks[i]){result.push(locks[i]);continue;}
  let angle=0;
  if(scheme==="analogous")angle=(i-(count-1)/2)*20;
  if(scheme==="complementary")angle=(i%2)*180;
  if(scheme==="triadic")angle=(i%3)*120;
  if(scheme==="split-complementary")angle=[0,150,210][i%3];
  const h=mod((options.base??210)+angle+(r()-.5)*14,360);
  const chroma=Math.max(0.02,Math.min(.32,(options.s??.8)*.27*(.88+r()*.20)));
  const lightness=Math.max(.25,Math.min(.92,(options.l??.65)+(r()-.5)*.13));
  result.push(`oklch(${(lightness*100).toFixed(1)}% ${chroma.toFixed(3)} ${h.toFixed(1)})`);
 }
 return result;
}
class GoveeLightingDesigner extends HTMLElement {
 setConfig(config){
  this.config=config;
  this.effect=LIGHTING[0][0];
  this.variant="Dynamic";this.count=4;this.seed=Math.floor(Math.random()*4294967295);
  this.preset="Aurora";this.locked={};this.colors=palette(this.count,presets[this.preset],this.seed,this.locked);
  this.render();
 }
 set hass(hass){this._hass=hass;this.renderStatus();}
 getCardSize(){return 8;}
 pick(){this.colors=palette(this.count,presets[this.preset],this.seed,this.locked);this.render();}
 changeCount(n){this.count=n;this.locked={};this.pick();}
 async sendEffect(){
  const effect=EFFECTS.find(e=>e.name===this.effect);
  if(!this.config?.device_id){this.message("Configure device_id before sending");return;}
  if(!effect.verified){this.message("Carnival command not yet verified. Nothing sent.");return;}
  try {
   const frame=effectFrame(effect,this.variant);
   await this._hass.callService("govee","send_raw_ptreal",{device_id:this.config.device_id,frame});
   this.message("Effect command sent; device response not verified");
  }catch(e){this.message("Command failed: "+e.message);}
 }
 message(s){const el=this.querySelector(".gld-status");if(el)el.textContent=s;}
 renderStatus(){}
 render(){
  if(!this.config)return;
  const effects=EFFECTS.map(e=>`<option value="${e.name}" ${e.name===this.effect?"selected":""}>${e.group} · ${e.name}${e.verified?"":" (unverified)"}</option>`).join("");
  const e=EFFECTS.find(e=>e.name===this.effect);
  this.innerHTML=`<ha-card header="Govee Lighting Designer">
    <div class="gld">
      <div class="gld-label">Lighting effect</div>
      <select class="effect">${effects}</select>
      <div class="gld-variants" ${e.variants?"":"hidden"}>
       <label><input type="radio" name="gld-variant" value="Dynamic" ${this.variant==="Dynamic"?"checked":""}> Dynamic</label>
       <label><input type="radio" name="gld-variant" value="Soft" ${this.variant==="Soft"?"checked":""}> Soft</label>
      </div>
      <div class="gld-line"><span>Colour palette (up to 8)</span><select class="count">${[2,3,4,5,6,7,8].map(n=>`<option value="${n}" ${n===this.count?"selected":""}>${n}</option>`).join("")}</select></div>
      <div class="gld-line"><span>Colour character</span><select class="preset">${Object.keys(presets).map(p=>`<option ${p===this.preset?"selected":""}>${p}</option>`).join("")}</select></div>
      <div class="gld-swatches">${this.colors.map((c,i)=>`<button data-lock="${i}" style="background:${c}" title="Click to ${this.locked[i]?"unlock":"lock"} colour ${i+1}">${this.locked[i]?"🔒":""}</button>`).join("")}</div>
      <div class="gld-actions"><button class="shuffle">Shuffle unlocked colours</button><button class="copy">Copy palette</button></div>
      <div class="gld-actions"><button class="effect-send" ${!e.verified?"disabled":""}>Apply effect</button></div>
      <div class="gld-status" role="status">Palette preview only — colour transmission isn't decoded yet.</div>
    </div>
    <style>
      .gld{padding:0 16px 18px;display:grid;gap:12px;color:var(--primary-text-color);}
      .gld-label{font-weight:600}.gld-line,.gld-actions{display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap}
      .gld select{background:var(--card-background-color);color:inherit;border:1px solid var(--divider-color);border-radius:8px;padding:10px;max-width:100%}
      .gld-variants{display:flex;gap:20px}.gld-swatches{display:flex;gap:3px;min-height:78px}
      .gld-swatches button{flex:1;border:0;min-width:0;cursor:pointer;border-radius:5px;color:#fff;text-shadow:0 1px 3px #000}
      .gld-actions button{border:1px solid var(--divider-color);background:var(--secondary-background-color);color:inherit;border-radius:9px;padding:10px;cursor:pointer}
      .gld-actions button:disabled{opacity:.4;cursor:not-allowed}
      .gld-status{font-size:12px;color:var(--secondary-text-color)}
    </style></ha-card>`;
  this.querySelector(".effect").onchange=ev=>{this.effect=ev.target.value;this.render();};
  this.querySelectorAll('input[name="gld-variant"]').forEach(el=>el.onchange=ev=>{this.variant=ev.target.value;});
  this.querySelector(".count").onchange=ev=>this.changeCount(Number(ev.target.value));
  this.querySelector(".preset").onchange=ev=>{this.preset=ev.target.value;this.locked={};this.pick();};
  this.querySelector(".shuffle").onclick=()=>{this.seed++;this.pick();};
  this.querySelector(".copy").onclick=()=>{const data=JSON.stringify(this.colors);navigator.clipboard?.writeText(data).then(()=>this.message("Palette copied")).catch(()=>this.message("Clipboard unavailable"));};
  this.querySelector(".effect-send").onclick=()=>this.sendEffect();
  this.querySelectorAll("[data-lock]").forEach(el=>el.onclick=()=>{const i=Number(el.dataset.lock);if(this.locked[i])delete this.locked[i];else this.locked[i]=this.colors[i];this.render();});
 }
}
if(!customElements.get("govee-lighting-designer"))customElements.define("govee-lighting-designer",GoveeLightingDesigner);
window.customCards=window.customCards||[];
window.customCards.push({type:"govee-lighting-designer",name:"Govee Lighting Designer",description:"Effect selection and locally generated, lockable 2–8 colour palettes"});
