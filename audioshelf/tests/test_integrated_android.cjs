const assert = require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const directory=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(directory,'app/static/app.js'),'utf8');
const android=fs.readFileSync(path.join(directory,'android-helper/app/src/main/java/uk/co/justcop/audioshelf/helper/ShelfActivity.java'),'utf8');
const manifest=fs.readFileSync(path.join(directory,'android-helper/app/src/main/AndroidManifest.xml'),'utf8');
const gradle=fs.readFileSync(path.join(directory,'android-helper/app/build.gradle'),'utf8');
const media=fs.readFileSync(path.join(directory,'android-helper/app/src/main/java/uk/co/justcop/audioshelf/helper/SpotifyMediaListener.java'),'utf8');
const workflow=fs.readFileSync(path.join(directory,'../.github/workflows/audioshelf-android-helper.yml'),'utf8');
assert(android.includes('WebViewCompat.addWebMessageListener') && android.includes('Collections.singleton(trustedOrigin)'));
assert(android.includes('!isMainFrame') && android.includes('sameOrigin(sourceOrigin)'));
assert(android.includes('if(!"waiting".equals(result.state))') &&
  android.includes('Server terminal=') &&
  android.includes('stopWake("20-second deadline; server phase="'),
  'Integrated Spotify wake must continue until server confirms playback or reaches the independent 20-second deadline');
assert(android.includes('Spotify SDK watchdog: no success/failure callback after 10 seconds.'),
  'Integrated helper logs a missing Spotify SDK callback, not just server Play acceptance');
assert(android.includes('result.progress()') && android.includes('recordIntegrated('),
  'Integrated wake must include server-reported Spotify player observations in the copyable log');
assert(source.includes('sdk-single-track-test') && source.includes('sdk_single_track:true'),
  'Local SDK test must be explicitly opt-in, selectable in native Settings only');
assert(source.includes('if(sdkSingleTrackTestEnabled())') &&
  source.includes('await playbackHandoff(request,'),
  'SDK test must bypass ordinary full-album Web API Play completely');
assert(android.includes('session.getPlayerApi().play(pendingSdkFirstTrack)') &&
  android.includes('sdkPlayAttempted = true;'),
  'Native SDK test sends exactly one first-track Play without auto-retries');
assert(android.includes('if ("waiting".equals(result.state)) runSdkSingleTrackTest(ticket);'),
  'A cancelled or expired handoff must never initiate SDK Play');
assert(!android.includes('getPlayerApi().queue('),
  'Diagnostic experiment must never enqueue any additional album tracks');
assert(!android.includes('result.playAccepted ||'),
  'Accepted Play cannot disconnect the integrated Spotify SDK before the correct track starts');
assert(android.includes('stopWake()'));
assert(android.includes('WindowCompat.setDecorFitsSystemWindows(getWindow(), false)'));
assert(android.includes('ViewCompat.setOnApplyWindowInsetsListener(root'));
assert(android.includes('WindowInsetsCompat.Type.systemBars()') &&
  android.includes('WindowInsetsCompat.Type.displayCutout()') &&
  android.includes('WindowInsetsCompat.Type.ime()'));
assert(android.includes('Math.max(safe.bottom, keyboard.bottom)'));
assert(android.includes('scrollSafeHeaderReady ? 0 : statusBarPx'),
  'Native inset is only the temporary loading fallback; full WebView uses status-bar area');
assert(android.includes('scrollSafeHeaderReady ? statusBarPx : 0'),
  'Native overflow control remains below status icons after edge-to-edge');
assert(android.includes('onPageStarted(WebView view, String url') &&
  android.includes('scrollSafeHeaderReady = false;'),
  'Navigations restore native safety before recalculating page spacer');
assert(android.includes('applyScrollableHeader();') &&
  android.includes("b.style.setProperty('padding-top'") &&
  android.includes("h.style.setProperty('position','relative','important')"),
  'Top inset belongs to scrollable body, not a sticky or fixed header');
assert(android.includes('logo.getBoundingClientRect()') &&
  android.includes('settings.getBoundingClientRect()') &&
  android.includes("return (a.top>=") && android.includes("&&s.top>="),
  'Both rendered header controls must be measured clear of the status bar before edge-to-edge');
assert(!android.includes('applyStickyHeader()') && !android.includes('position:sticky !important'),
  'The header must scroll behind Android status icons instead of staying pinned');
assert(android.includes('return WindowInsetsCompat.CONSUMED'));
assert(android.includes('ViewCompat.requestApplyInsets(root)'));
assert(manifest.includes('android:windowSoftInputMode="adjustResize"'));
const currentVersion=gradle.match(/versionName '([0-9]+[.][0-9]+[.][0-9]+)'/)[1];
assert(workflow.includes('audioshelf-spotify-helper-'+currentVersion),
  'APK workflow artifact follows the Android version rather than an obsolete patch number');
