"""NHL Team Shots Analyzer 0.5 — static site and NHL API proxy."""
import json, os, re
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
ROOT=Path(__file__).resolve().parent
ALLOWED=re.compile(r'^/(?:club-schedule-season/[A-Z]{3}/\d{8}|gamecenter/\d+/boxscore)$')
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT),**kw)
 def do_GET(self):
  if not self.path.startswith('/api/'): return super().do_GET()
  path=self.path[4:].split('?',1)[0]
  if not ALLOWED.fullmatch(path): return self.respond(400,{'error':'Nepovolená cesta API'})
  try:
   req=Request('https://api-web.nhle.com/v1'+path,headers={'User-Agent':'NHLShotsAnalyzer/0.5','Accept':'application/json'})
   with urlopen(req,timeout=18) as r: payload=r.read(3_000_000)
   self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','public, max-age=180');self.end_headers();self.wfile.write(payload)
  except HTTPError as e: self.respond(502,{'error':f'NHL API HTTP {e.code}'})
  except (URLError,TimeoutError): self.respond(502,{'error':'NHL API není dostupné'})
 def respond(self,code,obj):
  b=json.dumps(obj,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
if __name__=='__main__': ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8000'))),Handler).serve_forever()
