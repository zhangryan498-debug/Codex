---
name: tiktok-campaign-ops-playbook
description: TikTok东南亚活动运营的方法论手册，含分站费率核算、达人分层佣金设计、COD履约风控、节庆倒推排期与站点玩法库沉淀规范。当需要做海外活动方案、分站利润测算、佣金策略设计或活动复盘时使用。
---

<!-- CODEX-ADAPTER-BEGIN -->
## Codex 执行适配（2026-11-08）

本段限定下方原方法论在当前项目中的适用范围；保留原正文以便追溯，不把历史政策或阈值当作实时事实。

- 先运行本 Skill 的 `scripts/resolve_project.py`，从 Skill 所在位置向上找到同时含 `runtime-agents/`、`docs/`、`templates/`、`data-pipeline/` 的项目根目录。正文内 `docs/`、`templates/`、`research/`、`data-pipeline/` 等路径均相对于该根目录，不是当前工作目录或 Skill 目录。可给解析脚本传入正文路径取得绝对路径；找不到资源须报告，不能臆造。
- 开始对应任务前读取下方引用的岗位知识、模板与数据渠道说明；需要的项目资源保持在根目录，共享资源不复制成容易失真的独立知识版本。
- 原“零网络／零凭证／完全跑通”等叙述，只能用作冻结快照离线复验的历史上下文。离线复验保持零网络门禁；实际市场研究按用户已授予的在线范围执行，工具和凭证是否可用须现场验证。不得把静态 Skill 安装或离线成功写成真实业务能力全部验证。
- 下方平台税费、评分门槛、履约方式、准入及活动政策均可能过期。实际经营决策前按当前站点官方来源复核日期、适用类目和履约模式；未复核明确待确认。JD 频次、历史比例仅是研究样本。
- 用户当前授权 Just One 与 FastMoss 的在线选品测试；使用授权免费额度，额外付费、外部联系、下单或投放须有相应用户授权。既有授权不重复询问。密钥仅从受保护凭证读取，不写入 Skill、提示词、仓库或报告。
- 使用任务给定数据与可追溯来源交付原岗位成果，缺经营数据则列缺口或用明确标注的演示输入，不能编造成交、利润、转化、供应商或验证记录。
<!-- CODEX-ADAPTER-END -->

# TikTok 东南亚活动运营方法论

本 Skill 面向 TikTok 东南亚出海活动运营场景。

⚠️ **不可套用抖音规则**：抖音的投流税、仅退款、商品卡免佣、爆单首选、积分打榜赛在东南亚不存在，反之亦然。

---

## 1. 岗位定义与考核指标

### 岗位定位
TikTok 出海业务的**营销活动操盘手**，负责站点差异化活动设计、达人机制设计、履约风控与跨站点跨岗位协同。

### 考核指标

| 指标 | JD 频次 | 说明 | 东南亚特有 |
|---|---|---|---|
| **GMV / 销售额** | 11/13 | 「Own GMV, revenue, margin, A&P efficiency」（Haleon 越南）；「为 GMV 负责」（猎聘·上海奉贤） | 须按签收口径折算 |
| **ROAS / ROI** | 9/13 | 「keep spend aligned to ROI and ROAS targets」（aCommerce MY）；「VSA、Spark Ads、GMV Max……管控 CAC、ROAS」 | |
| **转化率 CVR** | 8/13 | 「GMV, revenue, conversion rate, viewer engagement and traffic performance」（Singtel 泰国） | |
| **Affiliate GMV / 达人 ROI** | 7/13 | 「Own the affiliate GMV target」（LUXASIA 泰国）；「Track affiliate performance: GMV, orders, conversion rate, GPM, commission cost」 | 活动主引擎 |
| **活动 ROI / 补贴效率** | 5/13 | 字节官方大促核心指标：**补贴效率**（平台补贴 vs 商家让利的平衡） | |
| **Coupon / 券成本与核销** | 5/13 | aCommerce 把 commission cost 列为独立跟踪指标 | 各站券机制不同 |
| **GPM（每千次曝光成交额）** | 3/13 | aCommerce MY 明确列出——**东南亚市场特有口径，比 GMV 更能反映货架/内容效率** | 与直播 GPM 同一概念 |
| **A&P efficiency（广告与促销投入产出）** | 3/13 | Haleon 越南核心指标之一 | |
| **达人激活率 / 招募达成率** | 4/13 | aCommerce：「设定招募目标、跟踪**激活率**，并对不活跃达人做退出或再激活」 | 招募达成≠带量达成 |
| **大促招选渗透率 / 让利深度 / 供给稳定性** | 2/13 | 字节官方东南亚大促选品口径 | 与选品岗协同 |
| **净 ROI / 活动净利** | — | 扣退货后的真实投产比 | **必须乘签收率** |
| **签收 GMV** | — | 订单 GMV × 实际签收率 | 不可直接用订单 GMV 汇报 |
| **分站点贡献** | — | 印尼/泰国/越南/马来/菲律宾分列 | 不可跨站合并平均 |
| **佣金成本率** | — | 平台 + 达人 + 广告，三轨分拆 | 按站按层不同 |
| **达人参与数与带量效率** | — | 活动主引擎指标 | |
| **签收率 / 拒收率** | — | COD 场景的生命线 | 触阈值直接限流 |
| **现金流覆盖月数** | — | ≥3 个月 | 回款 12–18 天 |
| 复盘报告与 SOP 沉淀 | — | 站点玩法库 | |

