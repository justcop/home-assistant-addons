'use strict';
const content = document.querySelector('#content');
const modal = document.querySelector('#modal');
const modalContent = document.querySelector('#modal-content');
let statusInfo = {}, shelfView = 'artists', searchKind = 'artist', currentAlbum = null, routeGeneration = 0, toastTimer, artworkRevision = 0, releasePicker = null, settingsDirty = false;
const loadedAssets = document.documentElement.dataset.assetVersion;
let workerRegistration, pendingChanges = 0;
function noticeUpdate(build){
  if(build?.asset_version && build.asset_version!==loadedAssets)document.querySelector('#app-update').hidden=false;
}
function accountChanged(info){return !!statusInfo.account && (!info.authenticated||info.account?.id!==statusInfo.account.id);}
async function checkForUpdates(){
  if(document.hidden)return;
  try{const response=await fetch(new URL('api/status',document.baseURI),{credentials:'same-origin',cache:'no-store'});if(response.ok){const info=await response.json();noticeUpdate(info.build);if(accountChanged(info))window.location.reload();}}catch{}
  workerRegistration?.update().catch(()=>{});
}
function unsavedChanges(){
  return settingsDirty || modal.open || [...document.querySelectorAll('form input, form textarea, form select')].some(field=>{
    if(field.type==='checkbox'||field.type==='radio')return field.checked!==field.defaultChecked;
    if(field.tagName==='SELECT')return field.selectedIndex!==Math.max(0,[...field.options].findIndex(option=>option.defaultSelected));
    return field.value!==field.defaultValue;
  });
}
document.querySelector('#reload-update').addEventListener('click',()=>{
  if(pendingChanges){toast('Wait for the current action to finish before reloading.');return;}
  if(unsavedChanges()&&!window.confirm('Reloading will discard unsaved edits or close the open dialog. Reload now?'))return;
  window.location.reload();
});
window.addEventListener('focus',checkForUpdates);
document.addEventListener('visibilitychange',checkForUpdates);
setInterval(checkForUpdates,60000);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const id = value => encodeURIComponent(value);
const year = album => escapeHtml(album.release_date?.slice(0,4) || 'Date unknown');
const artistNames = album => escapeHtml((album.artists || []).map(a => a.name).join(', '));
const duration = ms => ms ? `${Math.floor(ms/60000)}:${String(Math.floor(ms%60000/1000)).padStart(2,'0')}` : '';
const loading = message => `<div class="loading">${escapeHtml(message)}</div>`;
function toast(message) {const el=document.querySelector('#toast');el.textContent=message;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),6500);}
async function api(path, method='GET', body={}) {
  const options = {method,credentials:'same-origin',cache:'no-store',headers:{}};
  if(statusInfo.account && !['login','status'].includes(path))options.headers['X-AudioShelf-Account']=statusInfo.account.id;
  if(method!=='GET'){Object.assign(options.headers,{'Content-Type':'application/json','X-AudioShelf-Request':'1'});options.body=JSON.stringify(body);}
  let response;
  if(method!=='GET')pendingChanges++;
  try {response=await fetch(new URL('api/'+path,document.baseURI),options);} catch {throw new Error('AudioShelf is offline. Check your connection and the add-on.');}
  finally{if(method!=='GET')pendingChanges--;}
  if(!response.ok){const error=await response.json().catch(()=>({error:'The request failed.'}));const failure=new Error(error.error);failure.status=response.status;if(error.error?.startsWith('The active account changed.'))window.location.reload();throw failure;}
  if(response.headers.get('Content-Type')?.includes('application/json')) {const result=await response.json();if(path==='status')noticeUpdate(result.build);return result;}
  return response;
}
function coverUrl(albumId,size){return `api/albums/${id(albumId)}/artwork?size=${size}&v=${artworkRevision}&account=${id(statusInfo.account?.id||'owner')}`;}
function cover(album,large=false){return `<a class="cover-wrap" href="#album/${id(album.id)}" aria-label="Open ${escapeHtml(album.title)}"><img class="cover" src="${coverUrl(album.id,large?640:320)}" srcset="${[128,320,640].map(size=>`${coverUrl(album.id,size)} ${size}w`).join(', ')}" sizes="${large?'(max-width: 700px) 85vw, 480px':'(max-width: 700px) 44vw, 260px'}" decoding="async" alt="${escapeHtml(album.title)} album cover" ${large?'fetchpriority="high"':'loading="lazy"'}></a>`;}

