"""Restore pinned pure-client dependencies; never call ecommerce data endpoints."""
from pathlib import Path
import argparse
import io
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def download_source(commit):
    urls = (
        f'https://codeload.github.com/Evil0ctal/Douyin_TikTok_Download_API/zip/{commit}',
        f'https://api.github.com/repos/Evil0ctal/Douyin_TikTok_Download_API/zipball/{commit}',
    )
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'D&T-Codex-handoff'})
            with urllib.request.urlopen(request, timeout=45) as response:
                return zipfile.ZipFile(io.BytesIO(response.read()))
        except (urllib.error.URLError, TimeoutError, zipfile.BadZipFile) as error:
            print(f'Upstream download failed from {url}: {error}')
    raise SystemExit('Could not download pinned dtk source from GitHub. Retry when GitHub is reachable.')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Only test local imports and client construction')
    args = parser.parse_args()
    lock = json.loads((ROOT / 'interface/dependency-lock.json').read_text(encoding='utf-8'))
    python = ROOT / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    if not args.check:
        if not python.exists():
            venv.create(ROOT / '.venv', with_pip=True)
        upstream = ROOT / 'data-pipeline/upstream'
        marker = upstream / '.dt-source-commit'
        if not marker.exists() or marker.read_text().strip() != lock['commit']:
            if upstream.exists() and any(upstream.iterdir()):
                raise SystemExit('Existing unverified upstream directory: preserve it and use a fresh directory before restore.')
            archive = download_source(lock['commit'])
            prefix = archive.namelist()[0]
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                target = (upstream / entry.filename[len(prefix):]).resolve()
                if not target.is_relative_to(upstream.resolve()):
                    raise ValueError('Unsafe archive path')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
            marker.write_text(lock['commit'] + '\n')
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '-r', str(ROOT / lock['requirements'])], check=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '--no-deps', '-e', str(upstream)], check=True)
    check = "import sys; sys.path.insert(0,'data-pipeline/collectors'); from douyin_signed import _import_dtk,DouyinSignedClient; assert len(_import_dtk())==8; c=DouyinSignedClient(); print('Pure-client imports and construction PASS; no platform request made')"
    subprocess.run([str(python), '-X', 'utf8', '-c', check], cwd=ROOT, check=True)

if __name__ == '__main__':
    main()
