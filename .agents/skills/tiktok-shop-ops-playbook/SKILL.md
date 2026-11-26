---
name: tiktok-shop-ops-playbook
description: TikTok东南亚商品页与listing运营的方法论手册，含本地化商品页规范、搜索标签与hashtag分工、达可见门槛监控、COD说明与退货率归因方法。当需要做listing本地化、商品页优化、标签策略或承接效率诊断时使用。
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

# TikTok 东南亚货架商城运营方法论

本 Skill 面向 TikTok 东南亚商品页/listing 运营。

⚠️ **不可套用抖音规则**：抖音的"搜索结果页定坑""搜索转化率约为推荐 12 倍""商品信息质量分""积分打榜赛/商品卡免佣"在东南亚不成立。

---

## 1. 岗位定义与核心认知

### 岗位定位
TikTok 出海业务的**商品页承接者**。核心任务不是"做搜索流量"，而是：

```
内容（短视频/直播/达人）带来流量 → 商品页接住 → 转化 → 且不退货
```

### 与抖音货架运营的根本差别

| | 抖音货架运营 | TikTok 商品页运营 |
|---|---|---|
| 货架地位 | GMV 占 40–50%，需重点经营 | **心智未建立，曝光主要靠内容引流** |
| 搜索价值 | 日均 4 亿次、转化率约为推荐 12 倍 | 权重弱；**作用是承接内容带来的主动搜索** |
| 核心抓手 | 搜索词 + 定坑 + 商品卡免佣等资源 | **本地化 + 承接效率 + 不退货** |
| 硬门槛 | 商品信息质量分 ≥85 | **商品评分 ≥4.3 + 近30天售出 ≥50件（达人可见）** |
| 退货影响 | 仅退款损耗 | **COD 退货 35–45%，退货不退佣金与平台费** |
| 平台资源 | 免佣/打榜赛/降佣等需申领 | 认证/站点活动，机制不同 |

### 考核指标

下表频次统计自 `research/jd/tiktok-shop-ops.md` 的 **13 篇正文型 JD**（按正文出现频次，非搜索摘要）；「东南亚特有」列来自 `docs/knowledge/platform-model.md`。

| 指标 | JD 频次 | 说明 | 东南亚特有 |
|---|---|---|---|
| **GMV / 销售额** | 12/13 | 「保障店铺 GMV、转化率等核心指标达成」「对店铺的 GMV 和健康负责」 | 须按签收口径折算 |
| **转化率 CVR** | 12/13 | 「对店铺数据负责，包括流量、点击率、转化率等，并形成报表」 | |
| **曝光量 / 点击率 CTR** | 10/13 | 「提高店铺入店流量，增大点击率、浏览量以及转化率」 | 曝光主要来自内容而非搜索 |
| **客单价 AOV** | 8/13 | 「跟踪销售额、客单价、转化率、广告花费、ROAS、退款退货率及利润」 | |
| **店铺评分 / 账号健康度 / 纠纷率** | 8/13 | 「确保店铺指标符合平台规范……降低纠纷率，维护店铺评分」 | 评分 ≥4.3 是达人可见前置，纠纷率直接影响曝光 |
| **退款退货率** | 7/13 | 「结合退款、商品成本和营销费用评估实际收益」 | **COD 退货 35–45%，且退货不退佣金与平台费** |
| **广告 ROAS / 成交成本 / CAC** | 7/13 | 「跟踪广告消耗、点击率、点击成本、成交成本及 ROAS」 | 与投手共担，**须扣退货** |
| **库存周转 / 断货率 / 滞销率** | 7/13 | 「确保库存健康运转，防止断货或滞销」 | 断货直接影响推荐权重与转化 |
| **店铺权重（平台侧分发指标）** | 5/13 | 「落实精细化运营标准，提升商品转化与**店铺权重**」 | 抖音侧对应的是商品信息质量分，**不是同一套权重逻辑** |
| **自然流量排名（商城入口）** | 4/13 | 「提高店铺 listing 在商城自然流量的排名」 | 东南亚商城心智未建立，自然流量占比低 |
| **达人分佣效率 / 单品打爆成功率** | 4/13 | 「提升单品打爆成功率」 | |
| **SPS（单均销售额）** | 1/13 | 猎聘·TK 主管：「对 GMV、ROI、SPS 等指标负责」 | |
| **净 ROI / 达人 Affiliate GMV** | — | 商品页承接联盟流量的转化效率 | |

