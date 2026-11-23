import test from 'node:test';
import assert from 'node:assert/strict';
import { collectMaintenanceProblems, WhatsAppUpstreamMonitor } from '../upstream-maintenance.mjs';
import { HomeAssistantUpstreamAlerts } from '../home-assistant-alerts.mjs';

test('upstream issue and failed image publisher are actionable; completed healthy builds clear them', () => {
  const a = collectMaintenanceProblems([{state:'open',title:'Review new Baileys',
    labels:[{name:'whatsapp-upstream-action-needed'}]}], {
    'whatsapp-upstream.yml':[{status:'completed',conclusion:'success'}],
    'addon-delivery.yml':[{status:'completed',conclusion:'failure'}]
  });
  assert.equal(a.length,2);
  assert.match(a.join('; '),/Baileys/);
  assert.match(a.join('; '),/addon-delivery/);
  assert.deepEqual(collectMaintenanceProblems([],{ 'addon-delivery.yml': [
    {status:'in_progress', conclusion:null}] }),[]);
});

test('Home Assistant receives one persistent issue notification; recovery dismisses it', async () => {
  const calls = [];
  const alerts = new HomeAssistantUpstreamAlerts({ token:'dummy',
    request:async (url, options) => {
      calls.push({url,body:JSON.parse(options.body)});
      return new Response('[]',{status:200});
    } });
  const issues = [{state:'open',title:'Upstream privacy review pending',
    labels:[{name:'whatsapp-upstream-action-needed'}]}];
  let failing = true;
  const monitor = new WhatsAppUpstreamMonitor({alerts, request:async url=>{
    if(url.includes('/issues?')) return new Response(JSON.stringify(failing?issues:[]),{status:200});
    return new Response(JSON.stringify({workflow_runs:[{
      status:'completed',conclusion:'success'
    }]}),{status:200});
  }});
  await monitor.check();
  await monitor.check();
  assert.equal(calls.length,1);
  assert.match(calls[0].url,/persistent_notification\/create$/);
  assert.equal(calls[0].body.notification_id,'whatsapp_upstream_maintenance');
  assert.doesNotMatch(calls[0].body.message,/Bearer dummy/);
  failing = false;
  await monitor.check();
  assert.equal(calls.length,2);
  assert.match(calls[1].url,/persistent_notification\/dismiss$/);
});

test('transient public GitHub API failure is retried before notifying HA', async () => {
  const messages = [];
  const monitor = new WhatsAppUpstreamMonitor({
    alerts:{failure:async m=>messages.push(m),recovered:async()=>{}},
    request:async()=>new Response('{}',{status:503})
  });
  await monitor.check();
  assert.equal(messages.length,0);
  await monitor.check();
  assert.equal(messages.length,1);
  assert.match(messages[0],/Unable to check WhatsApp upstream/);
});
