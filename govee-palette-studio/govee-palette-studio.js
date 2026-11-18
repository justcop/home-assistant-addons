(function CardSource(){
"use strict";
const LIGHT=[
["Rhythm",0,true],["Sprouting",1,true],["Shiny",2,true],["Beat",3,true],
["Waves",4,false],["Spectrum",5,false],["Rolling",6,false],["Hopping",11,false],
["Starlight",7,false],["Piano Keys",8,false],["Jumping",9,false],["Luminous",10,false]
];
const CARNIVAL=["Brilliant","Pulsate","Dazzle","Fascination","Aspiring","Cadence","Revel","Fluctuate","Funny","Shimmer"];
const EFFECTS=[...LIGHT.map(([name,id,dual])=>({name,id,dual,group:1,category:"Lighting Show"})),
...CARNIVAL.map((name,i)=>({name,id:i+0x20,dual:true,group:2,category:"Carnival"}))];
const PRESETS={
"Aurora":{h:196,c:76,l:67,contrast:42,spread:48,scheme:"analogous"},
"Golden Hour":{h:35,c:90,l:70,contrast:40,spread:45,scheme:"analogous"},
"Ocean Depths":{h:214,c:82,l:54,contrast:57,spread:55,scheme:"analogous"},
"Jewel Box":{h:270,c:87,l:61,contrast:58,spread:84,scheme:"triadic"},
"Neon Nights":{h:318,c:99,l:70,contrast:69,spread:96,scheme:"split"},
"Forest":{h:144,c:62,l:53,contrast:56,spread:48,scheme:"analogous"},
"Pastel Dream":{h:290,c:39,l:83,contrast:22,spread:58,scheme:"analogous"},
"Solar Flare":{h:14,c:100,l:66,contrast:70,spread:45,scheme:"analogous"},
"Cosmic":{h:255,c:86,l:48,contrast:76,spread:87,scheme:"split"},
"Tropical":{h:169,c:88,l:66,contrast:51,spread:91,scheme:"triadic"},
"Monochrome":{h:220,c:50,l:64,contrast:85,spread:16,scheme:"mono"}
};
const SCHEMES={analogous:"Analogous",complementary:"Complementary",triadic:"Triadic",split:"Split complementary",tetradic:"Tetradic",mono:"Monochromatic"};
const KEY="govee-palette-studio-v1";
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const wrap=v=>((v%360)+360)%360;
const esc=s=>String(s).replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const options=(items,value)=>items.map(([id,name])=>'<option value="'+esc(id)+'"'+(String(id)===String(value)?" selected":"")+'>'+esc(name)+'</option>').join("");
function random(seed){let n=seed>>>0;return ()=>{n=(Math.imul(1664525,n)+1013904223)>>>0;return n/4294967296;};}
function toHex(nums){return "#"+nums.map(n=>clamp(Math.round(n),0,255).toString(16).padStart(2,"0")).join("").toUpperCase();}
function oklch(L,C,h){
 const a=C*Math.cos(h*Math.PI/180),b=C*Math.sin(h*Math.PI/180);
 const l=(L+.3963377774*a+.2158037573*b)**3;
 const m=(L-.1055613458*a-.0638541728*b)**3;
 const s=(L-.0894841775*a-1.291485548*b)**3;
 return [
 4.0767416621*l-3.3077115913*m+.2309699292*s,
 -1.2684380046*l+2.6097574011*m-.3413193965*s,
 -.0041960863*l-.7034186147*m+1.707614701*s
 ].map(v=>v<=.0031308?12.92*v:1.055*Math.pow(v,1/2.4)-.055);
}
function gamutHex(L,C,h){
 let low=0,high=C;
 for(let i=0;i<15;i++){
  const mid=(low+high)/2;
  if(oklch(L,mid,h).every(v=>v>=0&&v<=1))low=mid;else high=mid;
 }
 return toHex(oklch(L,low,h).map(v=>v*255));
}
function angle(scheme,i,n){
 const t=n===1?0:i/(n-1);
 if(scheme==="complementary")return i%2?180:0;
 if(scheme==="triadic")return 120*(i%3);
 if(scheme==="split")return [0,150,210][i%3];
 if(scheme==="tetradic")return 90*(i%4);
 if(scheme==="mono")return 0;
 return 105*(t-.5);
}
function generate(settings,count,seed,locked){
 const rand=random(seed),out=[];
 for(let i=0;i<count;i++){
  if(/^#[0-9a-f]{6}$/i.test(locked[i]||"")){out.push(locked[i].toUpperCase());continue;}
  const t=count===1?.5:i/(count-1),j=(rand()-.5);
  const scale=settings.scheme==="analogous"?.36+settings.spread/100:1;
  const h=wrap(settings.h+angle(settings.scheme,i,count)*scale+j*13);
  const L=clamp(settings.l/100+(t-.5)*.28*settings.contrast/100+(rand()-.5)*.075,.24,.92);
  const C=clamp(settings.c/100*.34+(rand()-.5)*.04,.01,.36);
  out.push(gamutHex(L,C,h));
 }
 return out;
}
function slots8(colors){return Array.from({length:8},(_,i)=>colors[i%colors.length]);}
function toFrame(bytes){const sum=bytes.reduce((v,b)=>v^b,0);return [...bytes,sum].map(v=>v.toString(16).padStart(2,"0")).join(" ");}
function colorFrames(colors){
 if(colors.length<2||colors.length>8)throw Error("Palette must contain 2–8 colours");
 const bytes=slots8(colors).flatMap(s=>{
  if(!/^#[0-9a-f]{6}$/i.test(s))throw Error("Invalid colour: "+s);
  return [1,3,5].map(n=>parseInt(s.slice(n,n+2),16));
 });
 const a=[0xa3,0x00,0x01,0x02,0x51,0x08,...bytes.slice(0,13)];
 const b=[0xa3,0xff,...bytes.slice(13),...Array(6).fill(0)];
 if(a.length!==19||b.length!==19)throw Error("Palette frame size error");
 return [toFrame(a),toFrame(b)];
}
function effectFrame(effect,variant){
 const data=[0x33,0x60,0x02,effect.group,effect.id,0,effect.dual&&variant==="Soft"?1:0,0x64,...Array(11).fill(0)];
 return toFrame(data);
}
const CSS=`
:host{display:block;color:var(--primary-text-color,#ddd)}*{box-sizing:border-box}button,input,select{font:inherit}
button{cursor:pointer}ha-card{overflow:hidden;border-radius:18px}
.header{padding:25px 26px;background:linear-gradient(110deg,rgba(0,155,165,.17),rgba(158,79,178,.1));border-bottom:1px solid var(--divider-color,#444)}
.top{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
.kicker{font-size:10px;letter-spacing:1.8px;text-transform:uppercase;color:var(--secondary-text-color,#999);font-weight:750}
h2{font-size:27px;line-height:1.2;letter-spacing:-.7px;margin:6px 0 5px;font-weight:750}
h3{font-size:15px;letter-spacing:-.25px;margin:0 0 15px;font-weight:750}
.muted{font-size:11.5px;line-height:1.55;color:var(--secondary-text-color,#a2a5b2)}
.chip{display:inline-flex;align-items:center;gap:8px;font-size:11px;color:var(--secondary-text-color,#999);border:1px solid var(--divider-color,#555);padding:8px 10px;border-radius:24px}
.dot{width:7px;height:7px;border-radius:50%;background:#888}.dot.on{background:#3fd7a5;box-shadow:0 0 8px #3fd7a575}
.columns{display:grid;grid-template-columns:minmax(240px,.8fr) minmax(345px,1.2fr);gap:23px;padding:24px 25px}
.left{border-right:1px solid var(--divider-color,#444);padding-right:22px;min-width:0}.right{min-width:0}
.section{margin-bottom:21px}.label{font-size:11px;font-weight:700;color:var(--secondary-text-color,#aaa);text-transform:uppercase;letter-spacing:.55px;display:block;margin:0 0 9px}
select,input[type=text]{width:100%;background:var(--secondary-background-color,rgba(120,120,120,.1));color:var(--primary-text-color,inherit);border:1px solid var(--divider-color,#555);border-radius:9px;padding:10px;min-height:40px}
select option{background:#fff;color:#111}
.choices{display:flex;gap:7px;flex-wrap:wrap}
.choice{background:transparent;color:inherit;border:1px solid var(--divider-color,#555);border-radius:20px;font-size:11px;padding:8px 10px}
.choice.active{background:color-mix(in srgb,var(--primary-color,#21a0aa) 19%,transparent);border-color:var(--primary-color,#26a1af);font-weight:750}
.slider{display:grid;grid-template-columns:1fr 34px;gap:5px 8px;margin:13px 0}
.slider label{font-size:12px}.slider span{text-align:right;font-size:11px;color:var(--secondary-text-color,#aaa)}
.slider input{grid-column:1/-1;width:100%;accent-color:var(--primary-color,#20a3b0)}
.headrow{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.ribbon{display:flex;gap:3px;border-radius:12px;overflow:hidden;height:112px;margin:10px 0 11px;background:#151c28}
.ribbon span{flex:1;transition:background .2s}
.swatches{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:15px 0}
.swatch{border:1px solid var(--divider-color,#444);border-radius:10px;overflow:hidden;min-width:0}
.paint{height:53px;position:relative}
.paint input{width:100%;height:100%;position:absolute;inset:0;opacity:0;cursor:pointer}
.paint input:focus-visible{opacity:1}
.swatchbottom{display:flex;align-items:center;justify-content:space-between;padding:5px 4px 3px;gap:2px}
.hex{font-size:10px;font-family:ui-monospace,Consolas,monospace}
.iconbtn{border:0;background:transparent;color:inherit;padding:4px 5px;font-size:12px;border-radius:5px}
.arrows{display:flex;border-top:1px solid var(--divider-color,#444)}
.arrows button{flex:1;border:0;background:transparent;color:var(--secondary-text-color,#aab);padding:6px;font-size:14px}
.arrows button:disabled{opacity:.2}
.buttons{display:flex;gap:8px;flex-wrap:wrap;margin:13px 0}
.btn{background:var(--secondary-background-color,rgba(120,120,120,.12));border:1px solid var(--divider-color,#555);border-radius:9px;padding:10px 13px;color:inherit;font-size:12px;font-weight:650}
.btn.primary{background:var(--primary-color,#258c9f);border-color:transparent;color:var(--text-primary-color,#fff)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.rule{border:0;border-top:1px solid var(--divider-color,#444);margin:22px 0}
.save{display:grid;grid-template-columns:1fr auto;gap:8px}.saved{display:flex;gap:7px;flex-wrap:wrap;margin-top:11px}
.saveditem{border:1px solid var(--divider-color,#555);border-radius:7px;display:flex;align-items:center;gap:1px}
.saveditem button{color:inherit;background:none;border:0;padding:8px;font-size:11px}.saveditem .remove{color:var(--secondary-text-color,#aaa)}
.effects{display:grid;grid-template-columns:1fr 118px;gap:9px}
.notice{border-radius:9px;background:var(--secondary-background-color,rgba(120,120,120,.1));padding:10px 12px;font-size:11.5px;line-height:1.5;color:var(--secondary-text-color,#aab);margin:12px 0 0}
.notice.success{color:#3dbb93}.notice.error{color:#e46e75}
.footer{border-top:1px solid var(--divider-color,#444);padding:14px 25px;display:flex;justify-content:space-between;align-items:center;gap:13px;flex-wrap:wrap}
.live-summary{padding:17px 25px;border-bottom:1px solid var(--divider-color,#444);background:var(--secondary-background-color,rgba(120,120,120,.08))}
.live-top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.live-title{font-weight:750;font-size:14px}
.live-palette{display:flex;gap:2px;height:33px;overflow:hidden;border-radius:7px;margin:11px 0 6px}
.live-palette span{flex:1}.live-foot{font-size:11px;color:var(--secondary-text-color,#aaa);line-height:1.55;margin-top:5px}
.live-lights{display:flex;gap:9px;flex-wrap:wrap;margin-top:8px}
.live-lights span{font-size:10px;padding:3px 6px;border-radius:6px;border:1px solid var(--divider-color,#555)}
@media(max-width:860px){.columns{grid-template-columns:1fr}.left{border:0;border-bottom:1px solid var(--divider-color,#444);padding:0 0 20px}}
@media(max-width:480px){.columns{padding:18px;gap:17px}.header{padding:20px}h2{font-size:23px}.swatches{gap:6px}.hex{font-size:9px}.swatchbottom{flex-wrap:wrap;justify-content:center}.effects{grid-template-columns:1fr 96px}.ribbon{height:80px}.footer{padding:14px 18px}}
`;
class Studio extends HTMLElement{
 constructor(){
  super();this.attachShadow({mode:"open"});this.ha=null;this.config=null;this.busy=false;
  this.s={settings:{...PRESETS.Aurora},preset:"Aurora",count:8,seed:20261117,
  locked:{},colors:[],effect:"Rhythm",variant:"Dynamic",favorites:[],
  notice:"Create a palette, choose an effect and apply it to DreamView.",status:""};
 }
 setConfig(cfg){
  this.config={device_id:cfg.device_id||"0f19e21af64af9e364dfde26fb48ae69",dreamview_entity:cfg.dreamview_entity||"switch.floor_lamp_2_dreamview",movie_entity:cfg.movie_entity||"switch.tv_backlight_dreamview",palette_entity:cfg.palette_entity||"input_text.govee_bilresa_random_palette",effect_entity:cfg.effect_entity||"input_select.govee_bilresa_effect"};
  if(!this.ready){this.restore();if(!this.s.colors.length)this.s.colors=generate(this.s.settings,this.s.count,this.s.seed,this.s.locked);this.ready=true;this.render();}
 }
 set hass(hass){this.ha=hass;this.syncFromHA();this.refresh();}
 // Remote changes update shared HA helpers. These are not physical RGB readings.
 syncFromHA(){
  if(!this.ready||!this.config||!this.ha?.states)return;
  let changed=false;
  const palette=this.ha.states[this.config.palette_entity]?.state;
  const colors=typeof palette==="string"?palette.split(",").map(c=>c.trim().toUpperCase()):[];
  if(colors.length>=1&&colors.length<=8&&colors.every(c=>/^#[0-9A-F]{6}$/.test(c))&&palette!==this._seenPalette){
   this._seenPalette=palette;
   this.s.colors=colors;this.s.count=colors.length;this.s.locked={};this.s.preset="Custom";
   this.s.notice="Palette updated from Home Assistant / IKEA remote.";
   this.s.status="success";changed=true;
  }
  const choice=this.ha.states[this.config.effect_entity]?.state;
  if(typeof choice==="string"&&choice!==this._seenEffect){
   const match=/^(Lighting Show|Carnival): (.+?)(?: \| (Dynamic|Soft))?$/.exec(choice);
   const found=match?EFFECTS.find(e=>e.category===match[1]&&e.name===match[2]):null;
   if(found){
    this._seenEffect=choice;this.s.effect=found.name;
    this.s.variant=found.dual?(match[3]||"Dynamic"):"Dynamic";
    changed=true;
   }
  }
  if(changed){this.persist();this.render();}
 }
 getCardSize(){return 12;}
 getGridOptions(){return{columns:12,rows:"auto"};}
 restore(){
  try{
   const x=JSON.parse(localStorage.getItem(KEY)||"{}");
   if(x.settings&&typeof x.settings==="object")this.s.settings={...this.s.settings,...x.settings};
   if(PRESETS[x.preset]||x.preset==="Custom")this.s.preset=x.preset;
   if(Number.isInteger(x.count))this.s.count=clamp(x.count,2,8);
   if(Number.isInteger(x.seed))this.s.seed=x.seed;
   if(x.locked&&typeof x.locked==="object")this.s.locked=x.locked;
   if(Array.isArray(x.colors)&&x.colors.length===this.s.count&&x.colors.every(c=>/^#[a-f0-9]{6}$/i.test(c)))this.s.colors=x.colors;
   if(EFFECTS.some(e=>e.name===x.effect))this.s.effect=x.effect;
   if(["Dynamic","Soft"].includes(x.variant))this.s.variant=x.variant;
   if(Array.isArray(x.favorites))this.s.favorites=x.favorites.slice(0,30);
  }catch(_){}
 }
 persist(){try{const x=this.s;localStorage.setItem(KEY,JSON.stringify({settings:x.settings,preset:x.preset,count:x.count,seed:x.seed,locked:x.locked,colors:x.colors,effect:x.effect,variant:x.variant,favorites:x.favorites}));}catch(_){}}
 notice(message,status=""){this.s.notice=message;this.s.status=status;const el=this.shadowRoot.querySelector(".notice");if(el){el.textContent=message;el.className="notice "+status;}}
 effect(){return EFFECTS.find(e=>e.name===this.s.effect)||EFFECTS[0];}
 renew(){this.s.colors=generate(this.s.settings,this.s.count,this.s.seed,this.s.locked);this.persist();this.render();}
 refresh(){
  if(!this.ready||!this.config)return;
  const e=this.ha?.states?.[this.config.dreamview_entity],on=e?.state==="on";
  const label=this.shadowRoot.querySelector(".dream-state"),button=this.shadowRoot.querySelector(".toggle");
  if(label)label.innerHTML='<span class="dot '+(on?"on":"")+'"></span> Music DreamView '+(e?on?"on":"off":"unavailable");
  if(button){button.textContent=on?"Turn off":"Turn on";button.disabled=!e;}
  const panel=this.shadowRoot.querySelector(".live-summary");
  if(!panel)return;
  const states=this.ha?.states||{};
  const movie=states[this.config.movie_entity]?.state==="on",music=on;
  const effect=states[this.config.effect_entity]?.state||"Unknown effect";
  const palette=states[this.config.palette_entity]?.state||"";
  const colours=palette.split(",").map(c=>c.trim().toUpperCase());
  const valid=colours.length>=1&&colours.length<=8&&colours.every(c=>/^#[0-9A-F]{6}$/.test(c));
  const lights=[["Hexagons","light.wall_hexagons"],["Floor lamp","light.floor_lamp_2"],["TV backlight","light.tv_backlight"],["Living room light","light.living_room_light"]];
  const lamps=lights.map(([name,id])=>'<span>'+esc(name)+": "+esc(states[id]?.state||"unknown")+'</span>').join("");
  let title,detail,note,bar="";
  if(music){
   title=movie?"Music + Movie both reported active":"Music DreamView active";
   detail="Selected effect: "+effect;
   note="Palette below is the last Home Assistant-commanded selection, NOT a measured reading of the LEDs.";
   if(valid)bar='<div class="live-palette" aria-label="Last selected Music DreamView colours">'+colours.map(c=>'<span style="background:'+c+'"></span>').join("")+'</div>';
  }else if(movie){
   title="Movie DreamView active";
   detail="TV camera / video synchronisation";
   note="Movie colours change with the picture. This integration does not report real-time colour frames; the Music palette below is a draft, not the current movie output.";
  }else{
   title="DreamView inactive";
   detail="No active music or movie synchronisation";
   note="The editor below shows saved Music settings, not colours currently displayed.";
  }
  const html='<div class="live-top"><span class="live-title">'+esc(title)+'</span><span class="muted">Reported current mode</span></div>'+
    '<div class="muted">'+esc(detail)+'</div>'+bar+'<div class="live-foot">'+esc(note)+'</div>'+
    '<div class="live-lights">'+lamps+'</div>';
  if(panel.innerHTML!==html)panel.innerHTML=html;
 }
 async toggle(){
  const e=this.ha?.states?.[this.config.dreamview_entity];if(!e){this.notice("DreamView switch unavailable","error");return;}
  try{await this.ha.callService("switch",e.state==="on"?"turn_off":"turn_on",{entity_id:this.config.dreamview_entity});this.notice("DreamView on/off command sent","success");}
  catch(ex){this.notice("DreamView switch failed: "+ex.message,"error");}
 }
 async apply(){
  if(this.busy)return;
  if(!this.ha){this.notice("Home Assistant connection unavailable","error");return;}
  this.busy=true;this.render();
  try{
   const frames=[...colorFrames(this.s.colors),effectFrame(this.effect(),this.s.variant)];
   for(const frame of frames){
    await this.ha.callService("govee","send_raw_ptreal",{device_id:this.config.device_id,frame});
    await new Promise(resolve=>setTimeout(resolve,200));
   }
   this.notice("Three commands sent (two colour frames + effect). Check the actual lights to verify the colours.","success");
  }catch(err){this.notice("Could not apply palette: "+err.message,"error");}
  finally{this.busy=false;this.render();}
 }
 addFavorite(){
  const el=this.shadowRoot.querySelector(".fav-name"),name=(el.value.trim().slice(0,50)||"Palette "+(this.s.favorites.length+1));
  this.s.favorites.unshift({id:Date.now(),name,settings:{...this.s.settings},seed:this.s.seed,colors:[...this.s.colors],effect:this.s.effect,variant:this.s.variant});
  this.s.favorites=this.s.favorites.slice(0,30);this.persist();this.render();this.notice("Saved "+name+" in this browser","success");
 }
 loadFavorite(id){
  const item=this.s.favorites.find(f=>String(f.id)===id);if(!item)return;
  this.s.settings={...item.settings};this.s.colors=[...item.colors];this.s.count=this.s.colors.length;this.s.seed=item.seed;this.s.effect=item.effect;this.s.variant=item.variant;this.s.preset="Custom";this.s.locked={};
  this.persist();this.render();this.notice("Loaded "+item.name);
 }
 move(i,j){
  if(j<0||j>=this.s.count)return;
  const x=this.s;[x.colors[i],x.colors[j]]=[x.colors[j],x.colors[i]];
  const a=x.locked[i],b=x.locked[j];delete x.locked[i];delete x.locked[j];if(a)x.locked[j]=a;if(b)x.locked[i]=b;
  this.persist();this.render();
 }
 render(){
  if(!this.config)return;
  const x=this.s,p=x.settings,e=this.effect();
  const schemeButtons=Object.entries(SCHEMES).map(([id,label])=>'<button class="choice '+(p.scheme===id?"active":"")+'" data-scheme="'+id+'">'+esc(label)+'</button>').join("");
  const sliders=[["h","Base hue","°",0,359],["c","Vibrancy","%",10,100],["l","Lightness","%",25,90],["contrast","Contrast","%",0,100],["spread","Colour spread","%",0,100]].map(([id,name,unit,min,max])=>'<div class="slider"><label for="r-'+id+'">'+name+'</label><span class="out-'+id+'">'+p[id]+unit+'</span><input id="r-'+id+'" data-range="'+id+'" type="range" min="'+min+'" max="'+max+'" value="'+p[id]+'"></div>').join("");
  const swatches=x.colors.map((c,i)=>'<div class="swatch"><div class="paint" style="background:'+c+'"><input type="color" title="Edit colour '+(i+1)+'" aria-label="Edit colour '+(i+1)+'" value="'+c+'" data-color="'+i+'"></div><div class="swatchbottom"><span class="hex">'+c+'</span><button class="iconbtn" title="'+(x.locked[i]?"Unlock":"Lock")+' colour" data-lock="'+i+'">'+(x.locked[i]?"🔒":"◇")+'</button></div><div class="arrows"><button data-move="'+i+':-1" '+(i===0?"disabled":"")+' title="Move left">←</button><button data-move="'+i+':1" '+(i===x.count-1?"disabled":"")+' title="Move right">→</button></div></div>').join("");
  const saved=x.favorites.slice(0,20).map(f=>'<span class="saveditem"><button title="Load palette" data-load="'+f.id+'">'+esc(f.name)+'</button><button class="remove" title="Delete palette" data-delete="'+f.id+'">×</button></span>').join("");
  const effectOptions=["Lighting Show","Carnival"].map(group=>'<optgroup label="'+group+'">'+EFFECTS.filter(v=>v.category===group).map(v=>'<option '+(v.name===e.name?"selected":"")+' value="'+esc(v.name)+'">'+esc(v.name)+'</option>').join("")+'</optgroup>').join("");
  this.shadowRoot.innerHTML='<style>'+CSS+'</style><ha-card><div class="header"><div class="top"><div><div class="kicker">Govee · Music DreamView</div><h2>Palette Studio</h2><div class="muted">Perceptual colour harmony · eight output slots · local generation</div></div><div class="chip dream-state"><span class="dot"></span> Loading</div></div></div>'+
   '<div class="live-summary" role="status" aria-live="polite"></div><div class="columns"><section class="left"><h3>01 / Design</h3><div class="section"><label class="label" for="preset">Start with a mood</label><select id="preset">'+options([["Custom","Custom"],...Object.keys(PRESETS).map(v=>[v,v])],x.preset)+'</select></div>'+
   '<div class="section"><div class="label">Harmony</div><div class="choices">'+schemeButtons+'</div></div>'+
   '<div class="section"><div class="headrow"><label class="label" for="count">Distinct colours</label><span class="muted">2–8</span></div><select id="count">'+options([2,3,4,5,6,7,8].map(v=>[v,v+" colours"]),x.count)+'</select><div class="muted" style="margin-top:7px">Colours repeat across eight hardware slots if fewer than eight are chosen.</div></div>'+
   '<div class="section"><div class="label">Fine tune</div>'+sliders+'</div></section>'+
   '<section class="right"><div class="headrow"><h3>02 / Your palette</h3><span class="muted">'+x.count+' distinct · 8 output slots</span></div><div class="ribbon" aria-label="Eight colour preview">'+slots8(x.colors).map(c=>'<span title="'+c+'" style="background:'+c+'"></span>').join("")+'</div>'+
   '<div class="muted">Tap to edit · ◇ locks colours for shuffling · arrows reorder.</div><div class="swatches">'+swatches+'</div>'+
   '<div class="buttons"><button class="btn primary" data-action="shuffle">↻ Shuffle unlocked</button><button class="btn" data-action="copy">Copy HEX</button><button class="btn" data-action="frames">Copy commands</button></div>'+
   '<hr class="rule"><div class="label">Save favourites</div><div class="save"><input class="fav-name" maxlength="50" type="text" placeholder="Give this palette a name"><button class="btn" data-action="save">Save</button></div><div class="saved">'+(saved||'<span class="muted">No saved palettes yet</span>')+'</div>'+
   '<hr class="rule"><h3>03 / Apply to DreamView</h3><div class="label">Effect</div><div class="effects"><select id="effect">'+effectOptions+'</select><select id="variant" '+(!e.dual?"disabled":"")+'>'+options([["Dynamic","Dynamic"],["Soft","Soft"]],x.variant)+'</select></div><div class="muted" style="margin-top:7px">'+(e.dual?"Both Dynamic and Soft are available.":"This effect has no Dynamic/Soft option.")+'</div>'+
   '<div class="buttons"><button class="btn primary" data-action="apply" '+(this.busy?"disabled":"")+'>'+(this.busy?"Sending…":"Apply palette + effect")+'</button></div><div class="notice '+esc(x.status)+'" role="status" aria-live="polite">'+esc(x.notice)+'</div></section></div>'+
   '<div class="footer"><div><div class="kicker">DreamView master control</div><div class="muted">Separate from the lamp’s normal power</div></div><button class="btn toggle" data-action="toggle" disabled>Turn on</button></div></ha-card>';
  const root=this.shadowRoot;this.refresh();
  root.querySelector("#preset").onchange=ev=>{const v=ev.target.value;if(PRESETS[v]){x.settings={...PRESETS[v]};x.preset=v;x.locked={};x.seed=(Math.random()*4294967295)>>>0;this.renew();}};
  root.querySelector("#count").onchange=ev=>{x.count=Number(ev.target.value);x.locked=Object.fromEntries(Object.entries(x.locked).filter(([k])=>Number(k)<x.count));this.renew();};
  root.querySelector("#effect").onchange=ev=>{x.effect=ev.target.value;if(!this.effect().dual)x.variant="Dynamic";this.persist();this.render();};
  root.querySelector("#variant").onchange=ev=>{x.variant=ev.target.value;this.persist();};
  root.querySelectorAll("[data-scheme]").forEach(el=>el.onclick=()=>{p.scheme=el.dataset.scheme;x.preset="Custom";this.renew();});
  root.querySelectorAll("[data-range]").forEach(el=>{
   el.oninput=ev=>root.querySelector(".out-"+ev.target.dataset.range).textContent=ev.target.value+(ev.target.dataset.range==="h"?"°":"%");
   el.onchange=ev=>{p[ev.target.dataset.range]=Number(ev.target.value);x.preset="Custom";this.renew();};
  });
  root.querySelectorAll("[data-color]").forEach(el=>el.onchange=ev=>{const i=Number(el.dataset.color);x.colors[i]=ev.target.value.toUpperCase();x.locked[i]=x.colors[i];this.persist();this.render();});
  root.querySelectorAll("[data-lock]").forEach(el=>el.onclick=()=>{const i=Number(el.dataset.lock);if(x.locked[i])delete x.locked[i];else x.locked[i]=x.colors[i];this.persist();this.render();});
  root.querySelectorAll("[data-move]").forEach(el=>el.onclick=()=>{const [i,d]=el.dataset.move.split(":").map(Number);this.move(i,i+d);});
  root.querySelectorAll("[data-load]").forEach(el=>el.onclick=()=>this.loadFavorite(el.dataset.load));
  root.querySelectorAll("[data-delete]").forEach(el=>el.onclick=()=>{x.favorites=x.favorites.filter(f=>String(f.id)!==el.dataset.delete);this.persist();this.render();});
  root.querySelectorAll("[data-action]").forEach(el=>el.onclick=()=>{
   const act=el.dataset.action;
   if(act==="shuffle"){x.seed=(Math.random()*4294967295)>>>0;this.renew();}
   if(act==="copy"||act==="frames"){
    const text=act==="copy"?x.colors.join(", "):colorFrames(x.colors).join("\n");
    if(navigator.clipboard?.writeText)navigator.clipboard.writeText(text).then(()=>this.notice("Copied","success")).catch(()=>this.notice("Clipboard blocked","error"));
    else this.notice("Clipboard unavailable","error");
   }
   if(act==="save")this.addFavorite();
   if(act==="apply")this.apply();
   if(act==="toggle")this.toggle();
  });
 }
}
if(!customElements.get("govee-palette-studio"))customElements.define("govee-palette-studio",Studio);
window.customCards=window.customCards||[];
if(!window.customCards.some(x=>x.type==="govee-palette-studio"))window.customCards.push({type:"govee-palette-studio",name:"Govee Palette Studio",description:"Eight-colour perceptual palette designer for Music DreamView"});
})();