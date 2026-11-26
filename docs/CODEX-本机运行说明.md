# Windows / Codex 离线运行说明

本目录是交接包的独立开发副本。原解压副本在同级 `../handoff-2026-11-07/` 中保留，不随开发改动。

## 运行

需要 Python 3.12+，离线报告链路只使用标准库，不需要 pip 安装。Windows 在项目根运行：

```powershell
./run_offline.ps1
```

入口优先使用 `DT_PYTHON` 指定的解释器，其次寻找项目虚拟环境、Codex 本机随附解释器和 PATH 中的 Python（排除 WindowsApps 占位别名）。当前已找到 Python 3.12.14。脚本不安装或更改系统 Python。

其他环境或已选好 Python 时：

```text
python -X utf8 scripts/offline_pipeline.py
```

流程依次执行规则自测、全部统计与渲染门禁、快照质量审计、重复整条报告链路，再核对 docs 下所有 Markdown/JSON 的字节哈希和全部冻结快照文件。统计与渲染在开发副本中落盘。子 Python 进程注入网络阻断钩子，不调用采集器。

输出固定落在 `work/offline-runs/latest/`，每次覆盖本次摘要与日志；如需保留多次结果，可指定项目 work 内的新目录：

```text
python -X utf8 scripts/offline_pipeline.py --output-dir work/offline-runs/run-002
```

## 状态与退出码

| 退出码 | 状态 | 含义 |
|---|---|---|
| 0 | passed | 门禁、审计、幂等和冻结输入检查全部通过 |
| 2 | known_data_limits | 报告门禁通过；审计只存在下列已登记的三项空数据集；不是全绿 |
| 1 | failed | 发生其他失败、超时、产物缺失、复跑不一致或冻结输入变化 |

三项已登记限制为 `P1_hashtag_detail`、`P1_hashtag_video_list`、`P1_explore_post`，严格对应审计中的 `Q-<dataset>-rows`。原审计保持失败退出码，编排层单独识别，并保留失败明细。任何新增失败都不能按已知限制放行。

结果以 `summary.json` 和分阶段日志为准。快照审计自身可独立运行，保持 98/101 的历史事实；不存在“所有快照质量检查全绿”的声明。

## 本轮迁移内容

- 利润模型输出改为脚本旁的 JSON，移除 Mac 绝对路径。
- 进度统计默认核对包内 `runtime-agents/`，可用 `DT_RUNTIME_AGENTS` 指定其他副本。缺目录或不足 20 份明确失败；这只是文件一致性核对，不表示 Agent 已注册到 Codex。
- 门禁使用 Python 时间函数，子进程显式 UTF-8；生成文件统一 UTF-8/LF，路径元数据尽量使用项目相对路径和 `/`。
- 幂等检查覆盖 docs 全部 Markdown/JSON，包含报告目录之外的项目进度汇报，并检测新增与删除文件。
- 审计 `--json` 参数使用实际传入路径，避免结果写回冻结快照目录。
- 本地 Git 基线忽略原始采集数据、凭据、工作日志和上游仓库；冻结快照按原字节保存，不自动转换换行。

测试命令：

```text
python -X utf8 -m unittest discover -s scripts -p test_offline_pipeline.py -v
```

## 尚未解决的事项

联网采集、MiniMax 到 Codex 的岗位运行与任务调度、CI、剩余报告的自动渲染，以及真实商家经营验证仍待推进。交接包未附上游第三方库或凭据，不能从离线验证结果推断采集已恢复。

概述称 13 个渲染器，包内实际为 12 个，缺少概述末尾提到的概述渲染器；当前不凭空重建或改写历史数量。费用记录依旧未对账，不以假定金额修平差额。签收率和费率即使补齐，也仍需成本、结算、售后及投放实数才能支持真实利润与 ROI。

下一个经营数据接入步骤见 `docs/店铺数据准备清单.md`。优先从一个实际经营站点、一个店铺、少量真实 SKU 开始。
