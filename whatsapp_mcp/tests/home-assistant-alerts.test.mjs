import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { HomeAssistantBackupAlerts } from '../home-assistant-alerts.mjs';

test('backup failure creates one HA persistent notification and verified recovery clears it', async () => {
  const calls = [];
  const alerts = new HomeAssistantBackupAlerts({
    token: 'test-token',
    request: async (url, options) => {
      calls.push({url,body: JSON.parse(options.body),auth:options.headers.Authorization});
      return new Response('[]', { status: 200 });
    }
  });
  await alerts.failure('HTTP 403 PERMISSION_DENIED');
  await alerts.failure('HTTP 403 PERMISSION_DENIED');
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /persistent_notification\/create$/);
  assert.equal(calls[0].auth, 'Bearer test-token');
  assert.match(calls[0].body.message, /PERMISSION_DENIED/);
  assert.equal(calls[0].body.notification_id, 'whatsapp_cloud_backup_failure');
  await alerts.recovered();
  assert.equal(calls.length, 2);
  assert.match(calls[1].url, /persistent_notification\/dismiss$/);
  await alerts.recovered();
  assert.equal(calls.length, 2);
});

test('notification creation and dismissal are serialized even when called together', async () => {
  const calls = [];
  const alerts = new HomeAssistantBackupAlerts({ token:'test-token',
    request: async url => {
      await new Promise(resolve => setTimeout(resolve, 5));
      calls.push(url.split('/').at(-1));
      return new Response('[]',{status:200});
    }
  });
  await Promise.all([alerts.failure('error'),alerts.recovered()]);
  assert.deepEqual(calls, ['create','dismiss']);
});

test('failure notifications do not leak message contents or device tokens', async () => {
  const calls = [];
  const alerts = new HomeAssistantBackupAlerts({token:'test-token',request:async (_url,options)=>{
    calls.push(JSON.parse(options.body));return new Response('[]',{status:200});
  }});
  await alerts.failure('Google Drive API failed (HTTP 403, PERMISSION_DENIED)');
  assert.equal(calls.length, 1);
  assert.match(calls[0].message,/Google Drive API failed/);
  assert.doesNotMatch(calls[0].message,/https:\/\/www.googleapis.com\/upload/);
});

test('archive has an always-visible failure banner and settings entry in footer', () => {
  const html = fs.readFileSync(new URL('../archive-ui/index.html', import.meta.url),'utf8');
  const js = fs.readFileSync(new URL('../archive-ui/app.js', import.meta.url),'utf8');
  assert.match(html,/id="backup-alert"/);
  assert.match(html,/id="backup-alert-open"/);
  const footer = html.slice(html.indexOf('<footer class="sidebar-footer">'),html.indexOf('</footer>'));
  assert.match(footer,/id="show-backup"/);
  const search = html.slice(html.indexOf('<div class="side-search">'),html.indexOf('chat-tabs'));
  assert.doesNotMatch(search,/id="show-backup"/);
  assert.match(js,/refreshBackupAlert/);
  assert.match(js,/retentionPending/);
  const yaml=fs.readFileSync(new URL('../config.yaml',import.meta.url),'utf8');
  assert.match(yaml,/homeassistant_api: true/);
});
