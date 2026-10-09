'use strict';
const VERSION = new URL(self.location.href).searchParams.get('v') || 'legacy';
const CACHE = 'audioshelf-shell-'+VERSION;
const ARTWORK_PREFIX = 'audioshelf-artwork-v1-';
const ARTWORK_MAX_ENTRIES = 1600;
const ARTWORK_REFRESH_MS = 7*24*60*60*1000;
const root = self.registration.scope;
const asset = file => {const url=new URL('static/'+file,root);url.searchParams.set('v',VERSION);return url.href;};
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(async cache=>{
    for(const url of [root,asset('app.js'),asset('style.css'),asset('vinyl.js'),asset('vinyl.css')]){
      const response=await fetch(url,{cache:'no-store'});
      if(!response.ok)throw new Error('Could not load updated AudioShelf shell');
      await cache.put(url,response);
    }
    await self.skipWaiting();
  }));
});
self.addEventListener('activate', event => {
  event.waitUntil((async()=>{
    await Promise.all((await caches.keys()).filter(key=>key.startsWith('audioshelf-shell-')&&key!==CACHE).map(key=>caches.delete(key)));
    await self.clients.claim();
    for(const client of await self.clients.matchAll({type:'window'}))client.postMessage({type:'audioshelf-update'});
  })());
});

function shelfArtwork(url) {
  const match=url.pathname.match(/\/api\/albums\/([0-9a-f-]{36})\/artwork$/i);
  if(!match || !['128','320','640'].includes(url.searchParams.get('size')))return null;
  const account=url.searchParams.get('account');
  if(!account || !/^(?:owner|[0-9a-f]{32})$/i.test(account))return null;
  return {album:match[1],account};
}
function eligibleArtwork(response){
  return response.ok && response.type==='basic' &&
    (response.headers.get('Cache-Control')||'').includes('private') &&
    !(response.headers.get('Cache-Control')||'').includes('no-store') &&
    (response.headers.get('Content-Type')||'').toLowerCase().startsWith('image/webp') &&
    response.headers.get('X-Artwork-Source')!=='placeholder';
}
async function storeArtwork(cache,request,response){
  if(!eligibleArtwork(response))return;
  try {
    const headers=new Headers(response.headers);
    headers.set('X-AudioShelf-Cached-At',String(Date.now()));
    await cache.put(request,new Response(response.clone().body,{status:200,headers}));
    const keys=await cache.keys();
    if(keys.length>ARTWORK_MAX_ENTRIES)
      await Promise.all(keys.slice(0,keys.length-ARTWORK_MAX_ENTRIES).map(key=>cache.delete(key)));
  } catch(error) {
    // Quota or private mode may disable browser storage; network artwork still works.
    if(error.name!=='QuotaExceededError')console.debug('Artwork cache unavailable',error);
  }
}
async function loadArtwork(event,account) {
  const request=event.request;
  const cache=await caches.open(ARTWORK_PREFIX+account);
  const stored=await cache.match(request);
  if(stored){
    const last=Number(stored.headers.get('X-AudioShelf-Cached-At'))||0;
    if(Date.now()-last>ARTWORK_REFRESH_MS){
      event.waitUntil((async()=>{
        try {
          const response=await fetch(request,{cache:'no-store'});
          if(eligibleArtwork(response))await storeArtwork(cache,request,response);
          else if(response.status===403||response.status===401)await cache.delete(request);
        }catch{}
      })());
    }
    return stored;
  }
  const response=await fetch(request);
  if(eligibleArtwork(response))event.waitUntil(storeArtwork(cache,request,response));
  return response;
}
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if(event.request.method !== 'GET' || url.origin !== self.location.origin)return;
  const artwork=shelfArtwork(url);
  if(artwork){
    event.respondWith(loadArtwork(event,artwork.account).catch(()=>fetch(event.request)));
    return;
  }
  if(url.pathname.includes('/api/') || url.pathname.includes('/auth/') || url.pathname.endsWith('/health'))return;
  if(event.request.mode !== 'navigate' && !url.pathname.includes('/static/')) return;
  event.respondWith((async()=>{
    try{
      const response=await fetch(event.request,{cache:'no-store'});
      if(response.ok){const cache=await caches.open(CACHE);await cache.put(event.request,response.clone());}
      return response;
    }catch{return await caches.match(event.request) || Response.error();}
  })());
});
