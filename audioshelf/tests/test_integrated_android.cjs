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
assert(android.includes('WebViewCompat.addWebMessageListener') && android.includes('Collections.singleton(trustedOrigin)'));
assert(android.includes('!isMainFrame') && android.includes('sameOrigin(sourceOrigin)'));
assert(android.includes('result.playAccepted') && android.includes('stopWake()'));
assert(!android.includes('.play('),'No native SDK playback/queue command');
assert(manifest.includes('android.permission.BIND_NOTIFICATION_LISTENER_SERVICE'));
assert(media.includes('com.spotify.music') && media.includes('onPlaybackStateChanged'));
const versionCode=Number((gradle.match(/versionCode\\s+(\\d+)/)||[])[1]);
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
console.log('Integrated Android native wake and opt-in offline snapshot security checks passed');
