---
name: douyin-shop-ops-playbook
description: 抖音货架商城运营的方法论手册，含货架口径拆分规则、搜索词布局方法、商品卡五项红线、扶持资源申领清单与漏斗诊断流程。当需要做商品卡优化、关键词布局、货架流量诊断或资源申领时使用。
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

### 货架异常先核可比性

遇到商品卡、搜索或推荐漏斗异常时，先用 `docs/douyin-commerce-diagnostic.md` 核范围和分母，再填现有货架流量诊断表。按同 SKU、入口与有效窗口区分曝光、点击、支付和售后，优先选可逆的单项验证。入池原因、评分门槛和平台建议价须以当前适用证据为依据；活动和价格继续走原价审，诊断不替代已有门禁。普通商品卡文案或排期沿用原流程。

# 抖音货架商城运营方法论

本 Skill 面向抖音货架/商城场运营。**TikTok 侧货架心智未建立、搜索权重弱，本手册的搜索运营方法不可直接套用。**

---

## 1. 岗位定义与核心认知

### 岗位定位
抖音电商**货架与商城场的运营者**。产出物是高质量商品卡、有效的搜索词布局、 diagnosed 的流量漏斗和已申领的平台资源。

### 考核指标

下表归纳自 `research/jd/douyin-shelf-ops.md`（27 篇正文型 JD 的原文归类）；标注「归属」的列区分结果指标、转化效率、流量效率与平台健康度——**四类都要背，但优先级不同**。

| 指标 | JD 原文归类 | 归属 |
|---|---|---|
| **GMV / 成交 / 销售额** | 「对店铺 GMV、转化率、客单价、复购率负责」「达成商城销售目标，促进 GMV 增长」 | 结果指标 |
| **转化率 / 客单价 / 复购率** | 「统筹店铺货架场景、商城推荐、搜索流量、自然出单整体运营」 | 转化效率 |
| **UV 价值 / 搜索流量 / 访客** | 「每日监控店铺后台核心数据：访客、搜索流量、成交、UV 价值、退款率、店铺体验分」 | 流量效率 |
| **曝光 / 点击率 / 点击转化** | 「提升店铺的曝光和转化」「提升商品点击率与下单转化」 | 商品卡流量指标 |
| **动销率 / 库存周转** | 「监控商城流量、成交、动销数据」 | 商品侧配套指标 |
| **店铺体验分 / DSR / 退款率 / 品退** | 「持续维护店铺体验分，实时监控评价、品退、售后风险」「对店铺 DSR 各项评分指标负责」 | 平台健康度（权重） |
| **投产比 ROI** | 「独立操作小店随心推、千川商品卡投放，自主搭建推广计划」 | 投放侧 |
| **毛利 / 折扣 / 活动盈亏** | 「自主提报活动资源、核算活动盈亏」 | 利润 |
| **粉丝 / 互动 / 播放量** | 「拆解 GMV、ROI、粉丝、转化率、复购率等核心指标」 | 辅助指标 |

**与 TikTok 货架岗的考核结构差异（不可互换）**：抖音侧货架运营背 **GMV + 转化率 + 客单价 + 复购率**，健康度抓手是**店铺体验分 / DSR**；TikTok 侧背 **GMV + CVR + CTR + AOV**，健康度抓手是**商品评分（≥4.3 才达可见）+ 纠纷率 + 店铺权重**。抖音侧没有"评分掉线就全网达人不可见"这种硬连锁。

### 核心认知（这个岗位存在的理由）
**抖音的货架 GMV 有"口径幻象"。**

```
大货架 GMV 占 40%-50%
  ↓ 拆解①
商品卡（纯图文商品页成交，占大货架近一半）
  + 非商品卡（内容场引流至货架完成）
  ↓ 拆解②
主动搜索下单 仅占大盘约 10%（2025Q3；2025年前仅 7%）
```

**这意味着：如果你把竞品或自己的"货架 GMV 占比"直接拿来比较，会系统性高估。** 大部分所谓"货架增长"其实是内容场的流。

