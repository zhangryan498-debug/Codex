#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""D&T 报告一键复算 + 质量门禁（roadmap 阶段 1.3「断言与幂等进 CI」的落地脚本）

一条命令跑完全部报告的可复算校验，任何一项不过即非零退出：

  1. **断言门禁**：跑 `docs/reports/calc-*.py`，任一脚本 exit≠0 → 失败
  2. **渲染幂等**：跑 `docs/reports/render-*.py --apply` 两次，比对报告 md 的 sha256
  3. **平台差异门禁**：抖音侧报告扫 TikTok 机制词；TikTok 侧报告扫抖音机制词
     （带词边界，避免 `EXIT_CODE` 这类误命中）
  4. **跨站合并门禁**：TikTok 侧报告不得出现「五站合计/全站合计/跨站合计」
  5. **克重数值门禁**：抖音侧男装批次交付件不得出现克重数值（`\d{2,4}\s*[gG]`），
     冲突登记类文件（选品决策）豁免

用法：
    python3 -u scripts/verify_reports.py            # 全量校验
    python3 -u scripts/verify_reports.py --skip-render  # 跳过渲染（只跑断言+词扫描）

退出码：0 = 全绿；1 = 有失败项（明细打印到 stdout 与 stderr）
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORTS = ROOT / "docs" / "reports"

# --- 平台差异禁词（与各报告统计脚本内的黑名单同源） --------------------------
TIKTOK_MECHANISMS = [
    "TikTok", "COD", "签收率", "佣金联盟", "本地仓", "海外仓", "本地履约",
    "货到付款", "Spark Ads", "Tokopedia", "Shopee", "GoPay", "OVO", "DANA",
    "WhatsApp", "拒收", "J&T", "Ninja Van", "3PL", "SKU 平台", "跨境",
]
DOUYIN_MECHANISMS = [
    "仅退款", "投流税", "精选联盟", "商品卡GMV", "商品卡 GMV", "千川", "抖店",
    "巨量", "全域推广", "短视频挂车率",
]
# TikTok 侧另禁：快照未支撑的结论词
TIKTOK_UNSUPPORTED = [
    "挂车率", "达人佣金为", "库存充足", "ROI 为", "ROI=",
]

# 允许出现「另一侧平台」机制的语境标记：留空栏 / 跨平台转译对照 / 指向另一侧报告。
# 抖音侧报告里出现 TikTok 机制词，只有在**这些语境**里才合法；
# 抖音侧自己的结论与打法里出现，即判违规。
CROSS_PLATFORM_MARKERS = [
    "留空", "本批次不填", "本批次未填", "本批次不适用", "不填", "未填",
    "☑", "☐", "不可与本文", "见《", "本批次只跑", "本批次只", "TikTok 版本",
    "另一侧", "跨平台", "本地化", "转译", "不可直接搬运", "不可直接搬",
    "需重新评估", "需重测", "需换", "不可用", "留空未填", "全部留空",
    "必填", "platform-model.md", "直接可用", "已考虑", "若测试", "平台差异",
    "适配", "不适用", "未混入", "未引入", "解锁", "TikHub", "跨平台对照",
    "仅一行", "不入测算",
]
# 章节标题命中这些词 → 该节属于「留空栏 / 跨平台对照 / 平台适配」语境
SECTION_MARKERS = [
    "TikTok", "跨平台", "适配", "本地化", "转译", "另一侧", "平台差异", "风险检查",
]
# 纯平台名标签行（如「**TikTok**」「TikTok 专项」）不算机制词命中
PLATFORM_LABEL_ONLY = {
    "TikTok", "**TikTok**", "TikTok 专项", "TikTok 追加", "TikTok Ads Manager",
    "TikTok（东南亚）", "TikTok Live",
}

# 跨站合并：否定式表述（「禁止跨站加总」）是纪律声明，不算违规
CROSS_SITE_NEGATIONS = ["禁止", "不得", "不做", "不", "严禁"]

# 克重数值：仅允许出现在「冲突登记 / 待核 / 消解 / 审计」语境
GRAM_CONTEXT_MARKERS = [
    "冲突", "不一致", "待核", "待吊牌", "吊牌", "审计", "消解", "登记",
    "核对", "口径", "假设值", "整件重量", "修正记录",
]

CROSS_SITE_PATTERNS = [r"五站合计", r"全站合计", r"跨站合计"]
# 克重数值：`350g` / `600 g`；后随拉丁扩展字母（越南语 gói、印尼语 gawai 等）
# 不算克重，中文单位（350g 起 / 350克）仍然命中。
GRAM_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])\d{2,4}\s*[gG](?![A-Za-z\u00C0-\u024F\u1E00-\u1EFF])"
)

# 抖音侧报告 = 文件名**不含** TikTok 的 md（自动识别，新增报告免维护名单）
# TikTok 侧报告 = 文件名含 TikTok
def douyin_report_files() -> list[str]:
    return sorted(p.name for p in REPORTS.glob("*.md") if "TikTok" not in p.name)


def tiktok_report_files() -> list[str]:
    return sorted(p.name for p in REPORTS.glob("*TikTok*.md"))


