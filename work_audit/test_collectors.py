import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import websocket
from collectors import ha_loop, start_mqtt
from engine import Audit


class CollectorsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = Audit(Path(self.tmp.name) / 'test.db', {
            'phone_screen_entity': 'binary_sensor.screen', 'location_entity': 'person.justin'})

    def tearDown(self):
        self.audit.db.close()
        self.tmp.cleanup()

    def test_mqtt_subscriptions_and_rejected_subscription(self):
        client = MagicMock()
        with patch('paho.mqtt.client.Client', return_value=client):
            start_mqtt(self.audit, {'mqtt_host': 'broker', 'mqtt_port': 443,
                                   'mqtt_transport': 'websockets', 'mqtt_tls': True,
                                   'mqtt_username': 'test', 'mqtt_password': 'secret'})
        client.tls_set.assert_called_once()
        client.ws_set_options.assert_called_once_with(path='/mqtt')
        client.on_connect(client, None, None, SimpleNamespace(is_failure=False), None)
        client.subscribe.assert_called_once_with([('work/monitor/status', 1), ('work/monitor/idle', 1)])
        self.assertFalse(self.audit.mqtt_connected)
        client.on_subscribe(client, None, 1, [SimpleNamespace(is_failure=False)], None)
        self.assertTrue(self.audit.mqtt_connected)
        payload = json.dumps({'app': 'EmisWeb.exe', 'context': 'Results', 'patient': 'None'}).encode()
        client.on_message(client, None, SimpleNamespace(topic='work/monitor/status', payload=payload, retain=False))
        self.assertEqual(self.audit.status['context'], 'Results')
        client.on_disconnect(client, None, None, None, None)
        self.assertIsNone(self.audit.status)
        client.on_subscribe(client, None, 1, [SimpleNamespace(is_failure=True)], None)
        self.assertFalse(self.audit.mqtt_connected)

    def test_ha_snapshot_race_ping_and_reconnect(self):
        def event(state, updated):
            return {'type': 'event', 'event': {'data': {'entity_id': 'binary_sensor.screen',
                    'new_state': {'state': state, 'last_updated': updated}}}}
        messages = [
            {'type': 'auth_required'}, {'type': 'auth_ok'},
            {'type': 'result', 'id': 1, 'success': True, 'result': None},
            event('off', '2026-09-30T09:00:00+00:00'),
            {'type': 'result', 'id': 2, 'success': True, 'result': [
                {'entity_id': 'binary_sensor.screen', 'state': 'on', 'last_updated': '2026-09-30T09:00:01+00:00', 'attributes': {'friendly_name': 'Phone screen'}},
                {'entity_id': 'person.justin', 'state': 'Work', 'attributes': {'latitude': 51}},
                {'entity_id': 'sensor.unrelated', 'state': 'private'}]},
            websocket.WebSocketTimeoutException(), {'type': 'pong', 'id': 3},
            event('off', '2026-09-30T09:00:02+00:00'),
            websocket.WebSocketConnectionClosedException(),
        ]
        ws = MagicMock()
        def recv():
            result = messages.pop(0)
            if isinstance(result, Exception):
                raise result
            return json.dumps(result)
        ws.recv.side_effect = recv
        class StopLoop(Exception):
            pass
        with patch.dict(os.environ, {'SUPERVISOR_TOKEN': 'test'}), \
             patch('websocket.create_connection', return_value=ws), \
             patch('collectors.time.sleep', side_effect=StopLoop):
            with self.assertRaises(StopLoop):
                ha_loop(self.audit)
        states = [json.loads(r[0])['state'] for r in self.audit.db.execute("SELECT payload FROM events WHERE kind='binary_sensor.screen'")]
        self.assertEqual(states, ['on', 'off'])
        commands = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        self.assertEqual(commands[1]['type'], 'subscribe_events')
        self.assertEqual(commands[2]['type'], 'get_states')
        self.assertEqual(commands[3]['type'], 'ping')
        self.assertFalse(self.audit.ha_connected)
        self.assertEqual(self.audit.observe(self.audit.clock())['phone'][0], 'unknown')
        ws.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
