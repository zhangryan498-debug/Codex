"""Render the repository report from frozen JSON, with no business inferences."""
import argparse
from pathlib import Path
from live_window_queue import read

def render(root,summary):
    cn=summary['platforms']['CN'];ph=summary['platforms']['PH'];ind=summary['independent_ph']
    def score(value):
        if isinstance(value,dict) and 'passed' in value and 'total' in value:return str(value['passed'])+'/'+str(value['total'])
        return str(value)
    lines=['# 双平台直播逐窗离线开发与独立验收（2026-11-11）','',
    '结论：抖音逐窗离线阶段完成独立闭环后，单独开发并验收 TikTok 菲律宾适配。交付范围是合成数据上的复盘、继续／换品／受控增量提案与安全拦截。下一步可继续项目离线开发；现有证据不支持真实店铺操作或经营收益结论。','',
    '原始首轮、独立金标、机械评分、按公开协议的语义复核与修复后同题回归分别保留，没有用补验覆盖首分。','',
    '## 实际结果','',
    '| 项目 | 抖音 CN | TikTok PH |','|---|---:|---:|',
    '| 独立新题 | 12 | 12 |',
    '| 模型机械首分 | 11/12 | '+score(ind.get('first_model_mechanical'))+' |',
    '| 模型公开协议规范首分 | 12/12 | '+score(ind.get('first_model_semantic'))+' |',
    '| 系统首分 | 11/12 | '+score(ind.get('system_first'))+' |',
    '| 最终模型／系统会计与动作一致 | '+str(cn['host']['agreement'])+'/12 | '+str(ph['host']['agreement'])+'/12 |',
    '| 离线提案／安全拦截 | '+str(cn['host']['offline_proposals'])+' / '+str(cn['host']['blocked'])+' | '+str(ph['host']['offline_proposals'])+' / '+str(ph['host']['blocked'])+' |',
    '| 实际岗位 CLI 首轮批次 | '+str(cn['host']['actual_model_calls'])+' | '+str(ph['host']['actual_model_calls'])+' |','',
    '抖音模型机械首分的一题由独立作者确认：冲突金额候选完整保留在等价字段中，按公开允许的语义复核为通过；原 11/12 文件未改。系统 v1 同题确实缺少冲突候选来源，修复后 v6 同题回归及独立来源复核通过，33/33 候选可追溯。首次系统成绩仍为 11/12。','',
    'PH 模型机械首分的一题把旧 current SKU 完整账放在独立证据中，顶层保留新 target 的 null 基线；身份、时窗、结算口径及全部金额正确。独立作者及只读审计按原公开协议裁定为布局等价。原 strict host 11/12 仍保留；新版只在已验证换品、零增量、目标 null 且旧账全字段吻合时做通用归一化，保留原目标视图和路径哈希，并明确当前汇总是由模型已供窗口账计算。未重跑模型、未把旧 ROI 移给目标、未放松价格门禁。PH 模型、系统及最终回执评分原文在 `independent-reports`。','',
    '## 覆盖与调整','',
    '| 覆盖口径 | CN | PH |','|---|---:|---:|']
    for label,key in [('要求覆盖窗口','expected_windows'),('成熟金额可精确计算的窗口','mature_windows_exact'),('暂估金额可精确计算的窗口','estimated_windows_exact'),('成熟总额精确的题','complete_mature_totals'),('成熟总额未证明的题','unproven_mature_totals')]:lines.append('| '+label+' | '+str(cn['coverage'][key])+' | '+str(ph['coverage'][key])+' |')
    lines+=['', '规范评分通过包含正确保留 null 和拦截缺资料的题，不等于每题都有完整可执行净利。逐题覆盖、成熟／暂估、已知部分与冲突来源在最终 JSON 和独立覆盖矩阵中单列。', '',
    '通用逻辑已加固原始价格 caller 的 SKU／数量／版本／市场绑定、每个已知累计字段的回退检查、成本实际截止、等价时间、重复／重叠窗口、原始冲突事件及其候选来源。价格在每次评估时点重新核既有实际 host；没有新建或放宽价审放行。', '',
    '菲律宾按 PH/PHP 独立适配：confirmed_collected 才可进入成熟链；pending COD／订单额不代替实收，不同暂估 basis 不混算，先核原始事件与身份再筛选口径。PH 履约／现金流须有明确输入。未移植印尼费率、COD-only、拒收比例、固定回款天数或其他站点阈值。相关预审修复在新题冻结与首次推理前完成，原 v1／v2 审计保存。', '',
    '换品要求身份与定价版本核实，清零目标测量基线；增量要求同 SKU／场次／窗口的成熟受控证据，通过所有显式预算和最低边际利润限制。平均 ROI 不构成增量收益证明。', '',
    '## 验证与失败记录','',
    '完整工程回归 '+str(summary['engineering_tests'])+' 项通过；这是工程测试数量，包含各保留版本，不能换算为独立新题数。CN 108 项安全变异、7 项原答绑定变异均通过；PH 的同题变异结果单列于其 evidence JSON。独立审计没有重跑这些变异输入，不将日志复核称为独立新盲测。', '',
    'CN 初次准备封装未适配作者的 JSON list，发生在模型调用前；原目录保留，修正封装后首轮实际为 4 批。host 原答重提取校验曾需补齐队列 normalize 空字段，同样没有新增模型。所有实际首答及系统首分按独立评分文件保留。', '',
    '本轮 8 次实际岗位 CLI 均由日志核实为 gpt-6.1-sol／XHigh，service_tier=priority，MCP=0、tool 事件=0；具体原始绑定在 host receipts 的 actual_runs 中。未操作窗口模型／推理／快速模式控件，CLI 的 priority 日志不能证明客户端闪电 UI 设置，窗口设置未独立实测。', '',
    '## 证据与使用入口','',
    '- 工作流：[直播逐窗离线复盘与换品工作流](直播逐窗离线复盘与换品工作流.md)',
    '- CN 首轮、各版计算与独立闭环：`work/live-window-cn-20261111/`；最终 `host-v6-bound/`。',
    '- PH 首轮、计算与独立闭环：`work/live-window-ph-20261111/`；原 `host-v3-bound/`，最终 `host-v3-closed-bound/`；历次布局／来源加固回执均保留。',
    '- 汇总：`work/live-window-ph-20261111/final-acceptance-summary.json`；交付哈希：同目录 `delivery-manifest.json`。','',
    '此前商城活动和队列交付的 216 与 169 项哈希分别核验一致。原 PH_NEW_104 严格 7/8 与后续语义复核保留：49730 是可复算观察报价，可执行当前账／下界仍为 null，未用新直播题替换该首分。','',
    '边界：仅受控合成离线数据；价格 host 是已知基础设施前提，非本轮新价审盲题。未连接店铺，未自动开播、改价、投放或联络店铺；未验收整场直播排品／母语话术、真实库存同步、平台当前规则、长时并发／断电或真实经营效果。']
    target=root/'docs/双平台直播逐窗离线开发与独立验收-2026-11-11.md'
    with target.open('x',encoding='utf8',newline='\n') as f:f.write('\n'.join(lines)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--project',required=True,type=Path);p.add_argument('--summary',required=True,type=Path);a=p.parse_args();render(a.project.resolve(),read(a.summary))
