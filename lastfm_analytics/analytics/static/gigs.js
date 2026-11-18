const $=id=>document.getElementById(id);
const csrf=document.querySelector('meta[name="csrf-token"]').content;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let gigs=[];

// A separate overview keeps whole-history counts independent of the selected
// artist's timeline and independently from any setlist.fm cache lifetime.
let allTimelineData=null;
let allTimelineRequest=0;
let allEventPage=0;
let allSelectedEvent=null;
const allPageSize=18;
function visibleAllEvents() {
 if(!allTimelineData)return [];
 return allTimelineData.events.filter(ev=>
   ev.type==="festival"?$('all-festivals').checked:
   ev.type==="concert"?$('all-concerts').checked:
   $('all-unclassified').checked);
}
function eventLabel(event) {
 return event.name + (event.start!==event.end?' · '+event.start+'–'+event.end:event.start?' · '+event.start:'');
}
function allTimelineSVG(data,events) {
 const bins=data.bins||[];
 if(!bins.length)return '<p class="muted">No listening or attended gigs in this period.</p>';
 const precision=data.scale;
 const step=precision==='year'?78:precision==='month'?27:precision==='week'?17:14;
 const width=Math.max(930,bins.length*step+110),left=54,right=width-30;
 const yTop=28,yBottom=258,yMarkers=293;
 const max=Math.max(1,...bins.map(b=>b.plays));
 const first=Date.parse(bins[0].start+'T00:00:00Z');
 const last=Date.parse(bins[bins.length-1].end+'T00:00:00Z')+86400000;
 const xTime=t=>left+((t-first)/(last-first||1))*(right-left);
 const xDay=d=>xTime(Date.parse(d+'T12:00:00Z'));
 const yPlays=v=>yBottom-(v/max)*(yBottom-yTop);
 const lines=[0,.25,.5,.75,1].map(f=>{
   const y=yPlays(max*f);
   return '<line x1="'+left+'" y1="'+y+'" x2="'+right+'" y2="'+y+
     '" stroke="currentColor" opacity=".13"/>'+
     '<text x="'+(left-5)+'" y="'+(y+4)+'" font-size="10" text-anchor="end" fill="currentColor">'+
     Math.round(max*f).toLocaleString()+'</text>';
 }).join('');
 const points=bins.map(b=>{
   const start=Date.parse(b.start+'T00:00:00Z');
   const finish=Date.parse(b.end+'T00:00:00Z')+86400000;
   return xTime((start+finish)/2)+','+yPlays(b.plays);
 }).join(' ');
 const dots=bins.map(b=>{
   const start=Date.parse(b.start+'T00:00:00Z');
   const finish=Date.parse(b.end+'T00:00:00Z')+86400000;
   return '<circle cx="'+xTime((start+finish)/2)+'" cy="'+yPlays(b.plays)+
     '" r="2.3" fill="#14b8a6"><title>'+esc(b.start+' to '+b.end+
     ': '+b.plays.toLocaleString()+' scrobbles')+'</title></circle>';
 }).join('');
 const every=Math.max(1,Math.ceil(bins.length/Math.min(14,Math.max(4,Math.floor(width/125)))));
 const labels=bins.filter((_,i)=>i%every===0||i===bins.length-1).map(b=>{
   const d=b.start;
   const label=precision==='year'?d.slice(0,4):precision==='month'?d.slice(0,7):d.slice(5);
   return '<text x="'+xDay(d)+'" y="336" fill="currentColor" opacity=".8" font-size="11" text-anchor="middle">'+
     esc(label)+'</text>';
 }).join('');
 const collisions=new Map();
 const markers=events.map((ev,index)=>{
   const x=xDay(ev.start);
   const column=Math.round(x/17);
   const height=collisions.get(column)||0;
   collisions.set(column,height+1);
   const y=yMarkers+Math.min(height,3)*10;
   const shape=ev.type==='festival'
     ? '<path d="M'+x+' '+(y-8)+' l-7 14 h14 Z" fill="#8b5cf6"/>'
     : ev.type==='concert'
       ? '<circle cx="'+x+'" cy="'+y+'" r="6" fill="#f59e0b"/>'
       : '<rect x="'+(x-5)+'" y="'+(y-5)+'" width="10" height="10" fill="#9ca3af"/>';
   const title=eventLabel(ev)+' · '+ev.artists.join(', ')+' ('+ev.performances+' performances)';
   return '<g tabindex="0" role="button" aria-label="'+esc('Show '+title)+
     '" class="gig-timeline-marker" data-overview-event="'+index+'">'+
     '<title>'+esc(title)+'</title>'+
     '<line x1="'+x+'" y1="'+yBottom+'" x2="'+x+'" y2="'+(y-11)+
     '" stroke="'+(ev.type==='festival'?'#8b5cf6':ev.type==='concert'?'#f59e0b':'#9ca3af')+
     '" stroke-width="1" opacity=".45"/>'+shape+'</g>';
 }).join('');
 return '<div class="gig-timeline-scroll" tabindex="0" aria-label="Scroll the listening timeline horizontally">'+
   '<svg xmlns="http://www.w3.org/2000/svg" width="'+width+'" height="355" viewBox="0 0 '+width+' 355" role="img" aria-label="'+
   esc('Listening history from '+data.start+' to '+data.end+', with '+events.length+' live events')+'">'+
   lines+'<polyline points="'+points+'" fill="none" stroke="#14b8a6" stroke-linejoin="round" stroke-width="2.5"/>'+
   dots+markers+labels+'</svg></div>';
}
function allEventCard(ev,index) {
 const kind=ev.type==='festival'?'Festival':ev.type==='concert'?'Concert':'Unclassified';
 const icon=ev.type==='festival'?'▲':ev.type==='concert'?'●':'■';
 const day=ev.start===ev.end?ev.start:ev.start+' – '+ev.end;
 return '<article class="gig-event-card'+(allSelectedEvent===ev.id?' selected':'')+
   '" id="live-event-'+ev.id+'"><small>'+esc(day)+' · '+icon+' '+kind+
   ' · '+ev.performances+' attended performance'+(ev.performances===1?'':'s')+'</small>'+
   '<h3>'+esc(ev.name)+'</h3><ol class="gig-event-acts">'+
   ev.gigs.map(g=>'<li><button class="gig-artist-link" data-artist="'+esc(g.artist)+
     '" title="View this artist’s listening history">'+esc(g.artist)+'</button> '+
     '<small>'+esc(g.date)+(g.url?' · <a href="'+esc(g.url)+
     '" target="_blank" rel="noopener noreferrer">Setlist.fm ↗</a>':'')+
     '</small></li>').join('')+'</ol></article>';
}
function renderAllEventList(events) {
 const target=$('all-event-details');
 if(!events.length){target.innerHTML='<p class="muted">No live events match these filters.</p>';return;}
 const pages=Math.ceil(events.length/allPageSize);
 allEventPage=Math.max(0,Math.min(allEventPage,pages-1));
 const slice=events.slice(allEventPage*allPageSize,(allEventPage+1)*allPageSize);
 target.innerHTML='<h3>Concerts and festivals ('+events.length+')</h3>'+
  '<p class="muted">Select a chart marker to open its event. Click an artist to see the artist-specific timeline.</p>'+
  '<div class="gig-event-pager"><span>Events '+(allEventPage*allPageSize+1)+'–'+
  Math.min(events.length,(allEventPage+1)*allPageSize)+' of '+events.length+'</span>'+
  '<div><button data-event-page="previous" '+(!allEventPage?'disabled':'')+
  '>Previous</button><button data-event-page="next" '+(allEventPage===pages-1?'disabled':'')+
  '>Next</button></div></div>'+
  '<div class="gig-event-list">'+slice.map(allEventCard).join('')+'</div>';
}
function renderAllTimeline(){
 if(!allTimelineData)return;
 const events=visibleAllEvents();
 $('all-timeline').innerHTML=
  '<div class="gig-summary"><div><strong>'+Number(allTimelineData.total).toLocaleString()+
  '</strong><small>Scrobbles in period</small></div><div><strong>'+
  Number(allTimelineData.performances).toLocaleString()+
  '</strong><small>Attended performances</small></div><div><strong>'+
  events.length.toLocaleString()+'</strong><small>Visible concert/festival events</small></div></div>'+
  allTimelineSVG(allTimelineData,events)+
  '<div class="gig-timeline-legend"><span style="color:#14b8a6">— Listening</span>'+
  '<span style="color:#f59e0b">● Concert</span>'+
  '<span style="color:#8b5cf6">▲ Festival</span>'+
  '<span style="color:#9ca3af">■ Unclassified</span></div>';
 renderAllEventList(events);
}
async function loadAllTimeline(){
 const serial=++allTimelineRequest;
 const scale=$('all-scale').value;
 let year=$('all-year').value;
 if((scale==='day'||scale==='week')&&!year){
   const options=[...$('all-year').options].map(o=>o.value).filter(Boolean);
   if(options.length) year=options[options.length-1];
   if(!year)year=String(new Date().getFullYear());
   $('all-year').value=year;
 }
 $('all-timeline').textContent='Loading combined listening and gig timeline…';
 $('all-event-details').innerHTML='';
 try {
   const q=new URLSearchParams({scale});
   if(year)q.set('year',year);
   const response=await fetch('api/gigs/all-timeline?'+q);
   if(!response.ok)throw Error('Could not load the combined timeline');
   const result=await response.json();
   if(serial!==allTimelineRequest)return;
   allTimelineData=result;allSelectedEvent=null;allEventPage=0;
   const select=$('all-year');
   const choices=[...select.options].map(o=>o.value).filter(Boolean);
   if(!choices.length||choices.join(',')!==result.years.map(String).join(',')){
      select.innerHTML='<option value="">All years</option>'+
         result.years.map(y=>'<option value="'+y+'">'+y+'</option>').join('');
   }
   select.value=result.year||'';
   renderAllTimeline();
 }catch(e){
   if(serial===allTimelineRequest){
     allTimelineData=null;
     $('all-timeline').textContent=e.message;
   }
 }
}
function activateOverviewEvent(index){
 const events=visibleAllEvents(),ev=events[index];
 if(!ev)return;
 allSelectedEvent=ev.id;
 allEventPage=Math.floor(index/allPageSize);
 renderAllEventList(events);
 const node=document.getElementById('live-event-'+ev.id);
 if(node){node.scrollIntoView({behavior:'smooth',block:'center'});node.focus?.();}
}
$('all-scale').addEventListener('change',loadAllTimeline);
$('all-year').addEventListener('change',loadAllTimeline);
['all-concerts','all-festivals','all-unclassified'].forEach(id=>
  $(id).addEventListener('change',()=>{allEventPage=0;allSelectedEvent=null;renderAllTimeline();}));
