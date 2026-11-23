import test from 'node:test';
import assert from 'node:assert/strict';
import { WhatsAppMaintenanceMonitor } from '../maintenance-monitor.mjs';

function mock(outcomes) {
  const calls = [];
  const request = async (url, options = {}) => {
    calls.push({url, options});
    if (String(url).includes('api.github.com')) {
      const name = String(url).includes('whatsapp-upstream.yml') ? 'upstream' : 'delivery';
      const conclusion = outcomes[name];
      return new Response(JSON.stringify({workflow_runs: conclusion === 'none' ? [] : [{
        id: name === 'upstream' ? 100 : 200, status: 'completed', conclusion,
        updated_at: '2026-11-23T12:00:00Z'
      }]}),{status:200});
    }
    return new Response('[]',{status:200});
  };
  const monitor = new WhatsAppMaintenanceMonitor({request,haToken:'test-ha-token',
    logger:{warn() {}}});
  return {monitor,calls};
}
test('failed upstream run creates only one persistent HA notification and success clears it',async()=>{
  const outcomes={upstream:'failure',delivery:'success'};
  const {monitor,calls}=mock(outcomes);
  await monitor.check();
  let notices=calls.filter(c=>c.url.includes('/services/persistent_notification/'));
  assert.equal(notices.length,1);
  assert.match(notices[0].url,/create$/);
  assert.equal(JSON.parse(notices[0].options.body).notification_id,'whatsapp_maintenance_pipeline_failure');
  assert.match(JSON.parse(notices[0].options.body).message,/upstream checks: failure/);
  await monitor.check();
  assert.equal(calls.filter(c=>c.url.includes('persistent_notification')).length,1);
  outcomes.upstream='success';
  await monitor.check();
  notices=calls.filter(c=>c.url.includes('persistent_notification'));
  assert.equal(notices.length,2);
  assert.match(notices[1].url,/dismiss$/);
});
test('first healthy check dismisses stale notifications across restarts',async()=>{
  const {monitor,calls}=mock({upstream:'success',delivery:'success'});
  await monitor.check();
  assert.equal(calls.filter(c=>c.url.includes('/persistent_notification/dismiss')).length,1);
  assert.equal(monitor.status().problem,null);
});
test('incomplete or missing workflow runs never count as failures',async()=>{
  const {monitor,calls}=mock({upstream:'none',delivery:'success'});
  await monitor.check();
  assert.equal(monitor.status().problem,null);
  assert.equal(calls.some(c=>c.url.includes('/persistent_notification/create')),false);
});
test('GitHub API failure cannot clear an existing notification',async()=>{
  let broken=false;
  const calls=[];
  const request=async url=>{
    calls.push(String(url));
    if (String(url).includes('api.github.com')) {
      if (broken) return new Response('{}',{status:403});
      return new Response(JSON.stringify({workflow_runs:[{status:'completed',conclusion:'failure',id:123}]}),{status:200});
    }
    return new Response('[]',{status:200});
  };
  const monitor=new WhatsAppMaintenanceMonitor({request,haToken:'test',logger:{warn(){}}});
  await monitor.check();
  assert.ok(monitor.status().problem);
  broken=true;
  await monitor.check();
  assert.ok(monitor.status().problem);
  assert.equal(calls.filter(c=>c.endsWith('/persistent_notification/dismiss')).length,0);
});