**JD 频次说明**：13 篇正文型 JD 中 **GMV 11/13、ROAS/ROI 9/13、CVR 8/13** 三项为东南亚活动岗的标准配置；**补贴效率（平台补贴 vs 商家让利）是字节官方大促岗独有的核心指标**，不在抖音侧活动运营的考核清单里——东南亚大促的让利深度必须自己算清楚，否则会出现"活动做完了、补贴花超了"。

**GPM 为什么在东南亚特别重要**：GMV 受 COD 退货与站点体量影响，跨站、跨活动横向比较会失真；**GPM（每千次曝光成交额）把分母拉回曝光**，是唯一能把印尼活动与马来活动放在同一把尺子上比的效率指标。抖音活动侧通常直接用支付 GMV 与 ROI 即可，**GPM 不是必需项**。

### 硬性任职门槛
- **学历**：本科及以上（海外岗通常高于国内运营岗）
- **年限**：1-3 年（专员）/ 3-5 年（经理）/ 5 年以上（负责人）
- **业绩门槛**：必须有可追溯的 0→1 起盘或规模化增长案例，**要求附数据**
- **工具**：Excel 公式与数据透视表、BI/SQL、TikTok Shop 后台、Affiliate 后台、达人榜单工具
- **第一软性项**：跨部门沟通协调、资源整合、推动项目落地——东南亚版本还要加"跨文化协作"与"时差沟通"
- **额外要求**：目标市场经验、本地语言（印尼语/越南语/泰语/马来语强加分）、熟悉 COD 与本地仓配链路

---

## 2. 分站利润测算规则

用 `templates/活动运营/活动方案模板.md`。**每个站点一张表。**

### 2.1 成本项清单（TikTok）

| 项 | 说明 |
|---|---|
| 预估销量 | **必须乘实际签收率**（COD 印尼 35-45%，综合 8-15%） |
| 商品成本 | |
| 平台佣金 | 按站：印尼 5-8% / 泰国 6.42-9.63% / 越南 5-8%（本土店）⚠️ 需复核 |
| 交易手续费 | 泰国 3.21%（口径需确认） |
| 达人佣金 | 按站按层：印尼 10-20% / 泰国 12-25% / 越南 10-18% |
| 广告佣金 | 若投 Spark Ads |
| 物流 | 单票约 ¥8-12（≤500g） |
| COD 附加费 | 约 $0.3/单 |
| **退货/拒收损耗** | **必须单独一行。退货不退佣金与平台费** |
| 认证与本地化摊销 | 按销量摊 |
| 其他 | 客服、样品 |

### 2.2 准入标准
- **净利率 <15% → 重新设计**
- **真实单均净利 = 单件净利 × 实际签收率** —— 这才是真实利润
- **必须做敏感性分析**（至少七项）：退货率 +5pct、达人佣金 +3pct、平台佣金 +1pct、物流 +¥1/单、售价 -10%、汇率 -5%、COD 附加费取消
- **必须算盈亏平衡点**：退货率到 ____% 时净利归零 / 佣金到 ____% 时归零 / 售价到 ____ 时归零

