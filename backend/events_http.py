"""Event routes reuse the campaign origin, visitor and administrator boundary."""
import re
from urllib.parse import parse_qs, urlsplit

from store import Problem


class EventsAPI:
    def __init__(self, api):
        self.api = api

    @property
    def events(self):
        return self.api.store.events

    @staticmethod
    def matches(path):
        return any(path == base or path.startswith(base + '/') for base in ('/api/campaign/events', '/api/admin/events'))

    def get(self, handler, path):
        api = self.api
        if path.startswith('/api/admin/'):
            api.admin(handler)
        else:
            api.store.limit(handler.ip(), 'events-read', 120, 60)
        if path == '/api/admin/events':
            return handler.send_json(200, self.events.list(admin=True))
        if path == '/api/campaign/events':
            return handler.send_json(200, self.events.list())
        if path == '/api/campaign/events/mine':
            return handler.send_json(200, self.events.mine(api.visitor(handler), api.origin))
        match = re.fullmatch(r'/api/admin/events/([^/]+)/registrations', path)
        if match:
            try:
                query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
                if set(query) - {'status', 'page'} or any(len(v) != 1 for v in query.values()):
                    raise ValueError()
                page = query.get('page', ['1'])[0]
                if not re.fullmatch(r'[1-9][0-9]{0,6}', page):
                    raise ValueError()
            except ValueError:
                raise Problem(400, '筛选或页码参数无效。')
            return handler.send_json(200, self.events.registrations(match[1], query.get('status', ['all'])[0], int(page)))
        match = re.fullmatch(r'/api/campaign/events/([^/]+)', path)
        if match:
            return handler.send_json(200, self.events.get(match[1]))
        raise Problem(404, '接口不存在。')

    def post(self, handler, path):
        api = self.api
        if path.startswith('/api/admin/'):
            session = api.admin(handler, writing=True)
            api.store.limit(handler.ip(), 'events-admin', 120, 60)
        data = api.json_body(handler)
        if path in ('/api/admin/events/check-in/preview', '/api/admin/events/check-in'):
            expected = {'code'} if path.endswith('/preview') else {'code', 'requestId'}
            if set(data) != expected:
                raise Problem(400, '请扫描入场券或输入票号。')
            result = self.events.preview(data['code'], api.origin) if path.endswith('/preview') else self.events.check_in(data['code'], data['requestId'], api.origin, session['username'])
            return handler.send_json(200, result)
        if path == '/api/admin/events':
            return handler.send_json(201, self.events.save(data))
        match = re.fullmatch(r'/api/admin/events/([^/]+)', path)
        if match:
            return handler.send_json(200, self.events.save(data, match[1]))
        if path in ('/api/campaign/events/ticket', '/api/campaign/events/cancel'):
            api.store.limit(handler.ip(), 'event-ticket', 60, 60)
            if set(data) not in ({'ticketToken'}, {'orderToken'}):
                raise Problem(400, '入场凭证无效。')
            visitor = api.visitor(handler)
            token = data.get('orderToken', data.get('ticketToken'))
            result = self.events.ticket(token, api.origin, visitor) if path.endswith('/ticket') else self.events.cancel(token, api.origin, visitor)
            return handler.send_json(200, result)
        match = re.fullmatch(r'/api/campaign/events/([^/]+)/register', path)
        if match:
            raise Problem(410, '新的活动报名请登录会员后使用活动页面入口，原有报名仍可凭原链接查看。')
        raise Problem(404, '接口不存在。')