$('all-timeline').addEventListener('click',event=>{
 const marker=event.target.closest('[data-overview-event]');
 if(marker)activateOverviewEvent(Number(marker.dataset.overviewEvent));
});
$('all-timeline').addEventListener('keydown',event=>{
 const marker=event.target.closest('[data-overview-event]');
 if(marker&&(event.key==='Enter'||event.key===' ')){
    event.preventDefault();activateOverviewEvent(Number(marker.dataset.overviewEvent));
 }
});
$('all-event-details').addEventListener('click',event=>{
 const pager=event.target.closest('[data-event-page]');
 if(pager){
   allEventPage+=pager.dataset.eventPage==='next'?1:-1;
   renderAllEventList(visibleAllEvents());
 }
 const button=event.target.closest('[data-artist]');
 if(button){
   $('artist').value=button.dataset.artist;
   document.getElementById('artist').scrollIntoView({behavior:'smooth',block:'center'});
   render();
 }
});


function collectionHTML(data){
 const number=value=>Number(value||0).toLocaleString();
 const metric=(name,value)=>'<div><strong>'+number(value)+'</strong><small>'+esc(name)+'</small></div>';
 const top=(heading,rows,metricName)=>'<article><h3>'+heading+'</h3>'+
  (rows.length?'<ol>'+rows.map(song=>'<li>'+esc(song.title)+' <small>· '+esc(song.artist)+
  (song.cover?' (cover)':'')+'</small><small>'+number(song[metricName])+
  (metricName==='listens'?' scrobbles':' recorded gigs')+'</small></li>').join('')+'</ol>':
  '<p class="muted">No matching cached setlist data yet.</p>')+'</article>';
 return '<div class="gig-summary">'+[
  metric('Attended performances',data.performances),metric('Distinct artists seen',data.artists),
  metric('Festival performances',data.festival_performances),
  metric('Concert performances',data.concert_performances),
  metric('Cached setlists',data.cached_setlists),
  metric('Distinct recorded live songs',data.distinct_recorded_songs),
  metric('Recorded live songs also scrobbled',data.recorded_songs_listened_to)
 ].join('')+'</div><p class="muted">'+esc(data.coverage_note)+'</p>'+
 '<div class="gig-analytics-grid">'+
 top('Your most-listened songs recorded live',data.most_listened_recorded_live||[],'listens')+
 top('Songs recorded at the most gigs',data.most_performed_recorded_live||[],'performances')+'</div>';
}
async function loadCollection(){
 try{
  const response=await fetch('api/gigs/live-collection');
  if(!response.ok)throw Error('Live Collection unavailable');
  $('live-collection').innerHTML=collectionHTML(await response.json());
 }catch(e){$('live-collection').textContent=e.message;}
}

