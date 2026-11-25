from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
from io import BytesIO
import json
from pathlib import Path
import sys
import time
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import patch
from zipfile import ZipFile


package = ModuleType('sketchfab_test')
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
transfers = importlib.import_module('sketchfab_test.transfers')
workers = importlib.import_module('sketchfab_test.worker_process')
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
        job = workers.WorkerJob(self.cache.name)
        self.addCleanup(job.stop)
        job.start_network(sys.executable, None, 'download', {
            'models_url': base + '/models', 'orgs_url': base + '/orgs',
            'memberships_url': base + '/me/orgs', 'uid': UID,
            'organization': None, 'organizations': [],
        }, {'Authorization': 'Token synthetic'})
        return job

    def test_download_uses_owned_directory_and_sends_no_token_to_archive(self):
        existing = Path(self.cache.name) / 'existing.zip'
        existing.write_bytes(b'unmanaged')
        job = self.start_job()
        job.process.wait(timeout=5)
        result = job.read_result()['data']
        self.assertTrue(Path(result['path']).is_file())
        self.assertEqual(result['metadata']['name'], 'Fixture')
        self.assertIn(('/archive', None), self.server.requests)
        job.stop()
        self.assertEqual(list(Path(self.cache.name).iterdir()), [existing])

    def test_interrupted_download_is_discarded_and_can_be_retried(self):
        self.server.mode = 'interrupt'
        job = self.start_job()
        job.process.wait(timeout=5)
        self.assertIn('error', job.read_result())
        job.stop()
        self.assertEqual(list(Path(self.cache.name).iterdir()), [])

        self.server.mode = 'normal'
        retry = self.start_job()
        retry.process.wait(timeout=5)
        self.assertTrue(Path(retry.read_result()['data']['path']).is_file())

    def test_cancelling_a_download_kills_process_and_discards_partial_output(self):
        self.server.mode = 'pause'
        self.server.archive = make_archive([('texture.png', b'x' * (2 * transfers.CHUNK_SIZE))])
        job = self.start_job()
        self.assertTrue(self.server.started.wait(5))
        job.stop()
        job.process.wait(timeout=2)
        workers.reap_workers()
        self.server.resume.set()
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
            job.process.wait(timeout=5)
            self.assertIn('error', job.read_result())
            job.stop()
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
            try:
                self.wfile.write(body[transfers.CHUNK_SIZE:])
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.wfile.write(body)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.directory = Path(self.workspace.name)
        self.addCleanup(self.workspace.cleanup)

    def test_worker_keeps_credentials_out_of_arguments_and_request_file(self):
        job = workers.WorkerJob(self.directory)
        self.addCleanup(job.stop)
        with patch.object(job, 'start') as start:
            job.start_network(sys.executable, None, 'upload', {'uid': UID},
                              {'Authorization': 'Token synthetic-auth'}, password='synthetic-password')
        command, env = start.call_args.args
        request = (job.directory / 'request.json').read_text()
        for secret in ('synthetic-auth', 'synthetic-password'):
            self.assertNotIn(secret, request)
            self.assertNotIn(secret, ' '.join(command))
            self.assertIn(secret, env['SKETCHFAB_WORKER_SECRETS'])

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
        job = workers.WorkerJob(self.directory)
        self.addCleanup(job.stop)
        job.start_network(sys.executable, None, 'upload', {
            'filepath': str(path), 'filename': path.name, 'method': 'PUT', 'url': url,
            'models_url': f'http://127.0.0.1:{server.server_port}/models',
            'orgs_url': '', 'uid': UID, 'organization': None, 'organizations': [], 'fields': fields,
        }, {'Authorization': token}, password='synthetic-password')
        job.process.wait(timeout=5)
        self.assertEqual(job.read_result()['data'], UID)
        self.assertEqual(received['token'], token)
        self.assertEqual(received['length'], len(received['content']))
        header = f"Content-Type: {received['content_type']}\r\n\r\n".encode()
        message = BytesParser(policy=policy.default).parsebytes(header + received['content'])
        title, published, password, attachment = list(message.iter_parts())
        self.assertEqual(title.get_payload(decode=True), b'A test model')
        self.assertEqual(published.get_payload(decode=True), b'False')
        self.assertEqual(password.get_payload(decode=True), b'synthetic-password')
        self.assertEqual(attachment.get_param('name', header='content-disposition'), 'modelFile')
        self.assertEqual(attachment.get_filename(), 'model.glb')
        self.assertEqual(sha256(attachment.get_payload(decode=True)).digest(), sha256(model).digest())

        with path.open('rb') as file:
            parts = iter(transfers.MultipartUpload(file, path, path.name, fields))
            next(parts)
            self.assertEqual(len(next(parts)), transfers.CHUNK_SIZE)
            self.assertEqual(file.tell(), transfers.CHUNK_SIZE)

    def test_cancel_stops_background_preparation_and_removes_temporary_files(self):
        job = workers.WorkerJob(self.directory)
        self.addCleanup(job.stop)
        started = job.directory / 'started'
        script = f"from pathlib import Path; import time; Path({str(started)!r}).touch(); time.sleep(60)"
        job.start([sys.executable, '-c', script])
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(started.exists())
        job.stop()
        job.process.wait(timeout=2)
        workers.reap_workers()
        self.assertEqual(list(self.directory.iterdir()), [])
