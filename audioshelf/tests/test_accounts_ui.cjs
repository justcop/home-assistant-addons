const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const port=process.env.AUDIOSHELF_TEST_PORT||'8103';
const base=`http://127.0.0.1:${port}`;
const server=spawn(process.env.AUDIOSHELF_TEST_PYTHON||'python',[path.join(__dirname,'ui_server.py')],{env:{...process.env,AUDIOSHELF_TEST_PORT:port,AUDIOSHELF_TEST_VINYL:'1'},stdio:['ignore','pipe','pipe']});
let logs='';server.stdout.on('data',d=>logs+=d);server.stderr.on('data',d=>logs+=d);
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
  let browser;
  try{
    let ready=false;
    for(let n=0;n<80;n++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}await wait(100);}
    assert(ready,logs);
    browser=await chromium.launch({headless:true,executablePath:process.env.AUDIOSHELF_TEST_BROWSER||undefined,args:process.env.AUDIOSHELF_TEST_BROWSER?['--no-sandbox','--disable-dev-shm-usage']:[]});
    const page=await browser.newPage({viewport:{width:390,height:844}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    async function login(username,password){
      await page.getByLabel('Username',{exact:true}).fill(username);
      await page.getByLabel('Password',{exact:true}).fill(password);
      await page.getByRole('button',{name:'Open AudioShelf',exact:true}).click();
      await page.waitForFunction(name=>document.querySelector('#account-name')?.textContent===name,username);
      await page.getByRole('heading',{name:'My shelf.',exact:true}).waitFor();
    }
    async function settings(){await page.getByRole('link',{name:'Open settings',exact:true}).click();await page.getByRole('heading',{name:'Settings.',exact:true}).waitFor();}
    async function signout(){await settings();await page.getByRole('button',{name:'Switch account / Sign out',exact:true}).click();await page.getByLabel('Username',{exact:true}).waitFor();}
    async function api(endpoint,method='GET',body){
      const response=await page.request.fetch(base+'/api/'+endpoint,{method,data:body,headers:{'X-AudioShelf-Request':'1'}});
      assert(response.ok(),await response.text());return response.json();
    }
    await page.goto(base);
    // Account fields must remain a vertical form on phones and wide screens.
    for(const width of [390,1440]){
      await page.setViewportSize({width,height:844});
      const boxes=await page.locator('#login-form > input, #login-form > .primary').evaluateAll(elements=>elements.map(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,bottom:r.bottom};}));
      assert.equal(boxes.length,4);
      for(let n=1;n<boxes.length;n++)assert(boxes[n].y>=boxes[n-1].bottom,'Sign-in fields overlap or flow sideways');
      assert(boxes.every(b=>Math.abs(b.x-boxes[0].x)<1&&Math.abs(b.width-boxes[0].width)<1));
      assert(boxes[0].width<=440);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      assert((await page.locator('#login-form input[type="checkbox"]').boundingBox()).width<=20);
    }
    await page.setViewportSize({width:390,height:844});await login('owner','fixture-owner-password');
    assert.equal((await api('shelf')).albums.length,14);
    await api('settings','PUT',{shelf_style:'cabinet'});
    await settings();await page.getByRole('button',{name:'Manage accounts',exact:true}).click();
    const creation=page.locator('[data-account-form][data-operation="create"]');
    for(const username of ['alice','bob']){
      await creation.getByLabel('Confirm your password').fill('fixture-owner-password');
      await creation.getByLabel('Username',{exact:true}).fill(username);
      await creation.getByLabel('New account password',{exact:true}).fill(username+'-password-long-enough');
      await creation.getByRole('button',{name:'Create account',exact:true}).click();
      await page.locator('#modal h3').filter({hasText:new RegExp('^'+username+'$')}).waitFor();
    }
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.locator('.close-modal').click();await signout();
    await login('alice','alice-password-long-enough');
    await page.getByRole('heading',{name:'Your first record awaits.',exact:true}).waitFor();
    assert.equal((await api('shelf')).albums.length,0);
    assert.equal((await api('status')).spotify_connected,false);
    await settings();assert.equal(await page.getByRole('button',{name:'Manage accounts'}).count(),0);
    const url=(await api('spotify/connect','POST',{})).url;
    const state=new URL(url).searchParams.get('state');
    const callback=await page.request.get(base+'/auth/spotify/callback?code=fixture&state='+encodeURIComponent(state));
    assert(callback.ok());assert((await callback.text()).includes('alice'));
    await page.reload();await page.getByText('Your Spotify account is connected.',{exact:false}).waitFor();
    assert.equal((await api('spotify/devices')).devices[0].name,'Personal player');
    await page.getByRole('button',{name:'Disconnect',exact:true}).click();
    await page.getByRole('button',{name:'Connect Spotify',exact:true}).waitFor();
    await page.getByRole('link',{name:'Record store',exact:false}).click();
    await page.getByRole('searchbox',{name:'Search record store'}).fill('The Artist');
    await page.getByRole('button',{name:'Search',exact:true}).click();
    await page.getByRole('link',{name:'The Artist',exact:false}).first().click();
    await page.getByRole('button',{name:'Add The Original Album to shelf',exact:true}).click();
    await page.getByRole('heading',{name:'Choose the album tracklist'}).waitFor();
    await page.locator('[data-action="select-variant"]').first().click();
    await page.getByRole('heading',{name:'Disc and side layout'}).waitFor();
    await page.getByRole('button',{name:'Find this tracklist on Spotify'}).click();
    await page.getByText('Connect Spotify in Settings to match the chosen tracklist.',{exact:false}).waitFor();
    assert.equal((await api('shelf')).albums.length,0,
      'Disconnected Spotify may browse MusicBrainz variants but cannot collect an unverified edition');
    await page.locator('.close-modal').click();
    const retryUrl=(await api('spotify/connect','POST',{})).url;
    const retryState=new URL(retryUrl).searchParams.get('state');
    const retryCallback=await page.request.get(base+'/auth/spotify/callback?code=fixture&state='+encodeURIComponent(retryState));
    assert(retryCallback.ok());
    await page.reload();
    await page.getByRole('button',{name:'Add The Original Album to shelf',exact:true}).click();
    await page.getByRole('heading',{name:'Choose the album tracklist'}).waitFor();
    await page.locator('[data-action="select-variant"]').first().click();
    await page.getByRole('heading',{name:'Disc and side layout'}).waitFor();
    await page.getByRole('button',{name:'Find this tracklist on Spotify'}).click();
    await page.getByRole('heading',{name:'Choose Spotify recordings'}).waitFor();
    await page.locator('[data-action="choose-release"][data-spotify-id]').first().click();
    await page.locator('#toast').filter({hasText:'The Original Album added'}).waitFor();
    assert.equal((await api('shelf')).albums.length,1);
    await page.locator('[data-nav="shelf"]').click();
    await page.locator('.collection-shelves[data-layout="rail"]').waitFor();
    assert.equal((await api('status')).shelf_style,'floating');
    await api('settings','PUT',{interface:'classic',theme:'midnight'});await page.reload();
    await settings();await page.getByLabel('Open Spotify after pressing Play on this browser').check();await page.getByRole('button',{name:'Open Security settings'}).click();
    const passwordForm=page.locator('[data-security-form][data-operation="password"]');
    await passwordForm.getByLabel('Confirm your password').fill('alice-password-long-enough');
    await passwordForm.getByLabel('New password',{exact:true}).fill('alice-changed-password-long-enough');
    await passwordForm.getByRole('button',{name:'Change password',exact:true}).click();
    await page.locator('#toast').filter({hasText:'Security settings updated'}).waitFor();
    await page.locator('.close-modal').click();await signout();await login('bob','bob-password-long-enough');
    await page.getByRole('heading',{name:'Your first record awaits.',exact:true}).waitFor();
    assert.equal((await api('status')).interface,'vinyl');assert.equal((await api('status')).spotify_connected,false);
    await settings();assert.equal(await page.getByLabel('Open Spotify after pressing Play on this browser').isChecked(),false);
    await signout();await login('alice','alice-changed-password-long-enough');
    assert.equal((await api('shelf')).albums.length,1);assert.equal((await api('status')).interface,'classic');
    await signout();await login('owner','fixture-owner-password');
    assert.equal((await api('shelf')).albums.length,14);assert.equal((await api('status')).spotify_connected,true);
    assert.equal((await api('status')).interface,'vinyl');
    await page.locator('.collection-shelves[data-layout="rail"]').waitFor();
    assert.equal((await api('status')).shelf_style,'cabinet');
    assert.deepEqual(errors,[]);
    console.log('Account browser flows passed: create, switch, independent libraries/Spotify/preferences, and personal password change.');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);console.error(logs);process.exitCode=1;});