function cards(albums){if(isVinyl())return vinylCards(albums);return `<div class="album-grid">${albums.map(a=>`<article class="album-card">${cover(a)}${a.on_shelf?'<span class="badge">On your shelf</span>':''}<a class="album-title" href="#album/${id(a.id)}">${escapeHtml(a.title)}</a><div class="album-meta">${artistNames(a)}<br>${year(a)}</div>${a.on_shelf?'':`<button class="card-action" data-action="add" data-id="${escapeHtml(a.id)}">+ Add to shelf</button>`}</article>`).join('')}</div>`;}
function empty(title,message){return `<div class="empty"><div class="vinyl" aria-hidden="true"></div><h2>${title}</h2><p>${message}</p><a class="primary" href="#store">Visit the record store ↗</a></div>`;}
function loginView(){return `<div class="eyebrow">Welcome back</div><h1>Open your shelf.</h1>${!statusInfo.password_configured?'<div class="note">Set the owner web password in Home Assistant configuration, or open AudioShelf through Home Assistant to create accounts.</div>':`<p class="intro">Sign in to your own library and Spotify connection.</p><form id="login-form"><label for="login-username">Username</label><input id="login-username" name="username" autocomplete="username" value="owner" maxlength="32" required><label for="login-password">Password</label><input id="login-password" type="password" name="password" autocomplete="current-password" required><label for="login-code">Authenticator or recovery code (if enabled)</label><input id="login-code" name="code" autocomplete="one-time-code" placeholder="Leave blank if not required"><label class="check-option"><input type="checkbox" name="remember"> Trust this browser for 30 days</label><p class="muted">Each account has its own shelf and Spotify connection. Trusted browsers still require your password when the 12-hour session expires.</p><button class="primary">Open AudioShelf</button></form>`}${statusInfo.ingress_available?'<p><button class="secondary" data-action="ingress-login">Use Home Assistant owner account</button></p>':''}`;}
function albumDiscs(album){return [...new Set(album.tracks.map(t=>t.disc_number||1))];}
function trackRows(album){
  const discs=albumDiscs(album);
  return `<div class="tracklist">${discs.map(disc=>{
    const tracks=album.tracks.filter(t=>(t.disc_number||1)===disc);
    const heading=discs.length>1?`<div class="actions"><h2>Disc ${disc}</h2><span class="muted">${tracks.length} tracks · ${duration(tracks.reduce((sum,t)=>sum+(t.duration_ms||0),0))}</span>${album.on_shelf?`<button class="secondary" data-action="play" data-disc="${disc}">▶ Play disc ${disc}</button>`:''}</div>`:'';
    return heading+tracks.map(t=>`<div class="track-row"><span class="track-number">${t.position}</span><span>${escapeHtml(t.title)}<br><small class="${t.verified?'mapped':'unmapped'}">${t.verified?(t.method==='manual'?'Manually mapped':'Mapped to Spotify'):(t.spotify_id?'Check this recording':'Not mapped yet')}</small></span><span class="duration">${duration(t.duration_ms)}</span><button class="quiet" data-action="track" data-position="${t.position}" aria-label="Correct Spotify mapping for ${escapeHtml(t.title)}">Edit</button></div>`).join('');
  }).join('')}</div>`;
}
function albumPage(album){
  currentAlbum=album;
  if(isVinyl())return vinylAlbum(album);
  return `<a class="back" href="#artist/${id(album.artists[0]?.id || '')}">← ${artistNames(album)}</a><div class="album-hero">${cover(album,true)}<div><div class="eyebrow">${year(album)} · Studio album</div><h1>${escapeHtml(album.title)}</h1><p class="muted">${artistNames(album)} · ${album.tracks.length} tracks</p><div class="actions">${album.on_shelf?`<button class="primary" data-action="play">▶ Play album</button>`:`<button class="primary" data-action="add" data-id="${escapeHtml(album.id)}">+ Add to shelf</button>`}<button class="secondary" data-action="resolve">${album.playable?'Find another edition':'Match Spotify tracks'}</button></div><button class="quiet" data-action="playback-devices">Change device</button><button class="quiet" data-action="album-settings">Album settings ↗</button></div></div>${!album.playable?'<div class="note">Match the tracks to Spotify before playing. AudioShelf sends only this tracklist, in this order.</div>':`<div class="note">Ready to play ${album.tracks.length} mapped tracks, without the extras.${album.spotify_album_name?` Playback edition: ${escapeHtml(album.spotify_album_name)}.`:''} AudioShelf starts your synced tracklist on an available Spotify device.</div>`}${/\bCassette\b/i.test(album.release_label||'')&&!album.release_filters?.formats.includes('cassette')?'<div class="note">This saved tracklist uses a cassette edition outside your current preferences. Album settings lets you choose a vinyl, CD or digital edition. Your current Spotify matches are kept until you replace the tracklist.</div>':''}${!album.canonical_reviewed?`<div class="note">Original tracklist selected from MusicBrainz: ${escapeHtml(album.release_label)}. If this includes bonus tracks or misses a track, choose another original edition in Album settings. <button class="quiet" data-action="review">This tracklist is correct ✓</button></div>`:''}${trackRows(album)}<p class="footer-note">Catalogue and artwork: <a href="https://musicbrainz.org/release-group/${id(album.id)}" target="_blank" rel="noopener">MusicBrainz / Cover Art Archive ↗</a>${album.spotify_album_id?` · Playback: <a href="https://open.spotify.com/album/${id(album.spotify_album_id)}" target="_blank" rel="noopener">Spotify ↗</a>`:''}</p>`;
}
const themeIds=['record-store','midnight','paper','forest','ocean','sunset','plum','monochrome','amber','high-contrast'];
function applyTheme(value){
  const theme=themeIds.includes(value)?value:'record-store';
  document.documentElement.dataset.theme=theme;
  document.querySelector('meta[name="theme-color"]').content=getComputedStyle(document.documentElement).getPropertyValue('--paper').trim();
  try{localStorage.setItem('audioshelf-theme',theme);}catch{}
}
try{applyTheme(localStorage.getItem('audioshelf-theme'));}catch{applyTheme('record-store');}
function themeSettings(){return `<section class="settings-block"><h2>Appearance</h2>${interfaceSettings()}${shelfStyleSettings()}<h3>Colour palette</h3><p>Choose a theme. It is saved with your collection and used on your other devices.</p><div class="theme-grid">${(statusInfo.themes||[]).map(theme=>`<button class="theme-choice" data-theme="${escapeHtml(theme.id)}" data-action="theme" data-id="${escapeHtml(theme.id)}" aria-label="Use ${escapeHtml(theme.name)} theme" aria-pressed="${theme.id===statusInfo.theme}"><span class="theme-sample" aria-hidden="true"><span class="sample-record"></span><span class="sample-lines"><i></i><i></i><i></i></span></span><strong>${escapeHtml(theme.name)}</strong><small>${escapeHtml(theme.description)}</small></button>`).join('')}</div></section>`;}
function releaseFilterSettings(){
  const filters=statusInfo.release_filters || {countries:['GB','US','XW','XE'],formats:['vinyl','cd','digital'],strict_countries:false};
  const labels={vinyl:'Vinyl',cd:'CD',digital:'Digital',cassette:'Cassette',other:'Other audio formats'};
  const order=[...filters.formats,...Object.keys(labels).filter(f=>!filters.formats.includes(f))];
  return `<section class="settings-block"><h2>MusicBrainz releases</h2><p>Prefer original standard editions. Choose country and format priorities for tracklists and the edition picker.</p><form id="release-filters-form"><label for="release-countries">Country preference order</label><input id="release-countries" name="countries" value="${escapeHtml(filters.countries.join(', '))}" placeholder="GB, US, XW, XE" aria-describedby="country-help"><p id="country-help" class="muted">First choice first: GB is the UK, US is the USA, XW is worldwide and XE is Europe. Other countries are fallbacks unless restricted below. Leave blank for no country preference.</p><label class="check-option"><input type="checkbox" name="strict_countries" ${filters.strict_countries?'checked':''}> Only use these countries</label><fieldset class="format-options"><legend>Audio formats, in preference order</legend>${order.map(value=>`<div class="format-row" data-format-row><label class="check-option"><input type="checkbox" name="formats" value="${value}" ${filters.formats.includes(value)?'checked':''}> ${labels[value]}</label><button type="button" class="quiet" data-action="format-up" aria-label="Move ${labels[value]} earlier">↑</button><button type="button" class="quiet" data-action="format-down" aria-label="Move ${labels[value]} later">↓</button></div>`).join('')}</fieldset><p class="muted">Defaults: prefer GB, then US, worldwide and Europe; vinyl, CD and digital are enabled in that order. Earlier original-year editions beat later reissues within a country. Cassette is excluded. Saving keeps existing tracklists and Spotify mappings. To replace one, use Album settings.</p><button class="primary">Save release filters</button></form></section>`;
}
let spotifyDevices=[];
function browserPreferenceKey(name){return name+(statusInfo.account?.id && statusInfo.account.id!=='owner'?':'+statusInfo.account.id:'');}
function shouldOpenSpotify(){try{return localStorage.getItem(browserPreferenceKey('audioshelf-open-spotify'))==='true';}catch{return false;}}
function spotifyAppLink(){return 'spotify:';}
function androidHelperEnabled(){try{return /Android/i.test(navigator.userAgent)&&localStorage.getItem(browserPreferenceKey('audioshelf-android-helper'))==='true';}catch{return false;}}
function androidHelperLink(automatic=false,request=null){
  const active=request||pendingPlayback;
  if(!androidHelperEnabled()||!statusInfo.spotify_client_id||statusInfo.preferred_device?.type!=='Smartphone'||location.protocol!=='https:'||!active?.job||!active.helperToken)return null;
  const query=new URLSearchParams({client_id:statusInfo.spotify_client_id,origin:location.origin,return_url:location.href,account_id:statusInfo.account.id,job_id:active.job,helper_token:active.helperToken});
  return `intent://wake?${query}#Intent;scheme=audioshelf-helper;package=uk.co.justcop.audioshelf.helper;${automatic?'':`S.browser_fallback_url=${encodeURIComponent(location.href)};`}end`;
}
function wakePlaybackHelper(request){
  if(pendingPlayback!==request||request.generation!==routeGeneration||!request.job||request.helperLaunched)return false;
  const link=androidHelperLink(true,request);
  if(!link)return false;
  request.helperLaunched=true;
  // No browser fallback navigation: a blocked launch must preserve the live job
  // and its manual wake link rather than reload and lose the pending request.
  location.href=link;
  return true;
}
function playbackWakeLinks(request=null){
  const helper=androidHelperLink(false,request);
  return `${helper?`<a class="primary" data-action="open-playback-helper" href="${escapeHtml(helper)}">Wake Spotify and return</a>`:''}<a class="${helper?'secondary':'primary'}" data-action="open-playback-spotify" href="${spotifyAppLink()}">Open Spotify</a>`;
}
function androidHelperSettings(){
  return /Android/i.test(navigator.userAgent)?`<label class="check-option"><input type="checkbox" id="android-helper" ${androidHelperEnabled()?'checked':''}> Use the installed Android Spotify helper on this phone</label><p class="muted">Press Play to start music normally. If the selected phone is unavailable, AudioShelf automatically opens the helper to wake Spotify and return. Install and configure the helper first. Available devices play normally. This setting is saved only in this browser.</p>`:'';
}
let pendingPlayback=null, checkingPlayback=false;
function cancelPendingPlayback(){
  const request=pendingPlayback;pendingPlayback=null;
  if(request?.job)api(`spotify/playback-handoff/${id(request.job)}`,'DELETE').catch(()=>{});
  if(request&&!request.chooseOnly)failPlaybackStart();
}
function playbackSettings(){
  const preferred=statusInfo.preferred_device;
  return `<section class="settings-block"><h2>Playback device</h2><p>Preferred device: ${preferred?escapeHtml(preferred.name):'Choose a device before playback'}. This choice is saved for this account across your devices.</p><button class="secondary" data-action="devices">Choose Spotify device</button><label class="check-option"><input type="checkbox" id="show-skip-controls" ${statusInfo.show_skip_controls?'checked':''}> Show previous and next buttons in Now Playing</label><p class="muted">Off by default for album-first listening. Tap the spinning record to pause or resume.</p><label class="check-option"><input type="checkbox" id="open-spotify" ${shouldOpenSpotify()?'checked':''}> Open Spotify after pressing Play on this browser</label><p class="muted">Opening Spotify does not change your selected playback device. If your device is unavailable, open Spotify and stay there while AudioShelf waits for it. Your browser may ask permission to open the app.</p>${androidHelperSettings()}</section>`;
}
function devicePicker(){
  showModal(`<h2>Choose playback device</h2><p>Choose where your synced tracklist should play. Open Spotify if your device is missing.</p><button class="choice" data-action="device" data-index="-1">Clear preferred device</button>${spotifyDevices.map((d,index)=>`<button class="choice" data-action="device" data-index="${index}" ${d.is_restricted?'disabled':''}>${escapeHtml(d.name)}<small>${escapeHtml(d.type)}${d.is_active?' · Active':''}${d.is_restricted?' · Cannot be controlled':''}</small></button>`).join('')}${spotifyDevices.length?'':'<p>No devices are available yet.</p>'}<div class="actions"><a class="secondary" href="spotify:">Open Spotify</a><button class="secondary" data-action="devices">Refresh devices</button></div>`);
}
async function playbackDevicePicker(request){
  pendingPlayback=request;
  if(request.job){await api(`spotify/playback-handoff/${id(request.job)}`,'DELETE');request.job=null;failPlaybackStart();}
  request.until=null;
  const result=await api('spotify/devices');
  if(pendingPlayback!==request||request.generation!==routeGeneration)return;
  spotifyDevices=result.devices;
  showModal(`<h2>Choose playback device</h2><p>${statusInfo.preferred_device?`Chosen device: ${escapeHtml(statusInfo.preferred_device.name)}.`:'Choose where you want to listen. AudioShelf remembers your choice.'} If your phone is missing, open Spotify on this phone, then return here. Playback waits for your choice.</p>${spotifyDevices.map((d,index)=>`<button class="choice" data-action="playback-device" data-index="${index}" ${d.is_restricted?'disabled':''}>${escapeHtml(d.name)}<small>${escapeHtml(d.type)}${d.is_active?' · Active':''}</small></button>`).join('')||'<p>No devices are available yet.</p>'}<div class="actions"><a class="primary" data-action="open-playback-spotify" href="${spotifyAppLink()}">Open Spotify</a><button class="secondary" data-action="playback-devices">Refresh devices</button></div>`);
}
async function playbackHandoff(request,message){
  pendingPlayback=request;
  let job;
  try{job=await api(`albums/${id(request.album.id)}/playback-handoff`,'POST',request.disc===null?{}:{disc_number:request.disc});}
  catch(error){if(pendingPlayback===request){pendingPlayback=null;failPlaybackStart();}throw error;}
  request.job=job.id;
  request.helperToken=job.helper_token;
  if(pendingPlayback!==request||request.generation!==routeGeneration){await api(`spotify/playback-handoff/${id(job.id)}`,'DELETE');return;}
  showModal(`<h2>Waiting for ${escapeHtml(statusInfo.preferred_device.name)}</h2><p>${escapeHtml(message)}</p><div class="actions">${playbackWakeLinks()}<button class="secondary" data-action="play" ${request.disc===null?'':`data-disc="${request.disc}"`}>Retry playback</button><button class="secondary" data-action="playback-devices">Change device</button></div><p>${androidHelperLink()?'The helper wakes Spotify and returns here automatically. If it did not open, tap “Wake Spotify and return”.':'You can stay in Spotify.'} AudioShelf waits up to one minute and sends this synced ${request.disc===null?'album':'disc'} to your chosen device. Closing this dialog cancels the request.</p>`);
  wakePlaybackHelper(request);
  retryPendingPlayback();
}
async function startPlayback(request,openApp=true){
  cancelPendingPlayback();
  const playbackRequest=++playbackCommand;
  const first=request.album.tracks.find(t=>request.disc===null||(t.disc_number||1)===request.disc);
  beginPlayback(request.album,{first_track:{id:first.spotify_id,title:first.title,duration_ms:first.duration_ms},device:statusInfo.preferred_device?.name},true);
  let result;
  try{result=await api(`albums/${id(request.album.id)}/play`,'POST',request.disc===null?{}:{disc_number:request.disc});}
  catch(error){if(playbackRequest===playbackCommand&&error.status!==409&&error.status!==404)failPlaybackStart();throw error;}
  if(playbackRequest===playbackCommand)beginPlayback(request.album,result);
  if(request.generation!==routeGeneration)return;
  pendingPlayback=null;modal.close();toast(`Playing ${result.track_count} tracks on ${result.device}.`);
  if(openApp&&shouldOpenSpotify()){
    showModal(`<h2>Playing on ${escapeHtml(result.device)}</h2><p>${result.track_count} synced tracks started.</p><a class="primary" href="${spotifyAppLink()}">Open Spotify</a><button class="secondary" data-action="playback-devices">Change device</button>`);
    location.href=spotifyAppLink();
  }
}
async function retryPendingPlayback(){
  const request=pendingPlayback;
  if(!request||checkingPlayback||document.hidden)return;
  if(request.generation!==routeGeneration||!modal.open){cancelPendingPlayback();return;}
  checkingPlayback=true;
  try{
    if(!request.job){
      if(request.until){request.until=null;await playbackDevicePicker(request);}
      return;
    }
    const job=await api(`spotify/playback-handoff/${id(request.job)}`);
    if(pendingPlayback!==request)return;
    if(job.state==='waiting')return;
    pendingPlayback=null;
    if(job.state==='started'){
      beginPlayback(request.album,job.result);modal.close();toast(`Playing ${job.result.track_count} tracks on ${job.result.device}.`);
    }else{
      failPlaybackStart();
      showModal(`<h2>${job.state==='unconfirmed'?'Spotify playback not confirmed':'Spotify did not start'}</h2><p>${escapeHtml(job.error||'Playback request cancelled.')}</p><div class="actions"><a class="secondary" href="${spotifyAppLink()}">Open Spotify</a><button class="primary" data-action="play" ${request.disc===null?'':`data-disc="${request.disc}"`}>Retry playback</button><button class="secondary" data-action="playback-devices">Change device</button></div>`);
    }
  }catch(error){if(pendingPlayback===request){cancelPendingPlayback();toast(error.message);}}
  finally{checkingPlayback=false;}
}
window.addEventListener('focus',retryPendingPlayback);
document.addEventListener('visibilitychange',retryPendingPlayback);
setInterval(retryPendingPlayback,2000);
modal.addEventListener('close',cancelPendingPlayback);
document.addEventListener('change',async event=>{
  if(event.target.id==='show-skip-controls'){
    const box=event.target,enabled=box.checked;box.disabled=true;
    statusInfo.show_skip_controls=enabled;renderTurntable();
    try{
      const updated=await api('settings','PUT',{show_skip_controls:enabled});
      statusInfo.show_skip_controls=updated.show_skip_controls;
      renderTurntable();toast('Playback controls updated.');
    }catch(error){box.checked=!enabled;statusInfo.show_skip_controls=!enabled;renderTurntable();toast(error.message);}
    finally{box.disabled=statusInfo.role==='view';}
  }
});
document.addEventListener('change',event=>{if(event.target.id==='android-helper'){try{localStorage.setItem(browserPreferenceKey('audioshelf-android-helper'),String(event.target.checked));}catch{event.target.checked=false;toast('This browser could not save the preference.');}}});
document.addEventListener('change',event=>{if(event.target.id==='open-spotify'){try{localStorage.setItem(browserPreferenceKey('audioshelf-open-spotify'),String(event.target.checked));}catch{event.target.checked=false;toast('This browser could not save the preference.');}}});
let securityInfo=null;
function reauthFields(){return securityInfo?.ingress?'<p>Authorised by your Home Assistant login.</p>':`<label>Confirm your password<input type="password" name="password" autocomplete="current-password" required></label>${securityInfo?.two_factor?'<label>Fresh authenticator or recovery code<input name="code" autocomplete="one-time-code" required></label>':''}`;}
async function securitySettings(){
  securityInfo=await api('security');
  const info=securityInfo;
  showModal(`<h2>Security</h2><p>Standalone password: ${info.password_configured?'configured':'not configured, standalone access locked'}. Sessions expire after 12 hours. Trusted browsers: ${info.trusted_devices}.</p>${statusInfo.account?.id!=='owner'?`<h3>Change your password</h3><form data-security-form data-operation="password">${reauthFields()}<label>New password<input type="password" name="new_password" autocomplete="new-password" minlength="12" required></label><button class="secondary">Change password</button></form>`:''}<h3>Two-factor authentication</h3><p>${info.two_factor?'Enabled.':'Not enabled.'} The owner can also use Home Assistant authentication through ingress.</p><form data-security-form data-operation="${info.two_factor?'totp/disable':'totp/start'}">${reauthFields()}<button class="secondary">${info.two_factor?'Disable two-factor authentication':'Set up authenticator'}</button></form><h3>Trusted browsers and sessions</h3><form data-security-form data-operation="revoke-sessions">${reauthFields()}<button class="secondary">Revoke other sessions and trusted browsers</button></form><h3>Temporary support access</h3>${info.support_enabled?`<form data-security-form data-operation="support">${reauthFields()}<label>Permission<select name="role"><option value="view">View only</option><option value="control">Allow changes and playback</option></select></label><label>Expires after<select name="hours"><option value="1">1 hour</option><option value="2">2 hours</option><option value="4">4 hours</option><option value="8">8 hours</option></select></label><button class="secondary">Create temporary login</button></form>`:'<p>Enable “Allow temporary support access” in Home Assistant add-on configuration to create temporary logins.</p>'}${info.grants.map(grant=>`<div class="settings-block"><p>${escapeHtml(grant.id)} · ${grant.role==='view'?'View only':'Changes and playback'} · Expires ${escapeHtml(new Date(grant.expires*1000).toLocaleString())} · ${grant.revoked?'Revoked':grant.expires*1000<Date.now()?'Expired':'Active'}</p>${!grant.revoked&&grant.expires*1000>Date.now()?`<form data-security-form data-operation="support/${id(grant.id)}/revoke">${reauthFields()}<button class="quiet">Revoke this login</button></form>`:''}</div>`).join('')}<h3>Recent security activity</h3>${info.events.map(e=>`<p class="muted">${escapeHtml(new Date(e.created*1000).toLocaleString())} · ${escapeHtml(e.event)} ${escapeHtml(e.details)}</p>`).join('')}`);
}
async function accountSettings(){
  securityInfo=await api('security');
  const result=await api('accounts');
  showModal(`<h2>AudioShelf accounts</h2><p>Every account has a separate library, Spotify connection, artwork and settings. Disabling an account revokes access and keeps its collection.</p><h3>Create account</h3><form data-account-form data-operation="create">${reauthFields()}<label>Username<input name="username" autocomplete="off" maxlength="32" pattern="[a-zA-Z0-9][a-zA-Z0-9_.\\-]{0,31}" required></label><label>New account password<input type="password" name="new_password" autocomplete="new-password" minlength="12" required></label><button class="primary">Create account</button></form>${result.accounts.map(account=>`<section class="settings-block"><h3>${escapeHtml(account.username)}</h3>${account.id==='owner'?'<p>The existing library and Spotify connection belong to owner. Its password is set in Home Assistant configuration.</p>':`<p>${account.disabled?'Disabled':'Enabled'}</p><form data-account-form data-operation="status" data-id="${escapeHtml(account.id)}" data-disabled="${account.disabled?'false':'true'}">${reauthFields()}<button class="secondary">${account.disabled?'Enable':'Disable'} account</button></form><form data-account-form data-operation="reset" data-id="${escapeHtml(account.id)}">${reauthFields()}<label>Replacement password<input type="password" name="new_password" autocomplete="new-password" minlength="12" required></label><label class="check-option"><input type="checkbox" name="reset_two_factor"> Also reset this account’s authenticator and recovery codes</label><p class="muted">Changing the password signs this account out on every device.</p><button class="secondary">Reset password</button></form>`}</section>`).join('')}`);
}
function accountSection(){return `<section class="settings-block"><h2>Your account</h2><p>Signed in as <strong>${escapeHtml(statusInfo.account?.username||'owner')}</strong>. Your shelf, Spotify connection and preferences belong to this account.</p><div class="actions"><button class="secondary" data-action="logout">Switch account / Sign out</button>${statusInfo.account?.admin?'<button class="secondary" data-action="accounts">Manage accounts</button>':''}</div></section>`;}
function settingsPage(){return `<div class="eyebrow">Make yourself at home</div><h1>Settings.</h1>${accountSection()}<section class="settings-block"><h2>Spotify</h2><p>${statusInfo.spotify_connected?'Your Spotify account is connected. Playback uses your chosen device. Unavailable devices are never replaced automatically.':'Connect your Spotify Premium account to match and play albums.'}</p>${!statusInfo.spotify_configured?'<div class="note">In Home Assistant, open AudioShelf configuration and set <code>spotify_client_id</code> and <code>spotify_redirect_uri</code>. Use your HTTPS address followed by <code>/auth/spotify/callback</code>, and add the exact same redirect URL to your Spotify developer app. No client secret is needed.</div>':''}<div class="actions">${statusInfo.role==='owner'?`<button class="primary" data-action="connect">${statusInfo.spotify_connected?'Reconnect Spotify':'Connect Spotify'}</button>${statusInfo.spotify_connected?'<button class="quiet" data-action="disconnect">Disconnect</button>':''}`:''}<button class="quiet" data-action="refresh-status">Refresh connection status</button></div><p class="muted">Catalogue market: ${escapeHtml(statusInfo.market || 'GB')}. Turn Autoplay off in Spotify if you want silence when the album finishes. AudioShelf switches Shuffle and Repeat off before starting an album.</p></section>${statusInfo.role==='owner'?'<section class="settings-block"><h2>Security</h2><p>Manage two-factor authentication, trusted browsers and temporary support logins.</p><button class="secondary" data-action="security">Open Security settings</button></section>':'<div class="note">Temporary support login. Access is limited and expires automatically.</div>'}${playbackSettings()}${themeSettings()}${releaseFilterSettings()}<section class="settings-block"><h2>Your collection</h2><p>Your shelf belongs to AudioShelf. Adding or removing a record here does not change your saved Spotify albums.</p>${statusInfo.role==='owner'?'<div class="actions"><a class="secondary" href="api/export" download>Export collection</a><button class="secondary" data-action="backup">Download database backup</button></div>':''}<p class="muted">Library: <code>${escapeHtml(statusInfo.data_directory)}</code>. Back up this folder as well as your add-on data.</p><p class="muted">Replaceable artwork and metadata cache: <code>${escapeHtml(statusInfo.cache_directory)}</code>. You can exclude this separate folder from backups. It rebuilds automatically.</p></section><section class="settings-block"><h2>About AudioShelf</h2><p>Albums, in their original order. No singles, compilation appearances or anniversary clutter.</p><p class="muted">Version ${escapeHtml(statusInfo.build?.version)} · ${escapeHtml(statusInfo.build?.channel)} · ${escapeHtml(statusInfo.build?.revision)}</p><p class="footer-note">Install AudioShelf from your phone browser using Add to Home Screen at your standalone HTTPS address.</p></section>`;}
async function route(){
  cancelPendingPlayback();
  const generation=++routeGeneration;
  const parts=(location.hash.slice(1)||'shelf').split('/');
  const [view,key,mode]=parts;
  const isStore=view==='store'||(view==='artist'&&mode==='store');
  document.documentElement.dataset.room=isStore?'store':view==='album'&&albumOrigin.includes('store')?'store':'shelf';
  document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===document.documentElement.dataset.room));
  content.innerHTML=loading(view==='album'?'Finding the original album tracklist…':'Opening your collection…');
  try {
    const latest=await api('status');if(accountChanged(latest)){window.location.reload();return;}statusInfo=latest;if(statusInfo.authenticated){applyTheme(statusInfo.theme);applyInterface(statusInfo.interface);}renderTurntable();
    if(generation!==routeGeneration)return;
    document.querySelector('#account-name').textContent=statusInfo.authenticated?statusInfo.account?.username||'owner':'';
    if(!statusInfo.authenticated){content.innerHTML=loginView();return;}
    let html;
    if(view==='shelf'){
      if(key)shelfFilter='';
      if(key&&!isVinyl())shelfView='artists';
      const shelf=await api('shelf');
      html=`<div class="eyebrow">The collection</div><div class="shelf-heading"><h1>My shelf.</h1><span class="collection-mark">A little less noise.</span></div><p class="intro">The records you love, all in one place.</p><div class="toolbar"><div class="segmented"><button class="${shelfView==='artists'?'active':''}" data-action="shelf-view" data-view="artists">Artists</button><button class="${shelfView==='albums'?'active':''}" data-action="shelf-view" data-view="albums">Albums</button></div><span class="count">${shelf.albums.length} ${shelf.albums.length===1?'record':'records'} · ${shelf.artists.length} ${shelf.artists.length===1?'artist':'artists'}</span></div>`;
      if(!shelf.albums.length)html+=empty('Your first record awaits.','Find an artist, choose an album, make it yours.');
      else if(shelfView==='albums')html+=cards(shelf.albums);
      else html+=`<input class="filter" id="artist-filter" placeholder="Find an artist on your shelf" aria-label="Filter shelf artists"><div class="artist-list">${shelf.artists.map(a=>`<a class="artist-row" data-shelf-id="${escapeHtml(a.id)}" data-filter="${escapeHtml(a.name.toLowerCase())}" href="#artist/${id(a.id)}"><span class="artist-initial">${escapeHtml(a.name[0])}</span><span><span class="artist-name">${escapeHtml(a.name)}</span><span class="artist-note">${a.album_count} ${a.album_count===1?'record':'records'} on your shelf</span></span><span class="row-arrow" aria-hidden="true">↗</span></a>`).join('')}</div>`;
      if(isVinyl())html=vinylShelf(shelf);
    }else if(view==='store'){
      if(['artist','album'].includes(key)&&mode){
        searchKind=key;
        storeSearch={kind:key,query:decodeURIComponent(mode),results:null};
        const result=await api(`search?q=${id(storeSearch.query)}&kind=${key}`);
        if(generation!==routeGeneration)return;
        storeSearch.results=result.results;
      }
      const shelf=await api('shelf');
      html=`<div class="eyebrow">A proper record store</div><h1>Find your next record.</h1><p class="intro">Explore the studio albums. Collect what you love.</p><div class="segmented"><button class="${searchKind==='artist'?'active':''}" data-action="search-kind" data-kind="artist">Artists</button><button class="${searchKind==='album'?'active':''}" data-action="search-kind" data-kind="album">Albums</button></div><form id="search-form" class="search-form"><input name="q" type="search" placeholder="${searchKind==='artist'?'Search artists':'Search albums'}" aria-label="Search record store" required><button class="primary">Search</button></form><p class="search-hint">Original releases, in chronological order. Singles, live albums and compilations stay outside.</p><div id="search-results">${shelf.artists.length?`<h2>More from your artists</h2>${shelf.artists.map(a=>`<a class="search-result" href="#artist/${id(a.id)}/store"><strong>${escapeHtml(a.name)}</strong><small>Browse their studio albums ↗</small></a>`).join('')}`:'<p class="muted">Start with an artist you love.</p>'}</div>`;
      if(isVinyl())html=vinylStore(shelf);
    }else if(view==='artist'){
      const result=await api(`artists/${id(key)}${mode==='store'?'?store=1':''}`);
      html=`<a class="back" href="#${mode==='store'?'store':'shelf'}">← ${mode==='store'?'Record store':'My shelf'}</a><div class="eyebrow">${mode==='store'?'Studio discography':'On your shelf'}</div><h1>${escapeHtml(result.artist.name)}</h1><div class="toolbar"><span class="count">${result.albums.length} records · oldest first</span><a class="secondary" href="#artist/${id(key)}${mode==='store'?'':'/store'}">${mode==='store'?'View my shelf':'Visit record store ↗'}</a>${mode==='store'?`<button class="quiet" data-action="catalogue" data-id="${escapeHtml(key)}">Manage catalogue</button>`:''}</div>${result.albums.length?cards(result.albums):empty('There’s room for more.','Browse this artist’s record store to add their studio albums.')}`;
      if(isVinyl())html=vinylArtist(result,key,mode);
    }else if(view==='album'){
      const album=await api('albums/'+id(key));
      if(generation!==routeGeneration)return;
      html=albumPage(album);
    }
    else if(view==='settings')html=settingsPage();
    else {location.hash='shelf';return;}
    if(generation!==routeGeneration)return;
    content.innerHTML=(statusInfo.role==='view'?'<div class="note">View-only temporary access. Changes and playback are disabled.</div>':'')+html;applyPermissions(content);settingsDirty=false;restoreBrowsing(generation);refreshPlayback();
    if(view==='store'&&['artist','album'].includes(key)&&mode&&!isVinyl()){
      const results=document.querySelector('#search-results');
      results.innerHTML=searchKind==='album'?cards(storeSearch.results):storeSearch.results.map(a=>`<a class="search-result" href="#artist/${id(a.id)}/store"><strong>${escapeHtml(a.name)}</strong></a>`).join('');
      document.querySelector('#search-form input').value=storeSearch.query;
    }
  }catch(error){if(generation===routeGeneration)content.innerHTML=`<div class="error-panel"><h2>Couldn’t open this page.</h2><p>${escapeHtml(error.message)}</p><button class="secondary" data-action="retry">Try again</button> <a class="quiet" href="#settings">Settings</a>${view==='album'?` <a class="secondary" href="api/albums/${id(key)}/diagnostics" download>Download diagnostic report</a>`:''}</div>`;}
}
function applyPermissions(root){
  if(statusInfo.role!=='view')return;
  const allowed=new Set(['retry','refresh-status','search-kind','shelf-view','shelf-expand-all','shelf-artist','load-releases','logout']);
  root.querySelectorAll('button[data-action]').forEach(button=>{if(!allowed.has(button.dataset.action))button.disabled=true;});
  root.querySelectorAll('form').forEach(form=>{if(form.id!=='search-form')form.querySelectorAll('input,select,button').forEach(control=>control.disabled=true);});
}
function showModal(html){modalContent.innerHTML=html;applyPermissions(modalContent);if(!modal.open)modal.showModal();}
async function albumSettings(){
  const a=currentAlbum;
  showModal(`<h2>Album settings</h2><p><strong>${escapeHtml(a.title)}</strong><br>Original edition: ${escapeHtml(a.release_label)}</p><button class="secondary" data-action="releases">Change original tracklist</button><form id="album-countries-form"><label for="album-countries">Country preference for this album</label><input id="album-countries" name="countries" value="${escapeHtml((a.release_countries||[]).join(', '))}" placeholder="GB, US, XW, XE"><label class="check-option"><input type="checkbox" name="inherit" ${a.release_countries===null?'checked':''}> Use global country preferences</label><p>Use an album-specific country order when regional editions differ. Leave the global preference enabled to use Settings. Format preferences always come from Settings.</p><button class="secondary">Save album preferences</button></form><p>Changing the original edition clears all Spotify mappings for this album.</p><h3>Album cover</h3><button class="secondary" data-action="cover-editions">Choose cover from another edition</button><p>The automatic cover comes from the album’s MusicBrainz release group. Downloaded covers are cached separately only for albums on your shelf. Store artwork and edition previews are not saved. A cover you upload is saved with your collection.</p><form id="artwork-form"><input type="file" name="image" accept="image/jpeg,image/png,image/webp" aria-label="Choose album cover" required><button class="secondary">Save cover</button></form><button class="quiet" data-action="reset-artwork">Restore automatic cover</button><h3>Spotify playback edition</h3><button class="secondary" data-action="resolve">${a.playable?'Find another edition':'Match Spotify tracks'}</button><p>Automatic matching prefers the newest labelled remaster or dated studio mix with a complete matching tracklist. Your manual choices are kept. Paste any Spotify edition. AudioShelf maps just the original tracks and ignores the extras.</p><form id="mapping-form"><input name="album" placeholder="https://open.spotify.com/album/…" aria-label="Spotify album link" required><button class="primary">Use this Spotify edition</button></form><h3>Report a problem</h3><p>Download this album’s tracklist, matching decisions, edition filters and recent errors. Reproduce the problem first, then send the JSON file for investigation. It contains no account credentials.</p><a class="secondary" href="api/albums/${id(a.id)}/diagnostics" download>Download diagnostic report</a>${a.on_shelf?'<div class="actions"><button class="quiet" data-action="remove">Remove from my shelf</button></div>':''}`);
}
document.addEventListener('error',event=>{if(event.target instanceof HTMLImageElement)event.target.classList.add('failed');},true);
document.querySelector('.close-modal').addEventListener('click',()=>modal.close());
modal.addEventListener('click',event=>{if(event.target===modal)modal.close();});
function releasePickerHtml(state,error=''){
  const coverMode=state.mode==='cover';
  return `<h2>${coverMode?'Choose album cover':'Choose the original edition'}</h2><p>${coverMode?'Choose a front cover without changing your tracklist or Spotify mappings. If a cover is unavailable, try another edition or upload one.':'Prefer the original standard release, without bonus tracks. Your choice replaces the tracklist and clears Spotify mappings.'}</p><p class="muted">Using this album’s country and format preferences. <a href="#settings">Global Settings ↗</a>. More editions may be on later pages.</p>${state.items.map(r=>`<button class="choice ${coverMode?'cover-choice':''}" data-action="${coverMode?'choose-cover':'choose-release'}" data-id="${escapeHtml(r.id)}">${coverMode?`<img src="api/albums/${id(currentAlbum.id)}/artwork-preview/${id(r.id)}" alt="Front cover preview" loading="lazy">`:''}<span>${escapeHtml(r.title)}<small>${escapeHtml([r.country,r.date,...(r.media||[]).map(m=>`${m.format||'Audio'}: ${m['track-count']||'?'} tracks`),r.disambiguation].filter(Boolean).join(' · '))}</small></span></button>`).join('')}${!state.items.length&&!error?`<div class="note">${state.next!==null?'No matching editions on this page. Load more to check the remaining editions.':'No editions match your release filters. Change the formats or restrictions in Settings.'}</div>`:''}${error?`<div class="note">${escapeHtml(error)}</div>`:''}${state.next!==null||error?`<button class="secondary" data-action="load-releases">${error?'Retry edition search':'Load more editions'}</button>`:''}`;
}
async function loadReleasePage(){
  const state=releasePicker,generation=routeGeneration;
  try{
    const result=await api(`albums/${id(state.album)}/releases?offset=${state.next??state.offset}`);
    if(generation!==routeGeneration||releasePicker!==state||!modal.open)return;
    state.offset=state.next??state.offset;state.next=result.next_offset;
    const seen=new Set(state.items.map(r=>r.id));state.items.push(...result.releases.filter(r=>!seen.has(r.id)));
    state.items.sort((a,b)=>{for(let n=0;n<(a.preference_rank||[]).length;n++){if(a.preference_rank[n]<b.preference_rank[n])return -1;if(a.preference_rank[n]>b.preference_rank[n])return 1;}return 0;});
    modalContent.innerHTML=releasePickerHtml(state);
  }catch(error){if(generation===routeGeneration&&releasePicker===state&&modal.open)modalContent.innerHTML=releasePickerHtml(state,error.message);}
}
document.addEventListener('change',async event=>{
  const select=event.target;if(!select.matches('[data-catalogue-choice]'))return;
  select.disabled=true;
  try{await api(`albums/${id(select.dataset.id)}/catalogue`,'POST',{choice:select.value});await route();toast('Catalogue rule saved.');}
  catch(error){toast(error.message);}finally{select.disabled=false;}
});
window.addEventListener('hashchange',event=>{saveBrowsing(new URL(event.oldURL).hash);modal.close();route();});
window.addEventListener('focus',async()=>{if(statusInfo.authenticated){try{const previousInterface=statusInfo.interface;const latest=await api('status');if(accountChanged(latest)){window.location.reload();return;}statusInfo=latest;applyTheme(statusInfo.theme);applyInterface(statusInfo.interface);renderTurntable();refreshPlayback(true);if(previousInterface!==statusInfo.interface&&!unsavedChanges()){await route();return;}if(location.hash==='#settings'&&!settingsDirty){content.innerHTML=settingsPage();applyPermissions(content);}}catch{}}});
document.addEventListener('input',event=>{if(event.target.closest('#release-filters-form'))settingsDirty=true;});
document.addEventListener('input',event=>{if(event.target.id==='artist-filter')document.querySelectorAll('.artist-row').forEach(el=>el.hidden=!el.dataset.filter.includes(event.target.value.toLowerCase()));});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!['search-form','login-form','mapping-form','track-form','artwork-form','release-filters-form','album-countries-form','catalogue-series-form','catalogue-search-form','security-form'].includes(form.id)&&!form.matches('[data-security-form], [data-account-form]'))return;
  event.preventDefault();const button=form.querySelector('button');button.disabled=true;
  const generation=routeGeneration;
  try {
    const data=new FormData(form);
    if(form.matches('[data-account-form]')){
      const body={password:data.get('password')||'',code:data.get('code')||''};
      if(form.dataset.operation==='create')Object.assign(body,{username:data.get('username'),new_password:data.get('new_password')});
      if(form.dataset.operation==='status')body.disabled=form.dataset.disabled==='true';
      if(form.dataset.operation==='reset')Object.assign(body,{new_password:data.get('new_password'),reset_two_factor:data.has('reset_two_factor')});
      await api('accounts'+(form.dataset.id?'/'+id(form.dataset.id):''),form.dataset.id?'PUT':'POST',body);
      await accountSettings();toast('Account saved.');return;
    }
    if(form.matches('[data-security-form]')){
      const operation=form.dataset.operation;
      const body={password:data.get('password')||'',code:data.get('code')||'',setup_code:data.get('setup_code')||'',new_password:data.get('new_password')||undefined,role:data.get('role')||'view',hours:Number(data.get('hours')||1)};
      const result=await api('security/'+operation,'POST',body);
      if(operation==='totp/start'){
        showModal(`<h2>Set up authenticator</h2><p>Scan this QR code in your authenticator app, or enter the setup key manually. Both are secret and generated locally.</p><img class="totp-qr" src="${escapeHtml(result.qr)}" alt="Scan to add AudioShelf to your authenticator app"><code>${escapeHtml(result.secret)}</code><p><a class="secondary" href="${escapeHtml(result.uri)}">Open compatible authenticator</a></p><form data-security-form data-operation="totp/confirm">${reauthFields()}<label>Six-digit setup code<input name="setup_code" inputmode="numeric" autocomplete="one-time-code" required></label><button class="primary">Enable two-factor authentication</button></form><p>Setup expires in ten minutes. Two-factor authentication stays off until confirmed.</p>`);
      }else if(operation==='totp/confirm'){
        showModal(`<h2>Save your recovery codes</h2><p>Two-factor authentication is enabled. Store these codes in your password manager. Each works once, alongside your password. They are shown once.</p><textarea readonly aria-label="Recovery codes" rows="10">${result.recovery_codes.join('\n')}</textarea><button class="secondary" data-action="security">I have saved the codes</button>`);
        statusInfo.two_factor_enabled=true;
      }else if(operation==='support'){
        showModal(`<h2>Temporary login created</h2><p>Username: <strong>${escapeHtml(statusInfo.account.username)}</strong></p><p>Permission: ${result.role==='view'?'View only':'Changes and playback'}. Expires ${escapeHtml(new Date(result.expires*1000).toLocaleString())}.</p><label>Password, shown once<textarea readonly aria-label="Temporary support password">${escapeHtml(result.password)}</textarea></label><p>Share only this temporary password. Use the normal AudioShelf login screen over HTTPS. Access can be revoked in Security settings.</p><button class="secondary" data-action="security">Back to Security</button>`);
      }else{await securitySettings();toast('Security settings updated.');}
    }

    if(form.id==='login-form'){await api('login','POST',{username:data.get('username'),password:data.get('password'),code:data.get('code')||'',remember:data.has('remember')});location.hash='shelf';window.location.reload();}
    if(form.id==='release-filters-form'){
      const countries=String(data.get('countries')).split(',').map(c=>c.trim()).filter(Boolean);
      const result=await api('settings','PUT',{release_filters:{countries,formats:data.getAll('formats'),strict_countries:data.has('strict_countries')}});
      statusInfo.release_filters=result.release_filters;
      if(generation!==routeGeneration)return;
      content.innerHTML=settingsPage();settingsDirty=false;toast('Release filters saved.');
    }
    if(form.id==='album-countries-form'){
      const countries=data.has('inherit')?null:String(data.get('countries')).split(',').map(c=>c.trim()).filter(Boolean);
      if(countries&&!countries.length)throw new Error('Enter a country preference or use the global preferences.');
      currentAlbum=await api(`albums/${id(currentAlbum.id)}/release-countries`,'PUT',{countries});
      if(generation!==routeGeneration)return;
      await albumSettings();toast('Album preferences saved.');
    }
    if(form.id==='catalogue-series-form'){
      const text=String(data.get('series')).trim();
      const series_id=text?text.replace(/\/$/,'').split('/').pop():null;
      await api(`artists/${id(form.dataset.artist)}/series`,'PUT',{series_id});
      modal.close();await route();toast('Curated catalogue saved.');
    }
    if(form.id==='catalogue-search-form'){
      const results=document.querySelector('#catalogue-search-results');
      results.innerHTML=loading('Looking for curated catalogues…');
      try{
        const result=await api(`artists/${id(form.dataset.artist)}/catalogue-series?q=${id(String(data.get('query')).trim())}`);
        if(generation!==routeGeneration||!modal.open)return;
        results.innerHTML=result.series.length?result.series.map(s=>`<div class="catalogue-row"><span><strong>${escapeHtml(s.name)}</strong><small>${escapeHtml(s.disambiguation||'Release-group series')}</small></span><button type="button" class="secondary" data-action="pick-catalogue" data-id="${escapeHtml(s.id)}">Choose ${escapeHtml(s.name)}</button></div>`).join(''):'<p class="muted">No release-group series found. Try another name, paste a series link, or use individual album rules.</p>';
      }catch(error){results.textContent=error.message;}
    }
    if(form.id==='search-form'){
      storeSearch={kind:searchKind,query:String(data.get('q')),results:null};
      const resultEl=document.querySelector('#search-results');
      resultEl.innerHTML=loading('Looking through the record store…');
      const result=await api(`search?q=${id(data.get('q'))}&kind=${searchKind}`);
      if(generation!==routeGeneration)return;
      storeSearch.results=result.results;
      if(isVinyl()){resultEl.innerHTML=vinylSearchResults();applyPermissions(resultEl);return;}
      resultEl.innerHTML=result.results.length?(searchKind==='album'?cards(result.results):result.results.map(a=>`<a class="search-result" href="#artist/${id(a.id)}/store"><strong>${escapeHtml(a.name)}</strong><small>${escapeHtml([a.disambiguation,a.country,a.type].filter(Boolean).join(' · '))}</small></a>`).join('')):'<p class="muted">No studio albums or artists found. Try a different spelling.</p>';
    }
    if(form.id==='artwork-form'){
      const file=data.get('image');
      if(!file || file.size>5*1024*1024)throw new Error('Choose an image smaller than 5 MB.');
      const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('Could not read the image.'));reader.readAsDataURL(file);});
      await api(`albums/${id(currentAlbum.id)}/artwork`,'POST',{image:encoded});
      if(generation!==routeGeneration)return;
      artworkRevision=Date.now();modal.close();content.innerHTML=albumPage(currentAlbum);toast('Album cover saved.');
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
    if(action==='interface'){const result=await api('settings','PUT',{interface:button.dataset.id});statusInfo.interface=result.interface;applyInterface(result.interface);await route();toast(`${result.interface==='vinyl'?'Vinyl':'Classic'} interface saved.`);}
    if(action==='shelf-expand-all')toggleAllShelves();
    if(action==='shelf-style'){const result=await api('settings','PUT',{shelf_style:button.dataset.id});statusInfo.shelf_style=result.shelf_style;applyInterface(statusInfo.interface);await route();toast('Shelf style saved.');}
    if(action==='shelf-artist')toggleShelfArtist(button);
    if(action==='shelf-view'){shelfView=button.dataset.view;await route();}
    if(action==='now-playing-album'){
      const destination=await api('spotify/album-destination');
      if(destination.view==='album'&&destination.album_id){
        albumOrigin=destination.on_shelf?'#shelf':'#store';
        location.hash='#album/'+id(destination.album_id);
      }else if(destination.view==='store-search'){
        location.hash='#store/album/'+id(destination.query);
      }
    }
    if(action==='spotify-control')await controlSpotify(button.dataset.command);
    if(action==='search-kind'){searchKind=button.dataset.kind;storeSearch={kind:searchKind,query:'',results:null};await route();}
    if(action==='add'){
      const a=await api(`albums/${id(button.dataset.id)}/shelf`,'POST');markCollected(a.id);toast(`${a.title} added to your shelf.`);
      if(generation!==routeGeneration)return;
      if(location.hash.startsWith('#album/'))content.innerHTML=albumPage(a);else{button.textContent='✓ On your shelf';button.setAttribute('aria-label',`${a.title} is on your shelf`);button.dataset.action='';button.closest('.album-card')?.classList.add('just-collected');}
    }
    if(action==='security')await securitySettings();
    if(action==='accounts')await accountSettings();
    if(action==='ingress-login'){await api('login/ingress','POST');location.hash='shelf';window.location.reload();}
    if(action==='devices'){
      const result=await api('spotify/devices');spotifyDevices=result.devices;devicePicker();
    }
    if(action==='open-playback-helper'||action==='open-playback-spotify'){if(pendingPlayback){pendingPlayback.until=Date.now()+60000;setTimeout(retryPendingPlayback,1500);}}
    if(action==='playback-devices'){const request=pendingPlayback||{album:currentAlbum,disc:null,generation:routeGeneration,chooseOnly:true};await playbackDevicePicker(request);}
    if(action==='playback-device'){
      const request=pendingPlayback,selected=spotifyDevices[Number(button.dataset.index)];
      if(!request||!selected)return;
      const result=await api('settings','PUT',{preferred_device:{id:selected.id,name:selected.name,type:selected.type}});statusInfo.preferred_device=result.preferred_device;
      if(request.chooseOnly){pendingPlayback=null;modal.close();toast('Playback device saved.');return;}
      try{await startPlayback(request,false);}catch(error){if(error.status===409||error.status===404)await playbackHandoff(request,error.message);else throw error;}
    }
    if(action==='device'){
      const selected=spotifyDevices[Number(button.dataset.index)];
      const preferred=selected?{id:selected.id,name:selected.name,type:selected.type}:null;
      const result=await api('settings','PUT',{preferred_device:preferred});
      statusInfo.preferred_device=result.preferred_device;modal.close();await route();toast('Playback device saved.');
    }
    if(action==='play'){
      if(checkingPlayback){toast('Waiting for Spotify to finish starting playback.');return;}
      if(!statusInfo.spotify_connected)throw new Error('Connect Spotify in Settings first.');
      if(!currentAlbum.canonical_reviewed)throw new Error('Check the displayed original tracklist, then choose “This tracklist is correct” before first playback.');
      const disc=button.dataset.disc?Number(button.dataset.disc):null;
      const selectedTracks=currentAlbum.tracks.filter(t=>disc===null||(t.disc_number||1)===disc);
      if(!selectedTracks.every(t=>t.verified&&t.spotify_id)){toast('Matching Spotify tracks first…');const result=await api(`albums/${id(currentAlbum.id)}/resolve`,'POST');if(generation!==routeGeneration)return;currentAlbum=result.album;content.innerHTML=albumPage(currentAlbum);}
      const request={album:currentAlbum,disc,generation};
      cancelPendingPlayback();
      if(!statusInfo.preferred_device){await playbackDevicePicker(request);return;}
      try{await startPlayback(request);}
      catch(error){if(generation!==routeGeneration){toast(error.message);return;}if(error.status===409||error.status===404)await playbackHandoff(request,error.message);else throw error;}
    }
    if(action==='resolve'){
      if(!statusInfo.spotify_connected)throw new Error('Connect Spotify in Settings first.');
      toast('Finding the best Spotify recordings. This may take a moment…');
      const result=await api(`albums/${id(currentAlbum.id)}/resolve`,'POST');if(generation!==routeGeneration)return;currentAlbum=result.album;content.innerHTML=albumPage(currentAlbum);
      showModal(`<h2>Spotify editions</h2><p>The best complete match is saved, preferring the latest labelled remaster or dated mix. Manual track choices are kept. Choose another edition if you prefer. Extra tracks are excluded from playback.</p>${result.candidates.map(c=>`<button class="choice" data-action="choose-mapping" data-id="${escapeHtml(c.id)}">${escapeHtml(c.name)}<small>${escapeHtml(c.release_date)}${c.edition_year?` · ${c.edition_year} remaster/mix`:""} · ${c.track_count} tracks in Spotify · ${c.verified}/${currentAlbum.tracks.length} canonical tracks verified</small></button>`).join('')}`);
    }
    if(action==='choose-mapping'){const result=await api(`albums/${id(currentAlbum.id)}/mapping`,'POST',{spotify_album_id:button.dataset.id});if(generation!==routeGeneration)return;modal.close();content.innerHTML=albumPage(result.album);toast('Spotify edition saved.');}
    if(action==='reset-artwork'){
      await api(`albums/${id(currentAlbum.id)}/artwork`,'DELETE');
      if(generation!==routeGeneration)return;
      artworkRevision=Date.now();modal.close();content.innerHTML=albumPage(currentAlbum);toast('Automatic artwork restored.');
    }
    if(action==='album-settings')await albumSettings();
    if(action==='review'){await api(`albums/${id(currentAlbum.id)}/review`,'POST');if(generation!==routeGeneration)return;currentAlbum.canonical_reviewed=1;content.innerHTML=albumPage(currentAlbum);toast('Original tracklist confirmed.');}
    if(action==='theme'){
      const result=await api('settings','PUT',{theme:button.dataset.id});
      statusInfo.theme=result.theme;applyTheme(result.theme);
      if(generation!==routeGeneration)return;
      document.querySelectorAll('.theme-choice').forEach(choice=>choice.setAttribute('aria-pressed',String(choice.dataset.id===result.theme)));toast('Theme saved.');
    }
    if(action==='format-up'||action==='format-down'){
      settingsDirty=true;const row=button.closest('[data-format-row]');
      if(action==='format-up'&&row.previousElementSibling?.matches('[data-format-row]'))row.parentNode.insertBefore(row,row.previousElementSibling);
      if(action==='format-down'&&row.nextElementSibling)row.parentNode.insertBefore(row.nextElementSibling,row);
    }
    if(action==='catalogue'){
      showModal('<h2>Manage catalogue</h2>'+loading('Loading album classifications…'));
      const result=await api(`artists/${id(button.dataset.id)}/catalogue`);
      if(generation!==routeGeneration)return;
      modalContent.innerHTML=`<h2>Manage catalogue</h2><p>Use a curated MusicBrainz release-group series when available, or the normal studio-album rules. Individual overrides take precedence. Your shelf is kept.</p>${result.series_snapshot?`<p>Selected catalogue: <strong>${escapeHtml(result.series_snapshot.name)}</strong>. Its last successful membership snapshot is retained for temporary MusicBrainz outages.</p>`:''}<form id="catalogue-search-form" data-artist="${escapeHtml(button.dataset.id)}"><label for="catalogue-query">Find a curated catalogue</label><input id="catalogue-query" name="query" placeholder="Artist or catalogue name"><button class="secondary">Find catalogues</button></form><div id="catalogue-search-results"></div><p>Search starts with this artist’s name. A series can describe a core catalogue, regional releases, reissues or another collection. Review its purpose before saving it.</p><form id="catalogue-series-form" data-artist="${escapeHtml(button.dataset.id)}"><label for="catalogue-series">MusicBrainz series link or ID</label><input id="catalogue-series" name="series" value="${escapeHtml(result.series_id||'')}" placeholder="https://musicbrainz.org/series/…"><button class="secondary">Save curated series</button></form><p>Leave blank for the normal rules. Series members marked live, compilation or other excluded types stay outside; original soundtrack albums can be included. You can override an individual album below.</p>${result.albums.map(a=>`<div class="catalogue-row"><span><strong>${escapeHtml(a.title)}</strong><small>${year(a)} · ${escapeHtml(a.secondary_types.join(', ')||'Album')} · ${a.catalogue_included?'Included':'Excluded'}</small></span><select data-catalogue-choice data-id="${escapeHtml(a.id)}" aria-label="Catalogue rule for ${escapeHtml(a.title)}">${[['auto','Auto'],['include','Include'],['exclude','Exclude']].map(([value,label])=>`<option value="${value}" ${value===(a.catalogue_override||'auto')?'selected':''}>${label}</option>`).join('')}</select></div>`).join('')}`;
    }
    if(action==='pick-catalogue')document.querySelector('#catalogue-series').value=button.dataset.id;
    if(action==='releases'||action==='cover-editions'){
      releasePicker={album:currentAlbum.id,mode:action==='cover-editions'?'cover':'tracks',items:[],offset:0,next:0};
      showModal(`<h2>${releasePicker.mode==='cover'?'Choose album cover':'Choose the original edition'}</h2>`+loading('Finding MusicBrainz editions…'));
      await loadReleasePage();
    }
    if(action==='load-releases')await loadReleasePage();
    if(action==='choose-cover'){
      await api(`albums/${id(currentAlbum.id)}/artwork-release`,'POST',{release_id:button.dataset.id});
      if(generation!==routeGeneration)return;
      artworkRevision=Date.now();modal.close();content.innerHTML=albumPage(currentAlbum);toast('Edition cover saved.');
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
      await api(`albums/${id(currentAlbum.id)}/shelf`,'DELETE');if(generation!==routeGeneration)return;currentAlbum.on_shelf=0;markCollected(currentAlbum.id,false);modal.close();content.innerHTML=albumPage(currentAlbum);toast('Removed from your shelf. The mapping is kept if you add it again.');
    }
    if(action==='connect'){
      const popup=window.open('about:blank','_blank');
      try{const result=await api('spotify/connect','POST');if(popup)popup.location.replace(result.url);else showModal(`<h2>Connect Spotify</h2><a class="primary" href="${escapeHtml(result.url)}" target="_blank" rel="noopener">Continue to Spotify</a>`);}catch(error){if(popup)popup.close();throw error;}
    }
    if(action==='disconnect'){await api('spotify/disconnect','POST');await route();toast('Spotify disconnected. Your shelf is unchanged.');}
    if(action==='logout'){await api('logout','POST');location.hash='shelf';window.location.reload();}
    if(action==='backup'){
      const response=await api('backup','POST');const blob=await response.blob();const url=URL.createObjectURL(blob);
      const link=document.createElement('a');link.href=url;link.download='audioshelf-backup.db';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('Database backup downloaded.');
    }
  }catch(error){toast(error.message);}
  finally{button.disabled=false;}
});
route();
if('serviceWorker' in navigator && !document.baseURI.includes('/hassio_ingress/')) {
  const workerURL=new URL('sw.js',document.baseURI);workerURL.searchParams.set('v',loadedAssets);
  navigator.serviceWorker.register(workerURL,{updateViaCache:'none'}).then(registration=>{workerRegistration=registration;registration.update().catch(()=>{});}).catch(()=>{});
  navigator.serviceWorker.addEventListener('message',event=>{if(event.data?.type==='audioshelf-update')checkForUpdates();});
}
