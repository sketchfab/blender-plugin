from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from cached_login import CachedLogin


class LoginInterpreterTests(unittest.TestCase):
    def test_uses_blender_reported_interpreter_with_legacy_name(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'python3.7m'
            binary.touch()
            app = SimpleNamespace(binary_path_python=str(binary),
                                  handlers=SimpleNamespace(persistent=lambda callback: callback))
            login = CachedLogin(SimpleNamespace(bpy=SimpleNamespace(app=app)))
            self.assertEqual(login.interpreter(), str(binary))
