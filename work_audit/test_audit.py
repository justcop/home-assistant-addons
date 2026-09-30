import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from engine import Audit
from app import create_app


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = dt.datetime(2026, 9, 30, 8, tzinfo=dt.timezone.utc).timestamp()
        self.db = Path(self.tmp.name) / 'audit.sqlite3'
        self.options = {'phone_screen_entity': 'binary_sensor.phone_screen',
                        'location_entity': 'person.justin', 'work_location_states': ['Work'],
                        'distracting_context_patterns': ['Reddit'], 'idle_threshold_seconds': 60}
        self.audit = Audit(self.db, self.options, clock=lambda: self.t)
        self.audit.connection('mqtt', True)
        self.audit.connection('ha', True)
        self.client = create_app(self.audit).test_client()
        self.ingress = {'REMOTE_ADDR': '172.30.32.2'}

    def tearDown(self):
        self.audit.db.close()
        self.tmp.cleanup()

    def advance(self, seconds):
        for _ in range(seconds // 5):
            self.t += 5
            self.audit.tick()

    def status(self, app='EmisWeb.exe', context='EditConsultation', patient='hashed_a', retained=False):
        self.audit.mqtt('status', json.dumps(dict(app=app, context=context, patient=patient)), retained)

    def totals(self):
        return self.audit.day('2026-09-30')['totals']

    def test_editor_and_idle_overlap_without_claiming_typing(self):
        self.status()
        self.audit.mqtt('idle', '0')
        self.advance(65)
        self.assertEqual(self.totals()['computer']['notes_editor'], 65)
        self.assertEqual(self.totals()['idle']['idle'], 5)
        self.assertNotIn('typing', self.totals()['computer'])

    def test_phone_overlap_requires_all_three_observations(self):
        self.status()
        self.audit.mqtt('idle', '100')
        self.audit.entity('binary_sensor.phone_screen', {'state': 'on'})
        self.audit.entity('person.justin', {'state': 'Work'})
        self.advance(20)
        self.assertEqual(self.totals()['phone_overlap']['possible_phone_use'], 20)
        self.audit.entity('person.justin', {'state': 'home'})
        self.advance(10)
        self.assertEqual(self.totals()['phone_overlap']['possible_phone_use'], 20)

    def test_unknown_screen_values_not_off(self):
        self.audit.entity('binary_sensor.phone_screen', {'state': 'unexpected'})
        self.assertEqual(self.audit.observe(self.t)['phone'][0], 'unknown')

    def test_browser_rules_conflict_is_ambiguous(self):
        self.status('chrome.exe', 'NICE Reddit discussion')
        self.assertEqual(self.audit.observe(self.t)['computer'][0], 'browser_ambiguous')
        self.status('chrome.exe', 'Random website')
        self.assertEqual(self.audit.observe(self.t)['computer'][0], 'browser_unclassified')

    def test_retained_status_does_not_start_session(self):
        self.status(retained=True)
        self.assertEqual(self.audit.observe(self.t)['computer'][0], 'unknown')

    def test_status_and_idle_heartbeats_expire_independently(self):
        self.status()
        self.audit.mqtt('idle', '0')
        self.advance(60)
        self.audit.mqtt('idle', '0')
        self.advance(35)
        self.assertEqual(self.audit.observe(self.t)['computer'][0], 'unknown')
        self.assertEqual(self.audit.observe(self.t)['idle'][0], 'recent_input')

    def test_mqtt_disconnect_clears_old_context(self):
        self.status()
        self.advance(20)
        self.audit.connection('mqtt', False)
        self.audit.connection('mqtt', True)
        self.audit.mqtt('idle', '0')
        self.advance(10)
        self.assertEqual(self.totals()['computer']['notes_editor'], 20)
        self.assertEqual(self.audit.observe(self.t)['patient'][0], 'unknown')

    def test_ha_disconnect_marks_phone_location_unknown(self):
        self.audit.entity('binary_sensor.phone_screen', {'state': 'on'})
        self.audit.connection('ha', False)
        self.assertEqual(self.audit.observe(self.t)['phone'][0], 'unknown')

    def test_restart_gap_is_not_counted_as_continued_activity(self):
        self.status()
        self.advance(20)
        self.audit.db.close()
        self.t += 3600
        self.audit = Audit(self.db, self.options, clock=lambda: self.t)
        self.advance(10)
        totals = self.totals()
        self.assertEqual(totals['computer']['notes_editor'], 20)
        self.assertGreaterEqual(totals['computer']['unknown'], 3600)

    def test_long_tick_gap_has_unknown_coverage(self):
        self.status()
        self.advance(10)
        self.t += 100
        self.audit.tick()
        totals = self.totals()
        self.assertLessEqual(totals['computer']['notes_editor'], 25)
        self.assertGreaterEqual(totals['computer']['unknown'], 85)

    def test_work_filter_intersects_phone_time(self):
        self.audit.entity('binary_sensor.phone_screen', {'state': 'on'})
        self.audit.set_override('work')
        self.advance(20)
        self.audit.set_override('break')
        self.advance(10)
        data = self.audit.day('2026-09-30', True)
        self.assertEqual(data['totals']['phone']['screen_on'], 20)
        self.assertEqual(data['covered_window_seconds'], 20)
        self.assertEqual(self.totals()['phone']['screen_on'], 30)

    def test_episode_labels_are_daily_and_do_not_expose_hashes(self):
        self.status(patient='hash_secret_a')
        self.advance(10)
        self.status(patient='hash_secret_b')
        self.advance(10)
        data = self.audit.day('2026-09-30')
        self.assertEqual([e['record'] for e in data['episodes']], ['Record 1', 'Record 2'])
        self.assertNotIn('hash_secret', json.dumps(data))

    def test_british_dst_day_boundaries(self):
        a, b = self.audit.bounds('2026-10-25')
        self.assertEqual(b-a, 25*3600)
        a, b = self.audit.bounds('2026-03-29')
        self.assertEqual(b-a, 23*3600)

    def test_unrelated_ha_entities_and_coordinates_not_recorded(self):
        self.audit.entity('sensor.private', {'state': 'secret'})
        self.audit.entity('person.justin', {'state': 'Work', 'attributes': {'latitude': 51.5}})
        events = self.audit.db.execute('SELECT payload FROM events').fetchall()
        text = json.dumps([row[0] for row in events])
        self.assertNotIn('latitude', text)
        self.assertNotIn('secret', text)

    def test_api_ingress_guard_and_input_validation(self):
        self.assertEqual(self.client.get('/api/day').status_code, 403)
        self.assertEqual(self.client.get('/api/day?date=bad', environ_overrides=self.ingress).status_code, 400)
        self.assertEqual(self.client.post('/api/session', json={'mode': 'work'}, environ_overrides=self.ingress).status_code, 403)
        response = self.client.post('/api/session', json={'mode': 'work'}, headers={'X-Work-Audit': '1'}, environ_overrides=self.ingress)
        self.assertEqual(response.status_code, 200)
        page = self.client.get('/', headers={'X-Ingress-Path': '/api/hassio_ingress/example'}, environ_overrides=self.ingress)
        self.assertIn(b'<base href="/api/hassio_ingress/example/">', page.data)

    def test_csv_formula_injection_escaped(self):
        self.status('chrome.exe', '=HYPERLINK("example")')
        self.advance(10)
        response = self.client.get('/api/export.csv?date=2026-09-30', environ_overrides=self.ingress)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"'=HYPERLINK", response.data)

    def test_invalid_payload_leaves_state_unchanged(self):
        self.status()
        for payload in ['NaN', '-1', 'Infinity']:
            with self.assertRaises(ValueError):
                self.audit.mqtt('idle', payload)
        with self.assertRaises(ValueError):
            self.audit.mqtt('status', '[]')
        self.assertEqual(self.audit.observe(self.t)['computer'][0], 'notes_editor')


if __name__ == '__main__':
    unittest.main()
