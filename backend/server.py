"""Loopback-only HTTP API behind the site's Nginx HTTPS virtual host."""
import base64
import binascii
import json
import os
import re
import struct
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit, unquote
from store import Store, Problem
from metadata import enrich_schools

MAX_BODY = 3 * 1024 * 1024
MAX_IMAGE = 2 * 1024 * 1024
ROOT = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(ROOT, 'schools.json'), encoding='utf-8') as catalog_file:
    CATALOG = json.load(catalog_file)
STORE = Store(os.environ.get('CLUB_DB', '/var/lib/leanmuscleclub/clubs.sqlite3'), CATALOG)
ORIGIN = os.environ.get('CLUB_ORIGIN', 'https://leanmuscleclub.com')


def validate_qr(value):
    if value is None or value == '':
        return value
    if not isinstance(value, str):
        raise Problem(400, '请上传PNG或JPG图片。')
    match = re.fullmatch(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)', value)
    if not match:
        raise Problem(400, '请上传PNG或JPG图片。')
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error):
        raise Problem(400, '图片内容无效，请重新上传。')
    if len(raw) > MAX_IMAGE:
        raise Problem(413, '图片请控制在2MB以内。')
    # Recheck dimensions/signatures server-side; never serve active SVG/HTML uploads.
    width = height = 0
    if match.group(1) == 'png' and raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) >= 24 and raw[12:16] == b'IHDR':
        width, height = struct.unpack('>II', raw[16:24])
    elif match.group(1) == 'jpeg' and raw.startswith(b'\xff\xd8'):
        pos = 2
        while pos + 4 < len(raw):
            if raw[pos] != 255:
                break
            marker = raw[pos + 1]
            pos += 2
            if marker == 255:
                pos -= 1
                continue
            if marker in (216, 217):
                continue
            length = int.from_bytes(raw[pos:pos + 2], 'big')
            if marker in (192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207) and pos + 7 <= len(raw):
                height, width = struct.unpack('>HH', raw[pos + 3:pos + 7])
                break
            if length < 2:
                break
            pos += length
    if not 32 <= width <= 6000 or not 32 <= height <= 6000 or width * height > 24000000:
        raise Problem(400, '图片无效或尺寸过大，请重新选择二维码图片。')
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = 'LeanClub'
    def log_message(self, fmt, *args):
        # Never log bodies, contacts or edit keys.
        sys.stderr.write('%s %s\n' % (self.command, self.path.split('?')[0]))

    def send_json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(raw)

    def ip(self):
        return self.headers.get('X-Real-IP', self.client_address[0])[:80]

    def do_GET(self):
        try:
            path = urlsplit(self.path).path
            if path == '/api/club/health':
                with STORE.connect() as db:
                    db.execute('SELECT 1').fetchone()
                return self.send_json(200, {'status': 'ok'})
            if path == '/api/club/schools':
                return self.send_json(200, {'schools': CATALOG, 'published': enrich_schools(STORE.summary())})
            if path.startswith('/api/club/profile/'):
                school_id = unquote(path[len('/api/club/profile/'):])
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', school_id):
                    raise Problem(400, '学校信息无效。')
                return self.send_json(200, {'club': STORE.profile(school_id, self.ip())})
            raise Problem(404, '页面不存在。')
        except Problem as exc:
            self.send_json(exc.status, {'error': exc.message})
        except Exception:
            self.send_json(500, {'error': '暂时无法读取，请稍后再试。'})

    def do_POST(self):
        try:
            if self.headers.get('Origin') != ORIGIN:
                raise Problem(403, '请从官网填写页面提交。')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise Problem(415, '请求格式不正确。')
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise Problem(400, '请求格式不正确。')
            if not 0 < length <= MAX_BODY:
                raise Problem(413, '提交内容过大，请缩小二维码图片。')
            self.connection.settimeout(15)
            try:
                data = json.loads(self.rfile.read(length).decode('utf-8'))
            except (ValueError, UnicodeError):
                raise Problem(400, '请求内容无效。')
            if not isinstance(data, dict):
                raise Problem(400, '请求内容无效。')
            path = urlsplit(self.path).path
            if path == '/api/club/edit':
                return self.send_json(200, {'club': STORE.edit_profile(data.get('editKey'))})
            if path == '/api/club/submit':
                data['qr'] = validate_qr(data.get('qr'))
                return self.send_json(200, STORE.save(data, self.ip()))
            raise Problem(404, '页面不存在。')
        except Problem as exc:
            self.send_json(exc.status, {'error': exc.message})
        except Exception:
            self.send_json(500, {'error': '提交未完成，请稍后重试，资料不会被清空。'})


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address


if __name__ == '__main__':
    Server(('127.0.0.1', int(os.environ.get('CLUB_PORT', '5381'))), Handler).serve_forever()
