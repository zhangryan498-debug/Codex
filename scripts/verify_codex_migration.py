"""Verify portable role/Skill sources and actual run receipts without model calls."""
from pathlib import Path
import argparse
import hashlib
import json
import tomllib

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'work/migration-verification.json')
    args = parser.parse_args()
    roles = json.loads((ROOT / '.codex/role-manifest.json').read_text(encoding='utf-8'))
    assert len(roles) == 20 and len({x['role'] for x in roles}) == 20
    for row in roles:
        source = ROOT / row['source']
        assert hashlib.sha256(source.read_bytes()).hexdigest() == row['source_sha256'], row['role']
        assert source.read_bytes() == (ROOT / 'agents' / row['role'] / 'agent.md').read_bytes(), row['role']
        config = tomllib.loads((ROOT / row['config']).read_text(encoding='utf-8'))
        assert config['name'] == row['role'] and source.read_text(encoding='utf-8-sig') in config['developer_instructions']
        assert (ROOT / row['skill']).is_file()
    receipts = []
    for result in sorted((ROOT / 'work/codex-runs').glob('*/result.json')):
        row = json.loads(result.read_text(encoding='utf-8-sig'))
        answer = result.parent / 'answer.md'
        row['answer_exists'] = answer.exists()
        row['evidence'] = result.parent.relative_to(ROOT).as_posix()
        # Successful process does not imply its business/task output passed review.
        row['task_review_required'] = True
        receipts.append(row)
    report = {'role_sources_and_mirrors': 'passed', 'role_count': 20,
              'native_named_role_execution': 'not_verified_interface_has_no_role_selector',
              'receipts': receipts,
              'scope': 'Structural verification; read actual answers for task acceptance.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(json.dumps({'role_count':20,'role_sources_and_mirrors':'passed','run_receipts':len(receipts)}))

if __name__ == '__main__':
    main()
