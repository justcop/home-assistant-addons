'use strict';
const content = document.querySelector('#content');
const modal = document.querySelector('#modal');
const modalContent = document.querySelector('#modal-content');
let statusInfo = {}, shelfView = 'artists', searchKind = 'artist', currentAlbum = null, routeGeneration = 0, toastTimer;
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const id = value => encodeURIComponent(value);
const year = album => escapeHtml(album.release_date?.slice(0,4) || 'Date unknown');
const artistNames = album => escapeHtml((album.artists || []).map(a => a.name).join(', '));
const duration = ms => ms ? `${Math.floor(ms/60000)}:${String(Math.floor(ms%60000/1000)).padStart(2,'0')}` : '';
const loading = message => `<div class="loading">${escapeHtml(message)}</div>`;
function toast(message) {const el=document.querySelector('#toast');el.textContent=message;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),6500);}
async function api(path, method='GET', body={}) {
  const options = {method,credentials:'same-origin'};
  if(method!=='GET'){options.headers={'Content-Type':'application/json','X-AudioShelf-Request':'1'};options.body=JSON.stringify(body);}
  let response;
  try {response=await fetch(new URL('api/'+path,document.baseURI),options);} catch {throw new Error('AudioShelf is offline. Check your connection and the add-on.');}
  if(!response.ok){const error=await response.json().catch(()=>({error:'The request failed.'}));throw new Error(error.error);}
  if(response.headers.get('Content-Type')?.includes('application/json')) return response.json();
  return response;
}
function cover(album,large=false){return `<a class="cover-wrap" href="#album/${id(album.id)}" aria-label="Open ${escapeHtml(album.title)}"><img class="cover" src="https://coverartarchive.org/release-group/${id(album.id)}/front-500" alt="${escapeHtml(album.title)} album cover" ${large?'':'loading="lazy"'}></a>`;}
function cards(albums){return `<div class="album-grid">${albums.map(a=>`<article class="album-card">${cover(a)}${a.on_shelf?'<span class="badge">On your shelf</span>':''}<a class="album-title" href="#album/${id(a.id)}">${escapeHtml(a.title)}</a><div class="album-meta">${artistNames(a)}<br>${year(a)}</div>${a.on_shelf?'':`<button class="card-action" data-action="add" data-id="${escapeHtml(a.id)}">+ Add to shelf</button>`}</article>`).join('')}</div>`;}
function empty(title,message){return `<div class="empty"><div class="vinyl" aria-hidden="true"></div><h2>${title}</h2><p>${message}</p><a class="primary" href="#store">Visit the record store ↗</a></div>`;}
function loginView(){return `<div class="eyebrow">Welcome back</div><h1>Open your shelf.</h1><p class="intro">Enter the web password set in your add-on configuration.</p><form id="login-form" class="search-form"><input type="password" name="password" autocomplete="current-password" aria-label="AudioShelf password" required><button class="primary">Open AudioShelf</button></form>`;}
function trackRows(album){return `<div class="tracklist">${album.tracks.map(t=>`<div class="track-row"><span class="track-number">${t.position}</span><span>${escapeHtml(t.title)}<br><small class="${t.verified?'mapped':'unmapped'}">${t.verified?(t.method==='manual'?'Manually mapped':'Mapped to Spotify'):(t.spotify_id?'Check this recording':'Not mapped yet')}</small></span><span class="duration">${duration(t.duration_ms)}</span><button class="quiet" data-action="track" data-position="${t.position}" aria-label="Correct Spotify mapping for ${escapeHtml(t.title)}">Edit</button></div>`).join('')}</div>`;}
function albumPage(album){
  currentAlbum=album;
  return `<a class="back" href="#artist/${id(album.artists[0]?.id || '')}">← ${artistNames(album)}</a><div class="album-hero">${cover(album,true)}<div><div class="eyebrow">${year(album)} · Studio album</div><h1>${escapeHtml(album.title)}</h1><p class="muted">${artistNames(album)} · ${album.tracks.length} tracks</p><div class="actions">${album.on_shelf?`<button class="primary" data-action="play">▶ Play album</button>`:`<button class="primary" data-action="add" data-id="${escapeHtml(album.id)}">+ Add to shelf</button>`}<button class="secondary" data-action="resolve">${album.playable?'Find another edition':'Match Spotify tracks'}</button></div><button class="quiet" data-action="album-settings">Album settings ↗</button></div></div>${!album.playable?'<div class="note">Match the tracks to Spotify before playing. AudioShelf sends only this tracklist, in this order.</div>':`<div class="note">Ready to play ${album.tracks.length} mapped tracks, without the extras. Open Spotify on your chosen device first.</div>`}${!album.canonical_reviewed?`<div class="note">Original tracklist selected from MusicBrainz: ${escapeHtml(album.release_label)}. If this includes bonus tracks or misses a track, choose another original edition in Album settings. <button class="quiet" data-action="review">This tracklist is correct ✓</button></div>`:''}${trackRows(album)}<p class="footer-note">Catalogue and artwork: <a href="https://musicbrainz.org/release-group/${id(album.id)}" target="_blank" rel="noopener">MusicBrainz / Cover Art Archive ↗</a>${album.spotify_album_id?` · Playback: <a href="https://open.spotify.com/album/${id(album.spotify_album_id)}" target="_blank" rel="noopener">Spotify ↗</a>`:''}</p>`;
}
function settingsPage(){return `<div class="eyebrow">Make yourself at home</div><h1>Settings.</h1><section class="settings-block"><h2>Spotify</h2><p>${statusInfo.spotify_connected?'Your Spotify account is connected. Playback uses the currently active device.':'Connect your Spotify Premium account to match and play albums.'}</p>${!statusInfo.spotify_configured?'<div class="note">In Home Assistant, open AudioShelf configuration and set <code>spotify_client_id</code> and <code>spotify_redirect_uri</code>. Use your HTTPS address followed by <code>/auth/spotify/callback</code>, and add the exact same redirect URL to your Spotify developer app. No client secret is needed.</div>':''}<div class="actions"><button class="primary" data-action="connect">${statusInfo.spotify_connected?'Reconnect Spotify':'Connect Spotify'}</button>${statusInfo.spotify_connected?'<button class="quiet" data-action="disconnect">Disconnect</button>':''}<button class="quiet" data-action="refresh-status">Refresh connection status</button></div><p class="muted">Catalogue market: ${escapeHtml(statusInfo.market || 'GB')}. Turn Autoplay off in Spotify if you want silence when the album finishes. AudioShelf switches Shuffle and Repeat off before starting an album.</p></section><section class="settings-block"><h2>Your collection</h2><p>Your shelf belongs to AudioShelf. Adding or removing a record here does not change your saved Spotify albums.</p><div class="actions"><a class="secondary" href="api/export" download>Export collection</a><button class="secondary" data-action="backup">Download database backup</button></div><p class="muted">Library: <code>${escapeHtml(statusInfo.data_directory)}</code>. Back up this folder as well as your add-on data.</p></section><section class="settings-block"><h2>About AudioShelf</h2><p>Albums, in their original order. No singles, compilation appearances or anniversary clutter.</p><p class="muted">Version ${escapeHtml(statusInfo.build?.version)} · ${escapeHtml(statusInfo.build?.channel)} · ${escapeHtml(statusInfo.build?.revision)}</p>${statusInfo.password_required?'<button class="quiet" data-action="logout">Lock AudioShelf</button>':''}<p class="footer-note">Install AudioShelf from your phone browser using Add to Home Screen at your standalone HTTPS address.</p></section>`;}
async function route(){
  const generation=++routeGeneration;
  const parts=(location.hash.slice(1)||'shelf').split('/');
  const [view,key,mode]=parts;
  const isStore=view==='store'||(view==='artist'&&mode==='store');
  document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===(isStore?'store':'shelf')));
  content.innerHTML=loading(view==='album'?'Finding the original album tracklist…':'Opening your collection…');
  try {
    statusInfo=await api('status');
    if(generation!==routeGeneration)return;
    if(!statusInfo.authenticated){content.innerHTML=loginView();return;}
    let html;
    if(view==='shelf'){
      const shelf=await api('shelf');
      html=`<div class="eyebrow">The collection</div><div class="shelf-heading"><h1>My shelf.</h1><span class="collection-mark">A little less noise.</span></div><p class="intro">The records you love, all in one place.</p><div class="toolbar"><div class="segmented"><button class="${shelfView==='artists'?'active':''}" data-action="shelf-view" data-view="artists">Artists</button><button class="${shelfView==='albums'?'active':''}" data-action="shelf-view" data-view="albums">Albums</button></div><span class="count">${shelf.albums.length} ${shelf.albums.length===1?'record':'records'} · ${shelf.artists.length} ${shelf.artists.length===1?'artist':'artists'}</span></div>`;
      if(!shelf.albums.length)html+=empty('Your first record awaits.','Find an artist, choose an album, make it yours.');
      else if(shelfView==='albums')html+=cards(shelf.albums);
      else html+=`<input class="filter" id="artist-filter" placeholder="Find an artist on your shelf" aria-label="Filter shelf artists"><div class="artist-list">${shelf.artists.map(a=>`<a class="artist-row" data-filter="${escapeHtml(a.name.toLowerCase())}" href="#artist/${id(a.id)}"><span class="artist-initial">${escapeHtml(a.name[0])}</span><span><span class="artist-name">${escapeHtml(a.name)}</span><span class="artist-note">${a.album_count} ${a.album_count===1?'record':'records'} on your shelf</span></span><span class="row-arrow" aria-hidden="true">↗</span></a>`).join('')}</div>`;
    }else if(view==='store'){
      const shelf=await api('shelf');
      html=`<div class="eyebrow">A proper record store</div><h1>Find your next record.</h1><p class="intro">Explore the studio albums. Collect what you love.</p><div class="segmented"><button class="${searchKind==='artist'?'active':''}" data-action="search-kind" data-kind="artist">Artists</button><button class="${searchKind==='album'?'active':''}" data-action="search-kind" data-kind="album">Albums</button></div><form id="search-form" class="search-form"><input name="q" type="search" placeholder="${searchKind==='artist'?'Search artists, e.g. Radiohead':'Search albums, e.g. In Rainbows'}" aria-label="Search record store" required><button class="primary">Search</button></form><p class="search-hint">Original releases, in chronological order. Singles, live albums and compilations stay outside.</p><div id="search-results">${shelf.artists.length?`<h2>More from your artists</h2>${shelf.artists.map(a=>`<a class="search-result" href="#artist/${id(a.id)}/store"><strong>${escapeHtml(a.name)}</strong><small>Browse their studio albums ↗</small></a>`).join('')}`:'<p class="muted">Start with an artist you love.</p>'}</div>`;
    }else if(view==='artist'){
      const result=await api(`artists/${id(key)}${mode==='store'?'?store=1':''}`);
      html=`<a class="back" href="#${mode==='store'?'store':'shelf'}">← ${mode==='store'?'Record store':'My shelf'}</a><div class="eyebrow">${mode==='store'?'Studio discography':'On your shelf'}</div><h1>${escapeHtml(result.artist.name)}</h1><div class="toolbar"><span class="count">${result.albums.length} records · oldest first</span><a class="secondary" href="#artist/${id(key)}${mode==='store'?'':'/store'}">${mode==='store'?'View my shelf':'Visit record store ↗'}</a></div>${result.albums.length?cards(result.albums):empty('There’s room for more.','Browse this artist’s record store to add their studio albums.')}`;
    }else if(view==='album'){
      const album=await api('albums/'+id(key));
      if(generation!==routeGeneration)return;
      html=albumPage(album);
    }
    else if(view==='settings')html=settingsPage();
    else {location.hash='shelf';return;}
    if(generation!==routeGeneration)return;
    content.innerHTML=html;
  }catch(error){if(generation===routeGeneration)content.innerHTML=`<div class="error-panel"><h2>Couldn’t open this page.</h2><p>${escapeHtml(error.message)}</p><button class="secondary" data-action="retry">Try again</button> <a class="quiet" href="#settings">Settings</a></div>`;}
}
function showModal(html){modalContent.innerHTML=html;if(!modal.open)modal.showModal();}
async function albumSettings(){
  const a=currentAlbum;
  showModal(`<h2>Album settings</h2><p><strong>${escapeHtml(a.title)}</strong><br>Original edition: ${escapeHtml(a.release_label)}</p><button class="secondary" data-action="releases">Change original tracklist</button><p>Changing the original edition clears all Spotify mappings for this album.</p><h3>Spotify playback edition</h3><p>Paste any Spotify edition. AudioShelf maps just the original tracks and ignores the extras.</p><form id="mapping-form"><input name="album" placeholder="https://open.spotify.com/album/…" aria-label="Spotify album link" required><button class="primary">Use this Spotify edition</button></form>${a.on_shelf?'<div class="actions"><button class="quiet" data-action="remove">Remove from my shelf</button></div>':''}`);
}
document.addEventListener('error',event=>{if(event.target instanceof HTMLImageElement)event.target.classList.add('failed');},true);
document.querySelector('.close-modal').addEventListener('click',()=>modal.close());
modal.addEventListener('click',event=>{if(event.target===modal)modal.close();});
window.addEventListener('hashchange',()=>{modal.close();route();window.scrollTo(0,0);});
window.addEventListener('focus',async()=>{if(statusInfo.authenticated){try{statusInfo=await api('status');if(location.hash==='#settings')content.innerHTML=settingsPage();}catch{}}});
document.addEventListener('input',event=>{if(event.target.id==='artist-filter')document.querySelectorAll('.artist-row').forEach(el=>el.hidden=!el.dataset.filter.includes(event.target.value.toLowerCase()));});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!['search-form','login-form','mapping-form','track-form'].includes(form.id))return;
  event.preventDefault();const button=form.querySelector('button');button.disabled=true;
  const generation=routeGeneration;
  try {
    const data=new FormData(form);
    if(form.id==='login-form'){await api('login','POST',{password:data.get('password')});await route();}
    if(form.id==='search-form'){
      const resultEl=document.querySelector('#search-results');
      resultEl.innerHTML=loading('Looking through the record store…');
      const result=await api(`search?q=${id(data.get('q'))}&kind=${searchKind}`);
      if(generation!==routeGeneration)return;
      resultEl.innerHTML=result.results.length?(searchKind==='album'?cards(result.results):result.results.map(a=>`<a class="search-result" href="#artist/${id(a.id)}/store"><strong>${escapeHtml(a.name)}</strong><small>${escapeHtml([a.disambiguation,a.country,a.type].filter(Boolean).join(' · '))}</small></a>`).join('')):'<p class="muted">No studio albums or artists found. Try a different spelling.</p>';
    }
    if(form.id==='mapping-form'){
      const result=await api(`albums/${id(currentAlbum.id)}/mapping`,'POST',{spotify_album_id:data.get('album')});
      if(generation!==routeGeneration)return;
      currentAlbum=result.album;modal.close();content.innerHTML=albumPage(currentAlbum);toast(`${result.candidate.verified} of ${currentAlbum.tracks.length} tracks verified. Review any remaining tracks using Edit.`);
    }
    if(form.id==='track-form'){
      const track=await api('spotify/track?id='+id(data.get('track')));
      if(generation!==routeGeneration)return;
      const position=form.dataset.position;
      if(!confirm(`Use “${track.name}” by ${track.artists.map(a=>a.name).join(', ')} (${duration(track.duration_ms)}) for canonical track ${position}?`))return;
      const album=await api(`albums/${id(currentAlbum.id)}/tracks/${position}`,'POST',{spotify_track_id:track.id,confirmed:true});
      if(generation!==routeGeneration)return;
      currentAlbum=album;
      modal.close();content.innerHTML=albumPage(currentAlbum);toast('Track mapping saved.');
    }
  }catch(error){toast(error.message);if(form.id==='search-form'){const target=document.querySelector('#search-results');if(target)target.innerHTML=`<p class="muted">${escapeHtml(error.message)}</p>`;}}
  finally{button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
  const action=button.dataset.action;button.disabled=true;
  const generation=routeGeneration;
  try {
    if(action==='retry'||action==='refresh-status')await route();
    if(action==='shelf-view'){shelfView=button.dataset.view;await route();}
    if(action==='search-kind'){searchKind=button.dataset.kind;await route();}
    if(action==='add'){
      const a=await api(`albums/${id(button.dataset.id)}/shelf`,'POST');toast(`${a.title} added to your shelf.`);
      if(generation!==routeGeneration)return;
      if(location.hash.startsWith('#album/'))content.innerHTML=albumPage(a);else{button.textContent='✓ On your shelf';button.dataset.action='';}
    }
    if(action==='play'){
      if(!statusInfo.spotify_connected)throw new Error('Connect Spotify in Settings first.');
      if(!currentAlbum.canonical_reviewed)throw new Error('Check the displayed original tracklist, then choose “This tracklist is correct” before first playback.');
      if(!currentAlbum.playable){toast('Matching Spotify tracks first…');const result=await api(`albums/${id(currentAlbum.id)}/resolve`,'POST');if(generation!==routeGeneration)return;currentAlbum=result.album;content.innerHTML=albumPage(currentAlbum);}
      const result=await api(`albums/${id(currentAlbum.id)}/play`,'POST');toast(`Playing ${result.track_count} tracks on ${result.device}.`);
    }
    if(action==='resolve'){
      if(!statusInfo.spotify_connected)throw new Error('Connect Spotify in Settings first.');
      toast('Finding the best Spotify recordings. This may take a moment…');
      const result=await api(`albums/${id(currentAlbum.id)}/resolve`,'POST');if(generation!==routeGeneration)return;currentAlbum=result.album;content.innerHTML=albumPage(currentAlbum);
      showModal(`<h2>Spotify editions</h2><p>Best match saved. Choose another if you prefer. Extra tracks are excluded from playback.</p>${result.candidates.map(c=>`<button class="choice" data-action="choose-mapping" data-id="${escapeHtml(c.id)}">${escapeHtml(c.name)}<small>${escapeHtml(c.release_date)} · ${c.track_count} tracks in Spotify · ${c.verified}/${currentAlbum.tracks.length} canonical tracks verified</small></button>`).join('')}`);
    }
    if(action==='choose-mapping'){const result=await api(`albums/${id(currentAlbum.id)}/mapping`,'POST',{spotify_album_id:button.dataset.id});if(generation!==routeGeneration)return;modal.close();content.innerHTML=albumPage(result.album);toast('Spotify edition saved.');}
    if(action==='album-settings')await albumSettings();
    if(action==='review'){await api(`albums/${id(currentAlbum.id)}/review`,'POST');if(generation!==routeGeneration)return;currentAlbum.canonical_reviewed=1;content.innerHTML=albumPage(currentAlbum);toast('Original tracklist confirmed.');}
    if(action==='releases'){
      showModal('<h2>Choose the original edition</h2>'+loading('Finding MusicBrainz editions…'));
      const result=await api(`albums/${id(currentAlbum.id)}/releases`);
      if(generation!==routeGeneration)return;
      modalContent.innerHTML=`<h2>Choose the original edition</h2><p>Prefer the original standard release, without bonus tracks. Your choice replaces the tracklist and clears Spotify mappings.</p>${result.releases.map(r=>`<button class="choice" data-action="choose-release" data-id="${escapeHtml(r.id)}">${escapeHtml(r.title)}<small>${escapeHtml([r.country,r.date,...(r.media||[]).map(m=>`${m.format||'Audio'}: ${m['track-count']||'?'} tracks`),r.disambiguation].filter(Boolean).join(' · '))}</small></button>`).join('')}`;
    }
    if(action==='choose-release'){
      if(!confirm('Replace the original tracklist with this edition and clear its Spotify mappings?'))return;
      const album=await api(`albums/${id(currentAlbum.id)}/release`,'POST',{release_id:button.dataset.id,confirmed:true});if(generation!==routeGeneration)return;currentAlbum=album;modal.close();content.innerHTML=albumPage(currentAlbum);toast('Original tracklist updated. Match Spotify tracks again.');
    }
    if(action==='track'){
      const track=currentAlbum.tracks.find(t=>t.position===Number(button.dataset.position));
      showModal(`<h2>Choose the recording</h2><p>Canonical track ${track.position}: <strong>${escapeHtml(track.title)}</strong> (${duration(track.duration_ms) || 'duration unknown'}).<br>Paste its Spotify track link. Check the recording in the confirmation before saving.</p><form id="track-form" data-position="${track.position}"><input name="track" placeholder="https://open.spotify.com/track/…" aria-label="Spotify track link" required><button class="primary">Preview and confirm</button></form>`);
    }
    if(action==='remove'){
      await api(`albums/${id(currentAlbum.id)}/shelf`,'DELETE');if(generation!==routeGeneration)return;currentAlbum.on_shelf=0;modal.close();content.innerHTML=albumPage(currentAlbum);toast('Removed from your shelf. The mapping is kept if you add it again.');
    }
    if(action==='connect'){
      const popup=window.open('about:blank','_blank');
      try{const result=await api('spotify/connect','POST');if(popup)popup.location.replace(result.url);else showModal(`<h2>Connect Spotify</h2><a class="primary" href="${escapeHtml(result.url)}" target="_blank" rel="noopener">Continue to Spotify</a>`);}catch(error){if(popup)popup.close();throw error;}
    }
    if(action==='disconnect'){await api('spotify/disconnect','POST');await route();toast('Spotify disconnected. Your shelf is unchanged.');}
    if(action==='logout'){await api('logout','POST');await route();}
    if(action==='backup'){
      const response=await api('backup','POST');const blob=await response.blob();const url=URL.createObjectURL(blob);
      const link=document.createElement('a');link.href=url;link.download='audioshelf-backup.db';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('Database backup downloaded.');
    }
  }catch(error){toast(error.message);}
  finally{button.disabled=false;}
});
route();
if('serviceWorker' in navigator && !document.baseURI.includes('/hassio_ingress/')) {
  navigator.serviceWorker.register(new URL('sw.js',document.baseURI)).catch(()=>{});
}
