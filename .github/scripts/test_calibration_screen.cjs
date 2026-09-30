const {chromium}=require('playwright');
const {spawn}=require('child_process');
const {createInterface}=require('readline');
const path=require('path');
const appDir=path.resolve(__dirname,'../../vinyl_guardian');
const http=require('http');
const assert=require('assert');
(async()=>{
const fixture=spawn('python',['-u','-c',`
import sys,threading,time
sys.path.insert(0,sys.argv[1])
import calibration_control as c
from calibration_web import make_server
c.begin(True); c.configure(lambda message:None)
c.set_stage(2,'Motor startup')
for i in range(30):c.append_log('Diagnostic sample '+str(i))
def wizard():
 c.wait_for_confirmation('Keep the needle raised. After Continue, switch the motor ON during the 10-second action window.',c.append_log)
 c.append_log('ACTION WINDOW (10s): Switch motor ON now!')
 time.sleep(2)
 c.set_stage(3,'Music to runout')
 c.wait_for_confirmation('Prepare the last track. Press Continue, then lower the needle.',c.append_log)
 c.set_status('Calibration finished. Disable calibration_mode and restart.',phase='complete')
threading.Thread(target=wizard,daemon=True).start()
s=make_server('127.0.0.1',0,allowed_peer='127.0.0.1');print(s.server_port);s.serve_forever()
`,appDir]);
let browser,proxy;
try{
const port=await new Promise(resolve=>{const reader=createInterface({input:fixture.stdout});reader.once('line',resolve)});
proxy=http.createServer((request,response)=>{const target=http.request({hostname:'127.0.0.1',port:Number(port),path:request.url.replace('/api/hassio_ingress/test/','/'),method:request.method,headers:request.headers},upstream=>{response.writeHead(upstream.statusCode,upstream.headers);upstream.pipe(response);});request.pipe(target);target.on('error',()=>{response.writeHead(502);response.end();});});
await new Promise(resolve=>proxy.listen(0,'127.0.0.1',resolve));
browser=await chromium.launch({headless:true}); const page=await browser.newPage({viewport:{width:1280,height:850}});
await page.goto('http://127.0.0.1:'+proxy.address().port+'/api/hassio_ingress/test/');await page.locator('button:enabled').waitFor();
assert(await page.locator('#instruction').innerText(), 'Instructions missing');
assert((await page.locator('#logs').innerText()).includes('Diagnostic sample 29'), 'Live logs missing');
const desktopButton=await page.locator('#continue').boundingBox();assert(desktopButton.y+desktopButton.height<850,'Desktop continue not visible');
await page.locator('#continue').click();
await page.waitForFunction(()=>document.querySelector('#continue').disabled);
await page.locator('#stage').filter({hasText:'Music to runout'}).waitFor();
await page.locator('button:enabled').waitFor();
await page.setViewportSize({width:390,height:844});await page.reload();await page.locator('button:enabled').waitFor();
const box=await page.locator('#continue').boundingBox();if(box.y+box.height>844)throw Error('Continue is outside phone viewport');
await page.locator('#continue').click();await page.locator('#phase').filter({hasText:'Finished'}).waitFor();
if(await page.locator('#continue').isEnabled())throw Error('Finished button enabled');
console.log('Desktop and mobile flow passed; instructions, logs, stage progression and completion verified.');
}finally{if(browser)await browser.close();if(proxy)await new Promise(resolve=>proxy.close(resolve));fixture.kill();}
})().catch(error=>{console.error(error);process.exitCode=1});
