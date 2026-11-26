"""Build the four D&T skill layouts without replacing legacy methodology.

Run with --check for a read-only integrity check. No network or credentials.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BEGIN = '<!-- CODEX-ADAPTER-BEGIN -->'
END = '<!-- CODEX-ADAPTER-END -->'
COMMON = '''## Codex 执行适配（2026-11-08）

本段限定下方原方法论在当前项目中的适用范围；保留原正文以便追溯，不把历史政策或阈值当作实时事实。

- 先运行本 Skill 的 `scripts/resolve_project.py`，从 Skill 所在位置向上找到同时含 `runtime-agents/`、`docs/`、`templates/`、`data-pipeline/` 的项目根目录。正文内 `docs/`、`templates/`、`research/`、`data-pipeline/` 等路径均相对于该根目录，不是当前工作目录或 Skill 目录。可给解析脚本传入正文路径取得绝对路径；找不到资源须报告，不能臆造。
- 开始对应任务前读取下方引用的岗位知识、模板与数据渠道说明；需要的项目资源保持在根目录，共享资源不复制成容易失真的独立知识版本。
- 原“零网络／零凭证／完全跑通”等叙述，只能用作冻结快照离线复验的历史上下文。离线复验保持零网络门禁；实际市场研究按用户已授予的在线范围执行，工具和凭证是否可用须现场验证。不得把静态 Skill 安装或离线成功写成真实业务能力全部验证。
- 下方平台税费、评分门槛、履约方式、准入及活动政策均可能过期。实际经营决策前按当前站点官方来源复核日期、适用类目和履约模式；未复核明确待确认。JD 频次、历史比例仅是研究样本。
- 用户当前授权 Just One 与 FastMoss 的在线选品测试；使用授权免费额度，额外付费、外部联系、下单或投放须有相应用户授权。既有授权不重复询问。密钥仅从受保护凭证读取，不写入 Skill、提示词、仓库或报告。
- 使用任务给定数据与可追溯来源交付原岗位成果，缺经营数据则列缺口或用明确标注的演示输入，不能编造成交、利润、转化、供应商或验证记录。
'''
SELECTION = '''
### 双平台选品数据口径

执行前必须读取项目根 `docs/双平台选品数据使用规范.md`，它是本岗位当前数据适配规范，优先于下方旧数据接入描述。保留原选品、利润测算及测品方法，但有证据才计算分数和利润。

收到货源、供应商或商务联系方式任务时，读取 `docs/选品供应商搜集与筛选规范.md`。按原候选 SKU 找货源，以商品页和同一商家公开联系入口交叉核对，分别列已确认同 SKU、可询价的近似款和仅线索。供应证据不够时如实给出短缺。

每轮研究 3–5 款候选，区分抖音/TikTok 站点、币种、原始值、真实统计起止日期与抓取日期。检查时效/节点、标品可比价、同行竞争证据与持续跑量证据；未覆盖维度明确缺口。首轮为低成本小玩具，TK 印尼，履约依据当前站点规则。

Just One 的 `sale_axis` 日序列与独立 `month_sale` 不混算；`cooper_author_num` 是合作达人而非同行卖家。FastMoss 使用当前 MCP schema，先查额度，7/28/90 天汇总与日序列分别保存。疑似未更新的最新零值不可当下滑，固定日期复抓应保留前后快照。

印尼本地展示 `Rp22.938` 是 22938 IDR；JSON 数字 22.94 不擅自乘 1000。保留字段类型和转换依据，价格冲突不用于算利润。已证明能力仅支持双平台初筛，不宣称五站、全类目、90 天抖音、精确同款份额和供应链全部覆盖。供应商要匹配规格、记录公开商务联系方式与来源日期，竞品店铺不默认工厂；未经指令不联系。
'''
COMPETITOR = '''
### 已固化：竞店、竞品、选品命中与货源联动

执行前读取 `docs/双平台竞店与竞品对比规范.md`，按 `templates/竞品分析/双平台竞店竞品对比报告模板.md` 交付；材质和评价处理读取 `docs/商详材质与评价词云工作流.md`；命中高潜候选后的货源分级读取 `docs/选品供应商搜集与筛选规范.md`。下列流程补充原方法论，历史案例的胜者、商品ID和供应商不作为新任务的默认答案。

1. **定对象与口径**：按本岗位平台、TikTok站点、店铺ID和商品ID解析任务。先确定比较维度与可比SKU，列同款已证、近似款或同用途对标。双平台分别分析，不混币种、站点或时窗。
2. **取证并冻结**：优先复用有效成功快照；需要新增数据时按当前授权和工具契约取数。保存来源、抓取时刻、实际统计日期、原值和转换依据。页面展示与第三方估算分列，余额／授权失败停止对应请求；旧快照复算必须明说未刷新销量。TikTok使用FastMoss时遵循项目AGENTS.md的工具路由。
3. **分析两店两品**：店铺覆盖性质／主体、官方层级、相对规模、货盘与集中度、渠道、目录标价、件均金额和订单客单；未知留空并说明。商品覆盖销量／增长、价格口径、SKU规格／包装、材质、评价及实读商详。对目录原始标题核实货盘，不从店名推测专营。
4. **材质、评价、词云**：复用工作流中的组件解析、图片OCR和按评价计数脚本。商家材质声明与买家称谓分列；保留SKU、否定、混合评价及激励标记。可做方向性质量比较，不以买样为完成本报告的前提；不得把小样本投诉换算成瑕疵率，或把封面当整页已读。
5. **对接高潜与供应商**：用平台／站点／商品ID匹配最近有效选品报告，分精确命中、同方向近似和未命中。精确命中调用既有清单，列公开联系方式、商品／联系来源、报价单位、MOQ、供货地、匹配等级及差异；同一商家去重。匹配原候选不等于确认供应商同SKU，近似竞品不得继承精确命中。不自动联系商家。
6. **结论与验收**：报告最前或最后明确“哪家店更值得对标、哪个产品更值得优先选、各自为什么”，区分整店优势、短期势头、商品商业机会和质量证据；不足以判质量胜负时仍给商业优先级及边界。建议写明接收岗位、具体动作与可检查交付。附计算JSON、原文／图片证据、词云、来源和未验收项；复算关键数字并核对供应商单位、SKU归属及本地链接。历史样例只用于检查格式，不替代本轮证据。

阶段验收以实际回执为准：已有双平台两店两品、材质／评价和高潜／供应商联动样例；完整TK评价分页、真实经营利润、主体资质及官方层级并未因此自动验收。
'''
AD_BUYER = '''
### 现行投放流程与报告标准（2026-11-09）

本节及后续平台专节是电商投放的执行口径。下方原方法论、原岗位说明和旧模板用于历史追溯或补充通用分析；其中旧入口、固定观察期、归因、费用、履约及处罚数值不作现行规则。单 Skill 模拟只用本 Skill 与题给输入即可完整执行。正常项目任务可按需读取模板和数据渠道；实际操作前另核账户权限、站点、预算限额、在投互斥及现行产品控件。

1. **建计划前**：记录平台／站点、币种时区、账户／店铺／商品／素材／直播间 ID、目标和归因范围。确认自有或授权、审核、库存与履约、商品链接可打开及身份一致。把基础预算、机动预算和品广费用分开，标注累计限额、允许控件、触发阈值及观察期。依据账户历史、保本线和风险余量选初始目标；后台推荐若低于经营护栏，先缩试验或搁置。预算下限超额度、身份不符或授权失效时暂停对应路径。
2. **自然流与素材**：搜索词、搜索／商城入口曝光→商品点击→支付→结算单列，记录标题、主图、规格、价格券、库存、时段和广告变更。每轮只改一个变量，其余保持或说明干扰。独立入口用于诊断与复测，重叠订单按 ID 核对，不与投放总 GMV 相加或由前后差直接认定增量。素材比较用同商品、可比时间窗和漏斗，先验挂链／授权，CTR 高而点击支付率低时查商品页承诺、价格券、规格与履约。没有素材级消耗和结算就留空该素材 CPA 与利润。
3. **经营账与目标口径**：同窗口、同对象、同批订单记录平台成交额 G、广告消耗 S、退款后结算收入 R、非广告变动成本 C。毛投产 G/S，结算回报 R/S，投前贡献 P=R−C，投后贡献 P−S。G/P 和 R/P 分别是对应报表口径的保本线，P 必须为正；采用净成交或综合成本出价时另以对应窗口和成本重算。结算已扣的退款及费用不可二扣。区分支付、签收、平台净成交、结算与贡献。预估与成熟回传分列，缺失或分母为零则 ROI 留空，不用公开竞品估算代填。
4. **盯盘与回退**：固定采样窗，记录更新时刻、齐数状态、累计消耗节奏、曝光／进房、有效停留、商品点击、支付、库存及排品事件。采样窗、平台归因窗和结算成熟期分开；逐窗回放只用当时已到数据。未齐数或过期冻结常规调参，已确认的库存、权限、履约、超支等硬风险可立即停对应推广及追加。低量先查权限、素材、库存、承接和节奏。有效样本有正贡献、同口径实际 ROI 达标并确有预算卡量时，仅加一次预算，受已批上限约束。预算未打满且确认目标过紧时，仅小步下调目标，守住同口径盈利护栏。需收紧效率且仍可安全观察时可小步上调目标；完整样本已证亏损先止损。未证实原因时保持基准。写明复测指标、观察时点、旧值和回退条件；暂停后先修复原因并核可靠数据，按旧基准或更低风险额度小量重启，重新累积达标窗口再扩。
5. **品广单列**：品广按品牌广告理解，独立触达目标、预算和审批。看去重触达、频次、CPM 或有效观看成本，按实际品牌产品可用控件执行。效果消耗与品牌消耗单列后再核合并贡献；直播间总体成交不能当品牌增量。无合适对照时，品牌增量与 ROI 留空。
6. **报告交付**：结论先行并标模拟或真实。逐路写计划、商品及素材身份、投放入口、控件／出价、预算与保本线、最小观察和止损。附自然入口与素材漏斗、平台投产与成熟经营账、逐窗调控表（时间／证据／动作与前后值／复测／回退），最后列负责人、缺口、来源和验收边界。实盘需核权限、账户报表、调控日志、真实成本和订单结算；模拟仅验证决策。按日／周／月复盘时沿用相同口径。
'''
DOUYIN_AD_BUYER = '''
#### 抖音中国站 · 巨量千川

- **商品卡与挂链视频**：商品全域推商品使用可用商品卡、主图和授权挂链视频体裁。按抖音号×商品核互斥；同品卡／视频按素材或入口诊断，不默认拆成同商品重复计划。主页挂车与广告推广卡片分别核验。抖店／罗盘独立经营搜索与商城入口，优化标题、主图、规格价格、评价和库存；全域投放按整体口径优化并汇总自然及付费，分开的入口观察用于货架经营。调支付目标用整体支付口径，调净成交目标用后台同口径。
- **直播全域与起量**：先核主播账号、直播间、货盘、排品、库存、审核和账户权限。常态用账户开放的控成本模式，以同口径 ROI 和房间承接盯盘。房间承接稳定且有短时整场增量机会时才考虑一键起量；明确单条已核授权视频有机会时才考虑视频追投。追加任务与主计划分项记账，单次、全场上限及结束条件先锁定，成本保障逐项核验。两种追加不要同开试验，数据未齐、承接不足或硬风险时关闭／不追加。品牌预热或直播引流按独立触达目标核账。
- **千川乘方**：乘方继承全域基础投放，同直播账号、同账号×商品按产品规则互斥。能升级时先核原计划状态、历史继承和互斥影响，实盘不在一场内反复切换。基础投放综合营销 ROI 为同段净成交金额÷整体消耗。商品侧含达人佣金／团长服务费的能力按实际开放和字段核成本。直播星选按账户与任务实际开放核对是否计入整体消耗，不能套商品佣金口径或重复扣成本。只用乘方生效期间同范围的分子分母；全域、乘方、追投和全场数据按模式／计划／时段分账。发现综合成本与同段基础消耗不符，先核导出截点、归因回补及成本明细，两套未对齐的试算均不能报作已核投产。

实盘复核入口：[千川推商品问答](https://school.jinritemai.com/doudian/web/course-series/cJZHjLnhaLyw/cJZHjLnhaLywlJZJUawr8L8r?from=qianchuan_delivery&from_school=1) · [直播全域手册](https://school.jinritemai.com/doudian/web/articlev0/aJpvpMFYyQzM) · [乘方产品手册](https://school.jinritemai.com/doudian/web/articlev0/aJq691ZQLzS6) · [乘方基础投放 FAQ](https://school.jinritemai.com/doudian/web/articlev0/aJq5t8zZjhr5) · [巨量引擎品效播](https://www.oceanengine.com/insight/823)。
'''
TIKTOK_AD_BUYER = '''
#### TikTok Shop · GMV Max

- **商品卡与挂链视频**：TikTok Shop 销售使用 Product GMV Max。商品卡与挂链视频是同品计划可用体裁，避免为同品重复建计划。核 Seller Center 店铺、Business Center／广告账户权限、商品锚点和授权有效期，自有或联盟素材按有效授权进入创意池；Spark 授权素材可作为创意来源，旧 VSA／PSA 或站外转化模板不作为当前 Shop 建计划入口。按商品与素材看报表。Product GMV Max 产品层包含对应自然及联盟成交，Seller Center 自然搜索／商城漏斗独立跟踪，重叠不加总。各站按本币、账户时区和履约规则记录。
- **Product 目标与经营账**：Target ROI 与预算为主要控件，先用历史同口径及系统建议与盈利护栏对照，不给没有的手动 CPC 或单卡出价。每档 ROI 至少观察三个完整日，安全止损可提前；签收、拒收、退款和结算等成熟队列另等，不把观察期当成熟期。订单、签收前后售后与退款后结算分列，优先用订单实额；签收率折算只能作明确的估算。履约、平台费、达人费、优惠等按当站实际核。直播间整体报表可能含多店商品，经营账按自家订单 ID／商品范围对齐。
- **复测可用余额**：报告逐路并列本审批周期累计上限、已耗、已承诺金额和可用余额。复启／调参的新增费用最多用该余额，不把历史消耗重置，也不把平台日预算当新增审批。若账户最低预算或可用控件无法将新增费用限制在余额内，保持暂停并记待验。新周期或新额度按明确批复重新核算。
- **LIVE GMV Max 与冲量**：核店铺与直播账号绑定、广告权限、在播状态、主推商品和履约。Video-to-LIVE 和 Live-to-Live 分素材看进房、有效停留、商品点击、成交；LIVE ROI 是活动期间房间口径，含符合规则的自然成交。Target ROI 为常态，Max Delivery 仅在承接稳定、有明确冲量目标、批复额度覆盖本账户最低预算且可排期时启用。使用独立预算，与 Target ROI 同一时段互斥。设开始和结束时间、追加上限与失效条件，到期或提前止损后确认回 Target ROI，按模式与时间分别对账。不将公开示例最低额换算为印尼的固定门槛，不将短采样窗直接当后台排期长度。品广另用可用的 Reach 等品牌目标核账。

实盘复核入口：[Product GMV Max](https://ads.tiktok.com/resources/help/article/about-product-gmv-max?lang=id) · [Product 调参](https://ads.tiktok.com/resources/help/article/best-practices-for-product-gmv-max) · [GMV Max 归因](https://ads.tiktok.com/resources/help/article/about-attribution-for-gmv-max?lang=id) · [LIVE GMV Max](https://ads.tiktok.com/resources/help/article/about-live-gmv-max) · [Max Delivery 排期](https://ads.tiktok.com/resources/help/article/how-to-turn-on-max-delivery-optimization-for-your-live-gmv-max-campaign) · [品牌 Reach](https://ads.tiktok.com/resources/help/article/reach-objective?lang=en)。
'''
VIDEO_DIRECTOR = '''
### 实拍自动混剪执行

收到实拍自动混剪任务时，按 `docs/双平台短视频自动成片双路径工作流.md` 的本地 OpenMontage 入口执行。先核商品事实卡、素材 ID、授权和版本；只从任务提供的本地素材定位镜头及原声入出点，可复用项目关键帧与本地 ASR。每个版本输出时间线、SRT、渲染 MP4、源镜头映射及质检记录。仅在素材或证据不足时列缺镜，保留真实物性连续段的原速和原声。

使用 `scripts/render_real_footage.py` 渲染，每个版本使用新的输出目录。抖音用中文，TikTok 按目标站点语言核字幕。渲染回执与人工看片分别记录，核对主体、商品一致性、字幕安全区、原声、切点和主片与钩子版本的单变量。合成测试素材只用于技术验收，获准实拍和业务效果另验。
'''
HELPER = '''"""Resolve D&T project-relative references from any installed project layout."""
from pathlib import Path
import sys

def project_root():
    for candidate in Path(__file__).resolve().parents:
        if all((candidate / part).is_dir() for part in ('runtime-agents', 'docs', 'templates', 'data-pipeline')):
            return candidate
    raise RuntimeError('D&T project root not found; keep this skill inside its project')

if __name__ == '__main__':
    root = project_root()
    target = (root / sys.argv[1]).resolve() if len(sys.argv) > 1 else root
    if not target.is_relative_to(root):
        raise SystemExit('Reference escapes project root')
    if not target.exists():
        raise SystemExit('Missing project resource: ' + str(target))
    print(target)
'''

AD_REVIEW_CONTRACT = '''
### 二次审查修订：可复现决策契约（2026-11-11，优先于上述概括及历史正文）

本节适用于双平台的离线决策与报告；不授予实盘操作权限。所有阈值必须来自当前已批准策略及适用平台控件，示例不是默认实盘值。数值复核使用项目 `scripts/ad_decision_contract.py`；这只是只读校验器，不连接平台执行。

1. **口径与时间可追溯**：每条 ROI、保本线附 metric（支付/净成交/结算/贡献）、scope（账户/商品/计划/订单群）、mode（窗口增量/累计）、区间、数据截止时刻、maturity、source/version。只能同口径比较。已齐窗增量为 ΔG/ΔS，不得拿累计数冒充；从累计求差要同一快照系列、非重置、同归因且标回补影响，不能据此直接声称广告因果边际回报。断档累计不插值。原模拟 20:05 的增量 275/50=5.50，已知累计345/85=4.0588，但含20:00未齐窗，累计同样暂读。不能把两数当同一指标。
2. **暂估与成熟阈值**：原直播盘中25%是假设贡献率，暂估保本4.00；收盘同批P=360、G=1650，成熟保本4.5833。二者可以并存，须分列适用时间与状态。盘中不得倒用后来结算；暂估过线只支持当前方向性观察，不证明成熟盈利。缺有效成熟/保守预测依据时不常规扩量。正式复盘以成熟账为准，解释偏差，不能把预估记录回填成成熟判断。
3. **扩量与卡量**：平均成熟ROI达标只是必要非充分条件。已验证预算限制证据需平台/账户/计划/控件、时刻、状态/原始回执；预算利用95%、泛化布尔或曝光损失都不能单独证明受预算限制，指标还需确实在该平台可得。离线题明确声明确认卡量可按题设接受，但标记模拟，不伪造截图。常规扩量需追加批次的可比成熟证据或保守边际预测与范围/假设，证明新增贡献可覆盖追加消耗；窗口同比增量不等于因果边际。缺边际依据时维持，或仅提出另有明确试验授权、单次额度/损失/比例上限、冷却窗与止损可执行条件的探索试验，不把探索写成已证盈利。原A仅有平均5.2及一次额度12，缺其余配置时不再直接180→192。
4. **库存与换品**：停线与复启线分开，复启线必须高于停线，含获批补货周期、安全缓冲及可售口径；未知复启阈值保持暂停，不自造42/50等值。复启要实际库存达到复启线、供货可确认、商品/价格券/权限/履约重新核验、数据齐新鲜，重新累积有效窗。商品身份或价格券变化独立于库存触发暂停受影响推广及追加，直到新绑定事实核验，不能以库存足够豁免；已经完整审核并预先批准的排品切换需按对应独立策略重新建基准，不混用旧窗。
5. **风险预算闭合**：各路径/币种/审批周期分账，可用余额=已批上限−已耗−未入账承诺，未知承诺不能当0。默认不借调；只有明确批准的来源路径、目标路径、金额与时间范围才能转移，双边同步扣增且不得重复占用。单次新增不得超过绝对上限、周期可用余额、已批比例×本次试验前可用余额、损失推导上限中的最小值；比例未批准则不自动追加。
6. **Max Delivery**：不能把Target ROI当成Max Delivery必有的硬ROI下限。若同口径保本B>0、可靠保守的追加回报下界r、贡献结构固定且无遗漏成本，损失系数=1−r/B。当0≤r<B，可推S≤剩余损失额度/(1−r/B)；r≥B时该公式不产生有限上限，但仍受审批/余额/现金流上限，不意味着无风险。无可信下界按零回收r=0；无成本上界或可存在额外损失时不能断言最大亏损等于花费。必须先扣已损失及监测延迟/停投生效期间的最坏风险预留，停止回执可验证；监控提醒不是硬预算锁。若账户最低可执行预算高于安全额度且无验证过的有效分段硬限，禁止开启。原独立活动批复230000、损失50000、最低160000，在缺r与延迟风险证明下不能排期，原结论撤回。
7. **自然搜索规模与效率**：分别报曝光与曝光→支付率。原例1500→2400、12→18，效率0.80%→0.75%（-0.05个百分点，-6.25%相对）；以旧效率分解，曝光规模项+7.2单、效率项-1.2单，合计+6。只是恒等式分解，非因果归因，不能证明曝光全部由标题带来。非随机前后对照保留干扰与待复测，不宣称标题净增量。
8. **不齐数据与承接指标**：统一四态：complete（同批齐新鲜）、partial（有值但未齐）、missing/stale（缺失或过期）、zero（真实零）。partial可列暂读数和假设估算但decision_eligible=false；missing不填数；0/0=null。不齐窗不计有效窗，不据其常规调参。硬停止优先。停留率/点击率必须说明分母、样本量、可比基线和已批阈值；66%→30%应标承接异常并查换品/流量结构，禁止无依据扩量，但30%本身不是所有类目的自动停线。需停/降档由已批门限及风险证据决定。
9. **控件、分级与冷却**：分别定义维持、降风险预算、收紧效率目标、暂停、受限试验和扩量；不能将“降目标”当通用降风险手段，降低ROI目标可能放松效率。动作前必须有同平台可用控件、已批单次步长、单日次数、冷却时长/完整窗、最小样本、盈利护栏及回退旧值。缺配置只提方案，不猜5.4或5.3后执行；硬风险可提前停止，不受冷却限制。每次仅改一项，记录前后值、时刻、触发证据、复测时刻、回退条件和实际回执。目标和护栏同值不必然错误：原A/B是独立题；同一配置也可同值，需明确目标是优化设定、护栏是风险门，两者触发动作不同。

报告逐项输出：原数据/来源、阈值版本与审批范围、动作、可执行性、缺口、模拟或真实；规则文档更新不等于模型全部通过或实际投放有效。不得破坏零分母留空、断档不插值、两套未对齐乘方试算均待核、收盘不倒推盘中、硬风险优先、平台最低预算不超批复等原纪律。
'''


AD_HANDOFF_CONTRACT = {'douyin-ad-buyer': '### 竞品交接的当前提案状态\n\n仅在处理带当前比较／使用提案及结构化状态的竞品交接时，读取 `docs/竞品交接提案状态与事实用途规范.md`。主状态指当前提案；历史或分站事实可另列 `allowed_claims`／`allowed_use`，不得据此放宽当前提案的阻断或待核状态。接收主状态不得比上游主提案和本路交接更宽松；反证交回上游另版复核，保留本次状态。此约束不扩大为否定所有事实或改写无关经营逻辑；完整状态对应与边界仅见共用规范。\n', 'tiktok-ad-buyer': '### 竞品交接的当前提案状态\n\n仅在处理带当前提案状态的竞品交接时，读取项目 `docs/竞品交接提案状态与事实用途规范.md`。主状态指当前提案；历史或分站事实可另列 `allowed_claims`／`allowed_use`，不得据此放宽 `BLOCK_COMPARISON`／`BLOCKED` 或 `PENDING_EVIDENCE`／`PENDING` 的限制。\n\n接收主状态不得比上游当前提案和本路交接状态更宽松；发现反证交回上游另版复核。具体映射、动作与边界由该规范单处维护；此约束不等于否定所有事实，也不扩展到无关业务。\n'}

AD_REVIEW_METHOD = '\n### 复盘口径、覆盖与增量验证（2026-11-16）\n\n做广告或整体经营复盘时，读取 `docs/双平台广告复盘口径与增量验证规范.md` 并用 `templates/广告投手/复盘口径与增量验证表.md` 交付。至少输出 facts、measurement_basis、coverage_and_gaps、findings、next_actions、experiment_plan：缺失不作零，按同币种/同成熟窗口/同分母聚合比率，分别列整体经营与广告归因；销售同步上涨或渠道替代只作观察线索，需可靠对照才能估广告增量。不能执行实验时列可行方案、缺口和待授权事项，不伪造回执。\n\n本节补充通用测量方法，不改变上述可复现决策契约、当前竞品提案状态或价格/库存/预算/权限门禁；不因复盘指标良好而破底价、把字段标记当可信成本或放行未知权限。TikTok 特有归因与控件不得套抖音。仅学习方法，不新增 Coupler 连接、外传、记忆写回或报告插件依赖。\n'


def clean(text):
    return re.sub(r'\n' + re.escape(BEGIN) + r'.*?' + re.escape(END) + r'\n', '', text, flags=re.S)

def adapted(text, role):
    original = clean(text)
    header = re.match(r'\A---\n.*?\n---\n', original, re.S)
    if not header:
        raise ValueError('Missing YAML frontmatter: ' + role)
    block = COMMON + (SELECTION if role.endswith('-selection') else '')
    if role.endswith('-competitor-analyst'):
        block += COMPETITOR
    if role.endswith('-ad-buyer'):
        block = block.replace('- 先运行本 Skill', '- 正常项目任务先运行本 Skill', 1)
        block = block.replace('- 开始对应任务前读取', '- 正常项目任务开始前读取', 1)
        block += AD_BUYER + (DOUYIN_AD_BUYER if role == 'douyin-ad-buyer' else TIKTOK_AD_BUYER) + AD_REVIEW_CONTRACT + "\n" + AD_HANDOFF_CONTRACT[role] + AD_REVIEW_METHOD
    if role.endswith('-video-director'):
        block += VIDEO_DIRECTOR
    i = header.end()
    return original[:i] + '\n' + BEGIN + '\n' + block + END + '\n' + original[i:]

def files(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--baseline', type=Path, help='Optional original unpacked handoff root')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    sources = sorted((ROOT / 'runtime-agents').glob('*/skills/*/SKILL.md'))
    assert len(sources) == 20, f'Expected 20 skills, found {len(sources)}'
    results = []
    for source in sources:
        folder = source.parent
        role = source.parents[2].name
        original = clean(source.read_text(encoding='utf-8'))
        expected = files(folder)
        expected['SKILL.md'] = adapted(original, role).encode('utf-8')
        expected['scripts/resolve_project.py'] = HELPER.encode('utf-8')
        layouts = [folder, ROOT / 'agents' / role / 'skills' / folder.name,
                   ROOT / 'skills' / folder.name, ROOT / '.agents/skills' / folder.name]
        if args.baseline:
            baseline = args.baseline / source.relative_to(ROOT)
            assert clean(baseline.read_text(encoding='utf-8')) == original, f'Legacy text changed: {role}'
            for name, data in files(baseline.parent).items():
                if name != 'SKILL.md':
                    assert expected.get(name) == data, f'Legacy resource changed: {role}/{name}'
        refs = sorted(set(re.findall(r'`((?:docs|templates|research|data-pipeline|config|tools)/[^`]+)`',
                                    expected['SKILL.md'].decode('utf-8'))))
        missing = [ref for ref in refs if not (ROOT / ref).exists()]
        assert not missing, f'Missing references {role}: {missing}'
        for layout in layouts:
            if not args.check:
                for name, data in expected.items():
                    target = layout / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            assert files(layout) == expected, f'Layout mismatch: {layout}'
            resolved = subprocess.check_output([sys.executable, str(layout / 'scripts/resolve_project.py')], text=True).strip()
            assert Path(resolved) == ROOT, f'Wrong root: {layout}'
        results.append({'role': role, 'files_per_layout': len(expected), 'references_checked': len(refs),
                        'sha256': hashlib.sha256(expected['SKILL.md']).hexdigest()})
    result = {'skills': len(results), 'layouts_per_skill': 4, 'legacy_comparison': bool(args.baseline),
              'network_calls': 0, 'results': results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
