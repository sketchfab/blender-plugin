from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit

MODEL_UID = 'fdecba9876543210fedcba9876543210'
ORG_UID = '0123456789abcdef0123456789abcdef'
PROJECT_UID = 'fedcba9876543210fedcba9876543210'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        mode = url.path.strip('/').split('/')[0]
        if mode == 'thumbnail':
            self.send_response(200 if 'Authorization' not in self.headers else 400)
            self.end_headers()
            self.wfile.write((Path(__file__).resolve().parents[1] / 'placeholder.png').read_bytes())
            return
        if mode == 'search':
            host = f'http://127.0.0.1:{self.server.server_port}'
            query = parse_qs(url.query)
            second_page = query.get('page') == ['2']
            allowed = (self.headers.get('Authorization') == 'Token synthetic-test-token'
                       and (second_page or (query.get('q') == ['mesh & rig'] and query.get('restricted') == ['1'])))
            self.send_response(200 if allowed else 400)
            self.end_headers()
            model = {'uid': MODEL_UID, 'name': 'Fixture', 'vertexCount': 3, 'faceCount': 1,
                     'user': {'displayName': 'Author'}, 'archives': {'gltf': {}},
                     'thumbnails': {'images': [{'url': host + '/thumbnail', 'height': 96}]}}
            page = {'results': [model]}
            if second_page:
                page['previous'] = 'https://unrelated.example/search'
            else:
                page['next'] = '?page=2'
            self.wfile.write(json.dumps(page).encode())
            return
        if mode == 'slow':
            time.sleep(2)
        if mode == 'redirect':
            self.send_response(302)
            self.send_header('Location', '/unexpected-redirect-target')
            self.end_headers()
            return
        if mode == 'unexpected-redirect-target':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"username":"redirected","displayName":"Redirected","account":"basic"}')
            return
        is_orgs = self.path.endswith('/orgs')
        status = 401 if mode == 'rejected' else 503 if mode == 'unavailable' or (mode == 'org-failure' and is_orgs) else 200
        self.send_response(status)
        self.end_headers()
        if mode == 'bad-data':
            payload = {'username': [], 'displayName': 'Probe', 'account': 'basic'}
        elif mode == 'large':
            payload = {'padding': 'x' * (1024 * 1024 + 1)}
        elif url.path == f'/models/{MODEL_UID}':
            payload = {'license': {'fullName': 'Fixture license'}, 'animationCount': 1}
        elif url.path == f'/fixture-teams/{ORG_UID}/projects':
            payload = {'results': [{'uid': PROJECT_UID, 'name': 'Test project'}]}
        elif is_orgs:
            org = {'uid': ORG_UID, 'displayName': 'Test organization', 'username': 'test-org'}
            payload = {'results': [org] if mode == 'organizations' else []}
        else:
            payload = {'username': 'probe', 'displayName': 'Probe', 'account': 'basic'}
            if mode == 'restricted':
                payload['allowsRestricted'] = True
            elif mode == 'invalid-preference':
                payload['allowsRestricted'] = 'true'
        try:
            self.wfile.write(json.dumps(payload).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', 0), Handler)
    print(server.server_port, flush=True)
    server.serve_forever()
