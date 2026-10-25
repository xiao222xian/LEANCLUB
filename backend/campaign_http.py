"""HTTP boundary for the standalone campaign module; no frontend file access."""
import hmac
import hashlib
from http.cookies import SimpleCookie, CookieError
import json
import re
import socket
from urllib.parse import parse_qs, urlsplit

from campaign import IPVoteConflict, MAX_POSTER_BYTES, SESSION_AGE, VISITOR_AGE
from campaign_feedback import validate as validate_feedback
from campaign_comments import validate as validate_comment
from events_http import EventsAPI
from store import Problem
from club_management import ClubManagement
from directory import Directory

VISITOR_COOKIE = 'lean_visitor'
SESSION_COOKIE = 'lean_admin'
JSON_LIMIT = 32 * 1024


class CampaignAPI:
    def __init__(self, store, origin, allow_local_setup=False):
        self.store, self.origin = store, origin
        parsed = urlsplit(origin)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('CLUB_ORIGIN must be one HTTP(S) origin, without a path or credentials.')
        configured = self.origin_key(origin)
        if configured is None:
            raise ValueError('CLUB_ORIGIN must be one valid HTTP(S) origin.')
        self.secure = parsed.scheme == 'https'
        self.local_setup = allow_local_setup and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
        self.allowed_origins = {configured}
        self.events = EventsAPI(self)
        self.clubs = ClubManagement(store) if store is not None else None
        self.directory = Directory(store) if store is not None else None
        if self.local_setup:
            # The explicit loopback launcher serves these names on the same port.
            # Production keeps the single configured origin.
            self.allowed_origins.update((configured[0], host, configured[2])
                                        for host in ('127.0.0.1', 'localhost', '::1'))

    @staticmethod
    def origin_key(value, referer=False):
        if not isinstance(value, str) or not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
            return None
        try:
            parsed = urlsplit(value)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.fragment or (not referer and (parsed.path or parsed.query))):
                return None
            port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
            if not 1 <= port <= 65535:
                return None
            return parsed.scheme, parsed.hostname.lower(), port
        except ValueError:
            return None

    @staticmethod
    def matches(path):
        return any(path == prefix or path.startswith(prefix + '/') for prefix in ('/api/campaign', '/api/analytics', '/api/admin', '/api/media', '/api/directory'))

    @staticmethod
    def cookie(handler, key):
        cookie = SimpleCookie()
        try:
            cookie.load(handler.headers.get('Cookie', ''))
            return cookie[key].value if key in cookie else None
        except CookieError:
            return None

    def set_cookie(self, handler, key, value, age):
        cookie = SimpleCookie()
        cookie[key] = value
        cookie[key]['path'] = '/'
        cookie[key]['max-age'] = str(age)
        cookie[key]['httponly'] = True
        cookie[key]['samesite'] = 'Strict'
        if self.secure:
            cookie[key]['secure'] = True
        handler.response_cookies.append(cookie[key].OutputString())

    def visitor(self, handler):
        visitor, fresh = self.store.visitor(self.cookie(handler, VISITOR_COOKIE))
        if fresh:
            self.set_cookie(handler, VISITOR_COOKIE, fresh, VISITOR_AGE)
        return visitor

    def require_origin(self, handler):
        origins = handler.headers.get_all('Origin', [])
        fetch_site = handler.headers.get('Sec-Fetch-Site')
        if fetch_site is not None and fetch_site != 'same-origin':
            raise Problem(403, '请从本站页面提交。')
        if origins:
            # An explicit foreign or opaque Origin must never fall back to Referer.
            valid = len(origins) == 1 and self.origin_key(origins[0]) in self.allowed_origins
        else:
            referers = handler.headers.get_all('Referer', [])
            valid = len(referers) == 1 and self.origin_key(referers[0], referer=True) in self.allowed_origins
        if not valid:
            raise Problem(403, '请从本站页面提交。')

    def admin(self, handler, writing=False):
        session = self.store.session(self.cookie(handler, SESSION_COOKIE))
        if session is None:
            raise Problem(401, '请先登录管理后台。')
        if writing:
            self.require_origin(handler)
            supplied = handler.headers.get('X-CSRF-Token', '')
            if not hmac.compare_digest(supplied.encode('utf-8'), session['csrfToken'].encode('utf-8')):
                raise Problem(403, '登录验证已失效，请刷新页面后重试。')
        return session

    @staticmethod
    def read_body(handler, maximum):
        if handler.headers.get('Transfer-Encoding') or len(handler.headers.get_all('Content-Length', [])) != 1:
            raise Problem(400, '请求长度无效。')
        try:
            length = int(handler.headers['Content-Length'])
        except (ValueError, TypeError):
            raise Problem(400, '请求长度无效。')
        if length <= 0:
            raise Problem(400, '请求内容为空。')
        if length > maximum:
            raise Problem(413, '提交内容过大。')
        handler.connection.settimeout(15)
        try:
            raw = handler.rfile.read(length)
        except (TimeoutError, socket.timeout):
            raise Problem(408, '上传超时，请稍后重试。')
        if len(raw) != length:
            raise Problem(400, '请求内容不完整。')
        return raw

    def json_body(self, handler, maximum=JSON_LIMIT):
        if handler.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            raise Problem(415, '请求须为 JSON 格式。')
        def reject_constant(_):
            raise ValueError()
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        try:
            data = json.loads(self.read_body(handler, maximum).decode('utf-8'),
                              parse_constant=reject_constant, object_pairs_hook=unique_object)
        except (UnicodeError, ValueError, RecursionError):
            raise Problem(400, '请求内容无效。')
        if not isinstance(data, dict):
            raise Problem(400, '请求须为 JSON 对象。')
        return data

    def get(self, handler, path):
        if path == '/api/campaign/presentation':
            # Public visual metadata only: never create or read a visitor/session.
            group = self.store.groups.active()
            ttl = 15
            if group and group.get('expiresAt'):
                ttl = max(0, min(ttl, int(group['expiresAt'] - self.store.clock())))
            raw = json.dumps({'cities': self.store.cities(), 'group': group}, ensure_ascii=False).encode('utf-8')
            handler.send_response(200)
            handler.send_header('Content-Type', 'application/json; charset=utf-8')
            handler.send_header('Content-Length', str(len(raw)))
            handler.send_header('Cache-Control', f'public, max-age={ttl}, must-revalidate')
            handler.send_header('X-Content-Type-Options', 'nosniff')
            handler.end_headers()
            handler.wfile.write(raw)
            return
        if path == '/api/campaign/group-qr':
            self.store.limit(handler.ip(), 'group-qr-read', 120, 60)
            return handler.send_json(200, {'group': self.store.groups.active()})
        if path == '/api/admin/group-qrs':
            self.admin(handler)
            return handler.send_json(200, self.store.groups.listing())
        public_qr = re.fullmatch(r'/api/campaign/group-qr/([a-f0-9]{32})/([0-9]+)', path)
        private_qr = re.fullmatch(r'/api/admin/group-qrs/([a-f0-9]{32})/image', path)
        if public_qr or private_qr:
            if private_qr:
                self.admin(handler)
            else:
                self.store.limit(handler.ip(), 'group-qr-image', 120, 60)
            raw, ttl = self.store.groups.media((private_qr or public_qr)[1], public_qr[2] if public_qr else None, private=bool(private_qr), with_cache=True)
            etag = '"' + hashlib.sha256(raw).hexdigest() + '"'
            unchanged = bool(public_qr) and handler.headers.get('If-None-Match') == etag
            handler.send_response(304 if unchanged else 200)
            handler.send_header('Content-Type', 'image/png')
            if not unchanged:
                handler.send_header('Content-Length', str(len(raw)))
            handler.send_header('Cache-Control', 'no-store' if private_qr else f'public, max-age={ttl}, must-revalidate')
            if public_qr:
                handler.send_header('ETag', etag)
            handler.send_header('Content-Disposition', 'inline; filename="lean-school-group.png"')
            handler.send_header('X-Content-Type-Options', 'nosniff')
            handler.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            handler.end_headers()
            if not unchanged:
                handler.wfile.write(raw)
            return
        if path == '/api/directory':
            self.store.limit(handler.ip(), 'directory-read', 180, 60)
            return handler.send_json(200, {'items': self.directory.listing()})
        match = re.fullmatch(r'/api/directory/([A-Za-z0-9_-]{1,80})', path)
        if match:
            self.store.limit(handler.ip(), 'directory-detail', 60, 60)
            return handler.send_json(200, self.directory.detail(match[1]))
        if path == '/api/admin/directory':
            self.admin(handler)
            return handler.send_json(200, {'items': self.directory.listing(private=True)})
        match = re.fullmatch(r'/api/admin/directory/([A-Za-z0-9_-]{1,80})', path)
        if match:
            self.admin(handler)
            return handler.send_json(200, self.directory.detail(match[1], private=True))
        if path == '/api/admin/schools' or path.startswith('/api/admin/schools/'):
            self.admin(handler)
            if path == '/api/admin/schools':
                return handler.send_json(200, self.clubs.listing())
            if path == '/api/admin/schools/history':
                query = parse_qs(urlsplit(handler.path).query)
                page = query.get('page', ['1'])[0]
                if not re.fullmatch(r'[1-9][0-9]{0,5}', page):
                    raise Problem(400, '页码无效。')
                return handler.send_json(200, self.clubs.history(int(page)))
            match = re.fullmatch(r'/api/admin/schools/([A-Za-z0-9_-]{1,80})', path)
            if match:
                return handler.send_json(200, self.clubs.detail(match[1]))
            raise Problem(404, '学校接口不存在。')
        if self.events.matches(path):
            return self.events.get(handler, path)
        replies_match = re.fullmatch(r'/api/campaign/comments/([^/]+)/replies', path)
        if path == '/api/campaign/comments' or replies_match:
            self.store.limit(handler.ip(), 'comments-read', 180, 60)
            try:
                query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True,
                                 strict_parsing=True, max_num_fields=2)
                if set(query) - {'limit', 'cursor'} or any(len(values) != 1 for values in query.values()):
                    raise ValueError()
                limit = query.get('limit', ['20'])[0]
                if not re.fullmatch(r'[1-9][0-9]?', limit):
                    raise ValueError()
            except ValueError:
                raise Problem(400, '评论分页参数无效。')
            cursor = query.get('cursor', [None])[0]
            result = (self.store.comments.public_replies(replies_match[1], int(limit), cursor) if replies_match else
                      self.store.comments.public_list(int(limit), cursor))
            return handler.send_json(200, result)
        if path == '/api/campaign':
            self.store.limit(handler.ip(), 'campaign-read', 120, 60)
            visitor = self.visitor(handler)
            return handler.send_json(200, dict(self.store.vote_state(visitor, handler.ip()), cities=self.store.cities(), group=self.store.groups.active()))
        if path == '/api/admin/session':
            self.store.limit(handler.ip(), 'session-read', 120, 60)
            session = self.store.session(self.cookie(handler, SESSION_COOKIE)) or {'authenticated': False}
            required = not self.store.has_admin()
            return handler.send_json(200, dict(session, setupRequired=required, setupAvailable=required and self.local_setup))
        if path == '/api/admin/stats':
            self.admin(handler)
            return handler.send_json(200, self.store.stats())
        if path in ('/api/admin/feedback', '/api/admin/comments'):
            self.admin(handler)
            comments = path == '/api/admin/comments'
            try:
                query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True,
                                 strict_parsing=True, max_num_fields=3 if comments else 2)
                allowed = {'status', 'page', 'reply'} if comments else {'status', 'page'}
                if set(query) - allowed or any(len(values) != 1 for values in query.values()):
                    raise ValueError()
                page = query.get('page', ['1'])[0]
                if not re.fullmatch(r'[1-9][0-9]{0,6}', page):
                    raise ValueError()
            except ValueError:
                raise Problem(400, '筛选或页码参数无效。')
            if comments:
                return handler.send_json(200, self.store.comments.list(
                    query.get('status', ['all'])[0], int(page), query.get('reply', ['all'])[0]))
            return handler.send_json(200, self.store.feedback.list(query.get('status', ['all'])[0], int(page)))
        if path == '/api/admin/feishu':
            self.admin(handler)
            return handler.send_json(200, self.store.notifications.settings())
        if path == '/api/admin/posters':
            self.admin(handler)
            return handler.send_json(200, {'cities': self.store.cities()})
        if path.startswith('/api/media/'):
            raw = self.store.media(path[len('/api/media/'):])
            handler.send_response(200)
            handler.send_header('Content-Type', 'image/webp')
            handler.send_header('Content-Length', str(len(raw)))
            handler.send_header('Cache-Control', 'public, max-age=31536000, immutable')
            handler.send_header('X-Content-Type-Options', 'nosniff')
            handler.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            handler.end_headers()
            handler.wfile.write(raw)
            return
        raise Problem(404, '接口不存在。')

    def post(self, handler, path):
        self.require_origin(handler)
        if path == '/api/admin/group-qrs':
            session = self.admin(handler, writing=True)
            return handler.send_json(200, self.store.groups.save(self.json_body(handler, 3 * 1024 * 1024), session['username']))
        if path == '/api/campaign/group-qr/events':
            self.store.limit(handler.ip(), 'group-qr-events', 60, 60)
            return handler.send_json(200, self.store.groups.track(self.json_body(handler)))
        if path == '/api/admin/directory':
            session = self.admin(handler, writing=True)
            return handler.send_json(201, self.directory.create(self.json_body(handler, 3 * 1024 * 1024), session['username']))
        if path in ('/api/directory/submit', '/api/directory/manage'):
            self.store.limit(handler.ip(), 'directory-manage', 120, 60)
            data = self.json_body(handler, 3 * 1024 * 1024)
            if path.endswith('/manage'):
                return handler.send_json(200, self.directory.manage(data.get('editKey')))
            self.store.limit(handler.ip(), 'directory-submit', 60, 3600)
            self.store.limit(self.directory.key(data.get('editKey')), 'directory-owner-submit', 10, 3600)
            return handler.send_json(200, self.directory.submit(data))
        match = re.fullmatch(r'/api/admin/directory/([A-Za-z0-9_-]{1,80})', path)
        if match:
            session = self.admin(handler, writing=True)
            return handler.send_json(200, self.directory.review(match[1], self.json_body(handler, 3 * 1024 * 1024), session['username']))
        match = re.fullmatch(r'/api/admin/schools/([A-Za-z0-9_-]{1,80})(/qr)?', path)
        if match:
            session = self.admin(handler, writing=True)
            if match[2]:
                revision = handler.headers.get('X-Club-Revision', '')
                if not re.fullmatch(r'[1-9][0-9]{0,9}', revision):
                    raise Problem(400, '资料版本无效。')
                raw = self.read_body(handler, 2 * 1024**2)
                qr = self.clubs.image(raw, handler.headers.get('Content-Type', '').split(';')[0])
                result = self.clubs.save(match[1], {'revision': int(revision)}, session['username'], qr=qr)
            else:
                result = self.clubs.save(match[1], self.json_body(handler), session['username'])
            return handler.send_json(200, result)
        if self.events.matches(path):
            return self.events.post(handler, path)
        if path == '/api/campaign/comments':
            data = self.json_body(handler)
            self.store.limit(handler.ip(), 'comments-request', 60, 60)
            validate_comment(data)
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                visitor, fresh = self.store.visitor(self.cookie(handler, VISITOR_COOKIE), db=db)
                result = self.store.comments.submit(visitor, handler.ip(), data, db=db)
            if fresh:
                self.set_cookie(handler, VISITOR_COOKIE, fresh, VISITOR_AGE)
            return handler.send_json(200 if result['duplicate'] else 201, result)
        if path == '/api/campaign/feedback':
            data = self.json_body(handler)
            self.store.limit(handler.ip(), 'feedback-request', 60, 60)
            validate_feedback(data)
            # Roll back newly issued anonymous identities with quota/transaction
            # failures, and do not set a cookie until the submission commits.
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                visitor, fresh = self.store.visitor(self.cookie(handler, VISITOR_COOKIE), db=db)
                result = self.store.feedback.submit(visitor, handler.ip(), data, db=db)
            if fresh:
                self.set_cookie(handler, VISITOR_COOKIE, fresh, VISITOR_AGE)
            return handler.send_json(200 if result['duplicate'] else 201, result)
        comment_match = re.fullmatch(r'/api/admin/comments/([^/]+)/(reply|status)', path)
        if comment_match:
            self.admin(handler, writing=True)
            data = self.json_body(handler, maximum=8192)
            if comment_match[2] == 'reply':
                return handler.send_json(200, self.store.comments.set_reply(comment_match[1], data))
            if set(data) != {'status'}:
                raise Problem(400, '请指定留言状态。')
            return handler.send_json(200, self.store.comments.set_status(comment_match[1], data['status']))
        feedback_match = re.fullmatch(r'/api/admin/feedback/([^/]+)/status', path)
        if feedback_match:
            self.admin(handler, writing=True)
            data = self.json_body(handler)
            if set(data) != {'status'}:
                raise Problem(400, '请指定意见状态。')
            return handler.send_json(200, self.store.feedback.set_status(feedback_match[1], data['status']))
        if path == '/api/campaign/vote':
            data = self.json_body(handler)
            self.store.limit(handler.ip(), 'vote', 60, 60)
            visitor = self.visitor(handler)
            try:
                self.store.cast_vote(visitor, data.get('city'), handler.ip())
            except IPVoteConflict as exc:
                return handler.send_json(409, {'error': exc.message, 'code': 'IP_ALREADY_VOTED',
                                               'vote': exc.vote, 'canChange': False})
            return handler.send_json(200, dict(self.store.vote_state(visitor, handler.ip()), group=self.store.groups.active()))
        if path == '/api/analytics/page':
            data = self.json_body(handler)
            self.store.limit(handler.ip(), 'analytics', 180, 60)
            self.store.track(self.visitor(handler), data.get('page'), data.get('eventId'))
            return handler.send_json(200, {'ok': True})
        if path == '/api/admin/login':
            data = self.json_body(handler)
            token, session = self.store.login(data.get('username'), data.get('password'), handler.ip())
            self.store.logout(self.cookie(handler, SESSION_COOKIE))
            self.set_cookie(handler, SESSION_COOKIE, token, SESSION_AGE)
            return handler.send_json(200, session)
        if path == '/api/admin/setup':
            if not self.local_setup:
                raise Problem(403, '请使用服务器命令创建管理员。')
            data = self.json_body(handler)
            self.store.limit(handler.ip(), 'setup', 5, 900)
            self.store.create_admin(data.get('username'), data.get('password'), first_only=True)
            token, session = self.store.new_session(data['username'])
            self.set_cookie(handler, SESSION_COOKIE, token, SESSION_AGE)
            return handler.send_json(201, session)
        if path == '/api/admin/logout':
            self.admin(handler, writing=True)
            self.json_body(handler)
            self.store.logout(self.cookie(handler, SESSION_COOKIE))
            self.set_cookie(handler, SESSION_COOKIE, '', 0)
            return handler.send_json(200, {'authenticated': False})
        if path in ('/api/admin/feishu', '/api/admin/feishu/test', '/api/admin/feishu/summary', '/api/admin/feishu/retry'):
            self.admin(handler, writing=True)
            self.store.limit(handler.ip(), 'feishu-write', 12, 60)
            data = self.json_body(handler)
            if path == '/api/admin/feishu':
                return handler.send_json(200, self.store.notifications.save(data))
            if data:
                raise Problem(400, '请先保存设置，再执行发送操作。')
            if path.endswith('/test'):
                return handler.send_json(202, self.store.notifications.queue_test())
            if path.endswith('/summary'):
                return handler.send_json(202, self.store.notifications.queue_summary())
            return handler.send_json(202, self.store.notifications.retry_failed())
        match = re.fullmatch(r'/api/admin/posters/(shenzhen|zhuhai|guangzhou)(?:/(position|reset))?', path)
        if match:
            self.admin(handler, writing=True)
            self.store.limit(handler.ip(), 'poster-write', 30, 60)
            city, action = match.groups()
            if action == 'position':
                data = self.json_body(handler)
                result = self.store.position(city, data.get('x'), data.get('y'))
            elif action == 'reset':
                self.json_body(handler)
                result = self.store.reset_poster(city)
            else:
                content_type = handler.headers.get('Content-Type', '').split(';')[0].strip().lower()
                if content_type not in ('image/png', 'image/jpeg', 'image/webp'):
                    raise Problem(415, '请上传 PNG、JPG 或 WebP 静态图片。')
                result = self.store.replace_poster(city, self.read_body(handler, MAX_POSTER_BYTES), content_type)
            return handler.send_json(200, {'city': result})
        raise Problem(404, '接口不存在。')