### 2.3 分站费率速查（⚠️ 行业参考值，需复核当前值）

| 维度 | 印尼 | 泰国 | 越南 |
|---|---|---|---|
| 平台佣金（本土店） | 5–8% | 6.42–9.63% | 5–8% |
| 达人佣金区间 | 10–20% | 12–25% | 10–18% |
| 客单价 | $5–15 | $10–30 | $8–20 |
| 备注 | COD 占比最高；低客单扛不住高佣金比例 | 佣金绝对值高 | — |

**关键推论：印尼客单价最低但佣金比例不低 → 印尼卖家承受的成本压力更大 → 印尼达人反而常要求更高佣金比例。不能把泰国逻辑套到印尼。**

---

## 3. 达人机制设计（活动的核心杠杆）

### 3.1 分层佣金设计
| 层级 | 粉丝量 | 佣金参考 | 特点 |
|---|---|---|---|
| 头部 | >50 万 | 20–25% | 量大、议价能力强、需广告佣金补偿 |
| 中腰部 | 5–50 万 | 12–18% | 出单主力，性价比最优 |
| 尾部 | 1–5 万 | 10–15% | 成本低但需样品通过率支撑 |

**佣金超过 25% 在低客单市场基本不赚钱。**

### 3.2 基础佣金 + 广告佣金分离
这是东南亚低成本放量的关键设计：
- **基础佣金**：按层级设置，作为达人保底
- **广告佣金**：走 Spark Ads 时作为放大达人内容的对价
- **分离的好处**：预算可控、达人愿意投优质内容、我们能选择放大真正跑量的内容

### 3.3 达人链路倒推
```
寄样 → 样品审核 3-5 天 → 合作确认 → 72 小时内排直播/发布
```
**本地节庆提前 6–8 周启动整条链路。**

### 3.4 达人 ROI 评估
达人视频转化率 1%–3%（播放 1 万出 5 单即说明受众不匹配）。**低佣金 + 高退货的达人组合是负 ROI**，要看净签收 ROI 而非表面 ROI。

---

## 4. COD 履约风控（活动方案的必填项）

### 4.1 平台红线

| 指标 | 阈值 | 后果 |
|---|---|---|
| 拒收率 | 连续 7 天 >45% | 限制商品曝光 |
| 拒收率 | 单日 >30% 持续 2 天 | 自动暂停推流 72 小时 |
| 签收率 | 连续两周 <60% | 限制短视频挂车功能 |
| 发货仓 | 非本地认证仓 | 虚假发货，3 倍罚款最高 $500 |
| 虚假发货 | 连续 3 单 | 冻结账户，已结佣不退 |
| 物流状态 | 48 小时无更新 | 判定虚假发货，扣分至停权 |
| 时效 | 超 3–7 天 | 自动判罚赔付订单金额 10% |

### 4.2 活动方案的履约必填项
- 备货量（**按下单量备，不是按签收量备**）+ ≥15 天销量预估
- 本地仓发货能力确认（含 COD 认证仓资质）
- 拒收率监控节奏与触发动作
- COD 话术与内容要求（转交编导、直播、美工）
- 现金流安排（覆盖 ≥3 个月）
- 拒收率上升时的降级预案（降预算、暂停推流、改预付引导）

---

## 5. 活动类型与本地节庆

### 平台活动
Flash Sale / Coupon-Voucher / Affiliate 活动 / 站内直播活动 / 站点专属大促

### 本地节庆（最大节点）
| 节庆 | 说明 |
|---|---|
| 斋月、开斋节 | 东南亚最大节点，提前 6–8 周 |
| 泼水节 | 泰国等 |
| 双九、双十 | 电商大促 |
| 圣诞 | 年末 |

**节庆活动必须提前 6–8 周启动**：达人排期 + 样品寄送 + 本地化物料准备。晚于这个窗口，物料和达人档期都来不及。

---

## 6. 归因顺序（复盘时按此走，分站点）

