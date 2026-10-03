'use strict';
const CACHE = 'audioshelf-shell-0.3.2';
const root = self.registration.scope;
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll([
    root, ...['app.js','style.css','icon.svg','manifest.webmanifest'].map(file => new URL('static/'+file,root).href)
  ])));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('audioshelf-shell-') && key !== CACHE).map(key => caches.delete(key)))));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if(event.request.method !== 'GET' || url.origin !== self.location.origin ||
     url.pathname.includes('/api/') || url.pathname.includes('/auth/') || url.pathname.endsWith('/health')) return;
  if(event.request.mode !== 'navigate' && !url.pathname.includes('/static/')) return;
  event.respondWith(fetch(event.request).then(response => {
    if(response.ok) {const copy=response.clone();caches.open(CACHE).then(cache=>cache.put(event.request,copy));}
    return response;
  }).catch(()=>caches.match(event.request).then(response=>response || Response.error())));
});
