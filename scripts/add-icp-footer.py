#!/usr/bin/env python3
"""Add the approved filing link without replacing live application pages."""
from pathlib import Path
import argparse
import datetime
import json
import re
import shutil

CSS = '''/* Shared public-site filing footer. Keep the QR/vote controls unobstructed. */
.site-filing{box-sizing:border-box;width:100%;padding:20px 16px;text-align:center;background:#f6f7f3;color:#59655d;font:400 11px/1.8 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;letter-spacing:.03em}
.site-filing a{display:inline-block;padding:5px 8px;color:inherit;text-decoration:none}
.site-filing a:hover{text-decoration:underline;text-underline-offset:3px}
.site-filing a:focus-visible{outline:2px solid currentColor;outline-offset:3px}
.site-filing--vote{position:fixed;inset:auto 0 0;z-index:6;padding:1px 12px calc(1px + env(safe-area-inset-bottom));background:linear-gradient(0deg,#060d16e6,#060d1655);color:#e1e5e8;font-size:10px;line-height:18px;letter-spacing:.04em}
.site-filing--vote a{padding:2px 8px}
body[data-site-filing="vote"] .page-note{bottom:35px}
@media(max-width:800px) and (orientation:portrait),(max-width:600px){
 body[data-site-filing="vote"] .city-content{bottom:calc(126px + env(safe-area-inset-bottom))}
 body[data-site-filing="vote"] .city-switcher{bottom:calc(65px + env(safe-area-inset-bottom))}
 body[data-site-filing="vote"] .top-nav .text-link{bottom:calc(28px + env(safe-area-inset-bottom))}
 body[data-site-filing="vote"] .top-nav .event-entry{bottom:calc(26px + env(safe-area-inset-bottom))}
}
@media(max-height:650px) and (max-width:600px){
 body[data-site-filing="vote"] .city-content{bottom:calc(109px + env(safe-area-inset-bottom))}
 body[data-site-filing="vote"] .city-switcher{bottom:calc(55px + env(safe-area-inset-bottom))}
 body[data-site-filing="vote"] .top-nav .text-link,body[data-site-filing="vote"] .top-nav .event-entry{bottom:calc(21px + env(safe-area-inset-bottom))}
}
@media(min-width:601px) and (max-height:440px) and (orientation:landscape){body[data-site-filing="vote"] .page-note{display:none}}
'''
LINK = '<link rel="stylesheet" href="/site-filing.css?v=20261023-icp1">'
NUMBER = '\u8fbdICP\u59072026010731\u53f7-1'
PAGES = ('index.html', 'lean-creator-home.html', 'lean-school-ranking.html', 'school-submit.html', 'city-vote/index.html')


def patch(text, vote):
    if vote and 'data-site-filing="vote"' not in text:
        text = re.sub(r"<body\b", '<body data-site-filing="vote"', text, count=1)
    if 'id="site-filing"' in text:
        return text
    assert '</head>' in text and '</body>' in text
    classes = 'site-filing site-filing--vote' if vote else 'site-filing'
    footer = ('\n<footer id="site-filing" class="' + classes + '">'
              '<a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">'
              + NUMBER + '</a></footer>\n')
    return text.replace('</head>', LINK + '\n</head>', 1).replace('</body>', footer + '</body>', 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-root', type=Path, required=True)
    parser.add_argument('--backup-root', type=Path)
    args = parser.parse_args()
    root = args.web_root.resolve(strict=True)
    changes = {}
    for name in PAGES:
        path = root / name
        assert path.is_file() and path.resolve().is_relative_to(root), name
        changes[name] = patch(path.read_text(encoding='utf-8'), name.startswith('city-vote/'))
    changes['site-filing.css'] = CSS
    if args.backup_root:
        backup = args.backup_root / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
        backup.mkdir(parents=True, mode=0o700)
        backup.parent.chmod(0o700)
        for name in changes:
            source = root / name
            if source.exists():
                target = backup / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        print('BACKUP', backup)
    for name, text in changes.items():
        target = root / name
        tmp = target.with_name(target.name + '.icp-new')
        tmp.write_text(text, encoding='utf-8', newline='\n')
        tmp.chmod(0o644)
        tmp.replace(target)
    print(json.dumps({'updated': list(changes), 'releaseUnchanged': root.parent.parent.name}))


if __name__ == '__main__':
    main()
