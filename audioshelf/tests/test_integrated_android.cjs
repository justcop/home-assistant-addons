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
assert(android.includes('result.playAccepted') && android.includes('stopWake()'));
assert(android.includes('WindowCompat.setDecorFitsSystemWindows(getWindow(), false)'));
assert(android.includes('ViewCompat.setOnApplyWindowInsetsListener(root'));
assert(android.includes('WindowInsetsCompat.Type.systemBars()') &&
  android.includes('WindowInsetsCompat.Type.displayCutout()') &&
  android.includes('WindowInsetsCompat.Type.ime()'));
assert(android.includes('Math.max(safe.bottom, keyboard.bottom)'));
assert(android.includes('view.setPadding(safe.left, 0, safe.right,'),'WebView must extend under status bar');
assert(android.includes('button.topMargin = desiredTop'),'Menu must clear the status icons');
assert(android.includes('applyWebStatusInset()'),'Apply status inset to header, not native root');
assert(android.includes('padding-top:max(12px,var(--audioshelf-native-status-top,32px)) !important;'),'Status inset replaces header padding');
assert(!android.includes('view.setPadding(safe.left, safe.top'),'No double top padding');
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
testAtomicOfflineRefresh().then(()=>
  console.log('Integrated Android native wake and atomic offline snapshot tests passed')
).catch(error=>{console.error(error);process.exitCode=1;});
