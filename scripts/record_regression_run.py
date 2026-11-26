"""Record one local Python regression command and preserve its exit code."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(paths):
    files = set()
    for path in paths:
        path = path.resolve(strict=True)
        if path.is_dir():
            files.update(
                entry for entry in path.rglob("*")
                if entry.is_file() and "__pycache__" not in entry.parts
            )
        elif path.is_file():
            files.add(path)
    result = {}
    for path in sorted(files):
        with path.open("rb") as handle:
            result[str(path)] = hashlib.file_digest(handle, "sha256").hexdigest()
    return result


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, usage="%(prog)s [options] -- <Python arguments>"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, default=ROOT)
    parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    parser.add_argument("--code", type=Path, nargs="+", required=True)
    if "--" not in argv or argv[-1] == "--":
        parser.error("provide Python arguments after --")
    split = argv.index("--")
    options = parser.parse_args(argv[:split])
    output = options.output_dir.resolve()
    if not output.is_relative_to(ROOT / "work"):
        parser.error("output directory must be within project work/")
    cwd = options.cwd.resolve(strict=True)
    inputs = fingerprint(options.inputs)
    code = fingerprint([Path(__file__), ROOT / "scripts/offline_guard/sitecustomize.py", *options.code])
    if not inputs:
        parser.error("no input files found")
    output.mkdir(parents=True, exist_ok=False)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [
        str(ROOT / "scripts/offline_guard"), env.get("PYTHONPATH"),
    ]))
    command = [sys.executable, "-X", "utf8", "-u", *argv[split + 1:]]
    record = {
        "cwd": str(cwd),
        "command": command,
        "git_head": git("rev-parse", "HEAD"),
        "worktree_dirty": bool(git("status", "--porcelain")),
        "python": sys.version,
        "input_sha256": inputs,
        "code_sha256": code,
        "environment": {key: env[key] for key in (
            "PYTHONPATH", "PYTHONUTF8", "PYTHONIOENCODING",
            "PYTHONDONTWRITEBYTECODE", "IMAGEIO_FFMPEG_EXE",
        ) if key in env},
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    started = time.perf_counter()
    stdout, stderr = output / "stdout.log", output / "stderr.log"
    with stdout.open("wb") as out, stderr.open("wb") as err:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=out, stderr=err)
    record.update(
        exit_code=result.returncode,
        finished_utc=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=round(time.perf_counter() - started, 3),
        log_sha256={Path(path).name: value for path, value in fingerprint([stdout, stderr]).items()},
    )
    (output / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"exit={result.returncode} {output / 'run.json'}", flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
