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
    await page.getByRole('button',{name:'This tracklist is correct'}).click();
    await page.getByRole('button',{name:'Match Spotify tracks'}).click();
    await page.getByRole('heading',{name:'Spotify editions'}).waitFor();
    await page.getByRole('button',{name:'Close',exact:true}).click();
    await page.getByRole('button',{name:'Play album'}).click();
    await page.locator('#toast').filter({hasText:'Playing 2 tracks on Fixture phone.'}).waitFor();
    const calls=await (await fetch(base+'/__test/play-calls')).json();
    assert.deepEqual(calls.calls,[{uris:['spotify:track:'+'a'.repeat(22),'spotify:track:'+'b'.repeat(22)],position_ms:0}]);
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
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
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
    console.log('Browser checks passed: phone collection, search, add, canonical review, deluxe matching, exact playback, filter, desktop, ingress paths.');
  }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;});