```
活动结果不及预期
│
├─ 1. 站点与货盘层：选对站、选对货了吗？
│   ├─ 客单价低于类目基准 → 选品问题
│   ├─ 佣金率高于类目基准 → 佣金设太高
│   └─ 认证/资质卡住 → 上架前置不足
│
├─ 2. 履约与支付层（★胜负手，常被跳过）
│   ├─ 签收率低 → 查 COD 占比/描述不符/尺码不合/时效超 3-7 天
│   ├─ 拒收率 >45% → 触发限曝光，立即处置
│   └─ 现金流覆盖 <3 个月 → 爆量前必须解决
│
├─ 3. 内容与本地化层：讲得清吗？
│   ├─ 四层本地化是否到位
│   └─ 限流？批量克隆脚本？
│
├─ 4. 达人分销层：带得动吗？
│   ├─ 寄样多但出单少 → 佣金设太低或样品不匹配
│   ├─ 转化率 <1% → 受众不匹配，加佣金无用，该换达人
│   └─ 头部不出量 → 佣金未达预期
│
├─ 5. 投放效率层：赚得回吗？
│   ├─ 表面 ROI 达标但净 ROI 低 → 退货未扣
│   └─ 消耗不动 → 素材量不足/审核不过
│
└─ 6. 售后与退款层（★常见主因，常被排到最后）
    ├─ 退货集中某 SKU → 规格/描述/尺码
    └─ 退货集中某达人 → 该达人内容有预期落差
```

**归因纪律**：东南亚场景下第 2 层和第 6 层是最高频的真实主因。**走树时这两层花的时间不少于其他层。**

---

## 7. 站点玩法库沉淀

出海业务最宝贵的资产。**每个站点单独记录**：

| 站点 | 跑通过的玩法 | 未跑通的玩法 | 佣金结构 | 达人生态特征 | 退货率水平 | 备注 |
|---|---|---|---|---|---|---|
| 印尼 | | | | | | |
| 泰国 | | | | | | |
| 越南 | | | | | | |

**为什么必须分站记录**：同一套玩法在印尼和泰国可能结果相反。合并记录会让最宝贵的信息消失。

---

## 8. 常见错误与雷区

| 错误 | 后果 | 正确做法 |
|---|---|---|
| **不乘签收率算利润** | 表面盈利实则亏损 | 一律用签收 GMV 与净 ROI |
| **跨站合并测算** | 掩盖站点差异，误配资源 | 分站分表 |
| **跨站套用佣金逻辑** | 印尼站佣金设崩 | 按站按当前费率核算 |
| **引用旧费率** | 2026 已多次上调，测算全错 | 标注时点并注明需复核 |
| **不设盈亏平衡点** | 不知道风险边界在哪 | 算三个维度的平衡点 |
| **佣金一刀切** | 头部嫌少、尾部不出量 | 按层设计 + 基础/广告佣金分离 |
| **佣金设太高不核算** | 低客单下 >25% 不赚钱 | 建联前先算利润线 |
| **不预估退货损耗** | 活动越大亏得越多 | 单独一行 + 敏感性分析 |
| **忽视拒收率阈值** | 限曝光，账号权重受损 | 活动方案内建监控与预案 |
| **用国内仓发 COD** | 3 倍罚款最高 $500 | 只从本地认证仓发货 |
| **爆量前不看现金流** | 回款断裂 | 覆盖 ≥3 个月，不足则降节奏 |
| **节庆没提前 6-8 周** | 物料与达人档期来不及 | 倒推排期 |
| **寄样与审核周期没留够** | 达人错过排期 | 审核 3-5 天 + 72 小时内排期 |
| **不沉淀站点玩法库** | 每站都从零开始 | 分站记录 |
| **直接搬运抖音打法** | 前置条件不成立 | 逐条过转译表 |

---

## 9. 平台差异提示（东南亚专属，不可外传）

| 东南亚特有 | 说明 |
|---|---|
| **分站费率结构** | 印尼/泰国/越南佣金与客单价完全不同 |
| **COD 占约 68%（印尼）** | 退货 35-45%，预付 12% |
| **签收率/拒收率阈值** | 限曝光、限挂车、暂停推流 72 小时 |
| **本地认证仓强制** | COD 必须本地仓发货，3 倍罚款最高 $500 |
| **本地仓时效 3-7 天** | 超时赔付 10% |
| **回款 12-18 天** | 现金流需 ≥3 个月缓冲 |
| **基础佣金+广告佣金分离** | 东南亚低成本放量关键 |
| **本地节庆** | 斋月/开斋节等，提前 6-8 周 |
| **达人链路倒推** | 寄样→审核 3-5 天→72 小时内排期 |
| **Flash Sale / Affiliate 活动** | 平台活动形态与抖音不同 |
| **四层本地化** | 物料与达人沟通的基础要求 |

