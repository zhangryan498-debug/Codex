# 接口配置｜TikHub 端点契约与计费属性（实测）

> 事实源：`docs/knowledge/data-channel.md`（纪律 1–8）；本表是它的可执行摘要。
> **原则：计费属性可用 ≠ 端点能返回内容。端点可用性必须实测，不能凭报价表推断。**

## 一、环境变量（只有这一个，绝不入包）

| 变量名 | 用途 | 从哪拿 | 值是否在本包内 |
|---|---|---|---|
| `TIKHUB_TOKEN` | TikHub OpenAPI 鉴权 | TikHub 控制台 | ❌ **不在包内，需自备** |

放置位置：项目根 `data-pipeline/.env`（已被 `.gitignore` 排除）。

## 二、端点契约（2026-11-06 实测）

| 端点 | 方法 | region | 实测结果 |
|---|---|---|---|
| `/tiktok/app/v3/fetch_video_search_result` | GET | ID 可用 | ✅ 作品 1217 条 |
| `/tiktok/app/v3/fetch_hashtag_search_result` | GET | **不接受 region** → 固定 US 站 | ✅ 786 条 |
| `/tiktok/app/v3/fetch_live_search_result` | GET | **无枚举限制** | ✅ 印尼 236 + US 58 |
| `/tiktok/app/v3/fetch_live_room_product_list` | GET | **非 US 需自带 cookie** | ⚠️ 印尼 `room_id` + `region=US` → `products: []` |
| `/tiktok/app/v3/fetch_hashtag_detail` / `_video_list` | GET | 仅 US/GB/FR/JP/VN/SG | ❌ 东南亚 400 |
| `/tiktok/app/v3/fetch_one_video` | GET | ID 可用 | ✅ 66 条 |
| `/tiktok/app/v3/fetch_home_feed` | GET | ID 可用 | ✅ 62 条（**不产出时长**） |
| `/tiktok/app/v3/fetch_trending_words` | GET | ID 可用 | ✅ 100 条（**端点不返回热度值 → 不产出热度排名**） |
| `/tiktok/web/fetch_search_video` / `fetch_general_search*` | — | — | ❌ 禁用（400 / result_status=4） |
| `/douyin/search/fetch_video_search_v2` | POST | CN | ✅ 作品 55 条 |
| `/douyin/search/fetch_challenge_search` | POST | CN | ✅ 话题 60 条 |
| `/douyin/xingtu_v2/search_creator` | GET | CN | ✅ 达人 422 个 |
| `/douyin/xingtu_v2/creator_base_info` | GET | CN | ✅ 详情 28 条 |
| `/douyin/xingtu_v2/kol_service_price` | GET | CN | ⚠️ 报价 1 条（成功率极低） |
| `/douyin/index/fetch_multi_keyword_hot_trend` | POST | CN | ✅ 热度 12 词 |

## 三、调用规约

- **限流 1 req/s / 路由**；单次延迟 2–6s
- **原始响应落盘** `data-pipeline/data/_raw/<日期>/`，24h 内可复用；**改口径从 `_raw/` 零花费重算**，不重复花钱
- **分页游标在 `next_page.cursor`**（不要硬算）
- **连续失败熔断**：同端点连错 8 次即停（避免参数契约错时空烧预算）
- **新样本写独立日期目录**，不覆盖旧快照

## 四、计费属性原始数据

端点计费与免费额度属性（实测扫描结果，原样保留）：

- `data-pipeline/snapshots/_tikhub_pricing_tiktok_all.json`（全量端点价格扫描）
- `data-pipeline/snapshots/2026-11-05-tikhub/_tikhub_endpoint_pricing.json`
- `data-pipeline/snapshots/2026-11-05-tiktok/_tiktok_endpoint_pricing.json`

预算测算脚本：`data-pipeline/scripts/tikhub_budget_plan.py`；实际成本记录在 `docs/roadmap.md` B 节。