**与抖音货架运营考核结构的差异**：抖音侧货架运营背 **GMV + 转化率 + 客单价 + 复购率**，并额外背**店铺体验分 / DSR**；TikTok 侧背 **GMV + CVR + CTR + AOV**，但把 **店铺评分、纠纷率、店铺权重** 三项平台健康度指标提到了 8/13 与 5/13 的高频位——**在 TikTok，商品页运营不只是转化角色，还是账号健康度的第一责任人**，因为评分掉到 4.3 以下会直接让商品对所有达人不可见。

---

## 2. 本地化商品页规范

### 2.1 四层本地化（商品页同样适用）

| 层 | 商品页具体要求 |
|---|---|
| **语言** | 标题、卖点、详情全部本地语言，**母语者校对**；术语用当地消费者实际说法 |
| **货币与度量衡** | 已换算并注明汇率口径与时点；尺码重量用本地常用单位 |
| **场景与模特** | 场景图必须是本地真实生活场景；模特审美本地化 |
| **文化符号** | 无中国特有元素；节庆/称呼符合当地语境 |

**注意：只翻译文案 = 只做了第 1 层，商品页在东南亚仍然会输。**

### 2.2 定价不是换算
印尼客单价 $5–15、泰国 $10–30、越南 $8–20。**同一商品在不同站的定价要重新设计，不是简单换算。** 商品页呈现的价格必须符合当地价格带认知。

---

## 3. 搜索词与 hashtag 分工

### 3.1 两者作用不同
| | 搜索词 | hashtag |
|---|---|---|
| 作用 | 承接内容带来的主动搜索 | 内容侧分发 |
| 归属 | 商品页（listing 标题/卖点） | 内容（编导发布时） |
| 本地化要求 | 母语者校对，**直译词几乎无搜索量** | 选本地流行趋势，**硬翻译 tag 基本无效** |
| 协同 | 需与编导对齐同一套本地关键词 | 同上 |

### 3.2 关键词库

【用 `templates/货架商城运营/搜索词与标题矩阵表.md` 落词表与标题矩阵，TikTok 侧须标校对人】
TikTok 侧词库仍分七类（品类/场景/需求/卖点/营销/人群/竞品），但**必须标注校对人**。

**本地语言词库里最常见的失败是：中文词直译过去，本地人根本不会这样搜。** 判断标准是本地人会不会这样搜。

---

## 4. 商品页必备信息模块

【商品卡体检用 `templates/货架商城运营/商品卡优化清单.md`，TikTok 侧补 COD 说明、本地认证展示与本地退换政策】

| 模块 | 东南亚要求 |
|---|---|
| **尺码对照** | 真人上身 + 尺寸表 + 模特参数；规格通用度说明 |
| **COD 说明** | 明确"仅支持货到付款"，坦诚说明拒收后果 |
| **本地退换政策** | TikTok 本地客服渠道与政策 |
| **发货时效** | 本地仓 3–7 天，与实际一致 |
| **类目认证** | 化妆品/食品/电器展示资质位（印尼含清真认证） |
| **实物展示** | 降低预期落差 → 降退货 |

**这些模块不是"合规要求"，是"退货控制手段"。** 印尼 COD 退货 35%–45%，退货不退佣金与平台费——商品页写清楚与不写清楚，差别是净利。

---

## 5. 达可见门槛监控（连锁风险的起点）

