const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../app/static/app.js'),'utf8');
// Exercise the shipped link builder, with browser globals for Android HTTPS.
const functions = source.slice(source.indexOf('function browserPreferenceKey('),source.indexOf('let pendingPlayback='));
const preferences = new Map([['audioshelf-android-helper','true']]);
const context = vm.createContext({
  URLSearchParams, encodeURIComponent,
  escapeHtml: value => String(value).replaceAll('&','&amp;').replaceAll('"','&quot;'),
  navigator:{userAgent:'Mozilla/5.0 Android'},
  location:{protocol:'https:',origin:'https://audioshelf.example',href:'https://audioshelf.example/#album/saved'},
  localStorage:{getItem:key=>preferences.get(key)},
  statusInfo:{account:{id:'owner'},spotify_client_id:'c'.repeat(32),preferred_device:{id:'phone',type:'Smartphone'}}
});
vm.runInContext(functions,context);
const link = context.androidHelperLink();
assert(link.startsWith('intent://wake?'));
assert(link.includes('package=uk.co.justcop.audioshelf.helper;'));
assert(link.includes('client_id='+'c'.repeat(32)));
assert(link.includes('origin=https%3A%2F%2Faudioshelf.example'));
assert(link.includes('S.browser_fallback_url=https%3A%2F%2Faudioshelf.example%2F%23album%2Fsaved;end'));
assert(!link.includes('album_id='),'The helper must not receive playback content');
assert(context.playbackWakeLinks().includes('Wake Spotify and return'));
assert(context.playbackWakeLinks().includes('href="spotify:"'),'Manual fallback stays available');
for(const type of ['Speaker','Computer','TV',undefined]){
  context.statusInfo.preferred_device.type=type;
  assert.equal(context.androidHelperLink(),null,`No phone helper for ${type}`);
}
context.statusInfo.preferred_device.type='Smartphone';
context.location.protocol='http:';
assert.equal(context.androidHelperLink(),null);
context.location.protocol='https:';
context.navigator.userAgent='Desktop Chrome';
assert.equal(context.androidHelperLink(),null);
context.navigator.userAgent='Android';
preferences.set('audioshelf-android-helper','false');
assert.equal(context.androidHelperLink(),null);
preferences.set('audioshelf-android-helper','true');
context.statusInfo.account.id='other-user';
assert.equal(context.androidHelperLink(),null,'Helper opt-in must be account scoped');
preferences.set('audioshelf-android-helper:other-user','true');
assert(context.androidHelperLink());
context.statusInfo.spotify_client_id='';
assert.equal(context.androidHelperLink(),null);
console.log('Android helper eligibility and intent tests passed');