const updater=fs.readFileSync(path.join(directory,
  'android-helper/app/src/main/java/uk/co/justcop/audioshelf/helper/HelperUpdater.java'),'utf8');
assert(android.includes('updater.check((version, action) ->') &&
  android.includes('download.setEnabled(false)') && android.includes('dialog.dismiss();'),
  'Integrated app must offer a direct Download button and dismiss before installation');
assert(updater.includes('check(BiConsumer<String, Runnable> offerDownload)') &&
  updater.includes('offerDownload.accept(update.versionName'),
  'Updater sends a validated version to the same integrated dialog');
assert(!android.includes('.play('),'No native SDK playback/queue command');
assert(manifest.includes('android.permission.BIND_NOTIFICATION_LISTENER_SERVICE'));
assert(media.includes('com.spotify.music') && media.includes('onPlaybackStateChanged'));
const versionCode=Number(gradle.split('versionCode ')[1].split(String.fromCharCode(10))[0].trim());
assert(versionCode>=28,'Integrated mode requires Android helper versionCode 28 or newer');
const selected=source.slice(source.indexOf('function nativeAudioShelf()'),source.indexOf('/* Local, account-partitioned PWA cache'));
const calls=[];
const sandbox=vm.createContext({
  window:{AudioShelfAndroid:{postMessage:message=>calls.push(JSON.parse(message))}},
  document:{documentElement:{classList:{add(){}}}},
  statusInfo:{spotify_client_id:'a'.repeat(32),account:{id:'owner'},preferred_device:{type:'Smartphone'}}
});
vm.runInContext(selected,sandbox);
assert.equal(sandbox.nativeAudioShelf(),true);
assert.equal(sandbox.nativeWake({job:'j'.repeat(24),helperToken:'t'.repeat(48)}),true);
assert.equal(calls.length,1);
assert.equal(calls[0].type,'wake');
assert.equal(calls[0].job_id,'j'.repeat(24));
assert.equal(calls[0].helper_token,'t'.repeat(48));
sandbox.statusInfo.preferred_device.type='Speaker';
assert.equal(sandbox.nativeWake({job:'j'.repeat(24),helperToken:'t'.repeat(48)}),false);
assert.equal(calls.length,1,'Native wake never redirects to a speaker');
const start=source.indexOf('/* Explicit, read-only offline shelf snapshot');
const stop=source.indexOf('/* Local, account-partitioned PWA cache');
const offline=source.slice(start,stop);
const cacheContext=vm.createContext({location:{href:'https://audioshelf.example'},document:{baseURI:'https://audioshelf.example/'},window:{},localStorage:{getItem:()=>null},caches:{}});
vm.runInContext(offline,cacheContext);
for(const route of ['status','shelf','artists/'+('a'.repeat(36)),'albums/'+('f'.repeat(36))])
  assert(cacheContext.offlineEligible(route),'Supported offline route '+route);
for(const route of ['login','spotify/playback','search?q=beatles','albums/'+('a'.repeat(36))+'/diagnostics'])
  assert(!cacheContext.offlineEligible(route),'Never cache '+route);
