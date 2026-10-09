const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const port = process.env.AUDIOSHELF_TEST_PORT || '8101';
const base = `http://127.0.0.1:${port}`;
let fixtureCookie='';
const fixtureFetch=(url,options={})=>fetch(url,{...options,headers:{...options.headers,Cookie:fixtureCookie}});
const python = process.env.AUDIOSHELF_TEST_PYTHON || 'python';
const server = spawn(python, [path.join(__dirname,'ui_server.py')], {env:{...process.env,AUDIOSHELF_TEST_PORT:port},stdio:['ignore','pipe','pipe']});
let logs='';server.stdout.on('data',d=>logs+=d);server.stderr.on('data',d=>logs+=d);
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const screenshotDir=process.env.AUDIOSHELF_SCREENSHOT_DIR;

(async()=>{
  let browser;
  try {
    let ready=false;
    for(let n=0;n<80;n++){try{if((await fixtureFetch(base+'/health')).ok){ready=true;break;}}catch{}await wait(100);}
    assert(ready,'Fixture server did not start: '+logs);
    browser=await chromium.launch({headless:true,executablePath:process.env.AUDIOSHELF_TEST_BROWSER || undefined,
      args:process.env.AUDIOSHELF_TEST_BROWSER ? ['--no-sandbox','--disable-dev-shm-usage'] : []});
    const errors=[];
    const page=await browser.newPage({viewport:{width:390,height:844}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('https://coverartarchive.org/**',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="500" height="500"><rect width="500" height="500" fill="#486359"/><circle cx="250" cy="250" r="180" fill="#282b27"/><circle cx="250" cy="250" r="50" fill="#e4d1a3"/></svg>'}));
    await page.goto(base);
    await page.getByLabel('Password',{exact:true}).fill('fixture-owner-password');
    await page.getByRole('button',{name:'Open AudioShelf',exact:true}).click();
    await page.getByRole('heading',{name:'My shelf.'}).waitFor();
    fixtureCookie=(await page.context().cookies()).map(c=>`${c.name}=${c.value}`).join('; ');
    await fixtureFetch(base+'/api/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-AudioShelf-Request':'1'},body:JSON.stringify({interface:'classic'})});
    await page.reload();
    await page.getByRole('heading',{name:'My shelf.'}).waitFor();
    await page.getByRole('heading',{name:'Your first record awaits.'}).waitFor();
    if(screenshotDir){fs.mkdirSync(screenshotDir,{recursive:true});await page.screenshot({path:path.join(screenshotDir,'empty-mobile.png'),fullPage:true});}
    await page.getByRole('link',{name:'Visit the record store'}).click();
    await page.getByRole('searchbox').fill('The Artist');
    await page.getByRole('button',{name:'Search',exact:true}).click();
    await page.locator('.search-result').filter({hasText:'The Artist'}).click();
    await page.getByRole('heading',{name:'The Artist',exact:true}).waitFor();
    await page.getByRole('button',{name:'Add to shelf'}).click();
    await page.getByRole('heading',{name:'Add a playable edition'}).waitFor();
    await page.locator('[data-action="choose-release"][data-spotify-id]').first().click();
    await page.locator('.album-card .badge').filter({hasText:'On your shelf'}).waitFor();
    await page.locator('.album-title').click();
    await page.getByRole('heading',{name:'The Original Album',exact:true}).waitFor();
    assert.equal(await page.locator('.track-row').count(),2);
    await page.locator('.cover').evaluate(image=>image.decode());
    assert.equal(await page.locator('.cover').evaluate(async image=>{const bitmap=await createImageBitmap(await (await fetch(image.currentSrc)).blob());const width=bitmap.width;bitmap.close();return width;}),50);
    assert((await page.locator('.cover').getAttribute('src')).startsWith('api/albums/'));
    assert((await page.locator('.cover').getAttribute('srcset')).includes('640w'));
    assert((await page.locator('.cover').evaluate(img=>img.currentSrc)).includes('size='));
    await page.getByRole('button',{name:'Album settings'}).click();
    const cover=await (await fixtureFetch(base+'/api/albums/f5093c06-23e3-4f01-aeaa-40f72885ee3a/artwork')).arrayBuffer();
    await page.getByLabel('Choose album cover').setInputFiles({name:'cover.png',mimeType:'image/png',buffer:Buffer.from(cover)});
    await page.getByRole('button',{name:'Save cover',exact:true}).click();
    await page.locator('#toast').filter({hasText:'Album cover saved.'}).waitFor();
    const custom=await fixtureFetch(base+'/api/albums/f5093c06-23e3-4f01-aeaa-40f72885ee3a/artwork');
    assert.equal(custom.headers.get('X-Artwork-Source'),'custom');
    await page.getByRole('button',{name:'Album settings'}).click();
    await page.getByRole('button',{name:'Restore automatic cover'}).click();
    await page.locator('#toast').filter({hasText:'Automatic artwork restored.'}).waitFor();
    await page.getByRole('button',{name:'Album settings'}).click();
    let failEditionRequest=true;
    await page.route('**/api/albums/*/releases?offset=*',async route=>{
      if(failEditionRequest){failEditionRequest=false;await route.fulfill({status:502,contentType:'application/json',body:JSON.stringify({error:'Fixture MusicBrainz failure'})});}
      else await route.continue();
    });
    await page.getByRole('button',{name:'Change original tracklist'}).click();
    await page.getByRole('button',{name:'Retry checks'}).click();
    await page.locator('[data-action="choose-release"]').first().waitFor();
    await page.getByRole('button',{name:'Close',exact:true}).click();
    await page.getByRole('button',{name:'Album settings'}).click();
    await page.getByRole('button',{name:'Choose cover from another edition'}).click();
    const thumbnail=page.locator('.cover-choice img').first();
    await thumbnail.evaluate(image=>image.decode());
    assert.equal(await thumbnail.evaluate(image=>image.naturalWidth),50);
    assert((await thumbnail.getAttribute('src')).includes('/artwork-preview/'));
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,'cover-picker-mobile.png'),fullPage:true});
    await page.locator('[data-action="choose-cover"]').first().click();
    await page.locator('#toast').filter({hasText:'Edition cover saved.'}).waitFor();
    await page.getByRole('button',{name:'This tracklist is correct'}).click();
    await page.getByRole('button',{name:'This tracklist is correct'}).waitFor({state:'hidden'});
    await page.getByRole('button',{name:'Find another edition'}).click();
    await page.getByRole('heading',{name:'Spotify editions'}).waitFor();
    await page.getByRole('button',{name:'Close',exact:true}).click();
    await page.getByRole('button',{name:'Play album'}).click();
    await page.getByRole('heading',{name:'Choose playback device',exact:true}).waitFor();
    await page.getByRole('button',{name:'Fixture phone',exact:false}).click();
    await page.locator('#toast').filter({hasText:'Playing 2 tracks on Fixture phone.'}).waitFor();
    const fixtureOptions=body=>fixtureFetch(base+'/__test/playback-options',{method:'POST',headers:{'Content-Type':'application/json','X-AudioShelf-Request':'1'},body:JSON.stringify(body)});
    await fixtureOptions({phone_available:false});
    await page.getByRole('button',{name:'Play album'}).click();
    await page.getByRole('heading',{name:'Starting playback on Fixture phone',exact:true}).waitFor();
    await page.locator('#playback-handoff-status').waitFor();
    assert((await page.locator('#playback-handoff-status').textContent()).length>0,
      'Live startup progress must be visible before opening Spotify');
    assert.equal(await page.getByRole('link',{name:'Open Spotify',exact:true}).getAttribute('href'),'spotify:');
    await page.evaluate(()=>document.addEventListener('click',event=>{if(event.target.closest('a[href="spotify:"]'))event.preventDefault();},true));
    await page.getByRole('link',{name:'Open Spotify',exact:true}).click();
    await page.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,get:()=>true}));
    await wait(2200);
    assert.equal((await (await fixtureFetch(base+'/__test/play-calls')).json()).calls.length,1,'An available speaker must not replace the chosen phone');
    await fixtureOptions({phone_available:true});
    for(let attempt=0;attempt<30;attempt++){
      if((await (await fixtureFetch(base+'/__test/play-calls')).json()).calls.length===2)break;
      await wait(200);
    }
    assert.equal((await (await fixtureFetch(base+'/__test/play-calls')).json()).calls.length,2,'Server must finish playback while AudioShelf is hidden');
    await page.evaluate(()=>{delete document.hidden;window.dispatchEvent(new Event('focus'));});
    await page.waitForFunction(()=>!document.querySelector('#modal').open);
    const calls=await (await fixtureFetch(base+'/__test/play-calls')).json();
    assert.equal(calls.calls.length,2);
    for(const call of calls.calls)assert.deepEqual(call,{uris:['spotify:track:'+'a'.repeat(22),'spotify:track:'+'b'.repeat(22)],position_ms:0});
    await page.getByRole('button',{name:'Change device',exact:true}).click();
    await page.getByRole('heading',{name:'Choose playback device',exact:true}).waitFor();
    await page.getByRole('button',{name:'Fixture phone',exact:false}).click();
    await page.waitForFunction(()=>!document.querySelector('#modal').open);
    assert.equal((await (await fixtureFetch(base+'/__test/play-calls')).json()).calls.length,2,'Changing the preferred device alone must not restart music');
    // The optional helper never participates in successful playback. On HTTP,
    // even an opted-in Android browser must retain the ordinary Spotify fallback.
    await page.evaluate(()=>{
      Object.defineProperty(navigator,'userAgent',{configurable:true,value:'Mozilla/5.0 Android'});
      localStorage.setItem(browserPreferenceKey('audioshelf-android-helper'),'true');
      statusInfo.spotify_client_id='c'.repeat(32);
    });
    assert.equal(await page.evaluate(()=>androidHelperEnabled()),true);
    assert.equal(await page.evaluate(()=>androidHelperLink()),null,'Helper requests require HTTPS');
    // Success handoff also opens the app without selecting Spotify content.
    await page.evaluate(()=>localStorage.setItem('audioshelf-open-spotify','true'));
    await page.getByRole('button',{name:'Play album'}).click();
    await page.getByRole('heading',{name:'Playing on Fixture phone',exact:true}).waitFor();
    assert.equal(await page.locator('[data-action="open-playback-helper"]').count(),0,'Available phone must never launch the helper');
    assert.equal(await page.getByRole('link',{name:'Open Spotify',exact:true}).getAttribute('href'),'spotify:');
    await page.getByRole('button',{name:'Close',exact:true}).click();
    await page.evaluate(()=>localStorage.removeItem('audioshelf-open-spotify'));
    // Background handoff preserves the selected disc and queues it once.
    await fixtureOptions({phone_available:false,disc:2});
    await page.evaluate(()=>{currentAlbum.tracks[1].disc_number=2;content.innerHTML=albumPage(currentAlbum);});
    await page.getByRole('button',{name:'Play disc 2'}).click();
    await page.getByRole('heading',{name:'Starting playback on Fixture phone',exact:true}).waitFor();
    await page.getByRole('link',{name:'Open Spotify',exact:true}).click();
    await fixtureOptions({phone_available:true});
    await page.waitForFunction(()=>!document.querySelector('#modal').open);
    const discCalls=(await (await fixtureFetch(base+'/__test/play-calls')).json()).calls;
    assert.equal(discCalls.length,4);
    assert.deepEqual(discCalls[3],{uris:['spotify:track:'+'b'.repeat(22)],position_ms:0});
    await fixtureOptions({phone_available:false});
    await page.getByRole('button',{name:'Play disc 2'}).click();
    await page.getByRole('heading',{name:'Starting playback on Fixture phone',exact:true}).waitFor();
    const cancelledJob=await page.evaluate(()=>pendingPlayback.job);
    await page.getByRole('button',{name:'Close',exact:true}).click();
    await page.waitForFunction(()=>pendingPlayback===null);
    let cancelledState;
    for(let attempt=0;attempt<30;attempt++){
      cancelledState=(await (await fixtureFetch(base+'/api/spotify/playback-handoff/'+cancelledJob)).json()).state;
      if(cancelledState==='cancelled')break;
      await wait(100);
    }
    assert.equal(cancelledState,'cancelled');
    await fixtureOptions({phone_available:true,disc:1});
    await wait(2200);
    assert.equal((await (await fixtureFetch(base+'/__test/play-calls')).json()).calls.length,4);
    await page.evaluate(()=>{currentAlbum.tracks[1].disc_number=1;content.innerHTML=albumPage(currentAlbum);});
    assert.equal(await page.locator('.track-row').count(),2);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Album page overflows phone viewport');
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,'album-mobile.png'),fullPage:true});
    await page.getByRole('link',{name:'My shelf',exact:false}).last().click();
    await page.locator('.artist-row').waitFor();
    await page.locator('#artist-filter').fill('missing');
    assert.equal(await page.locator('.artist-row:visible').count(),0);
    await page.locator('#artist-filter').fill('artist');
    assert.equal(await page.locator('.artist-row:visible').count(),1);
    await page.getByRole('button',{name:'Albums',exact:true}).click();
    await page.locator('.album-card').waitFor();
    await page.setViewportSize({width:1280,height:900});
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,'shelf-desktop.png'),fullPage:true});
    await page.getByRole('link',{name:'Open settings'}).click();
    await page.getByRole('heading',{name:'Settings.'}).waitFor();
    await page.getByRole('button',{name:'Choose Spotify device'}).click();
    await page.getByRole('button',{name:'Fixture phone'}).click();
    await page.locator('#toast').filter({hasText:'Playback device saved.'}).waitFor();
    assert.equal((await (await fixtureFetch(base+'/api/settings')).json()).preferred_device.id,'phone');
    await page.getByLabel('Open Spotify after pressing Play on this browser').check();
    await page.reload();await page.getByRole('heading',{name:'Settings.'}).waitFor();
    assert.equal(await page.getByLabel('Open Spotify after pressing Play on this browser').isChecked(),true);
    await page.getByLabel('Open Spotify after pressing Play on this browser').uncheck();
    const filterForm=page.locator('#release-filters-form');
    assert.equal(await page.getByLabel('Country preference order').inputValue(),'GB, US, XW, XE');
    assert.equal(await page.getByLabel('Only use these countries').isChecked(),false);
    assert.equal(await filterForm.getByLabel('Cassette',{exact:true}).isChecked(),false);
    await page.getByLabel('Country preference order').fill('GB, US');
    await page.getByRole('button',{name:'Move CD earlier',exact:true}).click();
    await page.getByRole('button',{name:'Save release filters'}).click();
    await page.locator('#toast').filter({hasText:'Release filters saved.'}).waitFor();
    assert.deepEqual((await (await fixtureFetch(base+'/api/settings')).json()).release_filters,
      {countries:['GB','US'],formats:['cd','vinyl','digital'],strict_countries:false});
    const themes=(await (await fixtureFetch(base+'/api/settings')).json()).themes;
    assert.equal(await page.locator('.theme-choice').count(),10);
    await page.setViewportSize({width:390,height:844});
    const backgrounds=new Set();
    for(const theme of themes){
      await page.getByRole('button',{name:`Use ${theme.name} theme`,exact:true}).click();
      await page.locator(`.theme-choice[data-id="${theme.id}"][aria-pressed="true"]`).waitFor();
      const palette=await page.evaluate(()=>{
        const root=getComputedStyle(document.documentElement);
        const get=key=>root.getPropertyValue('--'+key).trim();
        return {theme:document.documentElement.dataset.theme,paper:get('paper'),ink:get('ink'),muted:get('muted'),accent:get('wine'),onAccent:get('on-accent')};
      });
      assert.equal(palette.theme,theme.id);backgrounds.add(palette.paper);
      const previewBackgrounds=await page.locator('.theme-choice').evaluateAll(choices=>choices.map(choice=>getComputedStyle(choice).backgroundColor));
      assert.equal(new Set(previewBackgrounds).size,10,'Theme previews must retain their own palettes');
      const luminance=hex=>{const channels=hex.slice(1).match(/../g).map(value=>parseInt(value,16)/255).map(value=>value<=.04045?value/12.92:((value+.055)/1.055)**2.4);return .2126*channels[0]+.7152*channels[1]+.0722*channels[2];};
      const contrast=(a,b)=>{const values=[luminance(a),luminance(b)].sort((a,b)=>b-a);return (values[0]+.05)/(values[1]+.05);};
      assert(contrast(palette.ink,palette.paper)>=4.5,`${theme.name} text contrast`);
      assert(contrast(palette.muted,palette.paper)>=4.5,`${theme.name} muted text contrast`);
      assert(contrast(palette.accent,palette.onAccent)>=4.5,`${theme.name} button contrast`);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${theme.name} settings overflow`);
      if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,`theme-${theme.id}-mobile.png`),fullPage:true});
      await page.goto(base+'/#album/f5093c06-23e3-4f01-aeaa-40f72885ee3a');
      await page.getByRole('heading',{name:'The Original Album',exact:true}).waitFor();
      assert.equal(await page.locator('.track-row').count(),2);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${theme.name} album overflow`);
      if(screenshotDir&&(theme.id==='midnight'||theme.id==='high-contrast'))await page.screenshot({path:path.join(screenshotDir,`album-${theme.id}-mobile.png`),fullPage:true});
      await page.goto(base+'/#settings');await page.getByRole('heading',{name:'Settings.'}).waitFor();

    }
    assert.equal(backgrounds.size,10);
    await page.reload();await page.getByRole('heading',{name:'Settings.'}).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'high-contrast');
    await page.getByRole('button',{name:'Use Record Store theme',exact:true}).click();
    await page.locator('.theme-choice[data-id="record-store"][aria-pressed="true"]').waitFor();
    if(screenshotDir){await page.setViewportSize({width:1280,height:900});await page.screenshot({path:path.join(screenshotDir,'settings-themes-desktop.png'),fullPage:true});}
    await page.goto(base+'/#artist/f181961b-20f7-459e-89de-920ef03c7ed0/store');
    await page.getByRole('button',{name:'Manage catalogue'}).click();
    await page.getByRole('button',{name:'Find catalogues',exact:true}).click();
    await page.getByRole('button',{name:'Choose The Artist core catalogue',exact:true}).click();
    assert.equal(await page.getByLabel('MusicBrainz series link or ID').inputValue(),'55df510f-2f2b-4d1e-b7d6-0b54c1c9f48e');
    await page.getByRole('button',{name:'Save curated series',exact:true}).click();
    await page.locator('#toast').filter({hasText:'Curated catalogue saved.'}).waitFor();
    await page.getByRole('button',{name:'Manage catalogue'}).click();
    await page.getByText('The Artist core catalogue',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Catalogue discovery overflow');
    await page.getByLabel('Catalogue rule for The Original Album').selectOption('exclude');
    await page.locator('#toast').filter({hasText:'Catalogue rule saved.'}).waitFor();
    assert.equal(await page.locator('.album-card').count(),0);
    await page.getByLabel('Catalogue rule for The Original Album').selectOption('auto');
    await page.locator('.album-card').waitFor();
    await page.getByRole('button',{name:'Close',exact:true}).click();
    const report=await (await fixtureFetch(base+'/api/albums/f5093c06-23e3-4f01-aeaa-40f72885ee3a/diagnostics')).json();
    assert.equal(report.format,'audioshelf-diagnostics-1');
    assert(report.events.some(event=>event.event==='spotify_candidates'));
    assert(!JSON.stringify(report).includes('fixture-only'));
    assert.deepEqual(errors,[],'Browser JavaScript errors');
    const ingressPage=await browser.newPage({viewport:{width:390,height:844}});
    await ingressPage.route('**/api/hassio_ingress/fixture/**',async route=>{
      const incoming=route.request();const url=new URL(incoming.url());
      const localPath=url.pathname.replace('/api/hassio_ingress/fixture','') || '/';
      const response=await fixtureFetch(base+localPath+url.search,{method:incoming.method(),headers:{...incoming.headers(),'X-Ingress-Path':'/api/hassio_ingress/fixture'},body:incoming.postData()||undefined});
      let body=Buffer.from(await response.arrayBuffer());
      if(localPath==='/')body=Buffer.from(body.toString().replace('<base href="/">','<base href="/api/hassio_ingress/fixture/">'));
      await route.fulfill({status:response.status,headers:Object.fromEntries(response.headers),body});
    });
    await ingressPage.goto(base+'/api/hassio_ingress/fixture/');
    await ingressPage.getByRole('heading',{name:'My shelf.'}).waitFor();
    await ingressPage.locator('.artist-row').waitFor();
    await ingressPage.goto(base+'/api/hassio_ingress/fixture/#album/f5093c06-23e3-4f01-aeaa-40f72885ee3a');
    await ingressPage.getByRole('button',{name:'Album settings'}).click();
    await ingressPage.getByRole('button',{name:'Choose cover from another edition'}).click();
    await ingressPage.locator('.cover-choice img').first().evaluate(image=>image.decode());
    assert.equal(await ingressPage.locator('.cover-choice img').first().evaluate(image=>image.naturalWidth),50);

    await page.goto(base+'/#settings');
    await page.getByRole('heading',{name:'Settings.'}).waitFor();
    await page.getByRole('button',{name:'Open Security settings'}).click();
    const supportForm=page.locator('[data-operation="support"]');
    await supportForm.getByLabel('Confirm your password').fill('fixture-owner-password');
    await supportForm.getByRole('button',{name:'Create temporary login'}).click();
    const temporaryPassword=await page.getByLabel('Temporary support password').inputValue();
    const supportContext=await browser.newContext();
    const supportPage=await supportContext.newPage();
    await supportPage.goto(base);
    await supportPage.getByLabel('Password',{exact:true}).fill(temporaryPassword);
    await supportPage.getByRole('button',{name:'Open AudioShelf'}).click();
    await supportPage.getByRole('heading',{name:'My shelf.'}).waitFor();
    await supportPage.goto(base+'/#album/f5093c06-23e3-4f01-aeaa-40f72885ee3a');
    await supportPage.getByRole('heading',{name:'The Original Album',exact:true}).waitFor();
    assert.equal(await supportPage.getByRole('button',{name:'Play album'}).isDisabled(),true);
    await page.getByRole('button',{name:'Back to Security'}).click();
    const revokeForm=page.locator('[data-operation$="/revoke"]').first();
    await revokeForm.getByLabel('Confirm your password').fill('fixture-owner-password');
    await revokeForm.getByRole('button',{name:'Revoke this login'}).click();
    await page.locator('#toast').filter({hasText:'Security settings updated.'}).waitFor();
    await supportPage.reload();await supportPage.getByRole('heading',{name:'Open your shelf.'}).waitFor();
    await supportContext.close();
    const setupForm=page.locator('[data-operation="totp/start"]');
    await setupForm.getByLabel('Confirm your password').fill('fixture-owner-password');
    await setupForm.getByRole('button',{name:'Set up authenticator'}).click();
    await page.getByRole('heading',{name:'Set up authenticator',exact:true}).waitFor();
    await page.locator('.totp-qr').evaluate(image=>image.decode());
    assert((await page.locator('.totp-qr').getAttribute('src')).startsWith('data:image/png;base64,'));
    await page.getByLabel('Confirm your password').fill('fixture-owner-password');
    const code=(await (await fixtureFetch(base+'/__test/current-code')).json()).code;
    await page.getByLabel('Six-digit setup code').fill(code);
    await page.getByRole('button',{name:'Enable two-factor authentication'}).click();
    const recoveryCodes=(await page.getByLabel('Recovery codes').inputValue()).split('\n');
    assert.equal(recoveryCodes.length,10);
    fixtureCookie=(await page.context().cookies()).map(c=>`${c.name}=${c.value}`).join('; ');
    await page.getByRole('button',{name:'I have saved the codes'}).click();
    const disableForm=page.locator('[data-operation="totp/disable"]');
    await disableForm.getByLabel('Confirm your password').fill('fixture-owner-password');
    await disableForm.getByLabel('Fresh authenticator or recovery code').fill(recoveryCodes[0]);
    await disableForm.getByRole('button',{name:'Disable two-factor authentication'}).click();
    await page.getByRole('button',{name:'Set up authenticator'}).waitFor();
    // Disabling 2FA rotates the session; status mocks must retain current identity.
    fixtureCookie=(await page.context().cookies()).map(c=>`${c.name}=${c.value}`).join('; ');
    assert.deepEqual(errors,[],'Browser security flow errors');
    await page.getByRole('button',{name:'Close',exact:true}).click();
    await page.goto(base+'/#settings');
    await page.getByRole('heading',{name:'Settings.'}).waitFor();
    await page.getByLabel('Country preference order').fill('US, GB');
    // The standalone PWA retains private shelf thumbnails across app updates.
    assert.equal(await page.getByRole('heading',{name:'Offline artwork'}).count(),1);
    await page.waitForFunction(()=>!!navigator.serviceWorker.controller);
    await page.getByRole('button',{name:'Save my shelf’s artwork'}).click();
    await page.locator('#toast').filter({hasText:'Shelf artwork ready on this device'}).waitFor();
    await page.waitForFunction(()=>document.querySelector('#artwork-cache-stats')?.textContent.includes('stored on this device'));
    const cacheBefore=await page.evaluate(async()=>{
      const name=artworkCacheName(),cache=await caches.open(name),keys=await cache.keys();
      const stats=await artworkCacheStats();
      return {name,urls:keys.map(key=>key.url),...stats};
    });
    assert(cacheBefore.albums>=1&&cacheBefore.files>=1&&cacheBefore.bytes>0,'Artwork preloading must persist WebP thumbnails in Cache Storage');
    assert(cacheBefore.urls.every(source=>{const url=new URL(source);return url.searchParams.get('account')==='owner'&&['128','320','640'].includes(url.searchParams.get('size'))&&/\/api\/albums\/[a-f0-9-]{36}\/artwork$/i.test(url.pathname);}), 'Only private shelf thumbnails are cached');
    assert((await page.locator('#artwork-cache-stats').textContent()).includes('thumbnail'));
    // A stored thumbnail can be served by the service worker without network.
    await page.context().setOffline(true);
    const offlineCover=await page.evaluate(async url=>{
      const response=await fetch(url,{credentials:'same-origin'});
      return {ok:response.ok,type:response.headers.get('Content-Type'),bytes:(await response.blob()).size};
    },cacheBefore.urls[0]);
    await page.context().setOffline(false);
    assert(offlineCover.ok&&offlineCover.bytes>0&&offlineCover.type?.includes('image/'),'Offline browsing uses the locally stored cover');
    // Explicit clear reclaims space and does not affect the server-side artwork.
    await page.getByRole('button',{name:'Clear downloaded artwork'}).click();
    await page.waitForFunction(()=>document.querySelector('#artwork-cache-stats')?.textContent.includes('0 thumbnails'));
    assert.equal((await page.evaluate(()=>artworkCacheStats())).files,0);
    await page.getByRole('button',{name:'Save my shelf’s artwork'}).click();
    await page.locator('#toast').filter({hasText:'Shelf artwork ready on this device'}).waitFor();
    await page.waitForFunction(()=>document.querySelector('#artwork-cache-stats')?.textContent.includes('stored on this device') &&
      !document.querySelector('#artwork-cache-stats')?.textContent.includes('0 thumbnails'));
    const initialVersion=await page.evaluate(()=>document.documentElement.dataset.assetVersion);
    await page.route('**/api/status',async route=>{
      const response=await fixtureFetch(base+'/api/status');const body=await response.json();
      body.build.asset_version='future-build-test';
      await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    });
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await page.getByRole('button',{name:'Reload AudioShelf'}).waitFor();
    assert.equal(await page.locator('#app-update').getAttribute('role'),'alert');
    assert.equal(await page.locator('#app-update').evaluate(el=>getComputedStyle(el).position),'sticky');
    await page.evaluate(()=>window.scrollTo(0,800));
    assert(Math.abs((await page.locator('#app-update').boundingBox()).y)<2,'Reload banner remains visible while scrolling');
    assert((await page.getByRole('button',{name:'Reload AudioShelf'}).boundingBox()).height>=48);
    assert.equal(await page.getByLabel('Country preference order').inputValue(),'US, GB');
    page.once('dialog',dialog=>dialog.dismiss());
    await page.getByRole('button',{name:'Reload AudioShelf'}).click();
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.assetVersion),initialVersion);
    assert.equal(await page.getByLabel('Country preference order').inputValue(),'US, GB');
    await page.unroute('**/api/status');
    page.once('dialog',dialog=>dialog.accept());
    await page.getByRole('button',{name:'Reload AudioShelf'}).click();
    await page.getByRole('heading',{name:'Settings.'}).waitFor();
    assert.equal(await page.locator('#app-update').isVisible(),false);
    const workerState=await page.evaluate(async()=>{
      await navigator.serviceWorker.ready;
      await caches.open('audioshelf-shell-obsolete-test');
      const tag=document.documentElement.dataset.assetVersion+'-worker-test';
      const registration=await navigator.serviceWorker.register(new URL('sw.js?v='+tag,document.baseURI),{updateViaCache:'none'});
      for(let attempt=0;attempt<100;attempt++){
        const keys=await caches.keys();
        if(registration.active?.scriptURL.endsWith(tag)&&!keys.includes('audioshelf-shell-obsolete-test'))return {keys,tag};
        await new Promise(resolve=>setTimeout(resolve,100));
      }
      throw new Error('Updated service worker did not activate and remove stale cache');
    });
    assert(workerState.keys.includes('audioshelf-shell-'+workerState.tag));
    assert(workerState.keys.includes(cacheBefore.name),'Updating the PWA must not delete stored artwork');
    const accountSwap=await page.evaluate(async()=>{
      await syncArtworkCacheAccount('deadbeefdeadbeefdeadbeefdeadbeef');
      return (await caches.keys()).filter(key=>key.startsWith(ARTWORK_CACHE_PREFIX));
    });
    assert(!accountSwap.includes(cacheBefore.name),'Switching accounts must discard the previous account’s artwork');


    console.log('Browser checks passed: collection, artwork selection, edition retry, track review and playback, release preferences, all 10 themes and contrast, catalogue overrides, diagnostics, desktop and ingress.');
  }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;});
