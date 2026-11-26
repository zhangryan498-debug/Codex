"""Rebuild native Codex roles from preserved MiniMax role sources."""
import hashlib
import json
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.codex' / 'agents'
OUT.mkdir(parents=True, exist_ok=True)
rows = []
for src in sorted((ROOT / 'runtime-agents').glob('*/agent.md')):
    role = src.parent.name
    original = src.read_text(encoding='utf-8-sig')
    description = next(x.split(':', 1)[1].strip() for x in original.splitlines() if x.startswith('description:'))
    skill = f'.agents/skills/{role}-playbook/SKILL.md'
    overlay = f'''你是 D&T 项目的 {role} 岗位。以下原始岗位说明按资料保留，执行时以当前用户要求、项目 AGENTS.md 和本迁移适配说明为准。
业务范围以当前用户任务及项目 AGENTS.md 为准。阅读 {skill}，以及原岗位说明引用的本地知识和模板；不得用空泛摘要代替原有 Skill。
项目根目录是当前工作目录。原说明中的 旧 MiniMax 岗位目录 路径对应本项目 runtime-agents；mavis 命令与 .agent-instance-id 是原运行器历史资料，不是在 Codex 中执行或注册的指令。Codex 原生岗位位于 .codex/agents，Skill 位于 .agents/skills。
把冻结离线验收与获用户授权的在线业务任务区分；离线验收不得联网，在线任务遵从用户当前授权。历史阈值和政策视为待核实资料，不得视为当前平台规则。明确已验证、未验证和数据不足，输出证据路径。
负责人按岗位职责委派，可并行的独立任务使用子 Agent；不支持委派的环境输出具体交接单并使用单岗启动器。不得将配置生成等同于独立运行验收。

--- 原岗位说明开始 ---
{original}
'''
    content = '\n'.join(f'{k} = {json.dumps(v, ensure_ascii=False)}' for k, v in {'name':role,'description':description,'developer_instructions':overlay}.items()) + '\n'
    parsed = tomllib.loads(content)
    assert parsed['name'] == role
    target = OUT / f'{role}.toml'
    target.write_text(content, encoding='utf-8', newline='\n')
    rows.append({'role':role,'source':src.relative_to(ROOT).as_posix(),'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'config':target.relative_to(ROOT).as_posix(),'skill':skill})
assert len(rows) == 20, f'Expected 20 roles, found {len(rows)}'
(OUT.parent / 'role-manifest.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'roles':len(rows),'toml_valid':True,'model_override':False}))
