"""NHL Team Shots Analyzer: static web app and restricted NHL API proxy."""
import json
import os
import re
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ALLOWED = re.compile(r'^/(?:club-schedule-season/[A-Z]{3}/\d{8}|gamecenter/\d+/boxscore)$')

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if not self.path.startswith('/api/'):
            return super().do_GET()
        path = self.path[4:].split('?', 1)[0]
        if not ALLOWED.fullmatch(path):
            return self.respond(400, {'error': 'Nepovolená cesta API'})
        try:
            req = Request('https://api-web.nhle.com/v1' + path, headers={'User-Agent':'NHLShotsAnalyzer/0.2', 'Accept':'application/json'})
            with urlopen(req, timeout=18) as res:
                payload = res.read(3_000_000)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'public, max-age=180')
            self.end_headers()
            self.wfile.write(payload)
        except HTTPError as exc:
            self.respond(502, {'error': f'NHL API odpovědělo HTTP {exc.code}'})
        except (URLError, TimeoutError) as exc:
            self.respond(502, {'error': 'NHL API není dostupné'})

    def respond(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    print(f'Web běží na http://localhost:{port}')
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
