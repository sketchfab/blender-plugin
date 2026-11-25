from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from zipfile import ZipFile


package = ModuleType('sketchfab_test')
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
transfers = importlib.import_module('sketchfab_test.transfers')
UID = '0123456789abcdef0123456789abcdef'


def make_archive(extra=(), document=None):
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as archive:
        archive.writestr('scene.gltf', json.dumps(document or {'asset': {'version': '2.0'}}))
        for name, content in extra:
            archive.writestr(name, content)
    return buffer.getvalue()


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), DownloadHandler)
        self.server.archive = make_archive([('texture.png', b'image')])
        self.server.requests = []
        self.server.mode = 'normal'
        self.server.started = threading.Event()
        self.server.resume = threading.Event()
        self.server.base = 'http://127.0.0.1:{}'.format(self.server.server_port)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.resume.set)
        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)

    def start_job(self):
        base = self.server.base
        job = transfers.DownloadJob(self.cache.name, base + '/models', base + '/orgs', base + '/me/orgs',
                                    {'Authorization': 'Token synthetic'}, UID, None, ())
        job.start()
        self.addCleanup(job.cleanup)
        return job

    def test_download_uses_owned_directory_and_sends_no_token_to_archive(self):
        existing = Path(self.cache.name) / 'existing.zip'
        existing.write_bytes(b'unmanaged')
        job = self.start_job()
        job.join(5)
        self.assertFalse(job.is_alive())
        self.assertIsNone(job.error)
        self.assertTrue(job.path.is_file())
        self.assertEqual(job.metadata['name'], 'Fixture')
        self.assertIn(('/archive', None), self.server.requests)
        job.cleanup()
        self.assertEqual(list(Path(self.cache.name).iterdir()), [existing])

    def test_interrupted_download_is_discarded_and_can_be_retried(self):
        self.server.mode = 'interrupt'
        job = self.start_job()
        job.join(5)
        self.assertFalse(job.is_alive())
        self.assertTrue(job.error)
        self.assertEqual(list(Path(self.cache.name).iterdir()), [])

        self.server.mode = 'normal'
        retry = self.start_job()
        retry.join(5)
        self.assertIsNone(retry.error)
        self.assertTrue(retry.path.is_file())

    def test_cancelling_a_download_discards_partial_output(self):
        self.server.mode = 'pause'
        self.server.archive = make_archive([('texture.png', b'x' * (2 * transfers.CHUNK_SIZE))])
        job = self.start_job()
        self.assertTrue(self.server.started.wait(5))
        job.cancel()
        self.server.resume.set()
        job.join(5)
        self.assertFalse(job.is_alive())
        self.assertIsNone(job.path)
        self.assertEqual(list(Path(self.cache.name).iterdir()), [])

    def test_invalid_archive_members_and_external_paths_are_rejected(self):
        invalid = (
            make_archive([('../outside.png', b'')]),
            make_archive([('good\\..\\outside.png', b'')]),
            make_archive([('C:/outside.png', b'')]),
            make_archive(document={'images': [{'uri': '%2e%2e/outside.png'}]}),
        )
        for archive in invalid:
            self.server.archive = archive
            job = self.start_job()
            job.join(5)
            self.assertTrue(job.error)
            self.assertIsNone(job.path)
            self.assertEqual(list(Path(self.cache.name).iterdir()), [])


class DownloadHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.server.requests.append((self.path, self.headers.get('Authorization')))
        if self.path.endswith('/download'):
            body = json.dumps({'gltf': {'url': self.server.base + '/archive'}}).encode()
        elif self.path == '/archive':
            body = self.server.archive
        else:
            body = b'{"name":"Fixture"}'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.path == '/archive' and self.server.mode == 'interrupt':
            self.wfile.write(body[:10])
        elif self.path == '/archive' and self.server.mode == 'pause':
            self.wfile.write(body[:transfers.CHUNK_SIZE])
            self.server.started.set()
            self.server.resume.wait(5)
            self.wfile.write(body[transfers.CHUNK_SIZE:])
        else:
            self.wfile.write(body)
