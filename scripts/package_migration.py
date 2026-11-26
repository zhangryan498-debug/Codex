"""Package project and source data, excluding local credentials and dependencies."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '.venv', '.python', '__pycache__', 'node_modules', 'work', 'dist', 'upstream'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/D&T-Codex迁移交接包.zip')
    args = parser.parse_args()
    output = args.output.resolve()
    files = [p for p in ROOT.rglob('*') if p.is_file()
             and not EXCLUDED.intersection(p.relative_to(ROOT).parts)
             and not p.name.startswith('.env')
             and p.name not in {'.agent-instance-id', '.DS_Store'}
             and p.suffix not in {'.pyc', '.pyo', '.zip'} and p != output]
    secrets = [v for k, v in os.environ.items()
               if re.search(r'(TOKEN|API_KEY|SECRET|PASSWORD)$', k) and len(v) >= 12]
    for envfile in (ROOT / '.env', ROOT / 'data-pipeline/.env'):
        if envfile.exists():
            for line in envfile.read_text(encoding='utf-8-sig').splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    value = line.split('=', 1)[1].strip().strip(chr(34)).strip(chr(39))
                    if len(value) >= 12:
                        secrets.append(value)
    manifest = []
    for path in sorted(files):
        data = path.read_bytes()
        assert not any(s.encode() in data for s in secrets), 'Credential found: ' + str(path.relative_to(ROOT))
        manifest.append({'path': path.relative_to(ROOT).as_posix(), 'bytes': len(data),
                         'sha256': hashlib.sha256(data).hexdigest()})
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for entry in manifest:
            archive.write(ROOT / entry['path'], 'dt-project/' + entry['path'])
        archive.writestr('dt-project/PACKAGE-MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for entry in manifest:
            assert hashlib.sha256(archive.read('dt-project/' + entry['path'])).hexdigest() == entry['sha256']
    print(json.dumps({'file': str(output), 'files': len(files), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                      'credentials_included': False}, ensure_ascii=False))

if __name__ == '__main__':
    main()
