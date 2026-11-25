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


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(transfers.shutil.rmtree, self.directory, ignore_errors=True)

    def test_multipart_matches_content_length_and_streams_the_file(self):
        from email import policy
        from email.parser import BytesParser
        from hashlib import sha256

        received = {}

        class UploadHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_PUT(self):
                length = int(self.headers['Content-Length'])
                body = self.rfile.read(length)
                received['length'] = length
                received['content'] = body
                received['token'] = self.headers['Authorization']
                received['content_type'] = self.headers['Content-Type']
                self.send_response(204)
                self.end_headers()

        server = ThreadingHTTPServer(('127.0.0.1', 0), UploadHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        path = self.directory / 'model.glb'
        model = bytes(range(256)) * 2048
        path.write_bytes(model)
        url = f'http://127.0.0.1:{server.server_port}/models/{UID}'
        token = 'Token synthetic'
        fields = {'name': 'A test model', 'isPublished': False}
        result = transfers.send_model(path, path.name, 'PUT', url, {'Authorization': token}, fields, UID,
                                      threading.Event())
        self.assertEqual(result, UID)
        self.assertEqual(received['token'], token)
        self.assertEqual(received['length'], len(received['content']))
        header = f"Content-Type: {received['content_type']}\r\n\r\n".encode()
        message = BytesParser(policy=policy.default).parsebytes(header + received['content'])
        title, published, attachment = list(message.iter_parts())
        self.assertEqual(title.get_payload(decode=True), b'A test model')
        self.assertEqual(published.get_payload(decode=True), b'False')
        self.assertEqual(attachment.get_param('name', header='content-disposition'), 'modelFile')
        self.assertEqual(attachment.get_filename(), 'model.glb')
        self.assertEqual(sha256(attachment.get_payload(decode=True)).digest(), sha256(model).digest())

        cancelled = threading.Event()
        with path.open('rb') as file:
            parts = iter(transfers.MultipartUpload(file, path, path.name, fields, cancelled))
            next(parts)
            self.assertEqual(len(next(parts)), transfers.CHUNK_SIZE)
            self.assertEqual(file.tell(), transfers.CHUNK_SIZE)
            cancelled.set()
            with self.assertRaises(transfers.TransferCancelled):
                next(parts)

    def test_size_check_and_limit_skip_upload_and_remove_temporary_files(self):
        for send, limit in ((None, None), (self.fail, 2)):
            with self.subTest(limit=limit):
                directory = Path(tempfile.mkdtemp(dir=self.directory))
                path = directory / 'fixture.glb'
                result = {'size': 4, 'filepath': str(path), 'filename': path.name}
                script = (
                    f"from pathlib import Path\nPath({str(path)!r}).write_bytes(b'test')\n"
                    f"Path({str(directory / 'export-sketchfab.json')!r}).write_text({json.dumps(result)!r})\n"
                )
                job = transfers.UploadJob(directory, [sys.executable, '-c', script], limit, send)
                job.start()
                job.join(5)
                self.assertFalse(job.is_alive())
                self.assertIsNone(job.error)
                self.assertEqual(job.prepared['size'], 4)
                self.assertFalse(directory.exists())

    def test_cancel_stops_background_preparation_and_removes_temporary_files(self):
        import time

        started = self.directory / 'started'
        script = f"from pathlib import Path; import time; Path({str(started)!r}).touch(); time.sleep(60)"
        job = transfers.UploadJob(self.directory, [sys.executable, '-c', script], None, self.fail)
        job.start()
        self.addCleanup(job.cancelled.set)
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(started.exists())
        job.cancelled.set()
        job.join(5)
        self.assertFalse(job.is_alive())
        self.assertIsNone(job.prepared)
        self.assertIsNone(job.error)
        self.assertFalse(self.directory.exists())
