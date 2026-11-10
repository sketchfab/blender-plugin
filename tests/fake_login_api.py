from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import time


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        mode = self.path.strip('/').split('/')[0]
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
        elif is_orgs:
            payload = {'results': [{'uid': 'synthetic-org'}] if mode == 'organizations' else []}
        else:
            payload = {'username': 'probe', 'displayName': 'Probe', 'account': 'basic'}
        try:
            self.wfile.write(json.dumps(payload).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', 0), Handler)
    print(server.server_port, flush=True)
    server.serve_forever()
