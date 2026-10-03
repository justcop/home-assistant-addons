const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const port = process.env.AUDIOSHELF_TEST_PORT || '8101';
const base = `http://127.0.0.1:${port}`;
const python = process.env.AUDIOSHELF_TEST_PYTHON || 'python';
const server = spawn(python, [path.join(__dirname,'ui_server.py')], {env:{...process.env,AUDIOSHELF_TEST_PORT:port},stdio:['ignore','pipe','pipe']});
let logs='';server.stdout.on('data',d=>logs+=d);server.stderr.on('data',d=>logs+=d);
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const screenshotDir=process.env.AUDIOSHELF_SCREENSHOT_DIR;

(async()=>{
  let browser;
  try {
    let ready=false;
    for(let n=0;n<80;n++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}await wait(100);}
    assert(ready,'Fixture server did not start: '+logs);
    browser=await chromium.launch({headless:true,executablePath:process.env.AUDIOSHELF_TEST_BROWSER || undefined,
      args:process.env.AUDIOSHELF_TEST_BROWSER ? ['--no-sandbox','--disable-dev-shm-usage'] : []});
    const errors=[];
    const page=await browser.newPage({viewport:{width:390,height:844}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('https://coverartarchive.org/**',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="500" height="500"><rect width="500" height="500" fill="#486359"/><circle cx="250" cy="250" r="180" fill="#282b27"/><circle cx="250" cy="250" r="50" fill="#e4d1a3"/></svg>'}));
    await page.goto(base);
    await page.getByRole('heading',{name:'My shelf.'}).waitFor();
    await page.getByRole('heading',{name:'Your first record awaits.'}).waitFor();
    if(screenshotDir){fs.mkdirSync(screenshotDir,{recursive:true});await page.screenshot({path:path.join(screenshotDir,'empty-mobile.png'),fullPage:true});}
    await page.getByRole('link',{name:'Visit the record store'}).click();
    await page.getByRole('searchbox').fill('The Artist');
    await page.getByRole('button',{name:'Search',exact:true}).click();
    await page.locator('.search-result').filter({hasText:'The Artist'}).click();
    await page.getByRole('heading',{name:'The Artist',exact:true}).waitFor();
    await page.getByRole('button',{name:'Add to shelf'}).click();
    await page.getByRole('button',{name:'On your shelf'}).waitFor();
    await page.locator('.album-title').click();
    await page.getByRole('heading',{name:'The Original Album',exact:true}).waitFor();
    assert.equal(await page.locator('.track-row').count(),2);
    await page.locator('.cover').evaluate(image=>image.decode());
    assert.equal(await page.locator('.cover').evaluate(image=>image.naturalWidth),50);
    assert((await page.locator('.cover').getAttribute('src')).startsWith('api/albums/'));
    await page.getByRole('button',{name:'Album settings'}).click();
    const cover=await (await fetch(base+'/api/albums/f5093c06-23e3-4f01-aeaa-40f72885ee3a/artwork')).arrayBuffer();
    await page.getByLabel('Choose album cover').setInputFiles({name:'cover.png',mimeType:'image/png',buffer:Buffer.from(cover)});
    await page.getByRole('button',{name:'Save cover',exact:true}).click();
    await page.locator('#toast').filter({hasText:'Album cover saved.'}).waitFor();
    const custom=await fetch(base+'/api/albums/f5093c06-23e3-4f01-aeaa-40f72885ee3a/artwork');
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
    await page.getByRole('button',{name:'Retry edition search'}).click();
    await page.locator('[data-action="choose-release"]').first().waitFor();
    await page.getByRole('button',{name:'Close',exact:true}).click();
    await page.getByRole('button',{name:'Album settings'}).click();
    await page.getByRole('button',{name:'Choose cover from another edition'}).click();
    await page.locator('[data-action="choose-cover"]').first().click();
    await page.locator('#toast').filter({hasText:'Edition cover saved.'}).waitFor();
    await page.getByRole('button',{name:'This tracklist is correct'}).click();
    await page.getByRole('button',{name:'Match Spotify tracks'}).click();
    await page.getByRole('heading',{name:'Spotify editions'}).waitFor();
    await page.getByRole('button',{name:'Close',exact:true}).click();
    await page.getByRole('button',{name:'Play album'}).click();
    await page.locator('#toast').filter({hasText:'Playing 2 tracks on Fixture phone.'}).waitFor();
    let unavailable=true;
    await page.route('**/api/albums/*/play',async route=>{
      if(unavailable){unavailable=false;return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Open Spotify on your phone, then retry.'})});}
      await route.continue();
    });
    await page.getByRole('button',{name:'Play album'}).click();
    await page.getByRole('heading',{name:'Spotify playback',exact:true}).waitFor();
    assert((await page.getByRole('link',{name:'Open Spotify',exact:true}).getAttribute('href')).startsWith('spotify:'));
    await page.getByRole('button',{name:'Retry playback'}).click();
    await page.waitForFunction(()=>!document.querySelector('#modal').open);
    await page.unroute('**/api/albums/*/play');
    const calls=await (await fetch(base+'/__test/play-calls')).json();
    assert.equal(calls.calls.length,2);
    for(const call of calls.calls)assert.deepEqual(call,{uris:['spotify:track:'+'a'.repeat(22),'spotify:track:'+'b'.repeat(22)],position_ms:0});
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
    assert.equal((await (await fetch(base+'/api/settings')).json()).preferred_device.id,'phone');
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
    assert.deepEqual((await (await fetch(base+'/api/settings')).json()).release_filters,
      {countries:['GB','US'],formats:['cd','vinyl','digital'],strict_countries:false});
    const themes=(await (await fetch(base+'/api/settings')).json()).themes;
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
    const report=await (await fetch(base+'/api/albums/f5093c06-23e3-4f01-aeaa-40f72885ee3a/diagnostics')).json();
    assert.equal(report.format,'audioshelf-diagnostics-1');
    assert(report.events.some(event=>event.event==='spotify_candidates'));
    assert(!JSON.stringify(report).includes('fixture-only'));
    assert.deepEqual(errors,[],'Browser JavaScript errors');
    const ingressPage=await browser.newPage({viewport:{width:390,height:844}});
    await ingressPage.route('**/api/hassio_ingress/fixture/**',async route=>{
      const incoming=route.request();const url=new URL(incoming.url());
      const localPath=url.pathname.replace('/api/hassio_ingress/fixture','') || '/';
      const response=await fetch(base+localPath+url.search,{method:incoming.method(),headers:{...incoming.headers(),'X-Ingress-Path':'/api/hassio_ingress/fixture'},body:incoming.postData()||undefined});
      let body=Buffer.from(await response.arrayBuffer());
      if(localPath==='/')body=Buffer.from(body.toString().replace('<base href="/">','<base href="/api/hassio_ingress/fixture/">'));
      await route.fulfill({status:response.status,headers:Object.fromEntries(response.headers),body});
    });
    await ingressPage.goto(base+'/api/hassio_ingress/fixture/');
    await ingressPage.getByRole('heading',{name:'My shelf.'}).waitFor();
    await ingressPage.locator('.artist-row').waitFor();
    console.log('Browser checks passed: collection, artwork selection, edition retry, track review and playback, release preferences, all 10 themes and contrast, catalogue overrides, diagnostics, desktop and ingress.');
  }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;});