同时要理解平台方的动机：**抖音电商内容加载率有上限**（刷 100 个视频约见 9–12 条电商内容），继续加内容会伤体验、伤流量。**所以平台在给货架找新容器**——这既解释了为什么货架资源多，也说明这些资源是"分配来的"，必须主动申领。

---

## 2. 搜索运营方法（抖音货架的核心杠杆）

### 2.1 搜索的三个事实
| 事实 | 数值 | 含义 |
|---|---|---|
| 日均搜索量 | 突破 4 亿 | 已成中国第二大搜索引擎 |
| 搜索转化率 | **约为推荐流量的 12 倍** | 高客单、定制、2B 尤其明显 |
| 主动搜索成交占比 | 约 10%（剔除内容导流后） | 占比低但转化率高 → 值得专门做 |

### 2.2 七类关键词

【用 `templates/货架商城运营/搜索词与标题矩阵表.md` 落词表与标题矩阵】
| 词类 | 说明 | 作用 |
|---|---|---|
| 品类词 | 商品本身是什么 | 基础流量 |
| 场景词 | 使用场景 | 拓需求 |
| 需求词 | 用户想解决什么 | 拓需求 |
| 卖点词 | 差异化优势 | 提转化 |
| 营销词 | 特惠/立减等 | 促转化 |
| 人群词 | 目标人群 | 精准 |
| 竞品词 | 对标竞品 | 截流 |
| **热搜词** | 平台热搜 | 加热搜词预计平均搜索流量 **+20%** |

### 2.3 标题 30 字规则
- **前 15 字必须包含核心词**（算法读前段）
- 其余位置留给：卖点词 / 场景词 / 需求词 / 营销词 / 用户词
- 同一商品做 **5 个版本**做 A/B：品类+卖点 / 场景+需求 / 营销+人群 / 热搜+品类 / 竞品对标

### 2.4 三处必做搜索配置
1. **短视频搜索词配置 3 个**（与编导协同）
2. **搜索结果页定坑：前 5 个商品位**——不配置会展示竞品商品
3. **自定义词养词计划** + 加热热搜词

**这三件事不做，搜索运营等于没做。**

---

## 3. 商品卡五项红线

用 `templates/货架商城运营/商品卡优化清单.md`。任一不达标 → 该商品不进入货架放量阶段。

| 红线 | 阈值 | 说明 |
|---|---|---|
| **商品信息质量分** | **≥85**（目标 90+） | <85 有负向流量影响 |
| **描述与实物一致** | — | 不符会被投诉仲裁，赔付可达订单金额 150% |
| **无违规词/虚假宣传** | — | 虚假宣传扣分 5-20 分，累计封店 |
| **无虚假划线价** | — | 价格欺诈 |
| **无盗用主图/素材** | — | 版权 + 限流 |

### 3.1 好商品门槛（数据健康度）
| 指标 | 门槛 | 目标 |
|---|---|---|
| 销量 | >10 单 | — |
| 好评率 | ≥85% | 90%+ |
| 详细评价数 | ≥10 条（"10 字 + 1 图"） | — |
| 服务保障权益 | 开通 ≥2 项 | 显著提升下单率 |
| 主图视频 | ≥6 秒，20-30 秒为宜 | 竖屏优于横屏 |

### 3.2 主图与标题配合
主图五槽位（特点/优势/利益点/证明/场景）与标题关键词需配合——**标题带的词，主图要能印证；主图讲的优势，标题要有对应词。**

---

## 4. 扶持资源申领清单（每月核对）

| 资源 | 价值 | 动作 |
|---|---|---|
| **商品卡免佣** | 全年报名 1 次、每月完成 2-3 个任务，**单月最高可省百万元** | **固定为月度例行动作** |
| **积分打榜赛** | 基本经营动作达标即完成约 30% 进度 | **每期都参加** |
| 搜索扶持 | 最高 1500 曝光 | 逐品申请 |
| 新品出单概率 | +67% | 新品上架期必申 |
| 专属降佣 | 低至 2% | 按品类申请 |
| 商城秒杀 | 平台流量扶持 | 按节点报名 |
| 爆单首选 | 1 元补 6 元 | 按类目报名 |
| 服务保障权益 | 零成本提升下单率 | 至少开 2 项 |