async function testAtomicOfflineRefresh(){
  const saved=new Map(),blobs=new Map();
  const storage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,String(v)),removeItem:k=>saved.delete(k)};
  const cachesMock={
    open:async name=>{if(!blobs.has(name))blobs.set(name,new Map());const entries=blobs.get(name);
      return {put:async(k,v)=>entries.set(k,v.clone()),match:async k=>entries.get(k)?.clone()};},
    keys:async()=>[...blobs.keys()],delete:async name=>blobs.delete(name)
  };
  const artist='a'.repeat(36),album='b'.repeat(36);
  const shelf={artists:[{id:artist,name:'Artist'}],albums:[{id:album,title:'Album'}]};
  let broken=false,next=0;
  const context=vm.createContext({
    document:{baseURI:'https://audioshelf.example/',querySelector:()=>null,documentElement:{classList:{add(){}}}},
    window:{},localStorage:storage,caches:cachesMock,Response,toast:()=>{},
    navigator:{},URL,URLSearchParams,crypto:{randomUUID:()=>String(++next).padStart(32,'0')},
    offlineReadOnly:false,offlineSaving:false,
    statusInfo:{authenticated:true,account:{id:'owner',username:'Owner'},
      role:'owner',interface:'vinyl',shelf_style:'floating',theme:'record-store',
      themes:[],release_filters:{},build:{version:'test'}},
    api:async route=>{
      if(broken&&route==='albums/'+album)throw Error('offline');
      if(route==='shelf')return shelf;
      if(route==='artists/'+artist)return {artist:shelf.artists[0],albums:shelf.albums};
      if(route==='albums/'+album)return {id:album,title:'Album'};
      throw Error(route);
    }
  });
  vm.runInContext(offline,context);
  await vm.runInContext('saveOfflineShelf()',context);
  const first=vm.runInContext('offlineCacheName("owner")',context);
  assert.equal(blobs.size,1);
  vm.runInContext('offlineReadOnly=false',context);
  broken=true;
  await assert.rejects(vm.runInContext('saveOfflineShelf()',context),/Offline save interrupted/);
  assert.equal(vm.runInContext('offlineCacheName("owner")',context),first);
  assert.equal(blobs.size,1);
  assert.equal((await (await context.offlineResponse('albums/'+album)).json()).title,'Album');
  vm.runInContext('offlineReadOnly=false',context);
  broken=false;
  await vm.runInContext('saveOfflineShelf()',context);
  assert.notEqual(vm.runInContext('offlineCacheName("owner")',context),first);
  assert.equal(blobs.size,1);
  const status=(await (await context.offlineResponse('status')).json());
  assert.equal(status.offline_snapshot,true);
  assert(!('spotify_client_id' in status));
  await vm.runInContext('clearOfflineShelf()',context);
  assert.equal(blobs.size,0);
}
// Execute the Android-generated header script in a real mobile-sized browser.
// Static source assertions missed the regression where the logo clipped on a
// small scroll, and where a malformed JS string prevented native edge-to-edge.
async function testStickyMobileHeader(){
  const {chromium}=require('playwright');
  const beginning=android.indexOf('String script = ',android.indexOf('private void applyScrollableHeader()'));
  const ending=android.indexOf(';\n        final WebView target = web;',beginning);
  assert(beginning>=0&&ending>beginning,'Find the actual Android document-spacer JS');
  const expression=android.slice(beginning+'String script = '.length,ending);
  const script=vm.runInNewContext(expression,{cssTop:50});
  const style=fs.readFileSync(path.join(directory,'app/static/style.css'),'utf8');
  const vinyl=fs.readFileSync(path.join(directory,'app/static/vinyl.css'),'utf8');
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:393,height:852},deviceScaleFactor:2});
    const pageHtml=`<!doctype html><html lang="en" data-interface="vinyl" class="native-android">
      <head><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
      <style>${style}</style><style>${vinyl}</style>
      <style>#system-status{height:50px;position:fixed;inset:0 0 auto;
        background:#f5f0e6dd;z-index:80;pointer-events:none}</style></head><body>
      <div id="system-status" aria-label="Android status bar overlay"></div>
      <header class="masthead">
        <a class="brand" href="#shelf"><span>AudioShelf<small>Good records. Your collection.</small></span></a>
        <a class="settings-link" href="#settings">Settings ↗</a>
      </header>
      <main style="height:1800px"><h1>My shelf</h1><p>Records</p></main>
      </body></html>`;
    await page.setContent(pageHtml);
    for(const mode of ['vinyl','classic']){
      await page.evaluate(mode=>{
        document.documentElement.dataset.interface=mode;
        window.scrollTo(0,0);
      },mode);
      assert.equal(await page.evaluate(script),'safe',
        'Native script installs measured status-bar spacer and verifies real header geometry');
      const initial=await page.locator('.brand').boundingBox();
      const settings=await page.locator('.settings-link').boundingBox();
      assert(initial.y>=50,'Initial logo starts below status bar icons');
      assert(settings.y>=50,'Initial Settings starts below status bar icons');
      const styleInfo=await page.locator('.masthead').evaluate(el=>getComputedStyle(el).position);
      assert.notEqual(styleInfo,'sticky','Branding must scroll away, not stay pinned');
      await page.evaluate(()=>window.scrollTo(0,120));
      const scrolled=await page.locator('.brand').boundingBox();
      const scrolledSettings=await page.locator('.settings-link').boundingBox();
      assert(scrolled.y<50 && scrolledSettings.y<50,
        'After scrolling the logo and Settings move behind the status bar');
      assert(scrolled.y < initial.y-100,'Header actually moves upward with the page');
    }
  }finally{await browser.close();}
}
testAtomicOfflineRefresh()
  .then(testStickyMobileHeader)
  .then(()=>console.log('Integrated Android native wake, offline snapshot and real-browser sticky header checks passed'))
  .catch(error=>{console.error(error);process.exitCode=1;});
