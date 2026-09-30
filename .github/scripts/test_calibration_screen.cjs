const {chromium}=require('playwright');
const {spawn}=require('child_process');
const {createInterface}=require('readline');
const path=require('path');
const appDir=path.resolve(__dirname,'../../vinyl_guardian');
const http=require('http');
const assert=require('assert');
(async()=>{
const fixture=spawn('python',['-u','-c',`
import sys,threading,tempfile
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import calibration_control as c
from calibration_session import CalibrationSession,TITLES
from calibration_web import make_server
c.begin(True); c.configure(lambda message:None)
root=tempfile.TemporaryDirectory()
files={str(i):str(Path(root.name)/f'{i}.wav') for i in range(1,7)}
session=CalibrationSession();session.prepare(root.name,files,False,8)
for i in range(30):c.append_log('Diagnostic sample '+str(i))
def callback(index):
 def record():
  c.wait_for_confirmation('Prepare '+TITLES[index]+'. Press Continue to record.',c.append_log)
  c.append_log('ACTION WINDOW: '+TITLES[index])
  c.pause(2)
  if index:Path(files[str(index)]).write_text('recording')
  return 50 if index==0 else None
 return record
def wizard():
 while True:
  try:
   session.record([callback(i) for i in range(7)],c.append_log)
   c.set_status('Calibration finished. Disable calibration_mode and restart.',phase='complete')
   c.wait_for_navigation()
  except c.CalibrationNavigation as request:
   session.navigate(request,c.append_log)
threading.Thread(target=wizard,daemon=True).start()
s=make_server('127.0.0.1',0,allowed_peer='127.0.0.1');print(s.server_port);s.serve_forever()
`,appDir]);
let browser,proxy;
try{
const port=await new Promise((resolve,reject)=>{const reader=createInterface({input:fixture.stdout});reader.once('line',resolve);fixture.once('exit',()=>reject(Error('Fixture exited')));});
proxy=http.createServer((request,response)=>{const target=http.request({hostname:'127.0.0.1',port:Number(port),path:request.url.replace('/api/hassio_ingress/test/','/'),method:request.method,headers:request.headers},upstream=>{response.writeHead(upstream.statusCode,upstream.headers);upstream.pipe(response);});request.pipe(target);target.on('error',()=>{response.writeHead(502);response.end();});});
await new Promise(resolve=>proxy.listen(0,'127.0.0.1',resolve));
browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:850}});
await page.goto('http://127.0.0.1:'+proxy.address().port+'/api/hassio_ingress/test/');
async function waiting(title){await page.locator('#stage').filter({hasText:title}).waitFor();await page.locator('#continue:enabled').waitFor();}
await waiting('Input gain');
assert((await page.locator('#logs').innerText()).includes('Diagnostic sample 29'),'Live logs missing');
const desktopButton=await page.locator('#continue').boundingBox();assert(desktopButton.y+desktopButton.height<850,'Desktop continue not visible');
// Repeat while capture is active, then repeat a previous completed stage.
await page.locator('#continue').click();await page.locator('#phase').filter({hasText:'Recording / analysing'}).waitFor();
await page.locator('#repeat:enabled').click();await page.locator('#logs').filter({hasText:'Restarting from Input gain'}).waitFor();await waiting('Input gain');
await page.locator('#continue').click();await waiting('Quiet baseline');
await page.locator('#repeat-stage').selectOption('0');await page.locator('#repeat').click();await waiting('Input gain');
await page.locator('#restart').click();await page.locator('#continue:enabled').waitFor();
await page.setViewportSize({width:390,height:844});await page.reload();await waiting('Input gain');
const box=await page.locator('#continue').boundingBox();assert(box.y+box.height<844,'Continue outside phone viewport');
const titles=['Input gain','Quiet baseline','Motor startup','Music to runout','Needle lift','Motor shutdown','Room disturbances'];
for(const title of titles){await waiting(title);await page.locator('#continue').click();}
await waiting('Review recordings');await page.locator('#continue').click();await page.locator('#phase').filter({hasText:'Finished'}).waitFor();
assert(!(await page.locator('#continue').isEnabled()),'Finished Continue enabled');
await page.locator('#repeat-stage').selectOption('6');await page.locator('#repeat').click();await waiting('Room disturbances');await page.locator('#continue').click();
await waiting('Review recordings');await page.locator('#continue').click();await page.locator('#phase').filter({hasText:'Finished'}).waitFor();
await page.locator('#restart').click();await waiting('Input gain');
console.log('Desktop/mobile flow passed: capture interruption, previous-stage repeat, review, completion, repeat and restart after completion.');
}finally{if(browser)await browser.close();if(proxy)await new Promise(resolve=>proxy.close(resolve));fixture.kill();}
})().catch(error=>{console.error(error);process.exitCode=1});
