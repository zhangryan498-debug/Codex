# 按任务选用的交接字段

先给结论/调整、适用范围和`execution_authorized=false, release_status=not_authorized`。可以用Markdown或JSON，保留相同语义，不强制制造未请求的整包。

## 母稿与事实

`plan_id/version, evaluated_at/timezone, brand/master_version, product/SKU/unit_mapping, core_claims/evidence_ids, invariants, creative_flexibility, assumptions, conflicts, missing_evidence`。

## 分平台/渠道矩阵

| 渠道/站点/语言/用途 | SKU/销售单位 | 不变事实与品牌版本 | 合理差异/理由/来源 | 当前Offer/币种/范围 | 价格门禁状态/原件 | 素材/版本/状态 | 例外/失效/下一岗 |
|---|---|---|---|---|---|---|---|

价格状态表达：`not_requested / needs_existing_gate / blocked_unknown / blocked_known_failure / existing_evidence_checked`，注明具体证据与核验范围。完整可信原件未现场核验不能选最后一项。观察价、历史/反事实价另列，不写入当前可执行价字段；已知小计与未知完整成本分列，未知为null。若无价格资料可交安全的非价格母稿/素材设计，而非一律全阻断。

## 版本与审批

列本次变更、受影响依赖、可保留与失效素材、明确例外、审批人领域/证据/有效期及撤回处理。决定是离线可评审、部分可交接、待关键证据还是已知失败，写清是哪个字段/渠道的决定。

## 接收与证据索引

列接收岗、实际提供的输入版本、所需交付、验收依据及未执行项；只有真实运行/回执存在才说已交接完成。索引包含原始来源、事实/计算/规则/资产/审批引用与hash/时间；不删对结论必要的风险/资格/费用/时效。许可来源留内部，不带作者推广签名。品牌一致、价格安全、创意质量、实际制作/发布和经营效果分别报告。
