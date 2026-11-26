---
name: douyin-campaign-ops-playbook
description: 抖音电商活动运营的方法论手册，含利润测算规则、平台资源申领清单、跨岗排期方法、归因顺序与活动复盘规范。当需要做活动方案、节点排期、平台活动报名或活动复盘时使用。
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

# 抖音活动运营方法论

本 Skill 面向抖音电商活动运营场景。**TikTok 侧的 COD 履约、站级佣金、联盟活动机制完全不同，不可套用本手册。**

---

## 1. 岗位定义与考核指标

### 岗位定位
抖音电商的**营销活动操盘手**。核心产出是**可落地的活动方案**（含利润测算）、**跨岗可执行的排期**、**可沉淀的复盘 SOP**。

### 考核指标（19 篇真实 JD 统计）

| 指标 | 说明 | 频次 |
|---|---|---|
| **ROI / 投产比** | 活动、投放、达播的综合投产效率 | 8/19 |
| **GMV** | 商家侧直接压 GMV；平台侧对商家增量业绩负责 | 6/19 |
| **转化率** | 活动页/落地链路转化 | 4/19 |
| **复盘报告产出与 SOP 沉淀** | 过程性考核项，几乎每篇都有 | 9/19 |
| 商家动销与成长 | 中小商家稳定经营、动销、升级拐点达成 | 4/19 |
| 曝光 / 参与度 / 投稿规模 / DAU | UGC 与玩法类活动指标 | — |
| 客单价 / ROI 看板 | 搭建「蓄水、转化率、ROI、用户资产」看板 | — |
| 用户资产 / 留存 | 品牌侧沉淀人群资产、复购 | — |

### 硬性任职门槛（19 篇真实 JD 统计）
- **学历**：本科及以上（统招本科优先），市场营销/电子商务/工商管理相关专业优先。**字节跳动抖音电商体系全部为「本科及以上」**（8/19 篇明确写出学历要求）
- **年限**：1-3 年（策略运营/活动运营专员级）/ 3-5 年（活动运营经理级）/ 5 年以上（负责人/操盘手级）—— **三档各占约三分之一**
- **业绩门槛**：必须有可追溯的大促操盘或 0→1 起盘案例，**且要求附数据**——如「月出库流水达 100 万元及以上」「0→1 起盘或规模化增长案例（附数据优先）」「有完整的活动从 0 到 1 落地经验」
- **工具**：Excel 公式与数据透视表、BI/SQL、抖店后台、精选联盟、千川投放逻辑、巨量百应、蝉妈妈/抖查查/飞瓜/巨量
- **第一软性项**：**跨部门沟通协调、资源整合、推动项目落地**（11/19 篇，几乎必然出现）
- 其他软性：结构化思维、抗压、多项目线并行推进、数据敏感、自驱
- 加分：AI/AIGC 工具经验、激励体系设计、MCN 与作者生态运营、行业资源自带、垂类经验（3C/美妆/食品）

---

## 2. 利润测算规则（活动方案的准入门槛）

用 `templates/活动运营/活动方案模板.md`。

### 2.1 成本项清单（抖音）

| 项 | 说明 |
|---|---|
| 预估销量 × 客单价 | 活动价的销量会高于日常价，但**必须扣退款率变化** |
| 商品成本 | |
| 平台技术服务费 | 按类目；商品卡免佣活动可减免 |
| **投流成本** | **受投流税上限约束（多数类目 15%，美妆 30%）** |
| 活动补贴/优惠券成本 | 平台券 + 商家券 + 赠品 + 佣金差额 |
| 达人佣金 | 阶梯升佣 10%→20%+ |
| 物流 | 国内仓 |
| **退款与"仅退款"损耗** | **必须单独一行，冲量活动尤其重要** |
| 其他 | 客服、样品 |

### 2.2 准入标准
- **净利率 <15% → 重新设计，不予放行**
- **必须做敏感性分析**，至少四个情景：销量下滑、退款率上升、投流成本上涨、补贴加深

### 2.3 平台资源要先算能省多少

| 资源 | 价值 | 动作 |
|---|---|---|
| **商品卡免佣** | 全年报名 1 次、每月完成 2-3 个任务，**单月最高可省百万元** | 固定为月度例行动作 |
| **积分打榜赛** | 基本经营动作达标即完成约 30% 进度 | **每期都参加** |
| 商城秒杀 | 平台流量扶持 | 按节点报名 |
| 爆单首选 | 1 元补 6 元补贴 | 按类目报名 |
| 消费券 | 2026 年从拉新转向挖存量，额度更高、更聚焦高客单 | 配合高客单货盘 |

