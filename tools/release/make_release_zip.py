from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDES = [
    line.strip() for line in (Path(__file__).with_name('RELEASE_EXCLUDE.txt')).read_text(encoding='utf-8').splitlines()
    if line.strip() and not line.strip().startswith('#')
]


def excluded(rel: str) -> bool:
    rel = rel.replace('\\', '/').strip('/')
    rel_slash = f'/{rel}/'
    for raw in EXCLUDES:
        pat = raw.replace('\\', '/').strip()
        if not pat:
            continue
        if pat.endswith('/'):
            folder = pat.strip('/')
            if rel == folder or rel.startswith(folder + '/') or f'/{folder}/' in rel_slash:
                return True
            continue
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(Path(rel).name, pat):
            return True
    return False


def run_tests() -> None:
    if os.environ.get('LOCAL_BOORU_SKIP_RELEASE_TESTS', '').strip().lower() in {'1', 'true', 'yes', 'on'}:
        return
    tests_dir = ROOT / 'tests'
    if not tests_dir.exists():
        return
    print('Running pytest before release zip...')
    subprocess.check_call([sys.executable, '-m', 'pytest', '-q'], cwd=str(ROOT))


def main() -> int:
    run_tests()
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / (ROOT.name + '_release.zip')
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for path in ROOT.rglob('*'):
            if path.is_dir():
                continue
            rel = path.relative_to(ROOT).as_posix()
            if excluded(rel):
                continue
            zf.write(path, ROOT.name + '/' + rel)
    print(out)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