async function load(){
 const response=await fetch('api/gigs');if(!response.ok)throw Error('Could not load gigs');
 gigs=(await response.json()).gigs;
 $('count').textContent=gigs.length+' attended performances';
 $('rows').innerHTML=gigs.map(g=>'<tr><td>'+esc(g.date)+'</td><td><button class="gig-artist-link" data-artist="'+esc(g.artist)+'" title="Show artist listening and gigs">'+esc(g.artist)+'</button></td><td>'+esc(g.venue)+'</td><td>'+'<select class="gig-type" data-id="'+g.id+'"><option value="unknown" '+(g.show_type==='unknown'?'selected':'')+'>Unclassified</option><option value="concert" '+(g.show_type==='concert'?'selected':'')+'>Concert</option><option value="festival" '+(g.show_type==='festival'?'selected':'')+'>Festival</option></select>'+(g.festival?'<small>'+esc(g.festival)+'</small>':g.show_type==='festival'&&!g.type_override?'<small>Inferred festival</small>':'')+'</td><td>'+(g.setlist_url?'<a target="_blank" rel="noopener noreferrer" href="'+esc(g.setlist_url)+'">Source</a>':g.setlist_id?'Linked':'Not linked')+(g.sets?'<details><summary>Cached songs</summary><ol>'+g.sets.flatMap(set=>set.song||[]).map(song=>'<li>'+esc(song.name||'Unknown')+'</li>').join('')+'</ol></details>':'')+'</td><td>'+esc(g.last_checked||'Never')+'</td></tr>').join('');
 const names=[...new Set(gigs.map(g=>g.artist))].sort((a,b)=>a.localeCompare(b));
 $('artists').innerHTML=names.map(s=>'<option value="'+esc(s)+'">').join('');
 if($('artist').value)render();
 loadCollection();
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

let artistRenderSerial = 0;
function artistAnalyticsHTML(result) {
 const songs = result.songs || {};
 const impact = result.impact || [];
 const number = value => Number(value || 0).toLocaleString();
 const metric = (title, value) => '<div><strong>'+number(value)+'</strong><small>'+esc(title)+'</small></div>';
 const summary = '<div class="gig-summary">'+[
   metric('Attended performances',songs.performances),
   metric('Setlists currently cached',songs.cached_setlists),
   metric('Distinct songs in cached setlists',songs.cached_songs)
 ].join('')+'</div>';
 const asList = (rows, empty, live) => rows.length
   ? '<ol>'+rows.map(item=>'<li>'+esc(item.title)+
     '<small>'+number(item.listens)+' scrobbles'+
     (live ? ' · Recorded at '+number(item.performances)+' gig'+(item.performances===1?'':'s')+
       (item.cover?' · cover':'') : '')+
     '</small></li>').join('')+'</ol>'
   : '<p class="muted">'+esc(empty)+'</p>';
 const recorded = songs.live_songs || [];
 const absent = songs.not_in_cached_setlists || [];
 const songSection = '<div class="gig-analytics-grid"><article><h3>Live songs you listen to</h3>'+
   '<p class="muted">Songs recorded in currently cached setlists, ranked by your scrobbles.</p>'+
   asList(recorded.slice(0,25),'No cached live songs yet.',true)+
   '</article><article><h3>Popular songs not recorded in cached setlists</h3>'+
   '<p class="muted">Unknown, not “never heard live”. Setlists may be incomplete or expired.</p>'+
   asList(absent.slice(0,25),'No comparison data available.',false)+
   '</article></div>';
 const impactRows = impact.map(g=>{
   const gig = gigs.find(item=>item.id===g.id);
   const label = (gig?.festival || gig?.venue || 'Live performance');
   if(!g.available) return '<div class="gig-impact"><strong>'+esc(g.date)+' · '+esc(label)+
     '</strong><small class="muted">'+esc(g.reason || 'Not enough data')+'</small></div>';
   const change = g.share_change_pp;
   return '<div class="gig-impact"><strong>'+esc(g.date)+' · '+esc(label)+'</strong>'+
    '<span>'+number(g.artist_before)+' → '+number(g.artist_after)+' artist scrobbles</span> · '+
    '<span class="change">'+(change>=0?'+':'')+esc(change)+' percentage points</span>'+
    '<small class="muted" style="display:block">Share of all your listening: '+
      esc(g.share_before)+'% before → '+esc(g.share_after)+'% after ('+
      number(g.all_before)+' and '+number(g.all_after)+' total scrobbles)</small></div>';
 }).join('');
 return '<h2>Live music and listening</h2>'+summary+
   '<p class="muted">'+esc(songs.coverage_note||'')+'</p>'+
   songSection+'<h3>Listening before and after each gig</h3>'+
   '<p class="muted">30 complete days before versus 30 complete days after, excluding gig day. '+ 
   'The percentage-point change measures the artist’s share of all your scrobbles, not just raw listening volume. '+ 
   'These are associations, not evidence that the concert caused the change.</p>'+
   (impactRows || '<p class="muted">No recorded gigs for this artist.</p>');
}
async function render(){
 const artist=$('artist').value.trim();
 const serial=++artistRenderSerial;
 if(!artist) {
  $('timeline').textContent='Choose an artist to view listening and gigs together.';
  $('artist-analytics').innerHTML='';
  return;
 }
 $('timeline').textContent='Loading timeline…';
 $('artist-analytics').textContent='Analysing live songs and listening impact…';
 try {
  const q=new URLSearchParams({artist,granularity:$('scale').value});
  const [timelineResponse,insightsResponse]=await Promise.all([
    fetch('api/gigs/timeline?'+q),
    fetch('api/gigs/artist-insights?'+new URLSearchParams({artist}))
  ]);
  if(!timelineResponse.ok||!insightsResponse.ok)throw Error('Unable to load artist gig analytics');
  const [data, insights]=await Promise.all([timelineResponse.json(),insightsResponse.json()]);
  if(serial!==artistRenderSerial)return;
  $('timeline').innerHTML=makeSvg(data)+
   '<div class="legend"><span>🟢 Listening</span><span>🟠 Standalone gigs</span><span>🟣 Festivals</span></div>'+
   '<p class="muted">'+data.gigs.length+
   ' attended performances. Setlist contents expire according to add-on cache settings.</p>';
  $('artist-analytics').innerHTML=artistAnalyticsHTML(insights);
 }catch(e){
  if(serial===artistRenderSerial) {
   $('timeline').textContent='Could not load this artist.';
   $('artist-analytics').textContent=e.message;
  }
 }
}
async function loadAttendanceStatus(){
 try{
  const response=await fetch('api/gigs/attendance-status');
  if(!response.ok)throw Error('Status unavailable');
  const s=await response.json();
  $('attendance-status').textContent=s.configured
   ? s.imported+' gigs imported · '+(s.rate_limited ? 'setlist.fm rate limit reached · ' : '')+(s.last_completed?'Last full attendance scan: '+s.last_completed:'Import in progress, next page '+s.next_page+(s.total?' of approximately '+Math.ceil(s.total/20):''))+(s.last_error?' · '+s.last_error:'')
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
loadAllTimeline();
loadCollection();

$('rows').addEventListener('click',event=>{
 const button=event.target.closest('[data-artist]');
 if(!button)return;
 $('artist').value=button.dataset.artist;
 document.getElementById('artist').scrollIntoView({behavior:'smooth',block:'center'});
 render();
});
