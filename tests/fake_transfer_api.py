from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email import policy
from email.parser import BytesParser
from io import BytesIO
import json
from pathlib import Path
import sys
import time
from zipfile import ZipFile


WORK = Path(sys.argv[1])
UID = '0123456789abcdef0123456789abcdef'
CHUNK_SIZE = 128 * 1024


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, body):
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()

    def do_GET(self):
        try:
            if self.path == f'/models/{UID}/download' and self.headers.get('Authorization') == 'Token synthetic':
                (WORK / 'personal-download').touch()
                base = f'http://127.0.0.1:{self.server.server_port}'
                body = json.dumps({'gltf': {'url': base + '/archive'}}).encode()
            elif self.path == f'/models/{UID}':
                body = b'{"name":"Fixture"}'
            elif self.path == '/archive' and 'Authorization' not in self.headers:
                body = (WORK / 'fixture.zip').read_bytes()
            else:
                self.respond(404, b'')
                return
            self.respond(200, body)
            pause = WORK / 'pause'
            if self.path == '/archive' and pause.is_file():
                case = pause.read_text()
                self.wfile.write(body[:CHUNK_SIZE])
                (WORK / f'{case}-started').touch()
                deadline = time.monotonic() + 6
                while not (WORK / f'{case}-resume').exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                body = body[CHUNK_SIZE:]
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        uploaded = self.rfile.read(int(self.headers['Content-Length']))
        header = f'Content-Type: {self.headers["Content-Type"]}\r\n\r\n'.encode('ascii')
        message = BytesParser(policy=policy.default).parsebytes(header + uploaded)
        model = next(part for part in message.iter_parts() if part.get_param('name', header='content-disposition') == 'modelFile')
        data = model.get_payload(decode=True)
        with ZipFile(BytesIO(data)) as archive:
            source, = archive.infolist()
        summary = {'path': self.path, 'filename': model.get_filename(), 'size': len(data),
                   'source_name': source.filename, 'source_size': source.file_size,
                   'authenticated': self.headers.get('Authorization') == 'Token synthetic'}
        (WORK / 'upload.json').write_text(json.dumps(summary))
        body = json.dumps({'uid': UID}).encode()
        self.respond(201, body)
        self.wfile.write(body)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
print(server.server_port, flush=True)
server.serve_forever()