**先算能省多少，再算要花多少。** 不申领等于直接丢钱。

---

## 3. 跨岗排期方法

用 `templates/活动运营/活动排期表.md`。

### 3.1 排期顺序
```
立项/选品确认 → 货盘锁定 → 素材交付 → 达人对接完成 → 预热 → 引爆 → 返场 → 复盘
```

### 3.2 倒推原则
| 环节 | 前置缓冲 |
|---|---|
| 素材交付 | 活动前 **≥3 天** |
| 达人合作 | 寄样 → 样品审核 → 排期，**预留审核周期** |
| 备货 | 按活动预估销量备货，TikTok 场景需 ≥15 天销量预估 |
| 平台活动报名 | 平台有报名截止期，**提前锁定** |

### 3.3 资源冲突检查
- 同商品是否同期多活动（分散资源、互相稀释）
- 与日常直播排期是否冲突
- 素材产能是否够（编导日产量上限）
- 预算是否与同期投放冲突
- 现金流能否覆盖（TikTok 场景尤其）

---

## 4. 归因顺序（复盘时按此走，不要跳步）

```
活动结果不及预期
│
├─ 1. 货盘结构层：货配得上活动吗？
│   ├─ 活动品与货盘匹配度、库存是否够、是否有爆品承接
│   └─ 缺爆品 → 货盘问题（→ 选品）
│
├─ 2. 价格机制层：价格有吸引力吗？
│   ├─ 折扣深度、券叠加后的到手价、活动价与日常价的价差
│   └─ 无吸引力 → 机制问题
│
├─ 3. 内容素材层：素材撑得起吗？
│   ├─ 预热素材量、前3秒钩子、活动专属素材
│   └─ 不足 → 素材问题（→ 编导、美工）
│
├─ 4. 达人流量层：达人来不来、来得够不够？
│   ├─ 参与达人数、达人层级分布、佣金设置是否有吸引力
│   └─ 不足 → 达人机制问题
│
├─ 5. 投放层：投放接得住吗？
│   ├─ 投流消耗节奏、ROI、投流费占营收比是否触顶
│   └─ 不足 → 投放问题（→ 投手）
│
└─ 6. 履约客服层：交付拖后腿了吗？
    ├─ 发货时效、备货准确性、客服响应、退款率
    └─ 异常 → 履约问题
```

**归因纪律**：每层必须有指标证据。要么给出证据，要么标注"需 XX 岗位补数据后确认"。**不能出现"可能是价格问题"这种无数据表述。**

---

## 5. 活动复盘规范

用 `templates/活动运营/活动复盘表.md`。**活动后 3 个工作日内完成。**

### 5.1 复盘必答
- 目标达成率（GMV / 订单 / 新客 / ROI / 净利）
- **净利 vs 测算净利的差异归因**
- 有效动作（可复用） / 无效动作（避免重犯）
- **平台级变化 vs 自身执行问题的区分**
- 沉淀到哪个资产

### 5.2 跨平台经验标注
复盘结论若涉及其他平台，**必须标注"需本地化转译，不可直接套用"**。

---

## 6. 平台差异提示（抖音专属，不可外传）

| 抖音特有 | 说明 |
|---|---|
| **投流税上限** | 多数类目 15%、美妆 30% |
| **仅退款与高退货率** | 冲量活动的隐性成本 |
| **商品卡免佣** | 全年报名 1 次、每月 2-3 个任务，单月最高省百万 |
| **积分打榜赛** | 每期都参加 |
| **爆单首选 1 元补 6 元** | |
| **2026 消费券转向挖存量** | 配合高客单货盘 |
| **433 结构** | 店播/达人/货架三路同时设计 |
| **618/双11/双12/年货节** | 节点日历 |
| **阶梯升佣** | 10%→20%+ 冲量 |

---

## 7. 常见错误与雷区

