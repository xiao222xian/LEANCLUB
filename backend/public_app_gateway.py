"""Shared, bounded MEMBER-facade proxy for local preview and production Nginx."""
import http.client
import re
import socket
from urllib.parse import parse_qsl, urlsplit

PREFIX = '/app-api/leanclub'
MAX_BODY = 32 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
REQUEST_HEADERS = frozenset(('content-type', 'origin', 'referer', 'sec-fetch-site', 'authorization', 'accept'))
ROUTES = {
    ('GET', '/site/published'): frozenset(), ('GET', '/site/capabilities'): frozenset(),
    ('GET', '/school/list'): frozenset(('keyword', 'province', 'pageNo', 'pageSize')),
    ('GET', '/school/get'): frozenset(('id',)), ('GET', '/club/list'): frozenset(('schoolId',)),
    ('GET', '/profile/get'): frozenset(), ('PUT', '/profile/update'): frozenset(),
    ('POST', '/club/apply'): frozenset(), ('GET', '/club/mine'): frozenset(),
    ('POST', '/auth/send-sms-code'): frozenset(), ('POST', '/auth/sms-login'): frozenset(),
    ('POST', '/auth/logout'): frozenset(),
    ('GET', '/orders/page'): frozenset(('pageNo', 'pageSize', 'status')),
    ('GET', '/orders/get'): frozenset(('id',)), ('POST', '/orders/cancel'): frozenset(),
}
PUBLIC_PATHS = frozenset(('/site/published', '/site/capabilities', '/school/list', '/school/get',
                          '/club/list', '/auth/send-sms-code', '/auth/sms-login'))


def route_allowed(method, target, decoded_path=None):
    if not isinstance(target, str) or len(target) > 8192:
        return False
    try:
        parsed = urlsplit(target)
        if (parsed.scheme or parsed.netloc or parsed.fragment
                or not parsed.path.startswith(PREFIX + '/')
                or decoded_path is not None and decoded_path != parsed.path):
            return False
        suffix = parsed.path[len(PREFIX):]
        allowed = ROUTES.get((method, suffix))
        if method == 'POST' and re.fullmatch(r'/events/[a-f0-9]{32}/register', suffix):
            allowed = frozenset()
        if allowed is None:
            return False
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True,
                          encoding='utf-8', errors='strict', max_num_fields=12)
    except (ValueError, UnicodeError):
        return False
    names = [key for key, _ in pairs]
    return len(names) == len(set(names)) and set(names).issubset(allowed)


def proxy_public_api(handler):
    """Forward only whitelisted routes; never proxy cookies, tenants or admin paths."""
    if not route_allowed(handler.command, handler.path):
        return handler.error_json(404, '接口不存在。')
    auth = handler.headers.get_all('Authorization', [])
    private = urlsplit(handler.path).path[len(PREFIX):] not in PUBLIC_PATHS
    if len(auth) > 1 or auth and not re.fullmatch(r'Bearer [^\s,]+', auth[0]):
        return handler.error_json(401, '会员登录信息无效，请重新登录。')
    if private and len(auth) != 1:
        return handler.error_json(401, '请先登录会员账户。')
    # Do not collapse duplicate routing/security headers into a dictionary.
    if any(len(handler.headers.get_all(name, [])) > 1 for name in REQUEST_HEADERS):
        return handler.error_json(400, '请求头重复，请重新提交。')
    if handler.headers.get_all('Transfer-Encoding', []):
        return handler.error_json(400, '请发送带长度的请求。')
    lengths = handler.headers.get_all('Content-Length', [])
    try:
        if len(lengths) > 1 or lengths and not re.fullmatch(r'[0-9]+', lengths[0]):
            raise ValueError()
        length = int(lengths[0]) if lengths else 0
    except ValueError:
        return handler.error_json(400, '请求长度无效。')
    if length > MAX_BODY:
        handler.close_connection = True
        return handler.error_json(413, '请求内容过大。')
    if handler.command == 'GET' and length:
        return handler.error_json(400, '读取接口不接受请求正文。')
    try:
        body = handler.rfile.read(length) if length else None
    except (TimeoutError, socket.timeout, OSError):
        handler.close_connection = True
        return handler.error_json(408, '请求接收超时，请重试。')
    if body is not None and len(body) != length:
        return handler.error_json(400, '请求内容未完整接收。')
    headers = {key: value for key, value in handler.headers.items() if key.lower() in REQUEST_HEADERS}
    # Java remains responsible for verifying that a token is an enabled MEMBER.
    # Tenant identity belongs to the deployment, never a request/body cookie.
    headers['tenant-id'] = str(handler.server.public_tenant_id)
    connection = http.client.HTTPConnection('127.0.0.1', handler.server.yudao_port, timeout=20)
    try:
        connection.request(handler.command, handler.path, body=body, headers=headers)
        response = connection.getresponse()
        mime = response.getheader('Content-Type', '').split(';')[0].strip().lower()
        if 300 <= response.status < 400 or mime not in ('application/json', 'application/problem+json'):
            return handler.error_json(502, '会员服务返回格式异常，请稍后重试。')
        data = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            return handler.error_json(502, '会员服务返回内容过大。')
        handler.send_response(response.status)
        handler.send_header('Content-Type', 'application/json; charset=utf-8')
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        retry = response.getheader('Retry-After')
        if retry and re.fullmatch(r'[0-9]{1,6}', retry):
            handler.send_header('Retry-After', retry)
        # Set-Cookie, Location, CORS, authentication challenges and hop headers
        # deliberately do not cross this same-origin MEMBER facade.
        handler.send_header('Content-Length', str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
    except (OSError, TimeoutError, http.client.HTTPException):
        return handler.error_json(502, '暂时无法连接会员服务，请稍后重试。')
    finally:
        connection.close()
