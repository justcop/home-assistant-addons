'use strict';
// The two rooms share the catalogue, actions and permissions with Classic.
let albumOrigin='', storeSearch={kind:'artist',query:'',results:null}, shelfFilter='';
let playbackState=null, playbackBusy=false, playbackChecked=0, playbackEpoch=0, playbackCommand=0, playbackStart=null;
const browsingPositions=new Map();
if('scrollRestoration' in history)history.scrollRestoration='manual';
function isVinyl(){return document.documentElement.dataset.interface==='vinyl';}
function applyInterface(value){
  document.documentElement.dataset.interface=value==='classic'?'classic':'vinyl';
  renderTurntable();
}
function interfaceSettings(){return `<p>Choose how to browse your records. This choice is saved across your devices.</p><div class="interface-choices">${[['vinyl','Vinyl','Front-facing sleeves. A record store to explore, a shelf to come home to.'],['classic','Classic','The original AudioShelf interface, with artist lists and a compact album grid.']].map(([value,title,description])=>`<button class="interface-choice" data-action="interface" data-id="${value}" aria-pressed="${statusInfo.interface===value}"><span class="interface-preview ${value}" aria-hidden="true"><i></i><i></i><i></i></span><strong>${title}</strong><small>${description}</small></button>`).join('')}</div>`;}
function vinylHeading(kicker,title,description,detail=''){return `<header class="room-heading"><div><div class="eyebrow">${kicker}</div><h1>${title}</h1><p class="intro">${description}</p></div>${detail?`<div class="room-detail">${detail}</div>`:''}</header>`;}
function vinylCards(albums,room=document.documentElement.dataset.room){return `<div class="record-rack ${room==='store'?'store-rack':'shelf-rack'}">${albums.map(a=>`<article class="album-card sleeve" data-album="${escapeHtml(a.id)}">${cover(a)}<div class="sleeve-caption"><a class="album-title" href="#album/${id(a.id)}">${escapeHtml(a.title)}</a><div class="album-meta">${year(a)}</div></div>${room==='store'?`<div class="store-ticket"><span>${artistNames(a)}</span>${a.on_shelf?'<span class="owned-mark">✓ On your shelf</span>':`<button class="card-action" data-action="add" data-id="${escapeHtml(a.id)}" aria-label="Add ${escapeHtml(a.title)} to shelf">+ Add to shelf</button>`}</div>`:''}</article>`).join('')}</div>`;}
function artistDivider(artist,count,store=false){return `<div class="artist-divider"><a href="#artist/${id(artist.id)}${store?'/store':''}">${escapeHtml(artist.name)} <span aria-hidden="true">↗</span></a><span>${count} ${count===1?'record':'records'}</span></div>`;}
function vinylShelf(shelf){
  return vinylHeading('Your listening room','My shelf.','Something good deserves a whole side of your day.',`<strong>${shelf.albums.length}</strong><span>records collected</span>`)+
    (!shelf.albums.length?empty('Your first record awaits.','Find an artist, choose an album, make it yours.'):`<div class="room-toolbar"><label for="shelf-filter">Find a record</label><input id="shelf-filter" type="search" placeholder="Artist or album" value="${escapeHtml(shelfFilter)}"><a class="quiet" href="#store">Make room for another ↗</a></div><div class="collection-shelves">${shelf.artists.map(a=>{const albums=shelf.albums.filter(b=>b.artists.some(artist=>artist.id===a.id));return `<section class="artist-shelf" data-shelf-artist="${escapeHtml(a.name.toLowerCase())}">${artistDivider(a,albums.length)}${vinylCards(albums,'shelf')}</section>`;}).join('')}</div><p id="shelf-no-results" class="muted" hidden>No records match that search.</p>`);
}
function vinylStore(shelf){
  if(storeSearch.kind==='album'){const owned=new Set(shelf.albums.map(a=>a.id));for(const a of storeSearch.results||[])a.on_shelf=owned.has(a.id);}
  const query=storeSearch.kind===searchKind?storeSearch.query:'';
  return vinylHeading('The record store','Stay a little.\nFind a record.','Follow an artist. Pull out a sleeve. Find something worth keeping.','<span class="store-stamp">FULL ALBUMS<br>GOOD COMPANY<br><b>EST. YOUR TASTE</b></span>')+`<div class="store-search"><div class="segmented"><button class="${searchKind==='artist'?'active':''}" data-action="search-kind" data-kind="artist">Artists</button><button class="${searchKind==='album'?'active':''}" data-action="search-kind" data-kind="album">Albums</button></div><form id="search-form" class="search-form"><input name="q" type="search" value="${escapeHtml(query)}" placeholder="${searchKind==='artist'?'Which artist are you looking for?':'Find an album…'}" aria-label="Search record store" required><button class="primary">Search</button></form><p class="search-hint">Studio albums, original tracklists. The extras stay outside.</p></div><div id="search-results">${storeSearch.results!==null?vinylSearchResults():`<div class="section-label">Start with a familiar name <span>Browse the catalogues ↘</span></div>${shelf.artists.length?`<div class="artist-bins">${shelf.artists.map((a,index)=>`<a class="artist-bin search-result" href="#artist/${id(a.id)}/store"><span class="bin-index">${String(index+1).padStart(2,'0')}</span><strong>${escapeHtml(a.name)}</strong><small>Browse studio albums <span aria-hidden="true">↗</span></small></a>`).join('')}</div>`:'<p class="muted">Search for an artist you love to open their rack.</p>'}`}</div>`;
}
function vinylSearchResults(){
  const results=storeSearch.results||[];
  if(!results.length)return '<p class="muted">No studio albums or artists found. Try a different spelling.</p>';
  if(storeSearch.kind==='artist')return `<div class="artist-bins">${results.map((a,index)=>`<a class="artist-bin search-result" href="#artist/${id(a.id)}/store"><span class="bin-index">${String(index+1).padStart(2,'0')}</span><strong>${escapeHtml(a.name)}</strong><small>${escapeHtml([a.disambiguation,a.country,a.type].filter(Boolean).join(' · '))} ↗</small></a>`).join('')}</div>`;
  const groups=new Map();
  for(const album of results){const artist=album.artists?.[0]||{id:'',name:'Albums'};if(!groups.has(artist.id))groups.set(artist.id,{artist,albums:[]});groups.get(artist.id).albums.push(album);}
  return [...groups.values()].map(({artist,albums})=>`<section class="store-bin">${artistDivider(artist,albums.length,true)}${vinylCards(albums,'store')}</section>`).join('');
}
function vinylArtist(result,key,mode){
  const store=mode==='store';
  return `<a class="back" href="#${store?'store':'shelf'}">← ${store?'Record store':'My shelf'}</a>`+vinylHeading(store?'In the racks':'On your shelf',escapeHtml(result.artist.name),store?'Take your time. The albums are arranged from oldest to newest.':'Pull out a record. Settle in.',`<strong>${result.albums.length}</strong><span>records</span>`)+`<div class="room-toolbar"><span class="count">${store?'Studio catalogue':'Your collection'} · oldest first</span><a class="secondary" href="#artist/${id(key)}${store?'':'/store'}">${store?'View my shelf':'Visit record store ↗'}</a>${store?`<button class="quiet" data-action="catalogue" data-id="${escapeHtml(key)}">Manage catalogue</button>`:''}</div><section class="${store?'store-bin':'artist-shelf'}">${artistDivider(result.artist,result.albums.length,store)}${result.albums.length?vinylCards(result.albums,store?'store':'shelf'):empty('There’s room for more.','Browse the record store to add this artist’s albums.')}</section>`;
}
function vinylAlbum(album){
  const back=albumOrigin||`#${album.on_shelf?'shelf':'store'}`;
  const discCount=albumDiscs(album).length;
  const needsReview=!album.canonical_reviewed;
  const primary=album.on_shelf?'<button class="primary" data-action="play">▶ Play album</button>':`<button class="primary" data-action="add" data-id="${escapeHtml(album.id)}">+ Add to shelf</button>`;
  return `<a class="back" href="${escapeHtml(back)}">← Back to browsing</a><div class="listening-layout"><div class="listening-sleeve">${cover(album,true)}<div class="sleeve-footnote"><span>${year(album)}</span><span>${album.on_shelf?'FROM YOUR COLLECTION':'FROM THE RECORD STORE'}</span></div><div class="actions">${primary}<button class="quiet" data-action="album-settings">Album settings ↗</button></div><p class="listening-hint">${album.on_shelf?'The whole album. In its own time.':'A place on your shelf, whenever you’re ready.'}</p></div><section class="record-liner"><div class="eyebrow">${artistNames(album)}</div><h1>${escapeHtml(album.title)}</h1><p class="liner-summary">${album.tracks.length} tracks · ${duration(album.tracks.reduce((sum,t)=>sum+(t.duration_ms||0),0))}${discCount>1?` · ${discCount} discs`:''}</p>${needsReview?`<div class="tracklist-review"><strong>One check before the first listen</strong><p>Does this look like the original tracklist?</p><button class="secondary" data-action="review">This tracklist is correct ✓</button><button class="quiet" data-action="album-settings">Choose another tracklist</button></div>`:''}${trackRows(album)}<details class="record-details"><summary>Track matching & edition details</summary><p>${escapeHtml(album.release_label||'MusicBrainz tracklist')}${album.spotify_album_name?`<br>Spotify: ${escapeHtml(album.spotify_album_name)}`:''}</p><p>${album.playable?'All tracks are matched.':'Unmatched tracks will be matched to Spotify when you press Play.'}</p><button class="secondary" data-action="resolve">${album.playable?'Find another edition':'Match Spotify tracks'}</button><button class="quiet" data-action="album-settings">Cover, tracklist & diagnostics ↗</button></details><p class="footer-note">Catalogue and artwork: <a href="https://musicbrainz.org/release-group/${id(album.id)}" target="_blank" rel="noopener">MusicBrainz / Cover Art Archive ↗</a>${album.spotify_album_id?` · <a href="https://open.spotify.com/album/${id(album.spotify_album_id)}" target="_blank" rel="noopener">Spotify ↗</a>`:''}</p></section></div>`;
}
function saveBrowsing(hash){
  browsingPositions.set(hash||'#shelf',window.scrollY);
  const query=document.querySelector('#search-form input');if(query)storeSearch.query=query.value;
}
function restoreBrowsing(generation){
  filterShelf();
  requestAnimationFrame(()=>{if(generation===routeGeneration)window.scrollTo(0,browsingPositions.get(location.hash||'#shelf')||0);});
}
function filterShelf(){
  const term=shelfFilter.trim().toLocaleLowerCase();let visible=0;
  document.querySelectorAll('.artist-shelf[data-shelf-artist]').forEach(section=>{
    let count=0;
    section.querySelectorAll('.sleeve').forEach(card=>{card.hidden=!(section.dataset.shelfArtist+' '+card.querySelector('.album-title').textContent.toLocaleLowerCase()).includes(term);if(!card.hidden)count++;});
    section.hidden=count===0;visible+=count;
  });
  const empty=document.querySelector('#shelf-no-results');if(empty)empty.hidden=visible>0;
}
function markCollected(albumId,owned=true){for(const album of storeSearch.results||[])if(album.id===albumId)album.on_shelf=owned;}
document.addEventListener('input',event=>{if(event.target.id==='shelf-filter'){shelfFilter=event.target.value;filterShelf();}});
document.addEventListener('click',event=>{
  const link=event.target.closest('a[href^="#album/"]');
  if(link&&!location.hash.startsWith('#album/'))albumOrigin=location.hash||'#shelf';
},true);
function playbackTime(ms){return duration(ms)||'0:00';}
function trackProgress(p=playbackState){
  if(!p?.active||!Number.isFinite(p.duration_ms)||p.duration_ms<=0||!Number.isFinite(p.progress_ms))return null;
  const elapsed=p.playing&&!p.stale&&!p.starting?Math.max(0,performance.now()-(p.observedAt??performance.now())):0;
  return Math.min(p.duration_ms,Math.max(0,p.progress_ms+elapsed));
}
function progressHtml(p){
  if(trackProgress(p)===null)return '';
  return `<div class="track-progress"><span data-elapsed>0:00</span><progress max="${p.duration_ms}" value="0" aria-label="Song progress"></progress><span>${playbackTime(p.duration_ms)}</span></div>`;
}
function updateTrackProgress(){
  const progress=document.querySelector('#turntable progress'), position=trackProgress();
  if(!progress||position===null)return;
  progress.value=position;
  progress.setAttribute('aria-valuetext',`${playbackTime(position)} of ${playbackTime(playbackState.duration_ms)}`);
  document.querySelector('#turntable [data-elapsed]').textContent=playbackTime(position);
}
function beginPlayback(album,result){
  const first=result.first_track;
  // Invalidate reads started before this accepted Play command.
  playbackEpoch++;playbackBusy=false;
  playbackStart=first?{id:first.id,observedAt:performance.now(),expires:Date.now()+8000}:null;
  playbackState={active:true,playing:false,starting:true,album:album.title,album_id:album.id,
    artist:album.artists.map(a=>a.name).join(', '),track:first?.title||'Waiting for Spotify status',
    duration_ms:first?.duration_ms,progress_ms:0,observedAt:performance.now(),device:result.device};
  renderTurntable();refreshPlayback(true);
}
function renderTurntable(){
  const panel=document.querySelector('#turntable');if(!panel)return;
  panel.hidden=!isVinyl()||!statusInfo.authenticated;
  if(panel.hidden)return;
  const p=playbackState;
  if(!statusInfo.spotify_connected){panel.innerHTML='<span class="turntable-disc" aria-hidden="true"></span><div><small>ON THE TURNTABLE</small><strong>Ready when you are.</strong></div><a href="#settings" class="quiet">Connect Spotify ↗</a>';return;}
  if(!p?.active){panel.innerHTML=`<span class="turntable-disc" aria-hidden="true"></span><div><small>ON THE TURNTABLE</small><strong>${p?.unavailable?'Spotify status unavailable':'Pick a record. Press play.'}</strong><span>${p?.unavailable?'Open Spotify to check playback.':'Your next full-album listen starts here.'}</span></div>`;return;}
  const inner=`${p.album_id?`<img src="api/albums/${id(p.album_id)}/artwork?v=${artworkRevision}" alt="">`:'<span class="turntable-disc" aria-hidden="true"></span>'}<div><small>ON THE TURNTABLE <b>${p.stale?'STATUS UNAVAILABLE':p.starting?'STARTING':p.playing?'PLAYING':'PAUSED'}</b></small><strong>${escapeHtml(p.album||p.track)}</strong><span>${escapeHtml(p.track)} · ${escapeHtml(p.artist)}</span>${progressHtml(p)}</div>`;
  panel.innerHTML=`${p.album_id?`<a class="turntable-record" href="#album/${id(p.album_id)}">${inner}</a>`:`<div class="turntable-record">${inner}</div>`}<span class="turntable-device">${escapeHtml(p.device)}</span>`;
  updateTrackProgress();
}
async function refreshPlayback(force=false){
  const position=trackProgress();
  const interval=playbackStart?500:position!==null&&playbackState.playing&&position>=playbackState.duration_ms-500?1000:5000;
  if(!isVinyl()||!statusInfo.authenticated||!statusInfo.spotify_connected||document.hidden||(!force&&Date.now()-playbackChecked<interval))return;
  if(playbackBusy&&!force)return;
  const epoch=++playbackEpoch;playbackBusy=true;playbackChecked=Date.now();
  try{
    const state=await api('spotify/playback');
    if(epoch!==playbackEpoch)return;
    if(playbackStart&&Date.now()<playbackStart.expires){
      const freshPosition=!Number.isFinite(state.progress_ms)||state.progress_ms<=performance.now()-playbackStart.observedAt+2000;
      if(!state.playing||!state.track_ids?.includes(playbackStart.id)||!freshPosition)return;
    }
    playbackStart=null;playbackState={...state,observedAt:performance.now()};
  }catch{
    if(epoch===playbackEpoch){
      playbackStart=null;
      const position=trackProgress();
      playbackState=playbackState?.active?{...playbackState,progress_ms:position,stale:true,playing:false}:{unavailable:true};
    }
  }
  finally{if(epoch===playbackEpoch){playbackBusy=false;renderTurntable();}}
}
document.addEventListener('visibilitychange',()=>refreshPlayback(true));
setInterval(()=>{if(!document.hidden&&isVinyl()){updateTrackProgress();refreshPlayback();}},500);
