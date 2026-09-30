"""Calibration screen served only through authenticated Home Assistant ingress."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import threading
from urllib.parse import urlsplit
import calibration_control as control

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Vinyl Guardian calibration</title>
<style>
:root{color-scheme:light dark;--bg:#111820;--card:#1c2632;--text:#f1f5fa;--muted:#bdc9d7;--accent:#8bdbbd;--border:#354557}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px system-ui,sans-serif}main{max-width:1160px;margin:auto;padding:24px}header{margin-bottom:22px}h1{font-size:28px;margin:0 0 8px}h2{font-size:20px;margin:0 0 14px}p{line-height:1.6}.muted{color:var(--muted)}.layout{display:grid;grid-template-columns:minmax(280px,1fr) minmax(320px,1.4fr);gap:20px}.card{border:1px solid var(--border);border-radius:16px;background:var(--card);padding:22px}.badge{display:inline-block;background:#304356;color:var(--text);padding:5px 10px;border-radius:20px;margin-bottom:16px}#instruction{font-size:19px;margin:0 0 22px;white-space:pre-wrap}button{border:0;border-radius:10px;background:var(--accent);color:#102a20;font-size:18px;font-weight:700;padding:14px 20px;cursor:pointer;width:100%}button:disabled{background:#354557;color:var(--muted);cursor:default}button:focus-visible{outline:3px solid white;outline-offset:4px}#connection{min-height:1.5em;color:#ffd091}#steps{padding:0;list-style:none;margin:20px 0 0}#steps li{padding:6px 0;color:var(--muted)}#steps li.current{color:var(--accent);font-weight:700}#steps li.done{color:var(--text)}#logs{font:13px/1.65 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;overflow:auto;height:58vh;min-height:250px;margin:0}label{display:flex;gap:8px;margin-bottom:14px;color:var(--muted)}.retry{border-top:1px solid var(--border);padding-top:16px;margin-top:16px}.retry label{display:block}.retry select{width:100%;font:inherit;padding:10px;border-radius:8px;background:var(--bg);color:var(--text);border:1px solid var(--border);margin-bottom:10px}.retry button{font-size:15px;margin-bottom:10px;background:#304356;color:var(--text)}.help{font-size:14px;margin-bottom:0}@media(max-width:760px){main{padding:16px}.layout{grid-template-columns:1fr}.card{padding:18px}#logs{height:38vh}h1{font-size:24px}}
</style></head><body><main><header><h1>🎵 Vinyl Guardian calibration</h1><div class="muted">Prepare each step, then press Continue. Instructions and live calibration logs stay together.</div></header><div class="layout"><section class="card"><div id="phase" class="badge">Connecting…</div><h2 id="stage">Calibration</h2><p id="instruction" aria-live="polite">Loading the current calibration step…</p><button id="continue" disabled>Continue</button><div id="connection" role="status" aria-live="polite"></div><div class="retry"><label for="repeat-stage">Step to repeat</label><select id="repeat-stage" disabled aria-label="Step to repeat"></select><button id="repeat" disabled>Repeat selected step</button><button id="restart" disabled>Restart calibration</button><p class="muted help">Repeating stops the current recording and returns to preparation for the selected step. That step and all later recordings are replaced. Restart begins again from Input gain. Your active profile stays in place until a new result is saved.</p></div><ol id="steps"></ol><p class="muted help">For fresh recordings, turn Reuse calibration audio off. When finished, disable calibration_mode and restart the app. You can leave this screen open throughout calibration.</p></section><section class="card"><h2>Live calibration log</h2><label><input type="checkbox" id="follow" checked> Follow new messages</label><pre id="logs" tabindex="0" aria-label="Live calibration log">Waiting for calibration messages…</pre></section></div></main>
<script>
const button=document.getElementById('continue'),connection=document.getElementById('connection');
const labels=['Input gain','Quiet baseline','Motor startup','Music to runout','Needle lift','Motor shutdown','Room disturbances'];
let current=null,busy=false,lastLogs='',lastInstruction='',lastRepeat='',repeatChosen=false;
const repeatButton=document.getElementById('repeat'),restartButton=document.getElementById('restart'),repeatSelect=document.getElementById('repeat-stage');
function render(state){current=state;document.getElementById('phase').textContent=({inactive:'Not running',starting:'Starting',waiting:'Ready for you',recording:'Recording / analysing',complete:'Finished',failed:'Stopped',saving:'Saving',changing:'Changing step'})[state.phase]||state.phase;document.getElementById('stage').textContent=state.stage_title;const instruction=document.getElementById('instruction');if(lastInstruction!==state.instruction){instruction.textContent=state.instruction;lastInstruction=state.instruction;}button.disabled=busy||!state.waiting;button.textContent=busy?'Continuing…':state.waiting?'Continue':state.phase==='complete'?'Calibration finished':'Wait for the next step';const stages=state.repeat_stages||[],signature=JSON.stringify(stages);if(lastRepeat!==signature){const selected=Number(repeatSelect.value);repeatSelect.replaceChildren();stages.forEach(index=>{const option=document.createElement('option');option.value=index;option.textContent=labels[index];repeatSelect.append(option);});repeatSelect.value=String(repeatChosen&&stages.includes(selected)?selected:stages.at(-1));lastRepeat=signature;}repeatSelect.disabled=busy||!stages.length;repeatButton.disabled=busy||!stages.length;restartButton.disabled=busy||!state.can_restart;const list=document.getElementById('steps');list.replaceChildren();labels.forEach((label,index)=>{const item=document.createElement('li');item.className=state.phase==='complete'||index<state.stage?'done':index===state.stage?'current':'';item.textContent=(item.className==='done'?'✓ ':item.className==='current'?'● ':'○ ')+label;list.append(item);});const log=document.getElementById('logs'),text=state.logs.map(item=>item.time+'  '+item.message).join('\n');if(text!==lastLogs){log.textContent=text||'Waiting for calibration messages…';lastLogs=text;if(document.getElementById('follow').checked)log.scrollTop=log.scrollHeight;}}
async function poll(){try{const response=await fetch('api/state',{cache:'no-store'});if(!response.ok)throw Error('Connection unavailable');render(await response.json());connection.textContent='';}catch(error){button.disabled=true;repeatButton.disabled=true;restartButton.disabled=true;repeatSelect.disabled=true;connection.textContent='Connection lost. Reconnecting automatically…';}finally{setTimeout(poll,1000);}}
async function sendAction(action,stage){if(busy||!current)return;const step=current.step_id;busy=true;button.disabled=true;repeatButton.disabled=true;restartButton.disabled=true;repeatSelect.disabled=true;try{const response=await fetch('api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Calibration-Token':current.screen_token||'__TOKEN__'},body:JSON.stringify({step_id:step,stage})});const data=await response.json();if(!response.ok)throw Error(data.error||'Unable to change step');render(data);}catch(error){connection.textContent=error.message;}finally{busy=false;}}
button.addEventListener('click',()=>{if(current&&current.waiting)sendAction('continue');});
repeatSelect.addEventListener('change',()=>{repeatChosen=true;});
repeatButton.addEventListener('click',()=>{if(current&&(current.repeat_stages||[]).includes(Number(repeatSelect.value)))sendAction('repeat',Number(repeatSelect.value));});
restartButton.addEventListener('click',()=>{if(current&&current.can_restart)sendAction('restart');});
poll();
</script></body></html>'''


def make_server(host='0.0.0.0', port=8099, allowed_peer='172.30.32.2'):
    token = secrets.token_urlsafe(32)
    page = PAGE.replace('__TOKEN__', token).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type='application/json'):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            if self.client_address[0] != allowed_peer:
                self.send(403, {'error': 'Use Home Assistant Open Web UI.'})
                return False
            return True

        def state(self):
            return dict(control.snapshot(), screen_token=token)

        def do_GET(self):
            if not self.authorized():
                return
            path = urlsplit(self.path).path
            if path == '/':
                self.send(200, page, 'text/html; charset=utf-8')
            elif path == '/api/state':
                self.send(200, self.state())
            else:
                self.send(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.authorized():
                return
            path = urlsplit(self.path).path
            if path not in ('/api/continue', '/api/repeat', '/api/restart'):
                self.send(404, {'error': 'Not found'})
                return
            if not secrets.compare_digest(self.headers.get('X-Calibration-Token', ''), token):
                self.send(403, {'error': 'Reload the calibration screen.'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024:
                    raise ValueError('Invalid body size')
                payload = json.loads(self.rfile.read(size))
                step_id = payload['step_id']
                if type(step_id) is not int:
                    raise ValueError('Invalid step')
            except (ValueError, KeyError, TypeError):
                self.send(400, {'error': 'Invalid confirmation'})
                return
            accepted = control.confirm(step_id) if path == '/api/continue' else control.request_navigation(path.rsplit('/', 1)[-1], step_id, payload.get('stage'))
            if not accepted:
                self.send(409, {'error': 'The step has changed or is saving. Wait for the screen to refresh.'})
                return
            self.send(200, self.state())

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def start_server():
    server = make_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