```
商品评分 <4.3  或  近 30 天售出 <50 件
        ↓
达人在精选联盟看不到这个品
        ↓
无法挂车带货
        ↓
内容链路失效 → 没有内容带来流量
        ↓
商品页无流量 → 销量不涨 → 评分与销量继续不达标
        ↓
恶性循环
```

**所以这个门槛必须主动盯，不是等投流时才发现。** 提升评分与销量的动作（引导真实评价、优化商品页转化）本身就是商品页运营的职责。

---

## 6. 承接效率诊断

用 `templates/货架商城运营/货架流量诊断表.md`。**TikTok 侧流量结构按五源拆分**：

| 来源 | 说明 |
|---|---|
| 推荐流挂车 | 内容推荐直接挂车 |
| 商品页直接访问 | 用户主动访问店铺/商品 |
| 达人挂车（联盟） | 本地达人带货 |
| 搜索 / hashtag | 承接主动搜索 |
| 直播挂车 | 直播间 |

### 诊断路径
```
曝光低
  → 内容侧问题（转编导）：素材/钩子/hashtag
点击低
  → 商品页首图与价格感知（转美工）+ 本地化表达
转化低
  → 商品页疑虑未解答（尺码/COD/认证/时效/本地退换）（转美工）
  → 或人群不匹配（转投手）
转化正常但退货高
  → 商品页预期管理不足：缺尺码对照、缺实物展示、缺 COD 说明、描述与实物不符
```

**退货率归因是 TikTok 侧独有的诊断层，抖音侧没有这一层。**

---

## 7. 常见错误与雷区

| 错误 | 后果 | 正确做法 |
|---|---|---|
| **套用抖音搜索运营** | 权重错配，投入错方向 | 聚焦内容承接效率 |
| **直译本地关键词** | 无搜索量 | 母语者校对 |
| **硬翻译 hashtag** | 无效流量 | 选本地流行趋势 |
| **只翻译文案不做四层本地化** | 点击率起不来 | 逐层核验 |
| **商品页不写 COD 说明** | 拒收率飙升 | 明示支付方式与拒收后果 |
| **不提供尺码对照** | 退货率飙升 | 真人上身 + 尺寸表 |
| **不展示类目认证** | 下架或扣分 | 预留展示位 |
| **时效承诺超实际** | 超时赔付 10% | 与 3–7 天一致 |
| **不监控达可见门槛** | 恶性循环 | 主动盯评分与销量 |
| **忽略退货率归因** | 治标不治本 | 退货集中原因要定位到 SKU/达人 |
| **定价只做换算** | 无价格竞争力 | 按站重新定价 |
| **描述与实物不符** | 投诉仲裁，赔付可达订单金额 150% | 实物展示 + 描述一致 |
| **不沉淀本地化模板** | 每站从零开始 | 分站点分语种分类目沉淀 |

---

## 8. 平台差异提示（东南亚专属，不可外传）

| 东南亚特有 | 说明 |
|---|---|
| **货架心智未建立** | 曝光靠内容引流 |
| **hashtag 主导标签** | 本地流行趋势 |
| **四层本地化** | 语言/货币/场景/文化符号 |
| **达可见门槛** | 评分 ≥4.3 + 近 30 天售出 ≥50 件 |
| **COD 说明必需** | 明示支付方式与拒收后果 |
| **尺码对照为必备** | 降退货核心手段 |
| **类目认证前置** | 印尼含清真认证 |
| **本地仓 3–7 天** | 超时赔付 10% |
| **按站重新定价** | 客单价差异巨大 |
| **退货率归因** | TikTok 独有的诊断层 |

---

## 9. 数据与规则的时效性规范

- **类目认证要求、平台商品页规则、标签机制**：变动频繁，标注来源与时点，注明需复核。
- **本地流行趋势与 hashtag**：时效性极强，发布前重新确认。
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
