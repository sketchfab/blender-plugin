import json
from pathlib import Path
import ssl
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LoginWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = subprocess.Popen([sys.executable, str(ROOT / 'tests/fake_login_api.py')],
                                      stdout=subprocess.PIPE, text=True)
        cls.port = int(cls.server.stdout.readline())

    @classmethod
    def tearDownClass(cls):
        cls.server.kill()
        cls.server.communicate()

    def verify(self, mode, token='synthetic-test-token'):
        payload = {'url': 'http://127.0.0.1:{}/{}'.format(self.port, mode),
                   'token': token, 'ca_file': ssl.get_default_verify_paths().cafile}
        result = subprocess.run([sys.executable, '-I', str(ROOT / 'login_worker.py')],
                                input=json.dumps(payload).encode(), capture_output=True, timeout=10, check=True)
        self.assertNotIn(token.encode(), result.stdout + result.stderr)
        self.assertEqual(result.stderr, b'')
        return json.loads(result.stdout)

    def test_account_and_organization_membership(self):
        result = self.verify('organizations')
        self.assertEqual(result['username'], 'probe')
        self.assertTrue(result['user_has_orgs'])
        self.assertFalse(result['orgs_check_failed'])

    def test_rejected_token(self):
        self.assertEqual(self.verify('rejected'), {'error': 'rejected'})

    def test_server_failure(self):
        self.assertEqual(self.verify('unavailable'), {'error': 'unavailable'})

    def test_organization_failure_preserves_verified_account(self):
        result = self.verify('org-failure')
        self.assertEqual(result['username'], 'probe')
        self.assertTrue(result['orgs_check_failed'])

    def test_redirect_is_not_followed(self):
        self.assertEqual(self.verify('redirect'), {'error': 'unavailable'})

    def test_invalid_and_oversized_responses(self):
        for mode in ('bad-data', 'large'):
            with self.subTest(mode=mode):
                self.assertEqual(self.verify(mode), {'error': 'unavailable'})

    def test_malformed_and_oversized_credentials_are_not_echoed(self):
        for token in ('synthetic-test-token\nsensitive', 'synthetic-test-token' * 300):
            with self.subTest(length=len(token)):
                self.assertEqual(self.verify('ok', token), {'error': 'unavailable'})


if __name__ == '__main__':
    unittest.main()
