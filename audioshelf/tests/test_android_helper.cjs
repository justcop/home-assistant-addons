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
const sampleJob={job:'j'.repeat(25),helperToken:'t'.repeat(43)};
const link = context.androidHelperLink(false,sampleJob);
assert(link.startsWith('intent://wake?'));
assert(link.includes('package=uk.co.justcop.audioshelf.helper;'));
assert(link.includes('client_id='+'c'.repeat(32)));
assert(link.includes('origin=https%3A%2F%2Faudioshelf.example'));
assert(link.includes('job_id='+'j'.repeat(25)));
assert(link.includes('helper_token='+'t'.repeat(43)));
assert(link.includes('account_id=owner'));
assert(link.includes('S.browser_fallback_url=https%3A%2F%2Faudioshelf.example%2F%23album%2Fsaved;end'));
assert.equal(new URLSearchParams(link.slice(link.indexOf('?')+1,link.indexOf('#Intent'))).get('return_url'),context.location.href,'Return to the exact album page');
assert(!link.includes('album_id='),'The helper must not receive playback content');
assert(!context.androidHelperLink(false,{}),'No helper launch without scoped job credentials');
assert(context.playbackWakeLinks(sampleJob).includes('Wake Spotify and return'));
assert(context.playbackWakeLinks().includes('href="spotify:"'),'Manual fallback stays available');
for(const type of ['Speaker','Computer','TV',undefined]){
  context.statusInfo.preferred_device.type=type;
  assert.equal(context.androidHelperLink(false,sampleJob),null,`No phone helper for ${type}`);
}
context.statusInfo.preferred_device.type='Smartphone';
context.location.protocol='http:';
assert.equal(context.androidHelperLink(false,sampleJob),null);
context.location.protocol='https:';
context.navigator.userAgent='Desktop Chrome';
assert.equal(context.androidHelperLink(false,sampleJob),null);
context.navigator.userAgent='Android';
preferences.set('audioshelf-android-helper','false');
assert.equal(context.androidHelperLink(false,sampleJob),null);
preferences.set('audioshelf-android-helper','true');
context.statusInfo.account.id='other-user';
assert.equal(context.androidHelperLink(false,sampleJob),null,'Helper opt-in must be account scoped');
preferences.set('audioshelf-android-helper:other-user','true');
assert(context.androidHelperLink(false,sampleJob));
context.statusInfo.spotify_client_id='';
assert.equal(context.androidHelperLink(false,sampleJob),null);
// Exercise the real playback entry points, including async handoff creation.
let calls=[], launches=[], createJob;
context.statusInfo.spotify_client_id='c'.repeat(32);
context.statusInfo.preferred_device.name='My phone';
let pageUrl='https://audioshelf.example/#album/saved';
Object.defineProperty(context.location,'href',{get:()=>pageUrl,set:value=>launches.push(value)});
Object.assign(context,{
  routeGeneration:1, playbackCommand:0, checkingPlayback:false,
  id:encodeURIComponent, modal:{open:true,close(){}}, document:{hidden:false},
  beginPlayback(){}, failPlaybackStart(){}, toast(){},
  showModal(){context.modal.open=true;},
  api:async (url,method,body)=>{
    calls.push({url,method,body});
    if(url.endsWith('/playback-handoff')&&method==='POST')return createJob?createJob():{id:'job',state:'waiting',helper_token:'token-for-job'};
    if(url.endsWith('/play'))return {track_count:2,device:'My phone'};
    return {state:'waiting'};
  }
});
vm.runInContext(source.slice(source.indexOf('let pendingPlayback='),source.indexOf("window.addEventListener('focus',retryPendingPlayback)")),context);
const request=()=>({album:{id:'album',tracks:[{spotify_id:'track',title:'Track',duration_ms:1000,disc_number:2}]},disc:2,generation:1});
(async()=>{
  await context.startPlayback(request());
  assert.equal(launches.length,0,'Available phone plays without waking helper');
  assert.equal(calls[0].body.disc_number,2);
  calls=[];
  const pending=request();
  await context.playbackHandoff(pending,'Phone unavailable');
  assert.equal(launches.length,1,'Unavailable opted-in phone automatically wakes once');
  assert(launches[0].startsWith('intent://wake?'));
  assert(launches[0].includes('helper_token=token-for-job'));
  assert(launches[0].includes('job_id=job'));
  assert(!launches[0].includes('browser_fallback_url'),'Blocked automatic launch must not reload the page');
  assert.equal(calls[0].body.disc_number,2,'Handoff keeps the requested disc');
  assert.equal(context.wakePlaybackHelper(pending),false,'Never loop helper launches while waiting');
  context.cancelPendingPlayback();
  assert.equal(context.wakePlaybackHelper(pending),false,'Cancelled request cannot wake Spotify');
  assert(calls.some(call=>call.method==='DELETE'),'Cancellation still cancels server playback');
  for(const disabled of ['preference','speaker']){
    if(disabled==='preference')preferences.set('audioshelf-android-helper:other-user','false');
    else {preferences.set('audioshelf-android-helper:other-user','true');context.statusInfo.preferred_device.type='Speaker';}
    await context.playbackHandoff(request(),'Unavailable');
    assert.equal(launches.length,1,`No automatic wake for ${disabled}`);
    context.cancelPendingPlayback();
  }
  context.statusInfo.preferred_device.type='Smartphone';
  let resolveJob;
  createJob=()=>new Promise(resolve=>{resolveJob=resolve;});
  const stale=context.playbackHandoff(request(),'Unavailable');
  context.cancelPendingPlayback();
  resolveJob({id:'late-job',state:'waiting'});
  await stale;
  assert.equal(launches.length,1,'Cancelled async handoff never launches the helper');
  assert(calls.some(call=>call.url.endsWith('/late-job')&&call.method==='DELETE'));
  console.log('Android helper eligibility, automatic wake, playback and cancellation tests passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
