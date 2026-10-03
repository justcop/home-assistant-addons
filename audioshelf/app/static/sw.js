'use strict';
const VERSION = new URL(self.location.href).searchParams.get('v') || 'legacy';
const CACHE = 'audioshelf-shell-'+VERSION;
const root = self.registration.scope;
const asset = file => {const url=new URL('static/'+file,root);url.searchParams.set('v',VERSION);return url.href;};
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(async cache=>{
    for(const url of [root,asset('app.js'),asset('style.css')]){
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
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if(event.request.method !== 'GET' || url.origin !== self.location.origin ||
     url.pathname.includes('/api/') || url.pathname.includes('/auth/') || url.pathname.endsWith('/health')) return;
  if(event.request.mode !== 'navigate' && !url.pathname.includes('/static/')) return;
  event.respondWith((async()=>{
    try{
      const response=await fetch(event.request,{cache:'no-store'});
      if(response.ok){const cache=await caches.open(CACHE);await cache.put(event.request,response.clone());}
      return response;
    }catch{return await caches.match(event.request) || Response.error();}
  })());
});
