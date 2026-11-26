"""Generate queue acceptance from recorded engineering and actual-run evidence."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
from offline_queue_worker import read,sha,dump


def render(root):
    root=Path(root).resolve();b=root/'work/offline-role-queue-20261111';integration=read(b/'actual-integration/integration-summary.json')
    independent=b/'independent-evidence'
    first=read(independent/'first-replay/summary.json');fixed=read(independent/'repaired-replay-ba216a79/summary.json')
    review=read(b/'independent-integration-review.json')
    if review.get('passed') is not True:raise ValueError('Independent actual integration review has not passed')
    if sha(root/'scripts/offline_role_queue.py')!=fixed['implementation_sha256_after'] or fixed['case_pass_count']!=24 or fixed['step_pass_count']!=204:raise ValueError('Repaired independent replay source/coverage mismatch')
    log=(b/'engineering-full-suite.log').read_text(encoding='utf-8-sig')
    tests=re.findall(r'Ran (\d+) tests in',log)
    if not tests or tests[-1]!='501' or not re.search(r'(?m)^OK\s*$',log):raise ValueError('Engineering regression incomplete')
    if integration['actual_model_calls']!=4 or integration['actual_service_tiers']!=['priority'] or not integration['all_prior_files_unchanged']:raise ValueError('Actual run/preservation coverage incomplete')
    summary={'module':'offline-role-queue-v1','scope':'synthetic_offline','release_status':'not_authorized','selected_roadmap_item':'1.5','engineering_tests_passed':int(tests[-1]),'independent_first_cases':first['case_pass_count'],'independent_repaired_cases':fixed['case_pass_count'],'independent_cases':fixed['case_count'],'independent_steps_passed':fixed['step_pass_count'],'independent_steps':fixed['step_count'],'concurrent_groups':fixed['concurrent_group_count'],'independent_model_calls':0,'actual_integration':integration,'independent_actual_checks':len(review['checks']),'independent_actual_review_sha256':sha(b/'independent-integration-review.json'),'source_sha256':{n:sha(root/'scripts'/n) for n in ['offline_role_queue.py','offline_queue_worker.py','run_offline_queue.py','accept_offline_queue.py','test_offline_role_queue.py','test_offline_queue_worker.py']},'created_at':datetime.now(timezone.utc).isoformat(),'remaining_blockers':[],'limits':['Twenty routes were structurally exercised; only four role routes had actual CLI execution in this batch.','Independent state-machine cases use explicitly synthetic settle evidence and do not certify business or model semantics.','Actual price infrastructure replay reuses previously accepted inputs; it is not a new pricing blind test.','Crash recovery was a controlled loss of queue settlement after actual CLI completion; no actual host crash is claimed.','Local SQLite and hashes provide consistency, not cryptographic protection against rewriting the entire evidence set.','V1 validates dependency eligibility and archives outputs; automatic upstream full-text/summarized context injection is outside this version.','No live store, scheduled daemon, platform publishing, investment or campaign operation; window fast UI not independently inspected.']}
    dump(b/'summary.json',summary)
    text=f'''# 离线岗位任务队列开发与验收（2026-11-11）

结论：路线图1.5的本机离线队列v1已完成本轮开发与验收。任务可按明确岗位路由、依赖顺序领取，回收真实CLI产物；重复提交不重复运行，不确定执行不自动重跑。价格任务继续调用既有专用host，未知、破底、输入变化及过期依赖不能放行。

建议：后续按[使用说明](离线岗位任务队列使用说明.md)使用新数据库和有限任务批次；先逐岗接入专用证据门禁，再扩大自动化范围。当前没有接真实店铺、定时服务或前端部署。

选择依据：已读取现有路线图、岗位现状、失败清单及最新广告二审/商城活动离线报告。商城、活动和广告已有可复用证据；阶段1.5仍缺任务池到派发与归档的入口，且不依赖真实店铺即可离线开发。优先补这一共用模块，未重复商城活动原始盲测。

| 验收层次 | 结果 | 证明范围 |
|---|---|---|
| 原岗位路由 | 20个岗位名称结构验证 | 抖音CN、TikTok PH；不声称20岗全部重新跑模型，也未扩展印尼价格门禁 |
| 本地工程 | {tests[-1]}项完整回归通过，其中25项新增队列/worker测试 | 并发领取、幂等、租约、依赖、类型和价格通道防绕过 |
| 独立首轮 | {first['case_pass_count']}/24场景，203/204步 | Q05同批完全相同任务被误拒；保留原实现、首轮和失败记录 |
| 修复后独立同题回放 | {fixed['case_pass_count']}/24场景、{fixed['step_pass_count']}/204步，{fixed['concurrent_group_count']}个真实barrier并发组通过 | 原fixture和runner未改；不是新盲测，工程模拟settle不冒充实际host |
| 实际CLI派发 | {integration['actual_model_calls']}次：两负责人普通复核、两商城专用价审 | 加载原岗位与完整Skill；4条实际角色路线 |
| 价格凭证 | 2次实际专用host，2对象离线接收 | 复用既有安全输入做队列基础设施验证，不计作新价格盲测 |
| 中断恢复 | 受控丢失队列回写后uncertain→核查既有凭证→completed | PH-PLAN模型attempt仍1，恢复额外调用0；不声称发生真实进程崩溃 |
| 独立实际集成核对 | {len(review['checks'])}项只读核对通过 | 原始任务/提示词/回答/运行、实际日志、价审及队列恢复记录 |

**实际任务池。** 8任务重复提交后仍是8条。最终4条completed、3条blocked、1条因上游blocked而保持pending，符合本次验收设计。坏caller hash、已被原门禁拒绝的价格方案、过期上游证明均在模型前拦截。依赖失败的子任务attempt为0。普通CLI完成不产生价格接受资格；下游价格依赖必须复验原始caller及实际host。

CN-PLAN、PH-PRICE、CN-PRICE、PH-PLAN按依赖运行，产物分别保存任务、提示词、原答、事件、运行结果、源码及输入hash。PH-PLAN的实际CLI已完整完成，验收程序有意不回写队列完成态，再核对原attempt收口。完成后再次有限drain未执行新任务。独立审查另从原始产物核对角色、资料口径、恢复和价格依赖；没有重复模型调用。

**修复与保留。** 独立Q05发现：同一批次内两个完全相同的ID/key/payload应幂等保存一条，初稿却拒绝。现实现先规范化并去重，内容或身份不同仍整批原子拒绝。第一版SHA `565f2b432299797b0390945531b3ea0e95947942c9ec71c337207031bd58654b`与首轮23/24证据保留；修复版SHA为`{fixed['implementation_sha256_after']}`。单批重复、跨次重复、完成后重复和并发提交分别验证。

**运行和安全边界。** 4次实际CLI事件均为Sol / XHigh / priority、MCP0、工具调用0；无模型、推理或快速模式的持久配置写入。priority是后台实际字段，不能据此独立认证窗口蓝色闪电。payload不能传模型设置、shell参数或绕过价审通道；原门禁源码和商城活动已验收的216个交付文件核对不变。原24题和后续16题范围、严格原分及未覆盖边界均沿用原报告，没有升级为实盘结果。

本轮受控范围无执行阻塞。未验证原生按岗名子Agent调用、所有20岗业务产物、多机分布式队列、长期负载、真实进程崩溃和真实经营。v1校验依赖资格并归档产物，尚未自动把上游全文或摘要注入下游提示词；需要模型阅读的具体资料仍须明确提供。路由依赖显式任务元数据，没有用模型猜测岗位；数据库与hash为本机一致性证据，不能防止有权限的主体重写整个证据集。未知费用、真实库存、结算和店铺准入不会因队列完成而变成已确认。

主要证据：

- [机器汇总](../work/offline-role-queue-20261111/summary.json)
- [独立首轮23/24](../work/offline-role-queue-20261111/independent-evidence/first-replay/summary.json)
- [独立修复后204步](../work/offline-role-queue-20261111/independent-evidence/repaired-replay-ba216a79/summary.json)
- [独立源码审查](../work/offline-role-queue-20261111/independent-evidence/repaired-replay-ba216a79/source_review.json)
- [实际CLI证据](../work/offline-role-queue-20261111/actual-integration/actual-cli-evidence.json)
- [实际任务状态及原始凭证](../work/offline-role-queue-20261111/actual-integration/final-queue-state.json)
- [独立实际集成复核](../work/offline-role-queue-20261111/independent-integration-review.json)
- [完整工程日志](../work/offline-role-queue-20261111/engineering-full-suite.log)

报告由 `scripts/render_offline_queue_acceptance.py`从实际JSON和工程日志生成。最小输入与命令在使用说明；旧报告及原始失败留存。
'''
    target=root/'docs/离线岗位队列开发与验收-2026-11-11.md'
    with target.open('x',encoding='utf8',newline='\n') as stream:stream.write(text)
    print(json.dumps({'report':str(target),'sha256':sha(target),'summary_sha256':sha(b/'summary.json')},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--project',required=True);a=p.parse_args();render(a.project)
