#!/usr/bin/env python3
"""Validate the committed public design build without requiring Node on the server."""
from html.parser import HTMLParser
from pathlib import Path
import sys
from urllib.parse import urlsplit


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('src'):
            self.urls.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') in {'stylesheet', 'modulepreload', 'preload', 'icon'} and attrs.get('href'):
            self.urls.append(attrs['href'])


def validate(root):
    root = Path(root).resolve()
    design = root / 'web/creator/design'
    # Older fixed website revisions remain packageable by the current CI tools.
    if not design.exists() and not (root / 'frontend/package.json').exists():
        return
    index = design / 'index.html'
    if design.is_symlink() or not index.is_file():
        raise ValueError('Missing public design production files; build frontend before packaging.')
    html = index.read_text(encoding='utf-8')
    if '/@vite/client' in html or '/src/main.' in html:
        raise ValueError('Design HTML must be a production build.')
    for item in design.rglob('*'):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError('Design build contains a link or special file.')
        if (item.name.startswith('.') or item.name == 'node_modules'
                or item.suffix.lower() in {'.map', '.pem', '.key', '.pfx', '.p12', '.db', '.sqlite', '.sqlite3'}):
            raise ValueError('Design build contains a non-public file.')
    parser = Assets()
    parser.feed(html)
    if not any(urlsplit(url).path.endswith('.js') for url in parser.urls):
        raise ValueError('Design HTML must reference its JavaScript entry.')
    for url in parser.urls:
        parsed = urlsplit(url)
        if (parsed.scheme or parsed.netloc or not parsed.path.startswith('/design/')
                or '%' in parsed.path or '\\' in parsed.path):
            raise ValueError('Design assets must use the /design/ production base.')
        asset = (design / parsed.path.removeprefix('/design/')).resolve()
        if not asset.is_relative_to(design) or not asset.is_file() or not asset.stat().st_size:
            raise ValueError('A referenced design asset is missing or outside the design release.')


if __name__ == '__main__':
    validate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    print('Public design build validated.')
