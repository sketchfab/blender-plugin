import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from token_cache import TokenCache


class TokenCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / '.cache'

        class Cache(TokenCache):
            SKETCHFAB_CACHE_FILE = self.path

        self.cache = Cache

    @unittest.skipUnless(hasattr(os, 'fchmod'), 'POSIX file modes')
    def test_new_and_legacy_files_use_owner_only_permissions(self):
        self.cache.save_key('api_token', 'synthetic')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.path.chmod(0o644)
        self.assertEqual(self.cache.get_key('api_token'), 'synthetic')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_manual_save_recovers_from_invalid_json_or_non_object_cache(self):
        for raw in ('bad json', 'null', '[]'):
            with self.subTest(raw=raw):
                self.path.write_text(raw)
                with self.assertRaises(ValueError):
                    self.cache.read()
                self.cache.save_key('api_token', 'synthetic')
                self.assertEqual(json.loads(self.path.read_text()), {'api_token': 'synthetic'})

    def test_failed_replacement_leaves_original_and_removes_temporary_file(self):
        self.cache.save_key('api_token', 'original')
        with patch('token_cache.os.replace', side_effect=OSError), self.assertRaises(OSError):
            self.cache.save_key('api_token', 'replacement')
        self.assertEqual(self.cache.get_key('api_token'), 'original')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_logout_clears_credential_fields(self):
        self.cache.write({'api_token': 'synthetic', 'access_token': 'legacy', 'username': 'test', 'other': 1})
        self.cache.delete_keys('api_token', 'access_token', 'username')
        self.assertEqual(self.cache.read(), {'other': 1})
