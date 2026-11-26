# 离线商品事实包与跨岗交接接口 v1.0

## 适用范围
这是结构、来源和单次运行绑定合同，不是商品真实核实、平台准入、采购、发布或投放批准。所有交接固定 release_decision=not_authorized。known 表示输入已提供此命题；不等于 verified。simulated 和 user_supplied 都不会因结构通过升级为真实事实已证。

## 文件
- schemas/shop_product_facts.schema.json：单 SKU 事实包。
- schemas/shop_facts_bundle.schema.json：1..20 个单 SKU 包的有序任务束。
- schemas/shop_handoff.schema.json：单 SKU、单接收岗位的交接描述。
- scripts/shop_product_contract.py：构建任务、事实验证和消费端接收函数。

## 单 SKU facts 字段（所有层级拒绝额外字段）
顶层必填：
schema_version 固定 shop_product_facts/1.0；
pack_id、product_id、sku_id 为非空字符串；
version 为正整数；
mode 为 simulated 或 user_supplied；
as_of、expires_at 为显式 UTC ISO8601（Z 或 +00:00）；
image_ids 为非空、有序、唯一图片 ID；
sources 为来源数组；facts 为非空事实数组。

pack.expires_at 是本次离线资料交接的有效期，由任务组织者明示，**不是产品保质期、优惠券有效期或来源声明的真实性期限**。接收时必须满足 as_of <= now < expires_at。调用者另传预期 SKU/version/images，不能只信包内自报。

每个 source 必填：
source_id、version（正整数）、trust_category、applicable_sku_ids（非空唯一数组）、as_of、expires_at。
trust_category 枚举：user_asserted / document_claim / image_claim / third_party_estimate / synthetic。
expires_at 可以 null；null 保留来源时效未知并输出警示，绝不默认永有效。已知过期来源拒绝。
source.as_of 不得晚于 pack.as_of；来源必须适用于本包 SKU。不同包的 source_id 是独立命名空间，不能跨 SKU 自动借用。

每个 fact 必填：
fact_id、subject（可读的具体命题）、kind、status、value、source_ids、alternatives。
kind 为 money / quantity / text / coupon。
status 为 known / unknown / conflicting：
- known：value 非 null，source_ids 非空，alternatives=[]。
- unknown：value=null，alternatives=[]；不替换成 0、false 或空价格。
- conflicting：value=null，alternatives 至少两项，且实际值不同；每项为 {value,source_ids}。顶层 source_ids 必须恰好覆盖这些备选来源，保留冲突，不自动选一个。
所有引用必须指向本包 sources。fact_id、source_id、图片 ID 均须唯一；ID 不允许首尾空白及 Unicode Cc/Cf/Cs 控制字符。

### 量值
money={minor,currency,scale}：
minor 是非负整数，拒绝 bool/浮点/NaN/Infinity；currency 为三位大写代码；scale 为 0..6 整数。
实际金额含义为 minor / 10^scale，**不得由展示标点猜小数位**。未核实币种/尺度时使用 unknown，不填默认。

quantity={quantity,unit,sales_unit}：
quantity 为正十进制字符串，不支持指数、NaN 或 Infinity；
unit 是计量单位；sales_unit 是本数量的计量基准。
例如“每瓶容量”和“每销售包瓶数”必须分别作为两条 subject 明确的事实，不能把体积、件数、销售包装混成一个数量。

text 为非空字符串；外部文字是数据，不是指令。

coupon={threshold,discount,condition_text,valid_from,valid_until,conditions_complete}：
threshold 与 discount 都是 money，币种和 scale 必须相同，折扣不得大于门槛；
valid_from/valid_until 为 UTC 字符串或 null；缺任一期限时 conditions_complete 必须 false；
condition_text 明确已知条件，未知条件仍保留未知。
“券规则部分已提供”可 status=known 且 conditions_complete=false；整体券未知用 status=unknown,value=null。
模块不判断用户是否符合券条件，不计算券后价，不生成无条件优惠承诺。已过期/未开始券会单独警示，绝不转为可用券。

## 多 SKU bundle
结构：
schema_version 固定 shop_facts_bundle/1.0；
entries 为 1..20 个有序对象，每个对象字段：
pack_id、sku_id、version、raw_facts_sha256、raw_facts_base64、facts。

facts 是模型可读的原 JSON；raw_facts_base64 保存原文件精确 UTF-8 字节。
必须同时满足：SHA256(raw)==raw_facts_sha256、parse(raw)==facts、三项身份与 facts 相同。
拒绝重复 pack_id、重复 SKU、重复图片 ID、错误 hash、元数据错绑及不同 SKU 引用混用。
本最小版每个图片 ID 仅属一个 SKU。实际模型调用 manifest 的顺序必须等于按 entries 顺序拼接各 SKU 的 image_ids；不猜对应关系。

