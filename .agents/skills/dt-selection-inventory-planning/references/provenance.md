# 方法来源、取舍与许可

本 Skill 是D&T原选品岗位的规划补充。来源审阅于2026-11-13，仅吸收方法，未运行上游脚本、未引入其默认参数/工具链；没有模型训练、实盘连接、供应商外联或采购执行。

## 固定上游快照

- 仓库：[g305595965/cn-ecommerce-ops](https://github.com/g305595965/cn-ecommerce-ops)
- 固定commit：`be160c284364d1031c067265fa1cf81bd666efc9`
- 主入口：[固定SKILL.md](https://github.com/g305595965/cn-ecommerce-ops/blob/be160c284364d1031c067265fa1cf81bd666efc9/SKILL.md)
- 方法源码：同commit的`scripts/product_score.py`、`inventory.py`、`pricing.py`、`live.py`、`ad_calc.py`；均只读核对。快照为有限文件集，未包含上游引用的`references/`和`platform_fees.py`，未补抓，不声称完整仓库或脚本可运行性验收。
- 许可为MIT。根目录[LICENSE](../LICENSE)逐字节保留上游`Copyright (c) 2026 g305595965`和完整许可/免责文本。

| 固定文件 | 快照SHA-256 |
|---|---|
| LICENSE | `59c0669ecd8846bcdd8d0a3d715d499e0995d3ccb6982c2e7282935a2170f209` |
| SKILL.md | `00e192ecd6b8ce9e2221613890513b1c34d3eaec3fc762f0f61e6a800d84afaa` |
| scripts/product_score.py | `4f35592e51ba18bd3e6d1d503c187728b31d117f539b6a139d5a273838d29525` |
| scripts/inventory.py | `d486dda2a0fc8f291039d1f0f2d50d8bb420f9289c828e26c1bcb308c6ef79ed` |
| scripts/pricing.py | `ea0e1567c6b4455e319f961128d5606ea1a350da9d683b95d169b18941399988` |
| scripts/live.py | `e3404f1690d3aa738c53697916cf7c6a10074c08244e3181d52d9c27ccdddd1a` |
| scripts/ad_calc.py | `cc4acf5c46876aa1f8fed803911fe7153aa17cea9ad5f5222b1cab1ce05c4798` |

## 方法适配

| 来源做法 | 本Skill保留与修正 |
|---|---|
| 六维选品、原始数据到分数 | 保留分维证据与短板识别；不继承内置权重、分段映射、行业阈值或总分放行。先核硬门禁，缺项不补0/均值 |
| 覆盖周期、补货缺口、MOQ与资金测算 | 保留明确情景下的算术；现货扣预占和不可售，在途按归属/日期/状态分时使用；另报到货前缺口 |
| 固定7天安全期、90天滞销、日需求须大于0 | 不继承默认阈值；输入须有来源，零需求合法，不自动清仓 |
| 在库/在途/成本缺省0、资金=量×成本 | 缺失保留未知；补固定与按量支出、单位/币种/FX与成本完整性；明确0有据可用 |
| 利润/保本价/ROI与实时源桥接 | 吸收先核口径和数据日期；不运行脚本、不用内置费率或默认退款返佣；不用保本价round或平均ROI放行；价格交原门禁 |
| 合规关键词与固定平台规则 | 不引入词库或“命中即违法”；具体上下文/资质/日期由原责任岗核实 |

## D&T去重复用

本次完整阅读原`.agents/skills/douyin-selection-playbook/SKILL.md`、`tiktok-selection-playbook/SKILL.md`与项目`AGENTS.md`；以`docs/双平台选品数据使用规范.md`、`docs/选品供应商搜集与筛选规范.md`为数据及供应商口径，读取原选品评估、利润测算、测品计划三模板后仅引用不复制。原岗位继续负责候选、平台专项、供应商匹配和测品交接，本Skill集中新增库存时点、资金完整性与约束情景。

公开建设范围为本轮`public-inventory-scope-v1.md`；上游固定文件依据本轮`upstream-source-manifest.json`。实现不读取本轮题库、私有预期或执行答卷，也不据此调参。Skill可用性、独立行为验收与真实经营效果须分开陈述。
