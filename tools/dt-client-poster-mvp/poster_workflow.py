"""Run the installed D&T selling-point gate for the local single-poster client."""

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from model_connector import CLOUD_MODE
from profile_paths import open_readonly_database

_MARKETS = {
    "CN": ("Douyin-CN", "douyin-visual-designer", "Simplified Chinese"),
    "PH": ("TikTok-PH", "tiktok-visual-designer", "Filipino or natural Taglish"),
}
_SKILLS = {"A": "ecommerce-detail-page-generator-zh", "B": "ecommerce-visual-planning-skill"}
_METHOD = "local_single_poster_role_and_skill_projection_v1"
_RESTRICTED = re.compile(
    r"包治|治愈|根治|疗效保证|零风险|全网第一|国家级|顶级|官方认证|"
    r"\b(cures?|heals?|gamot sa|lunas sa|guaranteed results?|clinically proven|officially certified)\b",
    re.IGNORECASE,
)
_DRIVERS = {
    "rational": ("理性驱动型", ("续航", "功率", "适配", "capacity", "compatib", "battery", "voltage")),
    "problem": ("痛点驱动型", ("清洁", "收纳", "整理", "装在一起", "keep", "organiz", "storage", "clean")),
    "occasion": ("情感价值型", ("礼盒", "礼物", "节日", "gift", "birthday", "holiday")),
    "visual": ("视觉驱动型", ("外观", "颜色", "配色", "color", "colour", "décor", "decor")),
}
_FOCUS = {
    "rational": "Use at most one explicitly supplied performance detail, or use a neutral identity caption.",
    "problem": "Present one supplied practical use in the supporting line; avoid before/after and guaranteed effects.",
    "occasion": "Use a supplied occasion or a neutral product caption; omit implied packaging, contents and delivery promises.",
    "visual": "Lead with recognizable product identity and one supplied appearance detail; omit unsupported quality or material claims.",
    "identity_only": "Lead with product identity. Use a neutral supporting line when no clear feature is supported.",
}


class WorkflowError(ValueError):
    """A bounded local role, evidence, or copy gate did not complete."""


