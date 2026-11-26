"""Render the repository handoff from actual final JSON evidence; no model calls."""
import argparse
import json
from pathlib import Path
from replay_campaign_ledger_v3 import read,sha


def render(project):
    project=Path(project).resolve();base=project/'work/compound-ledger-v3-20261111'
    summary=read(base/'final-verification-v4/summary.json');r=summary['same_input_regression']
    audit=read(base/'final-code-audit.json')
    if not audit['conclusion']['v4_static_fix_closes_reported_path_without_relaxing_floor']:raise ValueError('Independent v4 audit has not passed')
    ph_dir=base/'independent-author-ph/review'
    strict=read(ph_dir/'first-run-original-structured-score.json')
    ph_score=read(ph_dir/'first-run-independent-score-semantic-adjudication.json')
    diff=next(c for c in strict['cases'] if not c['pass'])
    actual=read(base/'heldout-first/tiktok-activity-first/answer.md')
    actual_case=next(c for c in actual['cases'] if c['case_id']==diff['case_id'])
    expected=diff['failures'][0]['expected'];observed=actual_case['numbers']['displayed_quote_net_minor']
    if actual_case['numbers']['net_minor'] is not None or observed!=expected:raise ValueError('PH_NEW_104 evidence differs from the documented scope adjudication')
    counter=actual_case['counterfactuals']['remove_bmsm_only']['numbers']['net_minor']
    oldcov=r['coverage'];newcov=summary['new_heldout_coverage']
    text=f'''# 商城活动复合盲测整改与泛化离线验收（v4，2026-11-11）

结论：本轮约定的受控离线整改与补验已经完成。不破价门禁保持原样；当前账、历史/反事实账、未知总额与已知小计分开，安全方案能通过，已知失败和关键资料缺口会拦截。原24题首轮、严格评分、独立语义评分、失败尝试及后续补验均保留，不能把这些合并成“首次全通过”。

建议：后续同口径复算使用 `scripts/campaign_ledger_semantics_v4.py` 和 `scripts/replay_campaign_ledger_v4.py`；新来源、费用、规则、身份或时间发生变化时重新审查。未支持的新输入字段和平台规则继续列为缺口。本报告没有真实店铺接入或经营放行。

| 验证层次 | 实际结果 | 边界 |
|---|---|---|
| 原24题独立首分 | 活动决策/核心金额各24/24，353/353原子检查 | 原报告明确不宣称所有辅助字段24/24；首次host仍是28/31，2项安全误拒、3项无host不改写 |
| v4原题回归 | {r['same_input_regression_cases']}题、31对象：8通过、23拦截；{r['adversarial_blocked']}/{r['adversarial_total']}篡改拦截，31项过期拦截 | 同题确定性回归，模型调用0；不是新盲测 |
| 独立新复合题 | {summary['new_independent_cases']}题，抖音/菲律宾各8；语义复核各8/8 | 严格原分抖音6/8、菲律宾7/8分别保留，未重跑原答 |
| 新题真实商城交接 | {summary['new_host_batches']}批、{summary['new_host_subjects']}对象：{summary['new_host_accepted']}接收、{summary['new_host_rejected']}拒绝 | 模型及系统均0误放、0误拒；每批有实际完成host凭证 |
| 下游拒用 | {summary['expired_subjects_blocked']}项过期、{summary['changed_caller_receipt_reuse_blocked']}项变更后复用旧回执全部拦截 | 变更含原始输入、费用版本、身份修订及计算 |
| 工程/独立账核 | {summary['engineering_tests_passed']}项工程测试；{summary['separate_reference_decimal_checks']}项独立Decimal账核；{summary['auxiliary_checks']}项辅助补验 | 工程测试不等于模型语义或真实经营结果；Decimal账核不重复宣称已重建全部选价、分摊和规则语义 |

**通用修复及独立审计。** 原可信复算的3条取消、过期或禁叠净额改为当前 `null`，原数移入独立反事实分支。PLUS的基础费用小计、运费未定时的已知成本小计不再充当总额；有充分证明的闭区间仍保留。必要引用补足原可信复算遗漏的DY08、DY06、PH01，原活动答卷不因此改分。

独立只读审计发现v3的一条通用途径：未知非负附加成本可能把已有REJECT/FAIL改成HOLD/UNPROVEN。虽然仍拦截，但会抹去已知失败。v3与审计初稿已保留；v4仅在独立当前上界仍低于所声明整单或某SKU底线时保留REJECT/FAIL，未知精确净额和成本仍为null。不能用PLUS基础计划或假设报价作该上界。8项新增目标回归通过，独立静态审计确认未增加ALLOW路径。原24题当前金额和决策未因v4改变。

**计算覆盖矩阵。** 下表区分可计算程度与安全决策，HOLD/REJECT不是“已经精确算完”。

| 净额覆盖 | 原24题v4回归 | 独立新16题 |
|---|---:|---:|
| 整单及全部SKU净额均精确 | {oldcov['exact_current_ledger']} | {newcov['exact_current']} |
| 仅整单净额精确，SKU仍有缺口/区间 | {oldcov['exact_order_net_partial_sku_ledger']} | {newcov['exact_order_net_partial_sku_ledger']} |
| 有界净额区间 | {oldcov['bounded_current_ledger']} | {newcov['bounded_current']} |
| 当前不可执行，假设/观察账单列 | {oldcov['not_executable_current_with_separate_counterfactual']} | {newcov['not_executable']} |
| 仅已知部分，关键资料缺失 | {oldcov['partial_known_components_missing_information']} | {newcov['partial_known_missing_information']} |

原CN-8D21AF与新CN_NX_62均属于“整单净额已知，但SKU仍缺”。尤其不能拿整单盈余补单个SKU损失。区间跨越底线时继续HOLD；有保证的最坏结果已达所有底线时可以给离线ALLOW。

**PH_NEW_104差异单列。** 严格首分为7/8，唯一字段差异是冻结期望 `numbers.net_minor={expected}`，原模型输出 `net_minor=null`、`displayed_quote_net_minor={observed}`。这{observed}是题面明确要求核对的已显示观察报价，不能执行的BMSM+basic MDV组合没有有效当前活动净利。模型另算的“仅移除BMSM”反事实净额{counter}也未冒充当前结果。

独立评分按首轮前公开协议的等价字段条款裁定语义8/8；原严格7/8、首份独立评分、金标、reference和原答均保留未改。接收构建器将该题 `signed_current_lower_minor=null`、`lower_bound_verified=false`，商城模型和系统最终均拒绝。未为该差异重跑活动模型。

抖音严格6/8的两条差异也保留：原可选引用白名单漏列实际相关的DY07，以及 `cancelled` 配合 `terminal_event_status=owner_cancelled` 的等价终态表达。独立复核依据冻结规则与原协议作8/8裁定，没有修改原key或oracle。

**辅助缺口收口。** 原PH-830EA3单题补验给出普通券条件核销后余量9、已耗尽聊天券余量0、条件新增使用1；实际未commit，普通券实际余量仍10。独立Decimal复算净额10036，恰达含等号底线。原CN-7B905E继续拒绝过期恢复，预占释放一次且无新扣费/核销；没有独立有效的日常售价、适用期及费用凭据，`fallback_no_activity_net`仍为null。这是缺资料的正确拦截，不是杜撰一个备用净利来补分。

**证据保留与运行事实。** 下载评分ZIP保留于用户Downloads，SHA256 `78cb81dddf9f997141735f8db74e9b56a7533ac2895eac5a9cac60121e97b556`；已解压并验证5个文件hash。评分来源的原始证据ZIP SHA256 `e1bcd7089bf5c4572a1baf6825b46cc8bb491e3ea21d52acbebfd21fa4b75988`与PC原件一致。根线程重核{summary['preserved_files']}份原始及既有补验证据未变，独立审计另核49项项目内冻结文件及原门禁源码hash一致。

新题作者未看修复实现或旧题答案；公开输入与金标在活动首轮前冻结。标准化reference为后续整理/导出，其中抖音导出晚于活动首轮完成、菲律宾导出在首轮进行中；均不冒称首轮前生成。活动模型只收到公开题面、原岗位/Skill、协议和冻结规则，没有reference或金标。新题接收验证使用独立作者的复算/审查证据，不证明旧夹具引擎能解析任意新JSON方言。

本轮新增{summary['new_model_calls']}次实际CLI推理：2次新题活动岗、2次同题辅助补验、6次商城接收；实际运行事件均为`gpt-6.1-sol`、`XHigh`、`priority`，MCP和工具调用为0。未操作窗口模型、推理或快速模式设置；后台priority字段不能独立证明窗口蓝色闪电状态。模型推理在线，业务验证为离线合成输入；未连接真实店铺、发布、报名或投放。全部回执仍为`release_status=not_authorized`。

本轮受控范围无执行阻塞。16题样本不能外推为全场景或统计稳定的经营保证。PLUS联合费用、运费口径、配置优先级、分摊尾差和过期后基线等未给资料仍保持未知；真实经营结果未验证。活动Word的PC归档属于原独立事项，本轮未处理。

主要入口：

- [最终机器汇总](../work/compound-ledger-v3-20261111/final-verification-v4/summary.json)
- [原24题覆盖矩阵](../work/compound-ledger-v3-20261111/v4-same-input-regression/coverage-matrix.csv)
- [独立新题覆盖矩阵](../work/compound-ledger-v3-20261111/final-verification-v4/coverage-matrix.csv)
- [实际接收及下游独立复核](../work/compound-ledger-v3-20261111/final-verification-v4/receivers-independent-revalidation.json)
- [PH严格原分7/8](../work/compound-ledger-v3-20261111/independent-author-ph/review/first-run-original-structured-score.json)
- [PH独立语义裁定](../work/compound-ledger-v3-20261111/independent-author-ph/review/first-run-independent-score-semantic-adjudication.json)
- [CN独立首轮评分及差异](../work/compound-ledger-v3-20261111/independent-author-cn/review/first_independent_score/first-score.json)
- [独立代码及证据只读审计](../work/compound-ledger-v3-20261111/final-code-audit.json)
- [最终工程日志](../work/compound-ledger-v3-20261111/engineering-v4-final.log)

报告由 `scripts/render_compound_ledger_closeout_v4.py`读取实际JSON生成；对应机器结果和原答为判定依据。
'''
    for forbidden in ['真实经营已通过','首次全通过验收','所有字段24/24全通过']:
        if forbidden in text:raise ValueError('Unsupported report claim')
    target=project/'docs/商城活动复合盲测整改与泛化离线验收-2026-11-11.md'
    with target.open('x',encoding='utf8',newline='\n') as stream:stream.write(text)
    print(json.dumps({'report':str(target),'sha256':sha(target),'source_summary_sha256':sha(base/'final-verification-v4/summary.json'),'independent_audit_sha256':sha(base/'final-code-audit.json')},ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--project',required=True);a=ap.parse_args();render(a.project)
