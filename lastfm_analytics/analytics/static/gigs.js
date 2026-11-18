const $=id=>document.getElementById(id);
const csrf=document.querySelector('meta[name="csrf-token"]').content;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let gigs=[];
async function load(){
 const response=await fetch('api/gigs');if(!response.ok)throw Error('Could not load gigs');
 gigs=(await response.json()).gigs;
 $('count').textContent=gigs.length+' attended performances';
 $('rows').innerHTML=gigs.map(g=>'<tr><td>'+esc(g.date)+'</td><td>'+esc(g.artist)+'</td><td>'+esc(g.venue)+'</td><td>'+'<select class="gig-type" data-id="'+g.id+'"><option value="unknown" '+(g.show_type==='unknown'?'selected':'')+'>Unclassified</option><option value="concert" '+(g.show_type==='concert'?'selected':'')+'>Concert</option><option value="festival" '+(g.show_type==='festival'?'selected':'')+'>Festival</option></select>'+(g.festival?'<small>'+esc(g.festival)+'</small>':g.show_type==='festival'&&!g.type_override?'<small>Inferred festival</small>':'')+'</td><td>'+(g.setlist_url?'<a target="_blank" rel="noopener noreferrer" href="'+esc(g.setlist_url)+'">Source</a>':g.setlist_id?'Linked':'Not linked')+(g.sets?'<details><summary>Cached songs</summary><ol>'+g.sets.flatMap(set=>set.song||[]).map(song=>'<li>'+esc(song.name||'Unknown')+'</li>').join('')+'</ol></details>':'')+'</td><td>'+esc(g.last_checked||'Never')+'</td></tr>').join('');
 const names=[...new Set(gigs.map(g=>g.artist))].sort((a,b)=>a.localeCompare(b));
 $('artists').innerHTML=names.map(s=>'<option value="'+esc(s)+'">').join('');
 if($('artist').value)render();
}
function makeSvg(data){
 const counts=data.listens||[];
 const raw=data.gigs.filter(g=>g.show_type==='festival'?$('festivals').checked:g.show_type==='concert'?$('concerts').checked:($('concerts').checked||$('festivals').checked));
 const key=d=>d.slice(0,7);
 const dates=counts.map(d=>d.period).concat(raw.map(g=>g.date));
 if(!dates.length)return '<p>No matching listening or gig records.</p>';
 // Continuous time axis supports individual day precision for gig events.
 const time=d=>Date.parse(d.length===7?d+'-01':/^\d{4}-\d{2}$/.test(d)?d+'-01':d);
 const xs=dates.map(time).filter(Number.isFinite),min=Math.min(...xs),max=Math.max(...xs);
 const x=t=>44+((t-min)/(max-min||1))*820;
 const high=Math.max(1,...counts.map(d=>d.listens));
 const y=v=>226-(v/high)*190;
 const poly=counts.map(d=>x(time(d.period))+','+y(d.listens)).join(' ');
 const grid=[0,.25,.5,.75,1].map(v=>'<line x1="44" y1="'+y(v*high)+'" x2="865" y2="'+y(v*high)+'" stroke="currentColor" opacity=".12"/>').join('');
 const marks=raw.map(g=>{
  const xx=x(time(g.date)),festival=g.show_type==='festival',color=festival?'#8b5cf6':'#f59e0b';
  const shape=festival?'<path d="M'+xx+' 231 l-6 12 h12 Z" fill="'+color+'"/>':'<circle cx="'+xx+'" cy="236" r="5" fill="'+color+'"/>';
  return '<g class="timeline-marker"><title>'+esc(g.date+' · '+g.artist+' · '+(g.festival||g.venue))+'</title><line x1="'+xx+'" y1="24" x2="'+xx+'" y2="226" stroke="'+color+'" stroke-dasharray="4 4" opacity=".65"/>'+shape+'</g>';
 }).join('');
 return '<svg viewBox="0 0 900 278" role="img" aria-label="Listening counts with concert and festival date markers">'+grid+'<polyline points="'+poly+'" fill="none" stroke="#14b8a6" stroke-width="2.5"/>'+marks+'<text x="45" y="269" fill="currentColor" font-size="13">'+esc(new Date(min).toISOString().slice(0,10))+'</text><text x="864" y="269" text-anchor="end" fill="currentColor" font-size="13">'+esc(new Date(max).toISOString().slice(0,10))+'</text></svg>';
}
async function render(){
 const artist=$('artist').value.trim();if(!artist)return;
 $('timeline').textContent='Loading…';
 try{
 const q=new URLSearchParams({artist,granularity:$('scale').value});
 const response=await fetch('api/gigs/timeline?'+q);
 if(!response.ok)throw Error('Unable to load timeline');
 const data=await response.json();
 $('timeline').innerHTML=makeSvg(data)+'<div class="legend"><span>🟢 Listening</span><span>🟠 Standalone gigs</span><span>🟣 Festivals</span></div><p class="muted">'+data.gigs.length+' gigs. Setlist contents may expire according to add-on cache settings.</p>';
 }catch(e){$('timeline').textContent=e.message}
}
async function loadAttendanceStatus(){
 try{
  const response=await fetch('api/gigs/attendance-status');
  if(!response.ok)throw Error('Status unavailable');
  const s=await response.json();
  $('attendance-status').textContent=s.configured
   ? s.imported+' gigs imported · '+s.requests_today+'/'+s.daily_budget+' API requests used today · '+(s.last_completed?'Last full attendance scan: '+s.last_completed:'Import in progress, next page '+s.next_page+(s.total?' of approximately '+Math.ceil(s.total/20):''))+(s.last_error?' · '+s.last_error:'')
   : 'Configure setlistfm_user_id and setlistfm_api_key in the add-on settings to begin automatic import.';
 }catch(e){$('attendance-status').textContent=e.message}
}
async function classifyGig(id,type){
 const gig=gigs.find(g=>g.id===id);
 let festival=type==='festival'?(gig?.festival||''):'';
 if(type==='festival'&&!festival) festival=prompt('Festival name (optional):','')||'';
 const response=await fetch('api/gigs/classify',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({id,type,festival})});
 if(!response.ok)throw Error('Could not save classification');
 await load();
}
$('rows').addEventListener('change',event=>{
 if(!event.target.matches('.gig-type'))return;
 classifyGig(Number(event.target.dataset.id),event.target.value).catch(e=>alert(e.message));
});
['artist','scale','concerts','festivals'].forEach(id=>$(id).addEventListener('change',render));
$('artist').value=new URLSearchParams(location.search).get('artist')||'';
load().catch(e=>$('count').textContent=e.message);
loadAttendanceStatus();
