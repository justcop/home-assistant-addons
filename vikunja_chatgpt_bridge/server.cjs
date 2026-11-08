'use strict';
const http = require('node:http');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { Readable } = require('node:stream');

const cfg = JSON.parse(fs.readFileSync('/data/options.json', 'utf8'));
const keys=['vikunja_mcp_url','mcp_base_url','vikunja_mcp_token','oauth_password','session_secret'];
for(const k of keys) if(!cfg[k] || !String(cfg[k]).trim()) throw Error('Missing setting '+k);
if(cfg.session_secret.length<32||cfg.oauth_password.length<16)throw Error('Secrets too short');
const base=new URL(cfg.mcp_base_url), upstream=new URL(cfg.vikunja_mcp_url);
if(base.protocol!=='https:'||upstream.protocol!=='https:'||base.pathname!=='/'||base.search||base.hash||upstream.search||upstream.hash||!upstream.pathname.endsWith('/api/v2/mcp'))throw Error('Invalid URL configuration');
const issuer=base.origin, resource=issuer+'/mcp';
const path='/data/oauth-state.json';
let db={clients:{},codes:{},tokens:{},refresh:{}};
try{db={...db,...JSON.parse(fs.readFileSync(path,'utf8'))};}catch(e){if(e.code!=='ENOENT')throw e;}
const rand=()=>crypto.randomBytes(32).toString('base64url');
const hash=x=>crypto.createHash('sha256').update(String(x)).digest('hex');
const same=(a,b)=>{let x=Buffer.from(String(a)),y=Buffer.from(String(b));return x.length===y.length&&crypto.timingSafeEqual(x,y)};
const save=()=>{fs.writeFileSync(path+'.tmp',JSON.stringify(db),{mode:0o600});fs.renameSync(path+'.tmp',path)};
function send(res,status,body,headers={}){res.writeHead(status,{'cache-control':'no-store',...headers});res.end(body)}
function json(res,status,data,headers={}){send(res,status,JSON.stringify(data),{'content-type':'application/json',...headers})}
function fail(res,status,error){json(res,status,{error})}
async function read(req){return new Promise((ok,no)=>{let parts=[],bytes=0;req.on('data',b=>{bytes+=b.length;if(bytes>65536){no(Error('Too large'));req.destroy()}else parts.push(b)});req.on('end',()=>ok(Buffer.concat(parts).toString()));req.on('error',no)})}
async function fields(req){const b=await read(req);return (req.headers['content-type']||'').includes('application/json')?JSON.parse(b):Object.fromEntries(new URLSearchParams(b))}
function esc(s){return String(s).replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]))}
function invalid(q){const c=db.clients[q.client_id];if(!c||!c.redirect_uris.includes(q.redirect_uri))return 'Invalid client';if(q.response_type!=='code'||q.code_challenge_method!=='S256'||!/^[A-Za-z0-9_-]{43,128}$/.test(q.code_challenge||''))return 'PKCE S256 required';if(q.resource!==resource)return 'Invalid resource';return ''}
function form(q,error=''){const hidden=Object.entries(q).filter(([k])=>['client_id','redirect_uri','response_type','code_challenge','code_challenge_method','state','resource','scope'].includes(k)).map(([k,v])=>'<input type="hidden" name="'+esc(k)+'" value="'+esc(v)+'">').join('');return '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="font:16px system-ui;max-width:460px;margin:10vh auto;padding:20px"><h2>Authorize ChatGPT to access Vikunja</h2><p>The client receives the permissions of your Vikunja MCP token.</p>'+ (error?'<p style="color:red">'+esc(error)+'</p>':'')+'<form action="/authorize" method="post">'+hidden+'<label>Bridge password <input type="password" name="password" required autofocus></label><p><button>Authorize</button></p></form></body></html>'}
function token(req){const m=/^Bearer (.+)$/i.exec(req.headers.authorization||'');const v=m&&db.tokens[hash(m[1])];return v&&v.expires>Date.now()&&v.resource===resource&&v.secret===hash(cfg.session_secret)?v:null}
function unauthorized(res){json(res,401,{error:'unauthorized'},{'www-authenticate':'Bearer resource_metadata="'+issuer+'/.well-known/oauth-protected-resource"'})}
function goodRedirect(s){try{const u=new URL(s);return (u.protocol==='https:'||(u.protocol==='http:'&&['127.0.0.1','localhost'].includes(u.hostname)))&&!u.hash&&!u.username&&!u.password}catch{return false}}
const attempts=new Map();
async function proxy(req,res){if(!token(req))return unauthorized(res);const headers={...req.headers};delete headers.host;delete headers.authorization;delete headers['content-length'];headers.authorization='Bearer '+cfg.vikunja_mcp_token;headers['accept-encoding']='identity';
const url=new URL(upstream);url.search=new URL(req.url,issuer).search;
try{const reply=await fetch(url,{method:req.method,headers,body:['GET','HEAD'].includes(req.method)?undefined:req,duplex:'half',redirect:'manual',signal:AbortSignal.timeout(120000)});
const out={};for(const [k,v] of reply.headers)if(!['content-length','content-encoding','connection','transfer-encoding','set-cookie','www-authenticate'].includes(k.toLowerCase()))out[k]=v;
res.writeHead(reply.status,out);if(reply.body)Readable.fromWeb(reply.body).pipe(res);else res.end()
}catch(e){console.error('Proxy:',e.message);if(!res.headersSent)fail(res,502,'bad_gateway');else res.destroy()}}
http.createServer(async(req,res)=>{try{const u=new URL(req.url,issuer),p=u.pathname;
const trace=['/mcp','/.well-known/oauth-protected-resource','/.well-known/oauth-protected-resource/mcp','/.well-known/oauth-authorization-server','/register','/authorize','/token'].includes(p);
if(trace){console.log('[OAuth]',req.method,p,'requested');res.once('finish',()=>console.log('[OAuth]',req.method,p,'HTTP',res.statusCode));}
if(p==='/health'&&req.method==='GET')return json(res,200,{status:'ok'});
if((p==='/.well-known/oauth-protected-resource'||p==='/.well-known/oauth-protected-resource/mcp')&&req.method==='GET')return json(res,200,{resource,authorization_servers:[issuer],bearer_methods_supported:['header']});
if(p==='/.well-known/oauth-authorization-server'&&req.method==='GET')return json(res,200,{issuer,authorization_endpoint:issuer+'/authorize',token_endpoint:issuer+'/token',registration_endpoint:issuer+'/register',response_types_supported:['code'],grant_types_supported:['authorization_code','refresh_token'],code_challenge_methods_supported:['S256'],token_endpoint_auth_methods_supported:['none','client_secret_post','client_secret_basic'],scopes_supported:['vikunja']});
if(p==='/register'&&req.method==='POST'){const q=await fields(req),redirects=q.redirect_uris;console.log('[OAuth] DCR methods:',String(q.token_endpoint_auth_method||'none'),'redirect count:',Array.isArray(redirects)?redirects.length:'not-array');if(!Array.isArray(redirects)||!redirects.length||redirects.length>10||!redirects.every(goodRedirect))return fail(res,400,'invalid_redirect_uri');const method=q.token_endpoint_auth_method||'none';if(!['none','client_secret_post','client_secret_basic'].includes(method))return fail(res,400,'invalid_client_metadata');const id=rand(),secret=method==='none'?null:rand();db.clients[id]={redirect_uris:redirects,auth_method:method,secret_hash:secret?hash(secret):null};save();const registration={client_id:id,client_id_issued_at:Math.floor(Date.now()/1000),redirect_uris:redirects,grant_types:['authorization_code','refresh_token'],response_types:['code'],token_endpoint_auth_method:method};if(secret){registration.client_secret=secret;registration.client_secret_expires_at=0}return json(res,201,registration)}
if(p==='/authorize'&&req.method==='GET'){const q=Object.fromEntries(u.searchParams),issue=invalid(q);if(issue)return send(res,400,issue);return send(res,200,form(q),{'content-type':'text/html','content-security-policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"})}
if(p==='/authorize'&&req.method==='POST'){const q=await fields(req),issue=invalid(q);if(issue)return fail(res,400,'invalid_request');const ip=req.socket.remoteAddress||'unknown',a=attempts.get(ip)||{count:0,until:0};if(a.until>Date.now())return fail(res,429,'rate_limited');if(!same(q.password||'',cfg.oauth_password)){a.count++;if(a.count>=5){a.count=0;a.until=Date.now()+60000}attempts.set(ip,a);return send(res,401,form(q,'Incorrect password'),{'content-type':'text/html'})}attempts.delete(ip);const code=rand();db.codes[hash(code)]={client_id:q.client_id,redirect_uri:q.redirect_uri,challenge:q.code_challenge,expires:Date.now()+300000};save();const dest=new URL(q.redirect_uri);dest.searchParams.set('code',code);if(q.state)dest.searchParams.set('state',q.state);return send(res,302,'',{location:dest.toString()})}
if(p==='/token'&&req.method==='POST'){const q=await fields(req);let client;
const auth=req.headers.authorization||'';
let basicId=null,basicSecret=null;
if(auth.startsWith('Basic ')){try{const credentials=Buffer.from(auth.slice(6),'base64').toString('utf8');const ix=credentials.indexOf(':');if(ix>=0){basicId=decodeURIComponent(credentials.slice(0,ix).replace(/\+/g,' '));basicSecret=decodeURIComponent(credentials.slice(ix+1).replace(/\+/g,' '));}}catch{}}
const identity=basicId||q.client_id;
const reg=db.clients[identity];
if(!reg)return fail(res,401,'invalid_client');
const method=reg.auth_method||'none';
if(method==='client_secret_basic'&&(!basicId||!basicSecret||!same(hash(basicSecret),reg.secret_hash)))return fail(res,401,'invalid_client');
if(method==='client_secret_post'&&(!q.client_secret||!same(hash(q.client_secret),reg.secret_hash)))return fail(res,401,'invalid_client');
if(method==='none'&&(basicId||q.client_secret))return fail(res,401,'invalid_client');
q.client_id=identity;
if(q.grant_type==='authorization_code'){const code=db.codes[hash(q.code||'')];if(!code||code.client_id!==q.client_id||code.redirect_uri!==q.redirect_uri||code.expires<Date.now()||!q.code_verifier||!same(crypto.createHash('sha256').update(q.code_verifier).digest('base64url'),code.challenge))return fail(res,400,'invalid_grant');client=code.client_id;delete db.codes[hash(q.code)]}
else if(q.grant_type==='refresh_token'){const r=db.refresh[hash(q.refresh_token||'')];if(!r||r.client_id!==q.client_id||r.expires<Date.now()||r.secret!==hash(cfg.session_secret))return fail(res,400,'invalid_grant');client=r.client_id;delete db.refresh[hash(q.refresh_token)]}
else return fail(res,400,'unsupported_grant_type');
const access=rand(),refresh=rand(),now=Date.now(),secret=hash(cfg.session_secret);db.tokens[hash(access)]={client_id:client,expires:now+3600000,resource,secret};db.refresh[hash(refresh)]={client_id:client,expires:now+30*86400000,resource,secret};save();return json(res,200,{access_token:access,token_type:'Bearer',expires_in:3600,refresh_token:refresh,scope:'vikunja'})}
if(p==='/mcp'&&['GET','POST','DELETE'].includes(req.method))return proxy(req,res);
return fail(res,404,'not_found')
}catch(e){console.error('Request:',e.message);if(!res.headersSent)fail(res,500,'server_error');else res.destroy()}}).listen(3000,'0.0.0.0',()=>console.log('Vikunja native MCP OAuth bridge started'));
