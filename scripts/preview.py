"""Local-only same-origin frontend/API development preview."""
import os
import sys
from http.server import SimpleHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pathlib import Path
os.chdir(str(Path(__file__).resolve().parents[1]/'web/creator'))
class Handler(SimpleHTTPRequestHandler):
    def api(self):
        body=self.rfile.read(int(self.headers.get('Content-Length',0))) if self.command=='POST' else None
        request=Request('http://127.0.0.1:'+os.environ.get('CLUB_PORT','5381')+self.path,data=body,method=self.command,headers={k:v for k,v in self.headers.items() if k.lower() in ('content-type','origin')})
        try:response=urlopen(request,timeout=20)
        except HTTPError as error:response=error
        data=response.read();self.send_response(response.code)
        self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path.startswith('/api/'):return self.api()
        path, sep, query = self.path.partition('?')
        routes = {'/': '/index.html', '/schools': '/lean-school-ranking.html', '/join': '/school-submit.html'}
        self.path = routes.get(path, path) + (sep + query if sep else '')
        return super().do_GET()
    def do_POST(self):return self.api()
class Server(ThreadingMixIn,HTTPServer):daemon_threads=True
Server(('127.0.0.1',int(os.environ.get('PREVIEW_PORT','4180'))),Handler).serve_forever()