| 错误 | 后果 | 正确做法 |
|---|---|---|
| **先设计玩法再算账** | 活动做热闹了亏钱 | 利润测算先行，不通过不放行 |
| **不扣投流税上限** | 利润虚高 | 测算时先扣投流费占营收比上限 |
| **不预估活动后退款率** | 表面达标实际亏损 | 冲量活动必须预估退款率变化 |
| **不申领平台资源** | 直接丢钱 | 免佣/打榜赛每月例行核对 |
| **只看表面 ROI** | 假性盈利 | 用扣退款后的净 ROI |
| **排期不倒推** | 素材/达人赶不上 | 排期倒推 + 标依赖 + 留缓冲 |
| **归因跳步** | 结论错误 | 按 §4 六层顺序走 |
| **只做活动不做沉淀** | 下次从零开始 | 玩法库 + SOP 沉淀 |
| **跨平台直接搬运** | 前置条件不成立 | 逐条过转译表 |
| **不推跨岗协同** | 项目推不动（第一软性项） | 明确到人与交付物，定期对齐 |
| **数字不标口径** | 复盘会上争论口径 | 每个数字标口径与来源 |
| **活动后不复盘** | 无法积累 | 3 个工作日内完成 |

---

## 8. 数据与规则的时效性规范

- **费率、投流税、平台活动规则、券策略**：变动频繁。输出时标注来源与时点，注明"需向平台侧复核"。
- **数据通道权威源**是项目 `docs/knowledge/data-channel.md`；取数走项目 `data-pipeline/` 下的采集器。**抖音侧零凭据通道已完全跑通**：热榜（`douyin_public.py`）+ 推荐流/话题真实体量/创作者画像/作品详情/评论（`douyin_feed.py`，推荐流自举，不需要种子 ID、不需要登录、不需要分享链接）。TikTok 侧采集器代码就绪，需 API token。外部数据只用于趋势与竞品判断，**经营口径（结算/真实退货/实收）必须来自店铺后台**。
- **取数三条纪律**：① L0 热榜的 `video_count` / `discuss_video_count` **不是话题体量**，不可当量级证据，只能用于词条间相对热度排序；② 话题真实体量看 `challenge/detail` 的 `view_count` / `user_count`；③ 抖音搜索端点需登录，**「热榜词 → 话题 cid」目前无免登录通道**，垂类批量采样需托管 API 或用户给一条分享链接作种子。
- **平台知识库权威源**是项目 `docs/knowledge/platform-model.md`；与最新公告冲突以公告为准并立即更新。
- **JD 中的门槛值**属岗位市场统计口径，标注来源与统计时点。
- **不确定判断标注置信度**，说明需要谁去验证。

## Offline activity price floor protocol (2026-11-11)
For offline activity pricing or a price-floor handoff, read docs/campaign-price-floor-offline.md and docs/campaign-price-handoff.md. Keep complete synthetic review decisions separate from real execution authorization. Current task-defined floor basis/scope and versioned rules take precedence over historical percentage examples; unknown costs, funding, periods and conflicting combinations never become zero or automatic approval. Preserve the source-linked buyer/merchant/fee ledger and per-SKU floor checks. A structured shop-ops acceptance must additionally pass the independently invoked price-floor gate with trusted caller-frozen calculation evidence and actual-run binding. Model self-certification is not that evidence. Do not change model/fast settings or call live business systems for an offline test.

Structured JSON amounts are mandatory ledger data: mirror every material calculated merchant discount, merchant-funded coupon cost, confirmed reimbursement, fee and basis in the caller's numbers/amounts object using its documented field names. A correct number in prose alone does not satisfy a structured field. Preserve unknown as null or pending, never fill it with zero.
Keep decision and price-proof status separate. Forbidden, expired or out-of-scope requests remain rejected; missing data within a supported request stays pending. Do not turn a counterfactual expired price into an actual valid settlement net, or weaken an existing stop to align labels. Preserve separate currencies when the role does not support portfolio conversion.

### Known baseline and counterfactual branches
For a rejected/expired/uncertain configuration, continue every explicitly supported and fully calculable baseline branch as a separately labelled net amount, not just a list/base price. Retain current actual net, no-activity or flash-only baseline net, and hypothetical/counterfactual net as distinct fields when applicable. Use the caller's fields such as fallback_no_activity_net or fallback_flash_only_net; a base_price field is not an equivalent net field. Compute each net from that branch's buyer payment, confirmed reimbursement and explicit costs. Do not invent missing baseline eligibility, costs or rounding.
A safe baseline is only an alternative calculation, not a change already made and not acceptance of the rejected original request. Actual net for an expired/forbidden configuration stays null and its decision stays REJECT. Use PASS/FAIL for a valid applicable floor comparison; use UNPROVEN for a comparison affected by missing data; use NOT_APPLICABLE when the submitted configuration is invalid and produces no valid activity price, or no floor applies. Out-of-role portfolio conversion remains rejected with its unsupported floor unproven; never begin currency mixing to improve a label.
