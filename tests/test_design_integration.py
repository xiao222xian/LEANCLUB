"""Public design routing/build checks; only local temporary files and HTTP."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preview = load('design_preview', 'scripts/preview.py')
build = load('design_build_checks', 'scripts/check-design-build.py')


class DesignIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.design = self.root / 'web/creator/design'
        (self.design / 'assets').mkdir(parents=True)
        (self.design / 'index.html').write_text('<!doctype html><script type="module" src="/design/assets/app.js"></script><link rel="stylesheet" href="/design/assets/app.css">', encoding='utf-8')
        (self.design / 'assets/app.js').write_text('/* synthetic compiled app */', encoding='utf-8')
        (self.design / 'assets/app.css').write_text('body{margin:0}', encoding='utf-8')

    def test_production_assets_and_old_revision_compatibility(self):
        build.validate(self.root)
        with tempfile.TemporaryDirectory() as old:
            build.validate(Path(old))
            (Path(old) / 'frontend').mkdir()
            (Path(old) / 'frontend/package.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Missing public design'):
                build.validate(Path(old))

    def test_missing_assets_and_source_maps_fail_before_packaging(self):
        asset = self.design / 'assets/app.css'
        asset.unlink()
        with self.assertRaisesRegex(ValueError, 'missing or outside'):
            build.validate(self.root)
        asset.write_text('body{}')
        (self.design / 'assets/app.js.map').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'non-public'):
            build.validate(self.root)

    def test_development_wrong_base_and_encoded_paths_fail(self):
        for url in ('/src/main.ts', '/@vite/client', '/assets/app.js', 'https://example.invalid/app.js',
                    '/design/../index.js', '/design/%61ssets/app.js'):
            with self.subTest(url=url):
                (self.design / 'index.html').write_text('<script src="' + url + '"></script>')
                with self.assertRaises(ValueError):
                    build.validate(self.root)

    def test_exact_public_routes_assets_and_missing_file_over_http(self):
        with patch.object(preview, 'WEB_ROOT', self.root / 'web/creator'):
            server = ThreadingHTTPServer(('127.0.0.1', 0), preview.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for method, path, expected in (
                    ('GET', '/design', 200), ('GET', '/design/', 200),
                    ('GET', '/design/?from=home', 200), ('HEAD', '/design/', 200),
                    ('GET', '/design/assets/app.js', 200), ('GET', '/design/assets/app.css', 200),
                    ('GET', '/design/assets/missing.js', 404), ('GET', '/design/unknown', 404),
                    ('GET', '/design/%2e%2e/index.html', 400),
                ):
                    with self.subTest(method=method, path=path):
                        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                        try:
                            connection.request(method, path)
                            response = connection.getresponse()
                            self.assertEqual(response.status, expected)
                            body = response.read()
                            if method == 'HEAD':
                                self.assertEqual(body, b'')
                            if expected == 200:
                                self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
                        finally:
                            connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_both_nginx_templates_define_public_route_without_spa_fallback(self):
        for filename in ('leanschool.nginx.conf.in', 'bojizhuye.nginx.conf.in'):
            text = (ROOT / 'deploy/ubuntu' / filename).read_text()
            for path in ('/design', '/design/'):
                self.assertIn('location = ' + path + ' { try_files /design/index.html =404; }', text)
            self.assertNotIn('try_files $uri $uri/ /design/index.html', text)


if __name__ == '__main__':
    unittest.main()