**这是本岗位最容易丢钱的地方。** 不申领等于直接放弃。

---

## 5. 流量漏斗诊断

用 `templates/货架商城运营/货架流量诊断表.md`。六层逐层找卡点：

```
曝光
  ├─ 低 → 查：是否限流 / 质量分是否 <85 / 库存是否不足 / 扶持资源是否未申领
点击（CTR）
  ├─ 低 → 查：主图与标题（转美工）、价格感知、销量/评价展示
进店
  ├─ 低 → 查：主图承诺与详情页是否一致（转美工）、商品信息质量分
下单
  ├─ 低 → 查：详情页疑虑是否解答（转美工）、价格优势、规格选择难度
支付
  ├─ 低 → 查：券的使用规则、运费、发货时效预期
留存（退款/退货）
  └─ 高 → 查：详情页预期管理、尺码/规格信息、描述与实物一致性
```

**诊断纪律：每一层必须有指标证据。** 不能出现"可能是主图问题"这种无数据表述——要么给出证据，要么标注"需美工补数据后确认"。

---

## 6. 口径拆分规范（每次复盘必做）

```
货架 GMV
  ├─ 商品卡成交 / 非商品卡成交
  └─ 主动搜索 / 商城 / 店铺橱窗 / 其他 / 内容场导流
```

**不拆分的货架数据不写进复盘报告。** 写进报告的每个货架数字都要标明属于哪一层。

---

## 7. 常见错误与雷区

| 错误 | 后果 | 正确做法 |
|---|---|---|
| **把导流成交当货架增量** | 系统性高估货架价值，资源错配 | 做商品卡/非商品卡 + 主动搜索/导流拆分 |
| **不做商品卡体检** | 质量分 <85，自断流量 | 逐项打分，红线不过不放量 |
| **不配搜索结果页定坑** | 前 5 商品位展示竞品 | 必配 |
| **标题不把核心词放前 15 字** | 算法读不到 | 前 15 字核心词 |
| **不申领扶持资源** | 直接丢钱 | 每月核对清单 |
| **忽略服务保障权益** | 零成本提升下单率没用上 | 至少开 2 项 |
| **只报货架 GMV 占比** | 口径失真 | 拆分后报 |
| **诊断无指标证据** | 结论不可执行 | 每层要证据 |
| **不与编导协同搜索词** | 搜索词无入口 | 3 个词与编导对齐 |
| **详情与实物不一致** | 投诉仲裁，赔付可达订单金额 150% | 视觉与描述对齐 |
| **在低质量品上浪费扶持资源** | 资源浪费 | 先过红线再申资源 |

---

## 8. 平台差异提示（抖音专属，不可外传）

| 抖音特有 | 说明 |
|---|---|
| **货架口径幻象** | 40-50% vs 主动搜索仅约 10% |
| **搜索转化率约为推荐 12 倍** | 搜索值得专门做 |
| **搜索结果页定坑** | 前 5 商品位机制 |
| **商品信息质量分 ≥85** | 货架流量硬门槛 |
| **好商品三门槛** | 销量/好评率/详细评价 |
| **商品卡免佣** | 全年报名 1 次、每月 2-3 个任务，单月最高省百万 |
| **积分打榜赛** | 每期都参加 |
| **电商内容加载率上限** | 平台给货架找容器的根因 |
| **搜索扶持 1500 曝光 / 新品出单 +67% / 专属降佣 2%** | 需主动申领 |

---

## 9. 数据与规则的时效性规范

- **扶持政策、免佣规则、算法口径**：变动频繁，标注来源与时点，注明需复核。
- **大盘结构数据**：标注来源与年份，不同机构口径有差异。
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