---

## 10. 数据与规则的时效性规范

- **费率与佣金**：东南亚站点费率 2026 年已多次上调，**每次输出必须标注来源与时点并注明需复核**。
- **退货率、拒收率、签收率**：属行业实操区间，标注为区间参考值。
- **站点数据不可跨站套用**：任何结论必须限定站点。
- **数据通道权威源**是项目 `docs/knowledge/data-channel.md`；取数走项目 `data-pipeline/` 下的采集器。**抖音侧零凭据通道已完全跑通**：热榜（`douyin_public.py`）+ 推荐流/话题真实体量/创作者画像/作品详情/评论（`douyin_feed.py`，推荐流自举，不需要种子 ID、不需要登录、不需要分享链接）。TikTok 侧采集器代码就绪，需 API token。外部数据只用于趋势与竞品判断，**经营口径（结算/真实退货/实收）必须来自店铺后台**。
- **取数三条纪律**：① L0 热榜的 `video_count` / `discuss_video_count` **不是话题体量**，不可当量级证据，只能用于词条间相对热度排序；② 话题真实体量看 `challenge/detail` 的 `view_count` / `user_count`；③ 抖音搜索端点需登录，**「热榜词 → 话题 cid」目前无免登录通道**，垂类批量采样需托管 API 或用户给一条分享链接作种子。
- **平台知识库权威源**是项目 `docs/knowledge/platform-model.md`；与最新公告冲突以公告为准并立即更新。
- **不确定判断标注置信度**，说明需要谁去验证。

## Offline activity price floor protocol (2026-11-11)
For offline activity pricing or a price-floor handoff, read docs/campaign-price-floor-offline.md and docs/campaign-price-handoff.md. Keep complete synthetic review decisions separate from real execution authorization. Current task-defined floor basis/scope and versioned rules take precedence over historical percentage examples; unknown costs, funding, periods and conflicting combinations never become zero or automatic approval. Preserve the source-linked buyer/merchant/fee ledger and per-SKU floor checks. A structured shop-ops acceptance must additionally pass the independently invoked price-floor gate with trusted caller-frozen calculation evidence and actual-run binding. Model self-certification is not that evidence. Do not change model/fast settings or call live business systems for an offline test.

Structured JSON amounts are mandatory ledger data: mirror every material calculated merchant discount, merchant-funded coupon cost, confirmed reimbursement, fee and basis in the caller's numbers/amounts object using its documented field names. A correct number in prose alone does not satisfy a structured field. Preserve unknown as null or pending, never fill it with zero.
Keep decision and price-proof status separate. Forbidden, expired or out-of-scope requests remain rejected; missing data within a supported request stays pending. Do not turn a counterfactual expired price into an actual valid settlement net, or weaken an existing stop to align labels. Preserve separate currencies when the role does not support portfolio conversion.

### Known baseline and counterfactual branches
For a rejected/expired/uncertain configuration, continue every explicitly supported and fully calculable baseline branch as a separately labelled net amount, not just a list/base price. Retain current actual net, no-activity or flash-only baseline net, and hypothetical/counterfactual net as distinct fields when applicable. Use the caller's fields such as fallback_no_activity_net or fallback_flash_only_net; a base_price field is not an equivalent net field. Compute each net from that branch's buyer payment, confirmed reimbursement and explicit costs. Do not invent missing baseline eligibility, costs or rounding.
A safe baseline is only an alternative calculation, not a change already made and not acceptance of the rejected original request. Actual net for an expired/forbidden configuration stays null and its decision stays REJECT. Use PASS/FAIL for a valid applicable floor comparison; use UNPROVEN for a comparison affected by missing data; use NOT_APPLICABLE when the submitted configuration is invalid and produces no valid activity price, or no floor applies. Out-of-role portfolio conversion remains rejected with its unsupported floor unproven; never begin currency mixing to improve a label.
