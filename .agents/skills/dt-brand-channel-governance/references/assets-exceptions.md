# 素材版本、渠道差异与例外

为实际需要的成品记录`asset_id/version/hash, master_brief_version, fact/claim_ids, brand_version, SKU/variant, platform/country/language, use/format/dimensions/duration, offer_id/version_if_present, source_assets/rights, approval_refs, validity, status`。文本、分镜、预览图、最终成品和发布记录是不同阶段，不能互相冒充。

母稿固定真实商品识别、主张证据与品牌边界；衍生版根据用途变化语气、篇幅、构图、比例和CTA。尺寸依据用户要求或当前适用平台规格，不把上游统一3:4/1080×1440当所有平台硬规则。不得只裁切图就宣称已本地化；逐项核口播/字幕、图中文字、价格、优惠条件、链接和商品形态。

价格或活动变化时，通过`offer_id → 受影响素材/口播`追踪；带旧价、过期券、旧资格的衍生版停止复用。没有价格内容且独立事实/品牌未变的素材，可保留其离线可评审状态，不强制全包重做。事实变更则沿对应claim/商品引用追踪。保存原版，输出新版本、差异和替代关系，不覆盖原件掩盖失效记录。

例外记录`exception_id, requester, authorized_approver, approval_evidence, scope/channel/SKU, allowed_changes, forbidden_changes, validity, affected_versions, rollback/withdrawal`。核批准者确有该领域权限、明确批准而非“已阅”、当前尚未过期/撤回且精准覆盖本次对象。可批准的创意差异按范围执行；未批或越界部分待决。合法品牌例外不能让价格破底、未证事实、缺规则或未授权素材变有效。

翻译可以是AI拟稿，但不冒称母语审核；语义、货币/数字、强度、文化与CTA分别留状态。SKU警示、年龄限制、支付/履约和活动资格未知时，不在最终面客素材中留下可误用占位数字。内部草案可有明确不可发布占位字段，必须集中列缺口。

本Skill只组织/审核素材。真分层PSD须另外检查原生文字与独立产品/标志/图标/背景，不能用扁平图或PDF替代；真实音画感知、母语签字、发布与经营效果均凭实际证据单列，不因文字矩阵完整就判已完成。