def _save(path, payload):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _read_source(project, relative, required):
    try:
        path = project / relative
        raw = path.read_bytes()
        contents = raw.decode("utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise WorkflowError(f"无法读取本地岗位资源 {relative}。") from error
    if not all(item in contents for item in required):
        raise WorkflowError(f"本地岗位资源版本已变更，需核对适配规则：{relative}。")
    return contents, {"file": relative, "sha256": sha256(raw).hexdigest(), "applied_sections": list(required)}


def _run_gate(project, attempt, state, task_id, phase, receipt=None, *, user_copy_file=None):
    out = attempt / f"{phase}.json"
    argv = [sys.executable, "-E", "-s", "-B", str(project / "scripts/product_visual_flow.py"), phase,
            "--task-id", task_id, "--state", str(state), "--output", str(out)]
    if receipt is not None:
        argv.extend(("--receipt", str(receipt)))
    if user_copy_file is not None:
        argv.extend(("--user-copy-file", str(user_copy_file)))
    env = {key: value for key, value in os.environ.items() if key.upper() in
           {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE"}}
    try:
        completed = subprocess.run(argv, cwd=project, env=env, capture_output=True, timeout=25, check=False,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode or not out.is_file():
            _save(attempt / f"{phase}-error.json",
                  {"phase": phase, "exit_code": completed.returncode,
                   "error_type": "copy_gate_rejected"})
            raise WorkflowError(f"本地视觉文案门禁未完成（{phase}）。")
        return json.loads(out.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        if isinstance(error, WorkflowError):
            raise
        _save(attempt / f"{phase}-error.json", {"phase": phase, "error_type": type(error).__name__})
        raise WorkflowError(f"本地视觉文案门禁无法运行（{phase}）。") from error


def _image_evidence(task_dir, task):
    image_path = task_dir / "product.png"
    expected = task.get("source", {}).get("sha256")
    if image_path.is_symlink() or not image_path.is_file() or image_path.stat().st_size > 12 * 1024 * 1024:
        raise WorkflowError("当前任务缺少有效的本地商品图。")
    raw = image_path.read_bytes()
    actual = sha256(raw).hexdigest()
    if expected != actual:
        raise WorkflowError("本地商品图与任务原始素材不一致，请重新提交。")
    try:
        with Image.open(image_path, formats=["PNG"]) as image:
            width, height = image.size
            if max(width, height) > 4096 or width * height > 8_000_000 or image.n_frames != 1:
                raise WorkflowError("本地商品图尺寸或帧数无效。")
            image.load()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise WorkflowError("本地商品图无法解码。") from error
    return {"sha256": actual, "dimensions_px": [width, height], "decoded": True,
            "semantic_observation": "not_performed", "used_for": "original retained in the poster"}


def _role(project, task, task_dir):
    platform, role, language = _MARKETS[task["site"]]
    _, identity = _read_source(project, f"runtime-agents/{role}/agent.md", ("## 职责边界", "## 产出标准"))
    method_heading = "## 2. 主图设计规范" if task["site"] == "CN" else "## 2. 四层本地化视觉规范"
    _, playbook = _read_source(project, f".agents/skills/{role}-playbook/SKILL.md",
                               ("## Product visual production gate (2026-11-17)", method_heading))
    _, flow = _read_source(project, "docs/product-visual-flow.md",
                           ("## Required copy gate", "## Module responsibilities"))
    _, requirements = _read_source(project, "templates/美工设计/主图需求单.md",
                                   ("## 二、这张图要达成什么（必填）", "## 五、素材与合规"))
    _, review = _read_source(project, "templates/美工设计/视觉验收清单.md",
                             ("## 三、合规（红字项）", "## 六、交付完整性"))
    locale = {"language": language, "currency_and_units": "only if evidenced for target market",
              "scene": "supplied context only", "culture": "omit unsupported customs or symbols"}
    brief = {"role": role, "site": platform, "locale": locale, "purpose": "buyer identifies product and one relevant use",
             "selling_points_limit": 1, "hierarchy": ["title", "subtitle"],
             "image": _image_evidence(task_dir, task),
             "fact_status": "customer_supplied_unverified",
             "simulated_product": task["source"].get("simulated") is True,
             "reviews_pending": ["human copy/fact review", "platform publication review"]}
    if task["site"] == "PH":
        brief["reviews_pending"].append("Filipino native-language and cultural review")
    return brief, [identity, playbook, flow, requirements, review]


def _skill(project, prepared, brief, task):
    route = prepared.get("route")
    if route not in _SKILLS or prepared.get("layout_allowed") is not False:
        raise WorkflowError("本地视觉文案门禁未返回有效的 A/B 任务。")
    base = f".agents/skills/{_SKILLS[route]}"
    required = ("## 核心工作流", "卖点-证据映射", "文案撰写") if route == "A" else (
        "## Step 2：转化驱动力判断 + 风格锁", "## Step 4：《Storyboard》→ 确认门 2",
        "## Step 6：五维独立自审")
    body, skill = _read_source(project, f"{base}/SKILL.md", required)
    if prepared.get("skill_sha256") != skill["sha256"] or Path(prepared["skill_path"]) != project / base / "SKILL.md":
        raise WorkflowError("分配的 Skill 与本地岗位来源不一致。")

    fact_lines = [line.strip() for line in re.split(r"[\r\n。；;]+", task["facts"]) if line.strip()]
    candidate = next((line for line in fact_lines if not _RESTRICTED.search(line)), None)
    inputs = {"product_fact": candidate, "input_fact_lines": len(fact_lines),
              "additional_facts_omitted": len(fact_lines) - (candidate is not None),
              "fact_source": "customer supplied, not independently verified" if candidate else "none",
              "product_image_claims": "none; only file identity and dimensions locally inspected",
              "risk_check": "basic offline phrase filter; independent review pending",
              "allowed_claim": "single ordinary supplied use/feature" if candidate else "neutral product identity"}
    if route == "A":
        _, evidence = _read_source(project, f"{base}/references/claim-evidence-rules.md",
                                   ("## 证据门槛", "## 安全降级模式"))
        _, buyer = _read_source(project, f"{base}/references/buyer-decision-framework.md",
                                ("## 决策阶段标签", "## 页面规划规则"))
        plan = {"method": "buyer_question_and_claim_evidence",
                "question": "What is this product and why might it be relevant?",
                "claim_evidence": [{"intent": "product_identity", "source": "task.title"},
                                   {"intent": "single_use", "source": "task.facts" if candidate else None,
                                    "allowed": candidate is not None}]}
        guidance = ("Title answers what the product is. Subtitle conveys one ordinary supported use from "
                    "the selected product fact, or stays neutral when there is no eligible fact.")
        return inputs, plan, guidance, [skill, evidence, buyer]

    _, checks = _read_source(project, f"{base}/references/compliance-rules.md", ("##",))
    normalized = f"{task['title']} {candidate or ''}".casefold()
    matches = [name for name, (_, words) in _DRIVERS.items() if any(word in normalized for word in words)]
    driver = matches[0] if len(matches) == 1 else "identity_only"
    questions = []
    if driver in _DRIVERS:
        family = _DRIVERS[driver][0]
        section = body.split(f"**{family}：**", 1)[-1].split("\n**", 1)[0]
        questions = re.findall(r"^\|\s*KV[1-5]\s*\|[^|]+\|([^|]+)\|", section, re.MULTILINE)
        if len(questions) != 5 or len(set(questions)) != 5:
            raise WorkflowError("本地视觉策划 Skill 的分镜分工已变更，需复核。")
    plan = {"method": "conversion_driver_and_single_panel_storyboard", "driver": driver,
            "driver_candidates": matches, "classification_strength": "provisional" if matches else "unknown",
            "selected_panel": {"goal": "recognize product and understand one supporting point",
                               "visual_first": "provided_product_only", "copy_slots": ["T01", "T02"]},
            "full_storyboard": "out_of_scope_single_poster",
            "source_driver_reference_count": len(questions),
            "storyboard_reference_question_local_only": questions[0].strip(" *") if questions else None,
            "style_constraints": {"palette": "market template", "placement": "one original product asset",
                                  "lighting": "preserve uploaded image", "fonts": "market template native text",
                                  "prohibited_drift": "no additional logos, badges, claims or generated visual evidence"},
            "human_review": "no five-dimension score or approval inferred"}
    return inputs, plan, _FOCUS[driver], [skill, checks]


def _already_completed(path, task_id):
    if not path.exists():
        return False
    with closing(open_readonly_database(path)) as db:
        row = db.execute("SELECT receipt FROM rounds WHERE task=?", (task_id,)).fetchone()
        return bool(row and row[0])


def run_user_poster_workflow(*, project, task_dir, task, accept):
    """Pin explicit customer copy before rendering the existing local template."""
    folder = task_dir / "copy-workflow"
    folder.mkdir(exist_ok=True)
    if folder.resolve() != folder:
        raise WorkflowError("文案回执必须保存在当前任务内。")
    attempt = folder / "user-v1"
    attempt.mkdir()
    copy = task["confirmed_copy"]
    copy_body = json.dumps(copy, ensure_ascii=False, sort_keys=True)
    copy_file = attempt / "confirmed-copy.json"
    with copy_file.open("x", encoding="utf-8", newline="") as stream:
        stream.write(copy_body)
    gate = _run_gate(project, attempt, attempt / "local-gate.sqlite", task["id"],
                     "prepare", user_copy_file=copy_file)
    if (gate["route"] != "USER" or gate["layout_allowed"] is not True
            or gate["copy"]["copy"]["body"] != copy_body):
        raise WorkflowError("确认文案与本机直用回执不一致。")
    run_details = {"workflow": {**task["run"]["workflow"],
                                "copy_origin": "explicit_user_copy",
                                "copy_bound_version": 1,
                                "copy_sha256": gate["copy"]["sha256"],
                                "input_image_sha256": task["source"]["sha256"],
                                "layout_source": "fixed_local_template",
                                "product_identity_independently_verified": False,
                                "evidence_dir": attempt.relative_to(task_dir).as_posix()}}
    return accept(copy, run_details), run_details


def run_poster_workflow(*, project, profile, task_dir, task, config, key, draft, accept):
    """Return (renderer_result, run_details) after an accepted local render.

    Workbench supplies one worker and an OS profile lock. A failed task never
    writes the profile rotation; its attempt files and SQLite state remain
    separate for audit. Demo and customer generations use separate rotations.
    """
    task_id = task.get("id")
    profile = Path(profile).resolve(strict=True)
    project = Path(project).resolve(strict=True)
    task_dir = Path(task_dir).resolve(strict=True)
    site = task.get("site")
    mode = config.get("mode")
    if (site not in _MARKETS or mode not in {"ollama", "demo", CLOUD_MODE} or not isinstance(task_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", task_id) or task_dir != profile / "tasks" / task_id):
        raise WorkflowError("视觉工作流的任务、站点或本地目录无效。")
    if not isinstance(task.get("facts"), str) or not task["facts"].strip():
        raise WorkflowError("请提供至少一条可用于海报的商品事实。")

    folder = task_dir / "copy-workflow"
    folder.mkdir(exist_ok=True)
    if folder.resolve() != folder:
        raise WorkflowError("文案审核目录必须位于当前本地任务中。")
    attempt = folder / uuid4().hex[:16]
    attempt.mkdir()
    live_state = profile / f"poster-copy-{mode}.sqlite"
    if live_state.resolve() != live_state:
        raise WorkflowError("该文案轮转已提交，拒绝重复运行。")
    with TemporaryDirectory(dir=profile, prefix=".copy-") as work:
        return _run_attempt(project, task_dir, task, config, key, draft, accept, attempt, live_state,
                            Path(work) / "rotation.sqlite")


def _run_attempt(project, task_dir, task, config, key, draft, accept, attempt, live_state, state):
    task_id = task["id"]
    mode = config["mode"]
    if live_state.exists():
        shutil.copyfile(live_state, state)
        if _already_completed(state, task_id):
            raise WorkflowError("该文案轮转已提交，拒绝重复运行。")

    stage = "prepare"
    try:
        prepared = _run_gate(project, attempt, state, task_id, "prepare")
        brief, role_sources = _role(project, task, task_dir)
        inputs, plan, strategy, skill_sources = _skill(project, prepared, brief, task)
        route = prepared["route"]
        _save(attempt / "local-plan.json", {"role": brief, "route": route, "inputs": inputs, "plan": plan,
                                            "role_sources": role_sources, "skill_sources": skill_sources})
        stage = "copy_adapter"
        # Guidance is curated. No private role/Skill text, hashes, file paths
        # or original image bytes enter the customer-supplied model request.
        guidance = (
            f"Use {brief['locale']['language']} suitable for the requested market. "
            "Fit the supplied poster boxes. Retain one first point of focus and two short text layers. "
            "Omit unsupported price, currency conversions, measurements, certification, comparative results and "
            "cultural or seasonal references. The supplied image remains unchanged. "
            + strategy
        )
        facts = {"selected_product_fact": inputs["product_fact"], "fact_status": inputs["fact_source"],
                 "evidence_boundary": "customer assertion; no image claim confirmed"}
        drafted = draft(config, key, brief["site"], task["title"], facts, guidance)
        copy = {field: drafted[field] for field in ("title", "subtitle")}
        if any(not isinstance(value, str) or not value.strip() or _RESTRICTED.search(value)
               for value in copy.values()):
            raise WorkflowError("文案包含无依据的功效或背书，需修改资料后再提交。")
        if mode != "demo" and (drafted.get("simulated") is not False or drafted.get("response_validated") is not True):
            raise WorkflowError("未能核对客户模型的结构化文案回包。请检查模型兼容性后再试。")

        stage = "copy_gate"
        execution = ("local_role_skill_projection_fixed_offline_demo" if mode == "demo"
                     else "local_role_skill_projection_with_customer_model_adapter")
        receipt = attempt / "receipt.json"
        _save(receipt, {"variant": route, "copy": copy, "provenance": {
            "execution_method": execution, "role": brief["role"], "native_agent_dispatch": False,
            "model_requested": mode != "demo", "read_sources": role_sources + skill_sources,
            "applied_local_plan": "local-plan.json",
            "copy_origin": "fixed offline demonstration" if mode == "demo" else "customer-configured adapter",
        }})
        completed = _run_gate(project, attempt, state, task_id, "complete", receipt)
        approved = _run_gate(project, attempt, state, task_id, "copy")
        if (approved.get("copy") != copy or completed.get("sha256") != approved.get("sha256")
                or approved.get("route") != route
                or approved.get("invocation_id") != "sha256:" + sha256(receipt.read_bytes()).hexdigest()):
            raise WorkflowError("渲染前文案与来源回执不一致，未生成作品。")
        run_details = {"workflow": {
            "method": _METHOD, "role": brief["role"], "route": route, "skill": _SKILLS[route],
            "copy_origin": "fixed_offline_demo" if mode == "demo" else "customer_model_adapter",
            "native_agent_dispatch": False, "copy_sha256": approved["sha256"],
            "evidence_dir": attempt.relative_to(task_dir).as_posix(),
            "input_image": "decoded_original_only_no_semantic_inspection",
            "skill_scope": "selected method adapted to a single poster",
        }}
        if mode != "demo":
            run_details["response_validated"] = True
            run_details["runtime"] = drafted["runtime"]

        stage = "render"
        result = accept(approved["copy"], run_details)
        if not isinstance(result, dict) or result.get("version") != 1:
            raise WorkflowError("渲染结果未返回可接受的首版。")

        stage = "rotation_publish"
        _save(attempt / "render-accepted.json", {"version": result["version"], "route": route,
                                                 "copy_sha256": approved["sha256"]})
        # Same-volume atomic replace after the renderer confirms actual PNG/PSD.
        # Keep the exact committed gate transaction as attempt evidence.
        shutil.copyfile(state, attempt / "rotation.sqlite")
        candidate_path = attempt / "publish.sqlite"
        shutil.copyfile(state, candidate_path)
        os.replace(candidate_path, live_state)
        run_details["workflow"]["rotation_committed"] = True
        return result, run_details
    except Exception as error:
        _save(attempt / "failure.json", {"stage": stage, "error_type": type(error).__name__,
                                         "rotation_committed": False})
        raise