# 克重数值豁免：冲突登记类文件（数值用于审计消解，不是交付文案）
GRAM_EXEMPT = {"2026-11-05-选品决策-男生秋冬简约穿搭.md"}


def sha16(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=900)


def artifact_hashes() -> dict[str, str]:
    """覆盖 docs 下全部报告与 JSON，包含目录外的项目进度汇报。"""
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / "docs").rglob("*"))
            if p.is_file() and p.suffix in {".md", ".json"}}


def changed_artifacts(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def scan_mechanisms(text: str, words: list[str]) -> list[str]:
    """在文本中找「另一侧平台机制词」，但豁免三类合法语境：

    1. 所在**章节**是留空栏 / 跨平台对照 / 平台适配索引（章节标题含标记词）
    2. 所在**行**含留空 / 对照 / 指向标记（如「本批次不填」「☑」「不可与本文」）
    3. 该行**只是平台名标签**（如「**TikTok**」）

    其余一律判违规——即「把另一侧平台机制当成自己平台的机制来用」。
    """
    hits: list[str] = []
    cur_section = ""
    for ln in text.splitlines():
        if ln.lstrip().startswith("#"):
            cur_section = ln.strip()
            continue
        stripped = ln.strip().strip("*").strip()
        if stripped in PLATFORM_LABEL_ONLY:
            continue
        if any(m in cur_section for m in SECTION_MARKERS):
            continue
        if any(m in ln for m in CROSS_PLATFORM_MARKERS):
            continue
        for w in words:
            if re.search(re.escape(w) + r"(?![A-Za-z_])", ln):
                hits.append(f"{w}@「{ln.strip()[:45]}」（章节：{cur_section[:28]}）")
    return hits


SELFTEST_CASES = [
    # (文本, 预期是否判违规)
    ("## 三、履约规则\n本场履约按 COD 计算拒收率后再结算。\n", True),
    ("## 三、履约规则\n本场履约按平台标准执行退款流程。\n", False),
    ("## 三、TikTok 专项\nCOD 退货率折算口径（留空不填）\n", False),
    ("## 三、履约规则\n| 平台 | ☑ 抖音 ☐ TikTok |\n", False),
    ("## 三、履约规则\n佣金(TikTok 必填)\n", False),
    ("## 三、履约规则\n**TikTok**\n", False),
    ("## 三、履约规则\n本场按 COD 模式备货，签收率折算后再结算。\n", True),
    ("## 三、履约规则\nTikTok Live 专场不适用本场。\n", False),
    ("## 三、履约规则\n拒收率超过阈值时限制商品曝光。\n", True),
]

# 克重数值自测：真正克重必命中；越南语/印尼语量词不得误判
GRAM_SELFTEST_CASES = [
    ("面料克重 350g 起。\n", True),
    ("整件重量 600 g。\n", True),
    ("已移除具体克重数字。\n", False),
    ("Set 5/ Set 10 gói Bánh\n", False),
    ("harga 20 ribu\n", False),
    ("重 350g，待吊牌核对\n", True),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-render", action="store_true", help="跳过渲染幂等校验")
    ap.add_argument("--selftest", action="store_true", help="只跑门禁规则自测")
    args = ap.parse_args()

    if args.selftest:
        print("=== 门禁规则自测 ===")
        bad = 0
        for i, (text, should_flag) in enumerate(SELFTEST_CASES, start=1):
            got = bool(scan_mechanisms(text, TIKTOK_MECHANISMS))
            ok = got == should_flag
            bad += 0 if ok else 1
            print(f"  {'✅' if ok else '❌'} 用例 {i}：预期"
                  f"{'判违规' if should_flag else '放行'}，实际"
                  f"{'判违规' if got else '放行'}")
        for i, (text, should_flag) in enumerate(GRAM_SELFTEST_CASES, start=1):
            got = bool(GRAM_PATTERN.search(text))
            ok = got == should_flag
            bad += 0 if ok else 1
            print(f"  {'✅' if ok else '❌'} 克重用例 {i}：预期"
                  f"{'判命中' if should_flag else '放行'}，实际"
                  f"{'判命中' if got else '放行'}")
        total = len(SELFTEST_CASES) + len(GRAM_SELFTEST_CASES)
        print(f"自测结果：{total - bad}/{total} 通过")
        return 0 if bad == 0 else 1

    failures: list[str] = []
    print(f"项目根：{ROOT}")
    print(f"校验时间：{datetime.now().astimezone().isoformat(timespec='seconds')}")

    # ---------------------------------------------------------------- 1 断言门禁
    section("1. 断言门禁：calc-*.py 全部 exit 0")
    calc_scripts = sorted(REPORTS.glob("calc-*.py"))
    for sp in calc_scripts:
        proc = run([sys.executable, "-u", str(sp)])
        # 断言行可能形如「断言 79 项，不匹配 0 项」或「断言 43/43 通过，失败 0 项」
        m = re.search(r"断言[^\n]{0,40}?(\d+)\s*/\s*(\d+)[^\n]{0,20}?失败\s*(\d+)|"
                      r"断言\s*(\d+)\s*项[^\n]{0,10}?不匹配\s*(\d+)\s*项", proc.stdout)
        stat = ""
        if m:
            stat = f"（{m.group(0).strip()}）"
        ok = proc.returncode == 0
        print(f"  {'✅' if ok else '❌'} {sp.name}{stat}")
        if not ok:
            failures.append(f"[断言] {sp.name} exit={proc.returncode}")
            tail = (proc.stdout or "")[-600:] + (proc.stderr or "")[-600:]
            for ln in tail.splitlines()[-8:]:
                print(f"      {ln}")

    # ---------------------------------------------------------------- 2 渲染幂等
    if not args.skip_render:
        section("2. 渲染幂等：render-*.py --apply 连跑两次，md sha256 须一致")
        renderers = sorted(REPORTS.glob("render-*.py"))
        for rp in renderers:
            before = artifact_hashes()
            r1 = run([sys.executable, "-u", str(rp), "--apply"])
            if r1.returncode != 0:
                print(f"  ❌ {rp.name} 渲染失败 exit={r1.returncode}")
                failures.append(f"[渲染] {rp.name} exit={r1.returncode}")
                continue
            mid = artifact_hashes()
            r2 = run([sys.executable, "-u", str(rp), "--apply"])
            after = artifact_hashes()
            changed = changed_artifacts(mid, after)
            ok = r2.returncode == 0 and not changed
            print(f"  {'✅' if ok else '❌'} {rp.name}"
                  + (f"  非幂等文件：{changed}" if changed else ""))
            if not ok:
                failures.append(f"[幂等] {rp.name} 变化文件 {changed}")
            # 渲染器若改动了本轮未提交的 md，提示核对
            delta = changed_artifacts(before, after)
            if delta:
                print(f"      ⚠️ 本次渲染改写了：{delta}（应与 JSON 一致；若非预期请复核）")

    # ---------------------------------------------------------------- 3 平台差异门禁
    section("3. 平台差异门禁：抖音侧禁 TikTok 词 / TikTok 侧禁抖音词")
    for fn in douyin_report_files():
        p = REPORTS / fn
        if not p.exists():
            continue
        hit = scan_mechanisms(p.read_text(encoding="utf-8"), TIKTOK_MECHANISMS)
        print(f"  {'✅' if not hit else '❌'} {fn}" + (f"  命中：{hit}" if hit else ""))
        if hit:
            failures.append(f"[平台差异-抖音侧] {fn} 命中 {hit}")

    for p in [REPORTS / fn for fn in tiktok_report_files()]:
        text = p.read_text(encoding="utf-8")
        hit = [t for t in DOUYIN_MECHANISMS
               if re.search(re.escape(t) + r"(?![A-Za-z_])", text)]
        hit += [t for t in TIKTOK_UNSUPPORTED if t in text]
        print(f"  {'✅' if not hit else '❌'} {p.name}" + (f"  命中：{hit}" if hit else ""))
        if hit:
            failures.append(f"[平台差异-TikTok侧] {p.name} 命中 {hit}")

    # ---------------------------------------------------------------- 4 跨站合并门禁
    section("4. 跨站合并门禁：TikTok 侧报告不得跨站加总")
    for p in [REPORTS / fn for fn in tiktok_report_files()]:
        text = p.read_text(encoding="utf-8")
        hit = []
        for pat in CROSS_SITE_PATTERNS:
            for m in re.finditer(pat, text):
                line = text[:m.start()].splitlines()[-1] + text[m.start():].splitlines()[0]
                # 否定式纪律声明（「禁止跨站加总」）不算违规
                if any(neg in line for neg in CROSS_SITE_NEGATIONS):
                    continue
                hit.append(f"{pat}@「{line.strip()[:40]}」")
        print(f"  {'✅' if not hit else '❌'} {p.name}" + (f"  命中：{hit}" if hit else ""))
        if hit:
            failures.append(f"[跨站合并] {p.name} 命中 {hit}")

    # ---------------------------------------------------------------- 5 克重数值门禁
    section("5. 克重数值门禁：抖音侧交付件不得出现克重数值（冲突登记语境豁免）")
    for fn in douyin_report_files():
        if fn in GRAM_EXEMPT:
            continue
        p = REPORTS / fn
        if not p.exists():
            continue
        hit = []
        for ln in p.read_text(encoding="utf-8").splitlines():
            if not GRAM_PATTERN.search(ln):
                continue
            if any(m in ln for m in GRAM_CONTEXT_MARKERS):
                continue
            hit.append(ln.strip()[:50])
        print(f"  {'✅' if not hit else '❌'} {fn}" + (f"  命中：{hit}" if hit else ""))
        if hit:
            failures.append(f"[克重数值] {fn} 命中 {hit}")

    # ---------------------------------------------------------------- 汇总
    section("汇总")
    if failures:
        print(f"❌ 失败 {len(failures)} 项：")
        for f in failures:
            print("   -", f)
        return 1
    print("✅ 全部门禁通过（断言 / 渲染幂等 / 平台差异 / 跨站合并 / 克重数值）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
