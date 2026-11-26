"""D&T's local component, PSD, and managed revision bridge."""

from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from copy_constraints import LAYER_FIELDS, CopyConstraintError, normalize_copy


PIECES = {"CN": "P01", "PH": "P02"}
TEXT_IDS = tuple(LAYER_FIELDS)
CATEGORIES = {"B": "背景", "I": "图片", "M": "模块", "T": "文本"}
OUTPUTS = {"png": "original.png", "psd": "layered.psd", "review": "review-numbered.png"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024


class PosterError(ValueError):
    """A customer-facing rendering or version validation error."""


def _save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _image_digest(task_dir):
    path = task_dir / "product.png"
    if not path.is_file() or path.resolve() != path or path.stat().st_size > MAX_IMAGE_BYTES:
        raise PosterError("请提供任务目录内不超过 12 MB 的 PNG 商品图。")
    source = path.read_bytes()
    try:
        with Image.open(BytesIO(source), formats=["PNG"]) as image:
            width, height = image.size
            if max(width, height) > 4096 or width * height > 8_000_000 or image.n_frames != 1:
                raise PosterError("商品图须为单帧 PNG，最长边不超过 4096，且最多 800 万像素。")
            image.verify()
        with Image.open(BytesIO(source), formats=["PNG"]) as image:
            image.load()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise PosterError("商品 PNG 无法完整解码，请重新选择文件。") from error
    return sha256(source).hexdigest()


def _text(value, layer_id):
    try:
        return normalize_copy(value, LAYER_FIELDS[layer_id])
    except CopyConstraintError as error:
        raise PosterError(f"{layer_id} {error}") from None


def _layout(task_dir, task, copy):
    site = task.get("site")
    if site not in PIECES:
        raise PosterError("当前支持中国抖音和 TikTok 菲律宾。")
    # Use left aligned text: the current PSD engine writes the matching native anchor.
    elements = [
        dict(short="B01", type="rect", role="background", x=0, y=0, width=720, height=880,
             fill="#F0EEE5" if site == "CN" else "#EAF0EB"),
        dict(short="M01", type="rect", role="card", x=44, y=53, width=632, height=247,
             radius=26, fill="#DCE6E6" if site == "CN" else "#D5E6DA"),
        dict(short="T01", type="text", label="标题", x=74, y=81, width=572, height=111,
             fontSize=37, fill="#143233", text=_text(copy.get("title") or task.get("title"), "T01")),
        dict(short="T02", type="text", label="副标题", x=74, y=194, width=572, height=82,
             fontSize=25, fill="#2E514D", text=_text(copy.get("subtitle"), "T02")),
        dict(short="I01", type="image", role="product", x=84, y=350, width=552, height=437, fit="contain",
             path=str(task_dir / "product.png"), asset_sha256=_image_digest(task_dir),
             provenance="Exact local task product PNG"),
    ]
    return _register_layout(task, elements)


def _register_layout(task, elements):
    site = task["site"]
    piece = PIECES[site]
    font = ("Microsoft YaHei", "MicrosoftYaHei") if site == "CN" else ("Arial", "ArialMT")
    entries = {}
    for element in elements:
        short = element.pop("short")
        internal = f"DT-{piece}-{short}"
        element["id"] = internal
        if element["type"] == "text":
            element.update(fontFamily=font[0], psdFontName=font[1], padding=8, textInsetY=10, lineHeight=1.12)
        entries[internal] = {
            "internal_id": internal,
            "psd_layer_id": int(sha256(internal.encode()).hexdigest()[:7], 16),
            "short_id": short,
            "qualified_id": f"{piece}/{short}",
            "category": CATEGORIES[short[0]],
            "active": True,
        }
    return {
        "schema": "dt-layout-v1", "id": piece, "width": 720, "height": 880, "site": site,
        "task_id": task["task_id"], "sku": task.get("sku"), "scope": "local_poster",
        "elements": elements,
        "user_id_registry": {"schema": "dt-user-element-registry-v1", "pieces": {piece: {"entries": entries}}},
    }


@contextmanager
def _artwork_lock(task_dir):
    artwork = task_dir / "artwork"
    artwork.mkdir(exist_ok=True)
    if artwork.resolve() != artwork:
        raise PosterError("作品目录必须保存在当前任务内。")
    lock = artwork / ".client-render.lock"
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise PosterError("该作品正在生成或上次执行中断，请先检查任务恢复状态。") from error
    try:
        yield artwork
    finally:
        lock.rmdir()


def _run_engine(engine, node, args, attempt):
    # Credentials loaded by the application's model adapter are not passed to the renderer.
    public_environment = {
        "ALLUSERSPROFILE", "APPDATA", "DT_PSD_DEPENDENCIES", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH",
        "PATHEXT", "PROGRAMDATA", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "WINDIR",
    }
    renderer_env = {key: value for key, value in os.environ.items() if key.upper() in public_environment}
    try:
        run = subprocess.run(
            [node, str(engine.resolve(strict=True)), *map(str, args)], cwd=engine.parent,
            env=renderer_env, capture_output=True, encoding="utf-8", errors="replace",
            timeout=90, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _save(attempt / "engine.json", {"error_type": type(error).__name__})
        raise PosterError("海报引擎未能启动或超时，原有版本已保留。") from error
    _save(attempt / "engine.json", {"returncode": run.returncode, "stderr": run.stderr[-3000:]})
    if run.returncode:
        if "glyph ink" in run.stderr or "text_width_overflow" in run.stderr or "text_height_overflow" in run.stderr:
            raise PosterError("文字超出可见范围，请缩短或分行后重新提交。")
        if "STALE_BASE_VERSION" in run.stderr:
            raise PosterError("编辑底版已过期，请选择当前版本。")
        raise PosterError("海报导出失败，原有版本已保留。详情可在本地引擎回执中查看。")


def _result(task_dir, out, version):
    data = _read(out / "manifest.json")
    if (data["psd"]["status"] != "EXPORTED_AND_READ_BACK"
            or data["nativeText"]["count"] != len(TEXT_IDS)
            or not data["exportIntegrity"]["pass"]):
        raise PosterError("PNG 或可编辑 PSD 的读回验证未通过。")
    files = {}
    for key, filename in OUTPUTS.items():
        path = out / filename
        if not path.is_file() or path.resolve() != path or not path.stat().st_size:
            raise PosterError("输出文件不完整，未生成可取回版本。")
        files[key] = path.relative_to(task_dir).as_posix()
    return {
        "version": version, "piece": data["layoutId"], "files": files,
        "selected_elements": [
            {"id": layer["qualifiedReference"], "text": layer["native_text_requested"]}
            for layer in data["layers"] if layer["type"] == "text"
        ],
    }


def _current_version(artwork):
    return max((int(path.name[1:]) for path in artwork.iterdir()
                if path.is_dir() and re.fullmatch(r"v[1-9][0-9]*", path.name)), default=0)


def _attempt(artwork):
    path = artwork / f"attempt-{uuid4().hex}"
    path.mkdir()
    return path


def _complete(task_dir, out, version, attempt, engine, node, args, *, finalize=None):
    if out.exists():
        raise PosterError(f"v{version} 已存在，原有文件保留。")
    try:
        _run_engine(engine, node, args, attempt)
        result = _result(task_dir, out, version)
        return finalize(result) if finalize else result
    except Exception:
        if out.is_dir():
            out.rename(attempt / "incomplete-output")
        raise


def render_initial(task_dir: Path, task: dict, copy: dict, engine: Path, node: str, *,
                   approved_layout=None, finalize=None) -> dict:
    task_dir = task_dir.resolve(strict=True)
    with _artwork_lock(task_dir) as artwork:
        if _current_version(artwork):
            raise PosterError("作品已有首版，请选择当前版本进行局部修改。")
        attempt = _attempt(artwork)
        layout = attempt / "initial-layout.json"
        _save(layout, _layout(task_dir, task, copy) if approved_layout is None else approved_layout)
        out = artwork / "v1"
        return _complete(task_dir, out, 1, attempt, engine, node, ["render", layout, out], finalize=finalize)


def render_revision(task_dir: Path, base_version: int, element_ref: str, text: str,
                    engine: Path, node: str, *, reviewed_changes=None, finalize=None) -> dict:
    task_dir = task_dir.resolve(strict=True)
    with _artwork_lock(task_dir) as artwork:
        current = _current_version(artwork)
        if not current:
            raise PosterError("作品暂无可编辑版本。")
        if type(base_version) is not int or base_version != current:
            raise PosterError(f"编辑底版已过期，请选择当前版本 v{current}。")
        base = artwork / f"v{current}"
        if base.resolve() != base:
            raise PosterError("底版必须保存在当前作品内。")
        manifest = _result(task_dir, base, current)
        if reviewed_changes is None:
            entries = {entry["id"]: entry["text"] for entry in manifest["selected_elements"]}
            if not isinstance(element_ref, str) or element_ref not in entries:
                raise PosterError(f"请选择当前作品 {manifest['piece']} 中的可编辑文字编号。")
            updated = _text(text, element_ref.split("/")[-1])
            if updated == entries[element_ref]:
                raise PosterError("文字未变化，无需创建新版本。")
            reviewed_changes = {"text": updated}
        attempt = _attempt(artwork)
        patch = attempt / "patch.json"
        _save(patch, {"selectedId": element_ref, "changes": reviewed_changes})
        out = artwork / f"v{current + 1}"
        return _complete(task_dir, out, current + 1, attempt, engine, node,
                         ["edit", base / "layout.json", patch, out / "layout.json", out], finalize=finalize)
