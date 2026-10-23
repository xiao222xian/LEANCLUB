"""Write an artifact manifest only from a clean, committed source checkout."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from preflight import inspect_jar

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--source', required=True, type=Path)
p.add_argument('--jar', required=True, type=Path)
p.add_argument('--output', required=True, type=Path)
a = p.parse_args()
def git(*args):
    return subprocess.check_output(['git', '-C', str(a.source), *args], text=True).strip()
if git('status', '--porcelain'):
    raise SystemExit('Commit reviewed source changes before stamping a release')
commit = git('rev-parse', 'HEAD')
report = {'format': 1, 'sourceCommit': commit,
          'createdAtUtc': datetime.now(timezone.utc).isoformat(),
          'artifact': inspect_jar(a.jar),
          'note': 'Build this JAR from the recorded clean commit; manifest is not a test certificate.'}
with a.output.open('x', encoding='utf-8') as stream:
    json.dump(report, stream, ensure_ascii=False, indent=2)
print(json.dumps({'manifest': str(a.output), 'sourceCommit': commit, 'sha256': report['artifact']['sha256']}))
