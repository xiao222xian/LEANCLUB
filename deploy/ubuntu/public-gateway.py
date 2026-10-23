#!/usr/bin/env python3
"""Loopback-only production MEMBER facade. No static files, database or credentials."""
import json
import os
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from campaign_http import CampaignAPI
from campaign_hosts import compatibility_apis, https_origin_key, select_api
from public_app_gateway import proxy_public_api, route_allowed
from store import Problem


class Handler(BaseHTTPRequestHandler):
    server_version = 'LeanSchoolGateway'

    def setup(self):
        super().setup()
        self.connection.settimeout(25)

    def log_message(self, *_):
        # Fixed method/path only; never query values, bearer tokens or body data.
        sys.stderr.write('%s %s\n' % (self.command, urlsplit(self.path).path))

    def error_json(self, status, message):
        self.close_connection = True
        self.send_json(status, {'error': message})

    def send_json(self, status, value):
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(raw)

    def dispatch(self):
        try:
            api = select_api(self.server.primary_api, self.server.compatible_apis,
                             self.headers, self.client_address[0])
            if self.command == 'GET' and self.path == '/healthz':
                return self.send_json(200, {'status': 'ok', 'service': 'public-app-gateway'})
            if not route_allowed(self.command, self.path):
                return self.error_json(404, '接口不存在。')
            if self.command in ('POST', 'PUT'):
                api.require_origin(self)
            return proxy_public_api(self)
        except Problem as error:
            return self.error_json(error.status, error.message)

    do_GET = dispatch
    do_HEAD = dispatch
    do_POST = dispatch
    do_PUT = dispatch
    do_DELETE = dispatch
    do_PATCH = dispatch
    do_OPTIONS = dispatch
    do_TRACE = dispatch


def configuration(environment):
    origin = environment.get('CLUB_ORIGIN', '')
    https_origin_key(origin)
    primary = CampaignAPI(None, origin)
    compatible = compatibility_apis(primary, environment.get('CLUB_COMPAT_ORIGINS', ''))
    port = int(environment.get('LEANCLUB_PUBLIC_GATEWAY_PORT', '5382'))
    upstream = int(environment.get('LEANCLUB_YUDAO_PORT', '48080'))
    tenant = int(environment.get('LEANCLUB_PUBLIC_TENANT_ID', '1'))
    if not (1 <= port <= 65535 and 1 <= upstream <= 65535 and port != upstream
            and 1 <= tenant <= 9223372036854775807):
        raise ValueError('Invalid gateway port, Java port or tenant id.')
    return primary, compatible, port, upstream, tenant


def main():
    primary, compatible, port, upstream, tenant = configuration(os.environ)
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        server.daemon_threads = True
        server.primary_api, server.compatible_apis = primary, compatible
        server.yudao_port, server.public_tenant_id = upstream, tenant
        print('Public MEMBER gateway listening on loopback port %d' % port, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
