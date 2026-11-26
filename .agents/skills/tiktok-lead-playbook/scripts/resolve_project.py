"""Resolve D&T project-relative references from any installed project layout."""
from pathlib import Path
import sys

def project_root():
    for candidate in Path(__file__).resolve().parents:
        if all((candidate / part).is_dir() for part in ('runtime-agents', 'docs', 'templates', 'data-pipeline')):
            return candidate
    raise RuntimeError('D&T project root not found; keep this skill inside its project')

if __name__ == '__main__':
    root = project_root()
    target = (root / sys.argv[1]).resolve() if len(sys.argv) > 1 else root
    if not target.is_relative_to(root):
        raise SystemExit('Reference escapes project root')
    if not target.exists():
        raise SystemExit('Missing project resource: ' + str(target))
    print(target)