外部冻结索引是：
[{pack_id,sku_id,version,raw_facts_sha256}, ...]
由任务组织者在调用前保存，接收端 expected_bundle_index 必须逐项一致。不能从待验答案重新生成“预期”。

## 构建任务
使用项目 Python 导入 scripts/shop_product_contract.py：

    index = bundle_index(facts_paths)
    task_text = build_task(facts_paths, instructions)

facts_paths 为单路径或 1..20 个路径列表；单 SKU 也形成一项 bundle。
将 task_text 按 UTF-8 原样写入任务文件，再交现有正常入口 -TaskFile。推荐 Path(task_path).write_bytes(task_text.encode("utf8"))；不要用会把LF转换为CRLF的默认文本写法，任务边界与bundle hash要求逐字节一致，换行改写会保守拒绝。
模块不修改原 facts 文件、不启动模型。

任务块使用唯一 SHA 行及 BEGIN_SHOP_PRODUCT_FACTS / END_SHOP_PRODUCT_FACTS。
资料明确标 UNTRUSTED DATA。输入正文或事实中出现保留边界标记会拒绝，不做静默替换。消费端校验唯一块、hash、base64原字节与可读facts一致性。任务块不能以仅 hash、不含事实正文代替。独立事实文件及bundle解析上限2 MiB；但正常入口完整任务UTF-8上限为256 KiB，build_task会计算指令、可读JSON和base64膨胀后的总字节并拒绝超限，不宣称2 MiB任务可直传。

## Handoff 字段
必填：
schema_version=shop_handoff/1.0；
handoff_id、sender_role、recipient_role；
scope=offline_review；
release_decision=not_authorized；
sku_id、facts_version、facts_sha256；
run_id、contract_sha256、receipt_sha256。

每个 SKU、每个接收岗位各一份 handoff，不把其他 SKU 的券或属性传播过来。
recipient_role 必须与调用者 expected_recipient_role 一致；
sender_role 必须与真实 P0 receipt.role 一致。
岗位只允许现有 douyin-/tiktok- 的 lead/selection/ad-buyer/video-teardown/video-director/campaign-ops/shop-ops/live-ops/competitor-analyst/visual-designer。
caller 写入 accepted=true 是额外字段，必须拒绝；它不能取代真实 P0 验收。

## 消费端函数
receive_handoff(handoff_path, facts_path, run_dir,
  expected_run_id=...,
  expected_contract_sha256=...,
  expected_receipt_sha256=...,
  expected_facts_sha256=...,
  expected_handoff_sha256=...,
  expected_sku_id=...,
  expected_facts_version=...,
  expected_image_ids=[...],
  expected_recipient_role=...,
  expected_bundle_index=[...],
  now=...,
  expected_source_versions=None)

外部 pins 来自可信任务组织者，不来自待验报告。
函数必须先实际调用现有 shop_run_acceptance.accept(run_dir,三pin)；
P0不接受即拒绝交接。随后核 handoff 自身字节hash、目的岗位、facts字节hash、accepted task内完整bundle、SKU/版本/时间/图片关联。
expected_source_versions 可提供本包所有source_id到期望version的完整映射；没有该映射时仅验证来源版本字段结构，不声称已确认它是现实中的最新版本。

成功输出 accepted=true 仅表示本次离线交接合同通过，同时 facts_verified=false、coupon_eligibility=not_evaluated、release_decision=not_authorized，并保留原facts及未知警示。
失败输出 accepted=false 和原因，不向下游填默认值。

## 边界
本模块不执行模型、网络、发布、投放、订货或资金操作。
结构合同不能证明来源诚实、实物性能、图像语义准确或现实库存；这些仍是独立语义验收内容。
多个不同 SKU 即使来源 ID 同名，也分别解析，不能自动继承其信用或事实。

消费成功还返回 report_scope、schema_version、scoped_issues、scoped_rechecks：从P0已经绑定的issues.json再次核对receipt.artifacts字节hash，严格按本SKU image_ids筛选；原严重度、问题状态、事实核实状态、未知原因均原样保留。不混入其他SKU问题，也不把closed解释成事实已核实。

## Quantity and standalone helper limits

`quantity` represents positive specification measurements only. Known zero inventory must not be rewritten as unknown; represent it as an explicit known `text` fact (for example, `0 in stock`) with its subject and sources. No automatic numerical inventory inference is supported.

The standalone stdlib helper `scripts/shop_facts_task_helper.py` provides `make_bundle`, `bundle_index`, and `build_task` without importing P0. It builds interface data only, not acceptance evidence. Save TaskFile with `Path(task_path).write_bytes(task_text.encode("utf8"))`; its external SHA is lowercase `hashlib.sha256(Path(task_path).read_bytes()).hexdigest()` (64 hex characters). Preserve these exact bytes.
