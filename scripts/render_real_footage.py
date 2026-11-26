"""Render a D&T real-footage edit with the project-local OpenMontage runtime."""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from bisect import bisect_right
from decimal import Decimal
from pathlib import Path

from setup_openmontage import COMMIT, FFMPEG_VERSION, RUNTIME, sha256, verify_runtime

WIDTH, HEIGHT = 1080, 1920
MEDIA_FORMATS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm"}

REVIEW_ROLES = {"overview", "detail", "use", "hero", "other"}
REVIEW_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", re.ASCII)
WINDOWS_DEVICE_NAME = re.compile(r"CON|PRN|AUX|NUL|(?:COM|LPT)[0-9]", re.IGNORECASE)
SRT_TIMESTAMP = r"([0-9]{2}):([0-5][0-9]):([0-5][0-9]),([0-9]{3})"
SRT_TIMING = re.compile(rf"{SRT_TIMESTAMP}[ \t]+-->[ \t]+{SRT_TIMESTAMP}")


def run_media(name, *args):
    result = subprocess.run(
        [str(RUNTIME / "bin" / f"{name}.exe"), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode:
        raise ValueError(f"{name}: {result.stderr.strip() or result.returncode}")
    return result.stdout


def probe(path):
    return json.loads(run_media(
        "ffprobe", "-v", "error", "-protocol_whitelist", "file",
        "-show_streams", "-show_format", "-of", "json", path,
    ))


def local_file(root, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a nonempty path within the job folder.")
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"File missing or outside the job folder: {value}")
    return path


def seconds(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"Expected a finite numeric timestamp: {value}")
    return float(value)


def read_timeline(job, root):
    if job.get("production_route") != "real_footage_auto_edit":
        raise ValueError("Expected production_route real_footage_auto_edit.")
    platform, site = job["platform"], job["site"]
    if (platform, site) != ("douyin", "CN") and not (
        platform == "tiktok" and isinstance(site, str) and len(site) == 2
        and site.isascii() and site.isalpha() and site.isupper() and site != "CN"
    ):
        raise ValueError("Use douyin/CN or a specific TikTok market such as tiktok/ID.")
    product_id = job["publish_product_id"]
    if not isinstance(product_id, str) or not product_id.strip():
        raise ValueError("Missing publish_product_id.")

    assets = {}
    for item in job["asset_manifest"]:
        asset_id = item["asset_id"]
        if not isinstance(asset_id, str) or not asset_id.strip() or asset_id in assets:
            raise ValueError(f"Invalid or duplicate asset ID: {asset_id}")
        if item.get("rights") not in {"owned", "authorized"} or item["product_id"] != product_id:
            raise ValueError(f"Missing rights or product mismatch: {asset_id}")
        path = local_file(root, item["path"])
        if sha256(path) != item["sha256"]:
            raise ValueError(f"SHA-256 mismatch: {asset_id}")
        media = probe(path)
        streams = media["streams"]
        formats = set(media["format"]["format_name"].split(","))
        if not formats <= MEDIA_FORMATS or not any(s["codec_type"] == "video" and not
                                                  s.get("disposition", {}).get("attached_pic")
                                                  for s in streams):
            raise ValueError(f"Expected local MP4/MOV/Matroska/WebM video: {asset_id}")
        duration = float(media["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"Invalid source duration: {asset_id}")
        assets[asset_id] = (path, duration, any(s["codec_type"] == "audio" for s in streams))

    cuts, mapping = [], []
    cursor = 0.0
    for shot in job["cuts"]:
        asset_id = shot["source_asset_id"]
        if asset_id not in assets:
            raise ValueError(f"Cut refers to an unknown asset: {asset_id}")
        path, duration, has_audio = assets[asset_id]
        start, end = seconds(shot["in_seconds"]), seconds(shot["out_seconds"])
        if not 0 <= start < end <= duration or shot.get("speed", 1) != 1:
            raise ValueError(f"Cut out of bounds or not original speed: {asset_id} {start}–{end}")
        cuts.append({"source": str(path), "in_seconds": start, "out_seconds": end, "speed": 1.0})
        mapping.append({
            "source_asset_id": asset_id, "source_in": start, "source_out": end,
            "timeline_in": round(cursor, 6), "timeline_out": round(cursor + end - start, 6),
            "source_has_audio": has_audio,
        })
        cursor += end - start
    if not cuts:
        raise ValueError("No cuts supplied.")
    return cuts, mapping, cursor


def review_points(job, cuts, mapping):
    raw_points = job["review_points"]
    if not isinstance(raw_points, list) or not 1 <= len(raw_points) <= 8:
        raise ValueError("review_points must contain 1 to 8 points.")

    # The normal receipt rounds cut boundaries. Use the same cut spans at
    # full input precision so a point exactly on a cut maps to the next shot.
    cursor = Decimal(0)
    starts = []
    for cut in cuts:
        starts.append(cursor)
        cursor += Decimal(str(cut["out_seconds"])) - Decimal(str(cut["in_seconds"]))
    assets = {item["asset_id"]: item for item in job["asset_manifest"]}

    points, seen = [], set()
    previous_time = -1.0
    for point in raw_points:
        if not isinstance(point, dict) or set(point) != {"id", "time_seconds", "role"}:
            raise ValueError("Each review point requires only id, time_seconds and role.")
        point_id, role = point["id"], point["role"]
        if (not isinstance(point_id, str) or not REVIEW_ID.fullmatch(point_id)
                or WINDOWS_DEVICE_NAME.fullmatch(point_id) or point_id.lower() in seen):
            raise ValueError(f"Unsafe or duplicate review point ID: {point_id!r}")
        if not isinstance(role, str) or role not in REVIEW_ROLES:
            raise ValueError(f"Unknown review point role: {role!r}")
        try:
            requested = seconds(point["time_seconds"])
        except OverflowError as error:
            raise ValueError("Review point time must be finite.") from error
        time = Decimal(str(requested))
        if not 0 <= time < cursor or requested <= previous_time:
            raise ValueError("Review point times must be strictly increasing within the timeline.")

        index = bisect_right(starts, time) - 1
        shot = mapping[index]
        asset_id = shot["source_asset_id"]
        source_time = Decimal(str(shot["source_in"])) + (time - starts[index])
        points.append({
            "id": point_id,
            "role": role,
            "requested_time_seconds": requested,
            "source_asset_id": asset_id,
            "source_time_seconds": float(source_time),
            "source_sha256": assets[asset_id]["sha256"],
        })
        previous_time = requested
        seen.add(point_id.lower())
    return points


def validate_review_srt(path):
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError("Review requires a nonempty UTF-8 SRT.")

    previous_end_ms = 0
    for cue in re.split(r"\n(?:[ \t]*\n)+", text.strip()):
        lines = cue.splitlines()
        if (len(lines) < 3 or re.fullmatch(r"[0-9]+", lines[0].strip()) is None
                or not any(line.strip() for line in lines[2:])
                or any("-->" in line for line in lines[2:])):
            raise ValueError("Review SRT cue must contain an ID, timing and text.")
        match = SRT_TIMING.fullmatch(lines[1].strip())
        if match is None:
            raise ValueError("Review SRT requires HH:MM:SS,mmm --> HH:MM:SS,mmm timing.")
        hours, minutes, seconds_part, ms, end_h, end_m, end_s, end_ms = map(int, match.groups())
        start = ((hours * 60 + minutes) * 60 + seconds_part) * 1000 + ms
        end = ((end_h * 60 + end_m) * 60 + end_s) * 1000 + end_ms
        if not previous_end_ms <= start < end:
            raise ValueError("Review SRT cues must have positive length and be ordered without overlap.")
        previous_end_ms = end
    return previous_end_ms / 1000


def write_review(stage, video, points, receipt, job_root):
    # Inputs were verified by the normal source check before rendering. Check
    # the cited files again before creating any review delivery.
    cited = {point["source_asset_id"] for point in points}
    for item in receipt["asset_manifest"]:
        if item["asset_id"] in cited:
            path = local_file(job_root, item["path"])
            if sha256(path) != item["sha256"]:
                raise ValueError(f"Review source changed during rendering: {item['asset_id']}")

    frames = stage / "review_frames"
    frames.mkdir()
    for point in points:
        point_id = point["id"]
        requested = point["requested_time_seconds"]
        if requested >= receipt["duration"]:
            raise ValueError(f"Review point falls beyond the rendered file: {point_id}")
        frame = frames / f"{point_id}.jpg"
        run_media("ffmpeg", "-nostdin", "-v", "error", "-xerror", "-protocol_whitelist", "file",
                  "-i", video, "-ss", f"{requested:.9f}", "-map", "0:v:0", "-frames:v", "1",
                  "-c:v", "mjpeg", "-threads", "1", "-f", "image2", "-update", "1", frame)
        if not frame.is_file() or not frame.stat().st_size:
            raise ValueError(f"FFmpeg did not produce a review frame: {point_id}")
        run_media("ffmpeg", "-nostdin", "-v", "error", "-xerror", "-protocol_whitelist", "file",
                  "-i", frame, "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-")
        point.update(frame_path=f"review_frames/{frame.name}", frame_sha256=sha256(frame),
                     frame_size_bytes=frame.stat().st_size)

    review = {
        "schema_version": 1,
        "video_sha256": receipt["video_sha256"],
        "subtitle_sha256": receipt["subtitle_sha256"],
        "scope": (
            "JPEGs use requested seeks in the rendered MP4. Source times are planned cut mappings. "
            "Frame cadence and cut rounding can change the decoded frame near a boundary. "
            "Inspect the actual image visually. These samples provide no render, model, or "
            "commerce compliance verdict."
        ),
        "points": points,
    }
    (stage / "review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def render(job_file, output):
    verify_runtime()
    job_file, output = job_file.resolve(), output.resolve()
    if output.exists():
        raise ValueError(f"Use a new output directory: {output}")
    # Upstream concat and subtitle filters quote the working directory with apostrophes.
    if "'" in str(output.parent):
        raise ValueError("Use an output parent without apostrophes.")
    job = json.loads(job_file.read_text(encoding="utf-8-sig"))
    cuts, mapping, expected = read_timeline(job, job_file.parent)
    points = review_points(job, cuts, mapping) if "review_points" in job else None
    subtitles = local_file(job_file.parent, job["subtitle_path"])
    if subtitles.suffix.lower() != ".srt" or "-->" not in subtitles.read_text(encoding="utf-8-sig"):
        raise ValueError("Expected a prepared UTF-8 SRT.")

    os.environ["PATH"] = str(RUNTIME / "bin") + os.pathsep + os.environ.get("PATH", "")
    os.environ["OPENMONTAGE_ASSET_CACHE"] = "0"
    sys.path.insert(0, str(RUNTIME / "source"))
    from tools.video.video_compose import VideoCompose

    decisions = {"metadata": {"compose_target": {"width": WIDTH, "height": HEIGHT, "fit": "pad"}},
                 "cuts": cuts}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".montage-", dir=output.parent) as temp:
        stage = Path(temp) / "delivery"
        stage.mkdir()
        srt_path = stage / "captions.srt"
        shutil.copyfile(subtitles, srt_path)
        if points is not None:
            caption_end = validate_review_srt(srt_path)
            if caption_end > expected + 1e-9:
                raise ValueError("Review SRT ends after the planned timeline.")
        video = stage / "montage.mp4"
        result = VideoCompose().execute({
            "operation": "compose", "edit_decisions": decisions, "output_path": str(video),
            "subtitle_path": str(srt_path), "preset": "fast",
            "subtitle_style": {"font": "Microsoft YaHei", "font_size": 14, "outline_width": 1.5},
        })
        if not result.success:
            raise ValueError(f"OpenMontage compose: {result.error}")

        media = probe(video)
        video_stream = next((s for s in media["streams"] if s["codec_type"] == "video"), {})
        audio_stream = next((s for s in media["streams"] if s["codec_type"] == "audio"), {})
        duration = float(media["format"]["duration"])
        if (
            (video_stream.get("width"), video_stream.get("height"), video_stream.get("codec_name"))
            != (WIDTH, HEIGHT, "h264") or audio_stream.get("codec_name") != "aac"
            or not math.isfinite(duration) or abs(duration - expected) > 0.10 + len(cuts) * 0.08
        ):
            raise ValueError(f"Unexpected video/audio or duration: {duration:.3f}s vs {expected:.3f}s")
        run_media("ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", video,
                  "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-")
        receipt = {
            "platform": job["platform"], "site": job["site"],
            "publish_product_id": job["publish_product_id"],
            "reference_product_id": job.get("reference_product_id"), "openmontage_commit": COMMIT,
            "ffmpeg_ffprobe_version": FFMPEG_VERSION, "size": [WIDTH, HEIGHT],
            "duration": duration, "expected_duration": round(expected, 6),
            "decode": "passed", "timeline": mapping, "asset_manifest": job["asset_manifest"],
            "video_sha256": sha256(video), "subtitle_sha256": sha256(srt_path),
        }
        for name, data in (("edit_decisions.json", decisions), ("render_receipt.json", receipt)):
            (stage / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if points is not None:
            if caption_end > duration:
                raise ValueError("Review SRT ends after the rendered output.")
            write_review(stage, video, points, receipt, job_file.parent)
        stage.rename(output)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        render(args.job, args.output_dir)
    except (ValueError, TypeError, OSError, KeyError) as error:
        parser.exit(1, f"{error}\n")
    print(args.output_dir.resolve())
