import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from moneyhub import ApiResponse, MoneyhubClient, derive_intermediate_secret, extract_rows
from server import Store


class MoneyhubCrypto(unittest.TestCase):
    def test_intermediate_secret_fixed_vector(self):
        token = derive_intermediate_secret(
            'person@example.com',
            'secret',
            now=123456,
            jti='0011223344556677',
        )
        self.assertEqual(
            token,
            'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.'
            'eyJpbnRlcm1lZGlhdGVVc2VyU2VjcmV0S2V5IjoiZmJiZTExNmE3MjZiYjMwNzI4YmU1MTdjMGMzNzgwY2QwY2E2ZDNkMzQxN2Q1NTM5YzVhN2MzMjhhNzY3MGU1ZCIsImlhdCI6MTIzNDU2LCJqdGkiOiIwMDExMjIzMzQ0NTU2Njc3In0.'
            'TsV5EYduiW0qoWXEkXuFwQ7Osgv_SosL2BujbcngIms',
        )

    def test_extract_rows_common_shapes(self):
        rows = [{'uid': '1'}]
        self.assertEqual(extract_rows(rows, 'transactions'), rows)
        self.assertEqual(extract_rows({'transactions': rows}, 'transactions'), rows)
        self.assertEqual(extract_rows({'data': rows}, 'transactions'), rows)
        self.assertEqual(extract_rows({'data': {'transactions': rows}}, 'transactions'), rows)
        self.assertEqual(extract_rows({}, 'transactions'), [])


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class MoneyhubFlow(unittest.TestCase):
    def test_login_totp_and_pull(self):
        client = MoneyhubClient(
            'person@example.com',
            'tenant-1',
            device_id='device-1',
            base_url='https://example.invalid/',
        )
        calls = []

        def fake(path, method='GET', payload=None, query=None, protected=False):
            calls.append((path, method, payload, query, protected))
            if path == 'login' and payload.get('totpCode'):
                return ApiResponse({'email': 'person@example.com'}, FakeHeaders({'csrf-token': 'csrf-final'}))
            if path == 'login':
                self.assertEqual(payload['email'], 'person@example.com')
                self.assertEqual(payload['tenantId'], 'tenant-1')
                self.assertEqual(payload['deviceId'], 'device-1')
                self.assertEqual(payload['hash'], hashlib.sha256(b'secret').hexdigest())
                self.assertEqual(payload['intermediateUserSecret'].count('.'), 2)
                return ApiResponse({'loginToken': 'challenge'}, FakeHeaders())
            if path == 'apiv2/accounts/active':
                self.assertTrue(protected)
                return ApiResponse([{'uid': 'a1'}], FakeHeaders())
            if path == 'apiv2/accounts':
                self.assertTrue(protected)
                return ApiResponse([{'uid': 'a1', 'accountName': 'Example'}], FakeHeaders())
            if path == 'apiV2/transactions':
                self.assertTrue(protected)
                self.assertEqual(query, {'startDate': '2026-07-01', 'endDate': '2026-10-08'})
                return ApiResponse([{'uid': 't1', 'accountUid': 'a1', 'amount': -1}], FakeHeaders())
            raise AssertionError(path)

        client._request = fake
        self.assertEqual(client.start_login('secret'), {'status': 'totp_required'})
        self.assertEqual(client.status, 'totp_required')
        self.assertEqual(client.verify_totp(' 123 456 '), {'status': 'authenticated'})
        self.assertEqual(client.csrf_token, 'csrf-final')
        pulled = client.pull('2026-07-01', '2026-10-08')
        self.assertEqual(pulled['transactions'][0]['uid'], 't1')
        self.assertEqual([call[0] for call in calls[-3:]], [
            'apiv2/accounts/active', 'apiv2/accounts', 'apiV2/transactions'
        ])


class MoneyhubPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'money.sqlite')

    def tearDown(self):
        self.tmp.cleanup()

    def test_settings_exclude_credentials_and_pull_upserts(self):
        self.store.set_moneyhub_settings('person@example.com', 'tenant-1', 'device-1')
        summary = self.store.moneyhub_summary()
        self.assertEqual(summary['email'], 'person@example.com')
        self.assertNotIn('password', summary)
        self.assertNotIn('csrf_token', summary)

        self.store.save_moneyhub_pull(
            '2026-07-01',
            '2026-10-08',
            [{'uid': 'a1'}],
            [{'uid': 'a1', 'accountName': 'Current account'}],
            [{'uid': 't1', 'accountUid': 'a1', 'date': '2026-10-01T00:00:00.000Z',
              'dateModified': '2026-10-01T01:00:00.000Z', 'amount': -10, 'deleted': False}],
        )
        summary = self.store.moneyhub_summary()
        self.assertEqual(summary['account_count'], 1)
        self.assertEqual(summary['transaction_count'], 1)
        self.assertEqual(summary['last_pull']['transaction_count'], 1)

        self.store.save_moneyhub_pull(
            '2026-10-01',
            '2026-10-08',
            [{'uid': 'a1'}],
            [{'uid': 'a1', 'accountName': 'Renamed account'}],
            [{'uid': 't1', 'accountUid': 'a1', 'date': '2026-10-01T00:00:00.000Z',
              'dateModified': '2026-10-02T01:00:00.000Z', 'amount': -12, 'deleted': False}],
        )
        summary = self.store.moneyhub_summary()
        self.assertEqual(summary['account_count'], 1)
        self.assertEqual(summary['transaction_count'], 1)


if __name__ == '__main__':
    unittest.main()
