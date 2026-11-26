#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当日采集结果冻结成快照（报告只引用快照，不引用活动数据目录）。

为什么要有这一步（2026-11-05 实测教训）：
    采集器写的是活动目录 `data/<platform>/<dataset>/<day>/`，而报告的统计脚本读的是快照。
    两者不同步时，脚本与报告的数字会打架（曾出现"脚本 87 条 / 报告 120 条"）。
    冻结 = 拷贝一份不可变副本 + 记 manifest（记录数 + sha256 前 16 位），让每个数字都可追溯。

用法：
    python3 scripts/freeze_snapshot.py --day 2026-11-05 \
        --datasets hot_total_low_fan hot_total_topic hot_total_hot_word \
        --dir 2026-11-05-tikhub --channel "TikHub REST（L2 托管 API，$0.001/次，吃免费额度）"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SNAP = ROOT / "snapshots"


def sha16(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def count_records(path: pathlib.Path) -> int:
    with path.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, help="数据日期目录，如 2026-11-05")
    ap.add_argument("--datasets", nargs="+", required=True, help="要冻结的 dataset 名")
    ap.add_argument("--platform", default="douyin")
    ap.add_argument("--dir", default="", help="快照目录名（默认与 --day 同名）")
    ap.add_argument("--channel", default="", help="来源通道说明，写进 manifest")
    args = ap.parse_args()

    snap_name = args.dir or args.day
    snap_root = SNAP / snap_name
    snap_root.mkdir(parents=True, exist_ok=True)

    manifest_path = snap_root / "_manifest.json"
    manifest = []
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_ds = {m["dataset"]: m for m in manifest}

    missing = []
    for ds in args.datasets:
        src = DATA / args.platform / ds / args.day / f"{ds}.jsonl"
        if not src.exists():
            missing.append(ds)
            continue
        dst_dir = snap_root / ds
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{ds}.jsonl"
        shutil.copy2(src, dst)
        n = count_records(dst)
        entry = {
            "dataset": ds,
            "records": n,
            "sha256_16": sha16(dst),
            "source_day": args.day,
            "path": f"data-pipeline/snapshots/{snap_name}/{ds}/{ds}.jsonl",
        }
        if args.channel:
            entry["channel"] = args.channel
        by_ds[ds] = entry
        print(f"  ✅ {ds}: {n} 条 → {dst.relative_to(ROOT.parent)}")

    manifest = sorted(by_ds.values(), key=lambda m: m["dataset"])
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    total = sum(m["records"] for m in manifest)
    print(f"\n快照 {snap_name}: {len(manifest)} 个 dataset，合计 {total} 条")
    print(f"manifest → {manifest_path.relative_to(ROOT.parent)}")
    if missing:
        print(f"⚠️  未找到（跳过）：{', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
