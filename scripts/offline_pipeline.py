"""Offline report verification. Exit 0=passed, 2=known data limits, 1=failed."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
KNOWN_EMPTY = {
    "Q-P1_hashtag_detail-rows": "P1_hashtag_detail",
    "Q-P1_hashtag_video_list-rows": "P1_hashtag_video_list",
    "Q-P1_explore_post-rows": "P1_explore_post",
}


def hashes(base: Path, suffixes=None):
    return {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob("*"))
            if p.is_file() and (suffixes is None or p.suffix in suffixes)}


def differences(before, after):
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def audit_status(report, exit_code):
    checks = report.get("checks", [])
    if not checks or not report.get("datasets"):
        return "failed"
    failed = [c for c in checks if c.get("结果") != "通过"]
    if exit_code == 0 and not failed:
        return "passed"
    ids = [c.get("编号") for c in failed]
    if (exit_code == 1 and len(ids) == len(KNOWN_EMPTY)
            and set(ids) == set(KNOWN_EMPTY)
            and all(report["datasets"].get(ds, {}).get("rows") == 0
                    for ds in KNOWN_EMPTY.values())):
        return "known_data_limits"
    return "failed"


def run_stage(name, script, args, output, env):
    command = [sys.executable, "-X", "utf8", "-u", str(ROOT / script), *args]
    print(f"Running {name}...", flush=True)
    try:
        proc = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                              encoding="utf-8", timeout=900)
        code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        code, stdout, stderr = -1, "", str(exc)
    log = output / f"{name}.log"
    log.write_text(stdout + "\n" + stderr, encoding="utf-8", newline="\n")
    print(f"  exit={code}; {log.name}", flush=True)
    return {"stage": name, "exit": code, "log": log.name}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "work/offline-runs/latest")
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    # Logs must never be written into frozen inputs or generated report directories.
    if not output.is_relative_to(ROOT / "work"):
        parser.error("--output-dir must be inside this project's work directory")
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    env["PYTHONPATH"] = str(ROOT / "scripts/offline_guard") + os.pathsep + env.get("PYTHONPATH", "")
    snapshots = ROOT / "data-pipeline/snapshots"
    before_snapshots = hashes(snapshots)
    report = {"started_utc": datetime.now(timezone.utc).isoformat(),
              "python": sys.version, "project": str(ROOT),
              "mode": "existing frozen snapshots; Python network calls blocked",
              "stages": [], "errors": []}

    def stage(name, script, *params):
        item = run_stage(name, script, list(params), output, env)
        report["stages"].append(item)
        return item["exit"]

    if stage("rules-selftest", "scripts/verify_reports.py", "--selftest") != 0:
        report["errors"].append("Rules selftest failed")
    if not report["errors"] and stage("reports-first", "scripts/verify_reports.py") != 0:
        report["errors"].append("Report verification failed; see reports-first.log")

    audit_file = output / "sample-audit.json"
    # Remove only the previous generated audit result, to avoid stale success on failure.
    audit_file.unlink(missing_ok=True)
    audit_exit = stage("sample-audit", "data-pipeline/scripts/sample_quality_audit.py",
                       "--json", str(audit_file))
    try:
        audit = json.loads(audit_file.read_text(encoding="utf-8"))
        status = audit_status(audit, audit_exit)
        report["audit"] = {"status": status, "checks": len(audit["checks"]),
                           "passed": sum(c.get("结果") == "通过" for c in audit["checks"]),
                           "failures": [c for c in audit["checks"] if c.get("结果") != "通过"],
                           "rows": audit["total_rows"]}
        if status == "failed":
            report["errors"].append("Sample audit has unexpected failure(s)")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["errors"].append(f"Audit output is missing or invalid: {exc}")

    if not report["errors"]:
        first = hashes(ROOT / "docs", {".md", ".json"})
        if stage("reports-repeat", "scripts/verify_reports.py") != 0:
            report["errors"].append("Repeated report verification failed")
        changes = differences(first, hashes(ROOT / "docs", {".md", ".json"}))
        report["repeat_changed_artifacts"] = changes
        if changes:
            report["errors"].append("Whole report pipeline is not idempotent")

    snapshot_changes = differences(before_snapshots, hashes(snapshots))
    report["snapshot_changed_files"] = snapshot_changes
    report["snapshot_file_count"] = len(before_snapshots)
    if snapshot_changes:
        report["errors"].append("Frozen snapshots changed during verification")
    report["status"] = ("failed" if report["errors"] else
                        report.get("audit", {}).get("status", "failed"))
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                          encoding="utf-8", newline="\n")
    print(f"Result: {report['status']}; summary: {output / 'summary.json'}", flush=True)
    return 1 if report["errors"] else 2 if report["status"] == "known_data_limits" else 0


if __name__ == "__main__":
    raise SystemExit(main())
