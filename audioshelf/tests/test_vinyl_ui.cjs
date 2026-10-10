const { chromium }=require('playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const fs=require('node:fs');
const port=process.env.AUDIOSHELF_TEST_PORT||'8102';
const base=`http://127.0.0.1:${port}`;
const server=spawn(process.env.AUDIOSHELF_TEST_PYTHON||'python',[path.join(__dirname,'ui_server.py')],{env:{...process.env,AUDIOSHELF_TEST_PORT:port,AUDIOSHELF_TEST_VINYL:'1'},stdio:['ignore','pipe','pipe']});
let logs='';server.stdout.on('data',d=>logs+=d);server.stderr.on('data',d=>logs+=d);
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const screenshots=process.env.AUDIOSHELF_SCREENSHOT_DIR;
(async()=>{
  let browser;
  try{
    let ready=false;
    for(let n=0;n<80;n++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}await wait(100);}
    assert(ready,logs);
    browser=await chromium.launch({headless:true,executablePath:process.env.AUDIOSHELF_TEST_BROWSER||undefined,args:process.env.AUDIOSHELF_TEST_BROWSER?['--no-sandbox','--disable-dev-shm-usage']:[]});
    const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
    await page.route('https://i.scdn.co/image/**',async route=>{
      const response=await fetch(base+'/__test/vinyl-cover/4');
      await route.fulfill({contentType:'image/png',body:Buffer.from(await response.arrayBuffer())});
    });
    const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(r.url().includes('/api/'))requests.push('START '+r.method()+' '+new URL(r.url()).pathname);});
    page.on('response',r=>{if(r.url().includes('/api/'))requests.push('END '+r.status()+' '+new URL(r.url()).pathname);});
    async function waitPlaying(){
      try{await page.locator('#turntable small b').filter({hasText:'PLAYING'}).waitFor();}
      catch(error){console.error(JSON.stringify({errors,requests:requests.slice(-30),state:await page.evaluate(()=>({toast:document.querySelector('#toast').textContent,turntable:document.querySelector('#turntable').textContent,playbackState,playbackBusy,pendingPlayback,checkingPlayback}))}));throw error;}
    }
    // Synthetic covers keep screenshots reproducible, with no third-party downloads.
    await page.route('**/api/albums/*/artwork?*',async route=>{
      const key=route.request().url().split('/albums/')[1].split('/')[0];
      const index=Array.from(key).reduce((sum,c)=>sum+c.charCodeAt(0),0)%18;
      const response=await fetch(base+'/__test/vinyl-cover/'+index);
      await route.fulfill({contentType:'image/png',body:Buffer.from(await response.arrayBuffer())});
    });
    async function shot(name){if(screenshots){fs.mkdirSync(screenshots,{recursive:true});await page.screenshot({path:path.join(screenshots,'vinyl-'+name+'.png'),fullPage:true});}}
    async function noOverflow(){assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Horizontal overflow at '+page.url());}
    await page.goto(base);
    await page.getByLabel('Password',{exact:true}).fill('fixture-owner-password');
    await page.getByRole('button',{name:'Open AudioShelf',exact:true}).click();
    await page.getByRole('button',{name:'Expand all',exact:true}).waitFor();
    assert.equal(await page.locator('.sleeve:visible').count(),14,'Compact shelves show the album covers immediately');
    assert.equal(await page.locator('.collection-shelves[data-layout="rail"] .shelf-rack').count(),2);
    await page.getByRole('button',{name:'Expand all',exact:true}).click();
    await page.locator('.shelf-rack').first().waitFor();
    assert.equal(await page.locator('.sleeve').count(),14);
    assert.equal(await page.locator('html').getAttribute('data-interface'),'vinyl');
    // External artist links retain the full shelf and land at the artist section.
    await page.goto(base+'/#shelf/f181961b-20f7-459e-89de-920ef03c7ed0');
    await page.locator('[data-shelf-id]').first().waitFor();
    await page.waitForFunction(()=>Math.abs(document.querySelector('[data-shelf-id]').getBoundingClientRect().top-parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop))<3);
    assert.equal(await page.locator('.sleeve').count(),14);
    await page.goto(base+'/#store/artist/The%20Artist');
    await page.locator('.artist-bin').first().waitFor();
    assert.equal(await page.locator('#search-form input').inputValue(),'The Artist');
    await page.goto(base+'/#shelf');
    await page.locator('.shelf-rack').first().waitFor();
    await page.locator('.cover').first().evaluate(img=>img.decode());
    await noOverflow();await shot('shelf-desktop');
    await page.getByRole('searchbox',{name:'Find a record'}).fill('does not exist');
    assert.equal(await page.locator('.sleeve:visible').count(),0);
    await page.getByRole('searchbox',{name:'Find a record'}).fill('');
    const sleeve=page.locator('.sleeve .album-title').last();
    const inspected=await sleeve.textContent();
    await sleeve.scrollIntoViewIfNeeded();
    const scroll=await page.evaluate(()=>scrollY);assert(scroll>300);
    await sleeve.click();await page.getByRole('heading',{name:inspected,exact:true}).waitFor();
    await page.getByRole('link',{name:'Back to browsing'}).click();
    await page.locator('.shelf-rack').first().waitFor();
    await page.waitForFunction(y=>Math.abs(scrollY-y)<3,scroll);
    // Compact mode keeps all covers in one horizontally scrollable rail per artist.
    await page.getByRole('button',{name:'Collapse all',exact:true}).click();
    assert.equal(await page.locator('.sleeve:visible').count(),14);
    const dividers=page.locator('.shelf-artist-toggle');
    assert.equal(await dividers.count(),2);
    const firstRail=page.locator('.collection-shelves .shelf-rack').first();
    assert(await firstRail.evaluate(el=>el.scrollWidth>el.clientWidth),'A long artist collection scrolls sideways');
    assert.equal(await firstRail.locator('.shelf-row').count(),0,'Compact mode has no fragmented grid rows');
    assert.equal(await page.locator('.rail-ledge:visible').count(),2);
    assert.equal(await page.locator('.collection-shelves .shelf-artist-link:visible').count(),0,
      'Collapsed artists must not interrupt shelves with More from links');
    assert.equal(await page.locator('.collection-shelves .shelf-records .shelf-artist-link').count(),0,
      'More from belongs to the artist name bar, never beneath the records');
    await firstRail.evaluate(el=>{el.scrollLeft=el.scrollWidth;});
    assert(await firstRail.evaluate(el=>el.scrollLeft>0),'Horizontal shelf can be scrolled');
    await dividers.first().click();
    assert.equal(await page.locator('.shelf-artist-toggle[aria-expanded="true"]').count(),1);
    assert.equal(await page.locator('.collection-shelves .shelf-artist-link:visible').count(),1,
      'Exactly the expanded artist exposes its More from store action');
    assert.equal(await page.locator('.artist-shelf[data-view="expanded"] .shelf-artist-bar .shelf-artist-link').count(),1);
    assert((await page.locator('.artist-shelf[data-view="expanded"] .shelf-row').count())>0);
    const captionGeometry=await page.locator('.artist-shelf[data-view="expanded"] .shelf-row .sleeve').first().evaluate(el=>({
      caption:el.querySelector('.sleeve-caption').getBoundingClientRect().bottom,
      cover:el.querySelector('.cover-wrap').getBoundingClientRect().top
    }));
    assert(captionGeometry.caption<=captionGeometry.cover+2,'Album label stays above its own cover');
    await dividers.last().click();
    assert.equal(await dividers.first().getAttribute('aria-expanded'),'false');
    assert.equal(await dividers.last().getAttribute('aria-expanded'),'true');
    assert.equal(await page.locator('.collection-shelves .shelf-artist-link:visible').count(),1);
    const openId=await dividers.last().evaluate(el=>el.closest('[data-shelf-id]').dataset.shelfId);
    assert.equal(await page.locator('.artist-shelf[data-view="expanded"] .shelf-artist-link').getAttribute('href'),
      '#artist/'+openId+'/store', 'More from leads to the artist record-store catalogue');
    const compactAlbum=page.locator('.artist-shelf[data-view="expanded"] .album-title').first();
    const compactTitle=await compactAlbum.textContent();
    await compactAlbum.click();
    await page.getByRole('heading',{name:compactTitle,exact:true}).waitFor();
    await page.getByRole('link',{name:'Back to browsing'}).click();
    await page.locator('.shelf-artist-toggle[aria-expanded="true"]').waitFor();
    assert.equal(await page.locator('.shelf-artist-toggle[aria-expanded="true"]').evaluate(el=>el.closest('[data-shelf-id]').dataset.shelfId),openId);
    await page.getByRole('searchbox',{name:'Find a record'}).fill('does not exist');
    await page.locator('#shelf-no-results').waitFor();
    assert.equal(await page.locator('.shelf-artist-toggle:visible').count(),0);
    await page.getByRole('searchbox',{name:'Find a record'}).fill(compactTitle);
    assert.equal(await page.locator('.sleeve:visible').count(),1);
    await page.getByRole('searchbox',{name:'Find a record'}).fill('');
    await dividers.last().click();
    assert.equal(await page.locator('.artist-shelf[data-view="rail"]').count(),2);
    assert.equal(await page.locator('.collection-shelves .shelf-artist-link:visible').count(),0);
    await noOverflow();await shot('rail-desktop');
    await page.reload();
    await page.locator('.collection-shelves[data-layout="rail"]').waitFor();
    assert.equal(await page.locator('.sleeve:visible').count(),14,'Fresh visits show all record covers in compact rails');
    await page.goto(base+'/#shelf/'+openId);
    await page.locator('.shelf-artist-toggle[aria-expanded="true"]').waitFor();
    assert.equal(await page.locator('.shelf-artist-toggle[aria-expanded="true"]').evaluate(el=>el.closest('[data-shelf-id]').dataset.shelfId),openId);
    await page.setViewportSize({width:390,height:844});
    await noOverflow();await shot('rail-mobile');
    await dividers.last().click();
    await dividers.last().focus();await page.keyboard.press('Enter');
    assert.equal(await dividers.last().getAttribute('aria-expanded'),'true','Keyboard expands the artist');
    await page.setViewportSize({width:320,height:740});await noOverflow();
    await page.getByRole('button',{name:'Expand all',exact:true}).click();
    assert.equal(await page.locator('.sleeve:visible').count(),14);
    assert.equal(await page.locator('.collection-shelves .shelf-artist-link:visible').count(),2,
      'Expand all exposes the header action for each artist');
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('[data-nav="store"]').click();
    await page.getByRole('button',{name:'Albums',exact:true}).click();
    await page.getByRole('searchbox',{name:'Search record store'}).fill('all records');
    await page.getByRole('button',{name:'Search',exact:true}).click();
    await page.locator('.store-rack').first().waitFor();
    assert.equal(await page.locator('.sleeve').count(),17);
    await noOverflow();await shot('store-desktop');
    await page.getByRole('button',{name:'Add Open Windows to shelf',exact:true}).click();
    await page.getByRole('heading',{name:'Add a playable edition'}).waitFor();
    await page.locator('[data-action="choose-release"][data-spotify-id]').first().click();
    await page.locator('#toast').filter({hasText:'Open Windows added'}).waitFor();
    assert(page.url().endsWith('#store'));
    await page.getByRole('link',{name:'Open Windows',exact:true}).click();
    await page.getByRole('heading',{name:'Open Windows',exact:true}).waitFor();
    await page.getByRole('link',{name:'Back to browsing'}).click();
    await page.locator('.store-rack').first().waitFor();
    assert.equal(await page.getByRole('searchbox',{name:'Search record store'}).inputValue(),'all records');
    assert.equal(await page.locator('.sleeve').count(),17);
    assert.equal(await page.getByRole('button',{name:'Add Open Windows to shelf',exact:true}).count(),0);
    await page.getByRole('link',{name:'The Original Album',exact:true}).click();
    await page.getByRole('button',{name:'This tracklist is correct'}).click();
    await page.getByRole('button',{name:'This tracklist is correct'}).waitFor({state:'hidden'});
    // Spotify can briefly return the old song after accepting a new queue.
    let startupReads=0, confirmStartup;
    const startupGate=new Promise(resolve=>confirmStartup=resolve);
    await page.evaluate(()=>playbackChecked=Date.now());
    await page.route('**/api/spotify/playback',async route=>{
      startupReads++;
      if(startupReads===1){await route.fulfill({contentType:'application/json',body:JSON.stringify({active:true,playing:true,track:'Previous song',track_ids:['old-track'],progress_ms:120000,duration_ms:180000,album:'Previous album'})});return;}
      if(startupReads===2){await route.fulfill({contentType:'application/json',body:JSON.stringify({active:true,playing:true,track:'Opening',track_ids:['a'.repeat(22)],progress_ms:120000,duration_ms:180000,album:'The Original Album'})});return;}
      await startupGate;await route.continue();
    });
    await page.getByRole('button',{name:'Play album',exact:false}).click();
    await page.getByRole('heading',{name:'Choose playback device',exact:true}).waitFor();
    await page.getByRole('button',{name:'Fixture phone',exact:false}).click();
    await page.locator('#turntable small b').filter({hasText:'STARTING'}).waitFor();
    assert.equal(await page.locator('#turntable strong').textContent(),'The Original Album');
    assert((await page.locator('#turntable .turntable-record').textContent()).includes('Opening'));
    assert.equal(await page.getByRole('progressbar',{name:'Song progress'}).evaluate(el=>el.value),0);
    await page.waitForFunction(()=>document.querySelector('#turntable small b')?.textContent==='STARTING');
    confirmStartup();
    await page.locator('#turntable small b').filter({hasText:'PLAYING'}).waitFor({timeout:4000});
    assert(startupReads>=3,'Startup retries must not wait for the normal poll');
    await page.unroute('**/api/spotify/playback');
    const initialProgress=await page.getByRole('progressbar',{name:'Song progress'}).evaluate(el=>el.value);
    await page.waitForFunction(start=>document.querySelector('#turntable progress')?.value>start+500,initialProgress);
    assert.equal(await page.getByRole('progressbar',{name:'Song progress'}).evaluate(el=>el.max),180000);
    assert.equal(await page.locator('#turntable strong').textContent(),'The Original Album');
    assert.equal(await page.locator('.now-playing-art').count(),1,'Spotify images take priority over local album covers');
    assert((await page.locator('.now-playing-art').getAttribute('src')).startsWith('https://i.scdn.co/'));
    assert.equal(await page.getByRole('button',{name:'Next track'}).count(),0,'Skip controls are off by default');
    assert.equal(await page.getByRole('button',{name:'Pause Spotify'}).count(),1);
    await page.getByRole('button',{name:'Pause Spotify'}).click();
    await page.locator('#turntable small b').filter({hasText:'PAUSED'}).waitFor();
    assert.equal(await page.locator('.turntable-toggle.is-spinning').count(),0);
    await page.getByRole('button',{name:'Resume Spotify'}).click();
    await page.locator('#turntable small b').filter({hasText:'PLAYING'}).waitFor();
    assert.equal(await page.locator('.turntable-toggle.is-spinning').count(),1);
    // A rotating symmetrical circle looks stationary: the asymmetric label
    // and groove marker must visibly change orientation while playing.
    // Playback polling replaces the platter. Resolve and read the same attached
    // element within one browser turn, rather than sampling detached locator nodes.
    const platterStyle=await page.evaluate(()=>{
      const el=document.querySelector('.turntable-platter');
      const face=getComputedStyle(el),label=getComputedStyle(el,'::before');
      return {label:label.content,labelBackground:label.backgroundImage,
        faceBackground:face.backgroundImage,spindleWidth:getComputedStyle(el,'::after').width};
    });
    assert.equal(platterStyle.label,'"AS"');
    assert(platterStyle.labelBackground.includes('conic-gradient'),'The label uses the AudioShelf cream/green palette');
    assert(platterStyle.faceBackground.includes('repeating-radial-gradient'),'Fine matte grooves replace the reflective face');
    assert(!platterStyle.faceBackground.includes('linear-gradient'),'No moving glossy reflection on the disc');
    assert.equal(platterStyle.spindleWidth,'4px','A small spindle sits at the centre');
    await page.emulateMedia({reducedMotion:'no-preference'});
    await page.waitForFunction(()=>{const el=document.querySelector('.turntable-platter');return el&&getComputedStyle(el).animationName==='audioshelf-spin';});
    // The turntable is redrawn by playback polling. Sample the current attached
    // element atomically; a locator's resolved element can detach before evaluation.
    const angleBefore=await page.evaluate(()=>getComputedStyle(document.querySelector('.turntable-platter')).transform);
    await wait(200);
    const angleAfter=await page.evaluate(()=>getComputedStyle(document.querySelector('.turntable-platter')).transform);
    assert.notEqual(angleAfter,angleBefore,'Playing platter visibly rotates');
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.waitForFunction(()=>{const el=document.querySelector('.turntable-platter');return el&&getComputedStyle(el).animationName==='none';});
    // A proxy can report 502 after Spotify successfully paused the track.
    // Recheck actual player state rather than reporting a false failure.
    await page.route('**/api/spotify/control',async route=>{
      const response=await route.fetch();
      assert.equal(response.status(),200);
      await route.fulfill({status:502,contentType:'text/html',body:'Bad Gateway'});
    });
    await page.getByRole('button',{name:'Pause Spotify'}).click();
    await page.locator('#turntable small b').filter({hasText:'PAUSED'}).waitFor();
    assert.equal(await page.locator('#turntable .turntable-toggle.is-spinning').count(),0);
    assert(!((await page.locator('#toast').textContent())||'').includes('request failed'),'Applied command must not show a false failure');
    await page.unroute('**/api/spotify/control');
    await page.getByRole('button',{name:'Resume Spotify'}).click();
    await page.locator('#turntable small b').filter({hasText:'PLAYING'}).waitFor();
    const bar=await page.locator('#turntable').evaluate(el=>({bar:el.getBoundingClientRect().height,art:el.querySelector('.now-playing-art').getBoundingClientRect().height}));
    assert(bar.bar<=90&&bar.art>=bar.bar-16,'Larger Spotify art fits the existing bar');
    await noOverflow();await shot('album-desktop');
    // A late Play response must never replace the newly inspected sleeve.
    let releasePlay, playReceived;
    const received=new Promise(resolve=>playReceived=resolve);
    const release=new Promise(resolve=>releasePlay=resolve);
    await page.route('**/api/albums/*/play',async route=>{playReceived();await release;const response=await route.fetch();await route.fulfill({response});});
    await page.getByRole('button',{name:'Play album',exact:false}).click();
    await received;
    assert.equal(await page.locator('#turntable small b').textContent(),'STARTING','Requested song must appear before the playback API responds');
    assert((await page.locator('#turntable .turntable-record').textContent()).includes('Opening'));
    await page.getByRole('link',{name:'Back to browsing'}).click();
    await page.getByRole('link',{name:'First Light',exact:true}).click();
    releasePlay();
    await page.unroute('**/api/albums/*/play');
    await page.getByRole('heading',{name:'First Light',exact:true}).waitFor();
    await waitPlaying();
    assert.equal(await page.locator('#turntable strong').textContent(),'The Original Album');
    assert.equal(await page.getByRole('heading',{name:'First Light',exact:true}).count(),1);
    await page.getByRole('button',{name:'Album settings'}).click();
    await page.getByRole('button',{name:'Choose cover from another edition'}).waitFor();
    await page.getByRole('link',{name:'Download diagnostic report'}).waitFor();
    await page.getByRole('button',{name:'Close',exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    await noOverflow();await shot('album-mobile');
    await page.locator('[data-nav="shelf"]').click();
    await page.locator('.shelf-rack').first().waitFor();
    assert.equal(await page.locator('.sleeve').count(),15);
    assert.equal(await page.locator('.shelf-row').first().evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),2);
    await noOverflow();await shot('shelf-mobile');
    await page.locator('[data-nav="store"]').click();
    await page.locator('.store-rack').first().waitFor();
    await noOverflow();await shot('store-mobile');
    await page.route('**/api/spotify/playback',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({active:true,playing:false,album:'The Original Album',album_id:'f5093c06-23e3-4f01-aeaa-40f72885ee3a',track:'Opening',artist:'The Artist',device:'Phone',progress_ms:42000,duration_ms:180000,track_ids:['a'.repeat(22)]})}));
    await page.evaluate(()=>refreshPlayback(true));
    await page.locator('#turntable small b').filter({hasText:'PAUSED'}).waitFor();
    assert.equal(await page.getByRole('progressbar',{name:'Song progress'}).evaluate(el=>el.value),42000);
    const progressNode=await page.locator('#turntable progress').elementHandle();
    await page.waitForFunction(()=>document.querySelector('#turntable [data-elapsed]')?.textContent==='0:42');
    await page.evaluate(()=>{playbackState.observedAt-=10000;updateTrackProgress();});
    assert.equal(await progressNode.evaluate(el=>el.value),42000,'Paused progress must not advance');
    await noOverflow();await shot('progress-mobile');
    await page.unroute('**/api/spotify/playback');
    await page.route('**/api/spotify/playback',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Unavailable'})}));
    await page.evaluate(()=>refreshPlayback(true));
    await page.locator('#turntable small b').filter({hasText:'STATUS UNAVAILABLE'}).waitFor();
    await page.unroute('**/api/spotify/playback');
    // At a song boundary refresh promptly instead of leaving a full bar and old title.
    let boundaryReads=0;
    await page.route('**/api/spotify/playback',route=>{boundaryReads++;return route.fulfill({contentType:'application/json',body:JSON.stringify({active:true,playing:true,track:'Closing',album:'The Original Album',progress_ms:1000,duration_ms:240000,track_ids:['b'.repeat(22)]})});});
    await page.evaluate(()=>{
      playbackState={active:true,playing:true,track:'Opening',album:'The Original Album',progress_ms:179900,duration_ms:180000,observedAt:performance.now()};
      playbackChecked=Date.now()-1100;renderTurntable();
    });
    await page.waitForFunction(()=>document.querySelector('#turntable .turntable-record')?.textContent.includes('Closing'),null,{timeout:2500});
    assert(boundaryReads>0);
    assert.equal(await page.getByRole('progressbar',{name:'Song progress'}).evaluate(el=>el.max),240000);
    await page.unroute('**/api/spotify/playback');
    // An uncollected studio album opens its canonical AudioShelf Record Store page.
    await fetch(base+'/__test/playback-options',{method:'POST',headers:{'Content-Type':'application/json','X-AudioShelf-Request':'1'},body:JSON.stringify({external_album:true})});
    await page.evaluate(()=>refreshPlayback(true));
    await page.getByRole('button',{name:'Open After the Rain (2025 Remaster) in AudioShelf'}).waitFor();
    await page.getByRole('button',{name:'Open After the Rain (2025 Remaster) in AudioShelf'}).click();
    await page.getByRole('heading',{name:'After the Rain',exact:true}).waitFor();
    assert((await page.locator('.sleeve-footnote').textContent()).includes('RECORD STORE'));
    assert.equal(await page.getByRole('button',{name:'Add to shelf',exact:false}).count(),1);
    // Non-studio and unmatched releases remain in AudioShelf as a pre-filled search.
    await fetch(base+'/__test/playback-options',{method:'POST',headers:{'Content-Type':'application/json','X-AudioShelf-Request':'1'},body:JSON.stringify({external_album:true,album:'A Very Rare Live Collection'})});
    await page.evaluate(()=>refreshPlayback(true));
    await page.getByRole('button',{name:'Open A Very Rare Live Collection in AudioShelf'}).click();
    await page.getByRole('searchbox',{name:'Search record store'}).waitFor();
    assert.equal(await page.getByRole('searchbox',{name:'Search record store'}).inputValue(),'A Very Rare Live Collection');
    await page.getByRole('link',{name:'Open settings'}).click();
    assert.equal(await page.getByLabel('Show previous and next buttons in Now Playing').isChecked(),false);
    await page.getByLabel('Show previous and next buttons in Now Playing').check();
    await page.getByRole('button',{name:'Next track'}).waitFor();
    await page.getByRole('button',{name:'Next track'}).click();
    await page.reload();
    await page.getByRole('heading',{name:'Settings.'}).waitFor();
    assert.equal(await page.getByLabel('Show previous and next buttons in Now Playing').isChecked(),true,'Skip preference persists');
    await page.getByLabel('Show previous and next buttons in Now Playing').uncheck();
    assert.equal(await page.getByRole('button',{name:'Next track'}).count(),0);
    await page.setViewportSize({width:320,height:740});
    await noOverflow();
    await page.getByLabel('Show previous and next buttons in Now Playing').check();
    await page.getByRole('button',{name:'Next track'}).waitFor();
    await noOverflow();await shot('now-playing-skips-mobile');
    await page.getByLabel('Show previous and next buttons in Now Playing').uncheck();
    await page.getByRole('button',{name:'Classic The original AudioShelf interface',exact:false}).click();
    await page.locator('html[data-interface="classic"]').waitFor();
    assert.equal(await page.locator('#turntable').isVisible(),false);
    await page.reload();await page.getByRole('heading',{name:'Settings.'}).waitFor();
    assert.equal(await page.locator('html').getAttribute('data-interface'),'classic');
    await page.locator('[data-nav="shelf"]').click();
    await page.locator('.artist-row').first().waitFor();
    await page.getByRole('link',{name:'Open settings'}).click();
    await page.getByRole('button',{name:'Vinyl Front-facing sleeves',exact:false}).click();
    await page.locator('html[data-interface="vinyl"]').waitFor();
    await page.setViewportSize({width:320,height:740});await noOverflow();
    await page.locator('[data-nav="shelf"]').click();await page.getByRole('button',{name:'Expand all',exact:true}).click();await page.locator('.shelf-rack').first().waitFor();await noOverflow();
    // Both furniture styles persist and have continuous ledges even on short rows.
    for(const [style,label] of [['cabinet','White record cabinet'],['floating','Floating shelves']]){
      await page.getByRole('link',{name:'Open settings'}).click();
      await page.getByRole('button',{name:label,exact:false}).click();
      await page.locator('html[data-shelf-style="'+style+'"]').waitFor();
      await page.reload();await page.getByRole('heading',{name:'Settings.'}).waitFor();
      assert.equal(await page.locator('html').getAttribute('data-shelf-style'),style);
      await page.locator('[data-nav="shelf"]').click();
      await page.getByRole('button',{name:'Expand all',exact:true}).click();
      for(const width of [390,1440]){
        await page.setViewportSize({width,height:900});
        await page.waitForFunction(w=>document.querySelector('.shelf-row')?.querySelectorAll('.sleeve').length===(w<=700?2:4),width);
        await noOverflow();await shot(style+'-'+width);
        const shelves=await page.locator('.shelf-row').evaluateAll(rows=>rows.map(row=>{
          const shelf=row.querySelector('.shelf-ledge').getBoundingClientRect(),cover=row.querySelector('.cover-wrap').getBoundingClientRect(),rack=row.closest('.shelf-rack').getBoundingClientRect();
          return {width:shelf.width,rack:rack.width,gap:shelf.top-cover.bottom};
        }));
        assert(shelves.every(s=>s.width>=s.rack-22&&Math.abs(s.gap)<2),'Ledges span the rack and meet the sleeves');
      }
    }
    await page.evaluate(()=>applyTheme('midnight'));
    assert.equal(await page.locator('.shelf-row .album-title').first().evaluate(el=>getComputedStyle(el).color),'rgb(40, 43, 39)','White shelves retain readable captions with dark surrounding themes');
    // The server worker has already independently observed the exact first
    // track playing on the intended phone. Render PLAYING synchronously, without
    // waiting for a redundant Spotify /me/player request to complete.
    const verifiedStartup=await page.evaluate(()=>{
      const album={id:'f5093c06-23e3-4f01-aeaa-40f72885ee3a',
        title:'The Original Album',artists:[{name:'The Artist'}]};
      const result={first_track:{id:'a'.repeat(22),title:'Opening',duration_ms:180000},
        device:'Fixture phone',started_at:Date.now()/1000};
      const originalRefresh=refreshPlayback;
      refreshPlayback=async()=>{}; // Deliberately withhold the follow-up request.
      try{
        beginPlayback(album,result,true);
        const unverifiedLabel=document.querySelector('#turntable small b').textContent;
        beginPlayback(album,result,false,true);
        const verifiedLabel=document.querySelector('#turntable small b').textContent;
        return {unverifiedLabel,verifiedLabel,playing:playbackState.playing,starting:playbackState.starting};
      }finally{refreshPlayback=originalRefresh;}
    });
    assert.equal(verifiedStartup.unverifiedLabel,'STARTING');
    assert.equal(verifiedStartup.verifiedLabel,'PLAYING','Server-confirmed track must show PLAYING without a second Spotify request');
    assert.equal(verifiedStartup.playing,true);
    assert.equal(verifiedStartup.starting,false);
    assert.deepEqual(errors,[]);
    console.log('Vinyl browser checks passed: two rooms, responsive sleeves, browsing restoration, collect in place, rapid startup despite stale replies, progress/pause/track boundaries, playback independence/outage, album settings, Classic persistence.');
  }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);console.error(logs.slice(-4000));process.exitCode=1;});
