"""Local D&T workbench. No shared profiles or ambient model credentials."""

import argparse
import sys
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import threading
from zipfile import BadZipFile, ZipFile
import webbrowser
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from PIL import Image, ImageOps
from model_connector import (
    CLOUD_MODE, ConnectorError, draft_copy, protect_key, require_fixed_backend_route, test_connection, unprotect_key,
    validate_config,
)
from poster import PosterError, render_initial, render_revision
from copy_constraints import CopyConstraintError, LAYER_FIELDS, normalize_copy, public_limits
from poster_workflow import WorkflowError, run_poster_workflow, run_user_poster_workflow
import planned_poster
import poster_copy_executor
import interactive_agent_bridge
import foreground_agent_bridge
import fresh_poster_agent_bridge
import foreground_detail_outputs
import foreground_mall_combo
from prototype_models import AUTHORIZED_MOCKS, ModelWorkspace, WorkspaceError
from provider_model_workspace import ProviderModelWorkspace, fixture_url
from provider_task_routes import PATH as PROVIDER_TASK_PATH, TaskModelError, TaskModelOperations, task_binding
from ordinary_visual_chain import OrdinaryVisualChain, TRANSPORT as ORDINARY_VISUAL_TRANSPORT
from fresh_poster_contract import SCOPE as FRESH_FOREGROUND
import mall_agent_worker
from profile_paths import resolve_profile, verify_profile_path
from transcription_job import LANGUAGES, MAX_UPLOAD_BYTES, MediaError, run_transcription, validate_upload
from role_tools import RoleToolError, public_tools, run_role_tool
import long_detail
import main_images
import role_tools_extra
import visual_market
import visual_copy
import workflow_campaign
import workflow_commerce
import workflow_ph_campaign_offer
import workflow_cn_campaign_offer
import workflow_competitor
import workflow_competitor_detail
import workflow_ph_competitor_detail
import workflow_director
import workflow_director_version_intake
import workflow_live
import workflow_finance_offline
import workflow_offline_schedule
import workflow_live_rehearsal
import workflow_live_preflight
import workflow_live_paid_slot
import workflow_live_recording_review
import workflow_live_listening
import workflow_shoot_handoff
import workflow_ph_live_controller
import workflow_ph_creator_card
import workflow_cn_campaign_playbook
import workflow_campaign_reviews
import workflow_selection_trial
import workflow_selection_screen
import workflow_campaign_options
import workflow_ad_experiment
import workflow_cn_warehouse
import workflow_shop_stock_handoff
import workflow_director_review
import workflow_director_topic
import workflow_teardown_library
import workflow_teardown_report
import workflow_director_teardown_methods
import workflow_poster_delivery
import workflow_channel_alignment
import workflow_ad_channel_intake
import workflow_live_program
import workflow_ph_listing_library
import workflow_cn_sales_plans
import workflow_lead_delivery
import workflow_offline_planning
import workflow_offline_review
import workflow_teardown_handoff
import workflow_teardown_anchor
import workflow_director_preflight
import workflow_cn_selection_lifecycle
import workflow_local_insight
import workflow_admission
import workflow_ph_competitor_cod
import workflow_ad_lead_plan
import workflow_ad_mode_readback
import workflow_ad_creative_custody
import workflow_ph_stock_readback
import workflow_ph_cert_intake
import workflow_competitor_compare_readiness
import workflow_shop_period
import workflow_portfolio_handoff
import workflow_competitor_handoff
import workflow_campaign_sensitivity
import workflow_competitor_price_band
import workflow_competitor_bulletin
import workflow_competitor_dossier
import workflow_competitor_supplier
import workflow_competitor_watch
import workflow_creator_cohorts
import workflow_lead_weekly
import workflow_lead_monthly
import workflow_live_team
import workflow_live_offline
import workflow_media_library
import workflow_visual_surfaces
import workflow_shop_image_review
import workflow_lead
import workflow_lead_environment
import workflow_lead_transfer
import workflow_lead_quarter
import workflow_ads
import workflow_teardown

APP = Path(__file__).resolve().parent
ARTIFACTS = {"png": ("original.png", "image/png"), "psd": ("layered.psd", "application/octet-stream"),
             "review": ("review-numbered.png", "image/png")}
PLATFORMS = {"CN": ("Douyin-CN", "douyin-visual-designer"), "PH": ("TikTok-PH", "tiktok-visual-designer")}
BUSY = {"queued", "drafting", "rendering", "revising"}
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_BODY = 6 * 1024 * 1024
MAX_FOREGROUND_VISUAL_BODY = 28 * 1024 * 1024
MAX_MEDIA_BODY = ((MAX_UPLOAD_BYTES + 2) // 3) * 4 + 8192
MAX_WORKFLOW_BODY = MAX_MEDIA_BODY + 1024 * 1024 + 8192
LOCAL_PACKET_MODULES = {tool: module for module in (workflow_ph_creator_card, workflow_cn_campaign_playbook, workflow_selection_trial, workflow_selection_screen, workflow_campaign_options, workflow_ad_experiment, workflow_cn_warehouse, workflow_shop_stock_handoff, workflow_director_review, workflow_director_topic, workflow_teardown_library, workflow_teardown_report, workflow_director_teardown_methods, workflow_director_version_intake, workflow_poster_delivery, workflow_channel_alignment, workflow_ad_channel_intake, workflow_live_program, workflow_ph_listing_library, workflow_cn_sales_plans, workflow_lead_delivery, workflow_offline_planning, workflow_offline_review, workflow_teardown_handoff, workflow_teardown_anchor, workflow_shop_period, workflow_director_preflight, workflow_cn_selection_lifecycle, workflow_local_insight, workflow_admission, workflow_ph_live_controller, workflow_ph_cert_intake, workflow_ad_creative_custody, workflow_ph_competitor_cod, workflow_shoot_handoff, workflow_ad_lead_plan, workflow_ad_mode_readback, workflow_ph_stock_readback, workflow_competitor_compare_readiness)
                        for tool in module.TOOLS}

WORKFLOW_MODULES = (
    workflow_live_recording_review, workflow_live_listening,
    workflow_director, workflow_live, workflow_campaign, workflow_commerce,
    workflow_competitor, workflow_lead, workflow_ads, workflow_teardown, workflow_ph_campaign_offer,
    workflow_competitor_detail, workflow_live_team, workflow_lead_environment, workflow_lead_transfer, workflow_lead_quarter,
    workflow_live_offline, workflow_media_library, workflow_visual_surfaces, workflow_cn_campaign_offer,
    workflow_ph_competitor_detail, workflow_finance_offline, workflow_offline_schedule, workflow_live_rehearsal, workflow_portfolio_handoff, workflow_competitor_handoff, workflow_campaign_sensitivity, workflow_competitor_price_band, workflow_lead_weekly, workflow_lead_monthly, workflow_competitor_bulletin, workflow_competitor_watch, workflow_creator_cohorts, workflow_competitor_dossier, workflow_live_preflight, workflow_competitor_supplier, workflow_live_paid_slot, workflow_ph_creator_card, workflow_cn_campaign_playbook, workflow_campaign_reviews, workflow_selection_trial, workflow_selection_screen, workflow_campaign_options, workflow_ad_experiment, workflow_cn_warehouse, workflow_shop_stock_handoff, workflow_director_review, workflow_director_topic, workflow_teardown_library, workflow_teardown_report, workflow_director_teardown_methods, workflow_director_version_intake, workflow_poster_delivery, workflow_channel_alignment, workflow_ad_channel_intake, workflow_live_program, workflow_ph_listing_library, workflow_cn_sales_plans, workflow_lead_delivery, workflow_offline_planning, workflow_offline_review, workflow_teardown_handoff, workflow_teardown_anchor, workflow_shop_period, workflow_director_preflight, workflow_cn_selection_lifecycle, workflow_local_insight, workflow_admission, workflow_ph_live_controller, workflow_ph_cert_intake, workflow_ad_creative_custody, workflow_ph_competitor_cod, workflow_shoot_handoff, workflow_ad_lead_plan, workflow_ad_mode_readback, workflow_ph_stock_readback, workflow_competitor_compare_readiness,
)
CAMPAIGN_OFFERS = {"CN": workflow_cn_campaign_offer, "PH": workflow_ph_campaign_offer}
CAMPAIGN_INTAKES = {module.INTAKE for module in CAMPAIGN_OFFERS.values()}
CAMPAIGN_FILES = {tool for module in CAMPAIGN_OFFERS.values() for tool in (module.REVIEW,module.INTAKE)}
ARTWORK_RENDERERS = {main_images.TOOL_ID: main_images.render, long_detail.TOOL_ID: long_detail.render}
WORKFLOW_ERRORS = (MediaError, RoleToolError, main_images.MainImageError, long_detail.LongDetailError,
                    workflow_shop_image_review.WorkflowError, *(module.WorkflowError for module in WORKFLOW_MODULES))
MALL_VISUAL_FILES = {
    "mall_front_original": ("image/png", None),
    "mall_detail_original": ("image/png", None),
    "mall_visual_receipt": ("application/json; charset=utf-8", "mall-visual-readback.json"),
}
LOCAL_FILES = {
    **MALL_VISUAL_FILES,
    **foreground_mall_combo.FILES,
    "json": ("application/json; charset=utf-8", "result.json"),
    "text": ("text/plain; charset=utf-8", "transcription.txt"),
    "srt": ("text/plain; charset=utf-8", "transcription.srt"),
    "receipt": ("application/json; charset=utf-8", "run-receipt.json"),
    "video": ("video/mp4", "video.mp4"),
    "report": ("text/plain; charset=utf-8", "report.md"),
    "timeline": ("application/json; charset=utf-8", "timeline.json"),
    "input": ("application/json; charset=utf-8", "input.json"),
    "contact": ("image/png", "contact-sheet.png"),
    "preview": ("image/png", None),
    "bundle": ("application/zip", "main-images.zip"),
    "cover": ("image/png", "cover.png"),
    "detail_bundle": ("application/zip", "long-detail.zip"),
    "evidence_bundle": ("application/zip", "detail-evidence.zip"),
    "director_bundle": ("application/zip", "director-versions.zip"),
}
LOCAL_FILE = re.compile(r"/api/tasks/([0-9a-f]{32})/files/(" + "|".join(LOCAL_FILES) + ")")
# Worker output paths used by existing named-dispatch receipts.
NAMED_WORKER_FILES = {
    "campaign-ops": {
        "json": "worker/campaign-ledger.json", "receipt": "worker/campaign-ledger-receipt.json",
        "report": "worker/campaign-ledger.md",
    },
    "live-ops": {
        "input": "worker/live-window-replay/input.json", "json": "worker/live-window-replay/review.json",
        "receipt": "worker/live-window-replay/receipt.json", "report": "worker/live-window-replay/review.md",
    },
}
REQUEST_ID = re.compile(r"[A-Za-z0-9_.-]{8,100}")
TASK_PATH = re.compile(r"/api/tasks/([0-9a-f]{32})(?:/(revise|versions/([1-9][0-9]*)/(png|psd|review)))?")
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                "/workspace-design.js": ("workspace-design.js", "application/javascript; charset=utf-8"),
                "/workspace-design.css": ("workspace-design.css", "text/css; charset=utf-8"),
                "/app.css": ("app.css", "text/css; charset=utf-8")}

class APIError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


def utc():
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path, value):
    temporary = path.with_name("." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def bind_data_path(root, candidate):
    real = candidate.resolve(strict=True)
    if not real.is_relative_to(root):
        raise APIError(404, "missing_file", "本机结果文件不可用。")
    return real


def public_model(settings):
    return {**settings["connection"], "has_key": bool(settings.get("protected_key")), "revision": settings.get("revision", "")}


def require_current_cloud_consent(payload, settings):
    revision = settings.get("revision")
    consent = payload.get("cloud_consent")
    if settings["connection"]["mode"] != CLOUD_MODE and consent is None:
        return
    if settings["connection"]["mode"] != CLOUD_MODE or not revision or consent != revision:
        raise APIError(409, "cloud_consent_required", "请刷新并确认当前云端模型、发送内容及可能产生的费用。")


def from_image_data(value):
    try:
        if not isinstance(value, dict):
            raise ValueError
        encoded = value.get("data") or value.get("base64")
        if not isinstance(encoded, str):
            raise ValueError
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        if len(encoded) > (4 * MAX_IMAGE_BYTES // 3 + 8):
            raise ValueError
        raw = base64.b64decode(encoded, validate=True)
        if not 1 <= len(raw) <= MAX_IMAGE_BYTES:
            raise ValueError
        with Image.open(BytesIO(raw), formats=("PNG", "JPEG")) as source:
            if source.width * source.height > 9_000_000 or source.width < 20 or source.height < 20:
                raise ValueError
            if getattr(source, "is_animated", False):
                raise ValueError
            oriented = ImageOps.exif_transpose(source)
            image = oriented.convert("RGBA")
            image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, "PNG")
        return output.getvalue()
    except (ValueError, OSError, IndexError, binascii.Error, Image.DecompressionBombError):
        raise APIError(400, "invalid_image", "请选择不超过 4 MB 的有效 PNG 或 JPEG 商品图。") from None


@contextmanager
def profile_lock(folder):
    """The OS releases this lock on process exit, including abrupt termination."""
    lock = (folder / ".app.lock").open("a+b")
    held = False
    try:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            from fcntl import flock, LOCK_EX, LOCK_NB
            flock(lock.fileno(), LOCK_EX | LOCK_NB)
        held = True
        yield
    finally:
        if held and os.name == "nt":
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        lock.close()


class Workbench:
    def __init__(self, profile, project, engine, node, *, test_copy_executor=None,
                 poster_copy_runner=None, poster_copy_kind=None, cloud_fixture=None, agent_fixture=None,
                 agent_role_fixture=False, foreground_token_plan=False,
                 customer_model_workspace=False, account_catalog_fixture=None):
        self.foreground_token_plan = (foreground_token_plan and profile.name.startswith("bava-tokenplan-")
                                      and agent_fixture is None and cloud_fixture is None)
        self.foreground_active = None
        self.fresh_foreground = self.foreground_token_plan and profile.name.startswith("bava-tokenplan-new-")
        self.fresh_fixture_profile = self.fresh_foreground and profile.name.startswith("bava-tokenplan-new-test-")
        if type(agent_role_fixture) is not bool or (agent_role_fixture and agent_fixture is None):
            raise ValueError("Mall model simulation requires the customer loopback fixture.")
        self.agent_role_fixture = agent_role_fixture
        if agent_fixture is not None:
            interactive_agent_bridge.endpoint(agent_fixture)
            if cloud_fixture is not None or not profile.name.startswith("ab-test-agent-"):
                raise ValueError("Interactive simulation requires its own isolated profile.")
        self.agent_fixture = agent_fixture
        self.agent_active = None
        if cloud_fixture is not None:
            target = validate_config({"mode": CLOUD_MODE, "base_url": cloud_fixture, "model": "fixture"})
            if not profile.name.startswith("ab-test-") or urlsplit(target["base_url"]).hostname != "127.0.0.1":
                raise ValueError("Cloud fixture requires an isolated profile and exact loopback target.")
            cloud_fixture = target["base_url"]
        self.cloud_fixture = cloud_fixture
        self.model_gate = threading.Lock()
        if (poster_copy_runner is None) != (poster_copy_kind is None):
            raise ValueError("A poster copy adapter requires its execution scope.")
        if poster_copy_runner is not None and (not callable(poster_copy_runner)
                or poster_copy_kind not in {poster_copy_executor.TEST, poster_copy_executor.LOCAL}):
            raise ValueError("Invalid local poster copy adapter.")
        if poster_copy_kind == poster_copy_executor.TEST and not profile.name.startswith("ab-test-"):
            raise ValueError("Test copy requires a dedicated isolated profile.")
        self.poster_copy_runner = poster_copy_runner
        self.poster_copy_kind = poster_copy_kind
        if test_copy_executor is not None and not profile.name.startswith("ab-test-"):
            raise ValueError("Copy substitution requires a dedicated test profile.")
        self.test_copy_executor = test_copy_executor
        self.root = profile
        self.model_workspace = (ModelWorkspace(profile) if self.fresh_fixture_profile
                                and profile.name.startswith("bava-tokenplan-new-test-design") else None)
        self.customer_models = (ProviderModelWorkspace(profile, account_catalog_fixture)
                                if customer_model_workspace else None)
        self.ordinary_visual_chain = self.customer_models is not None
        if self.customer_models and (self.model_workspace or not self.fresh_foreground):
            raise ValueError("Customer models require the separate native foreground route.")
        self.project = project
        self.engine = engine
        self.node = node
        self.gate = threading.RLock()
        self.provider_tasks = (TaskModelOperations(profile, self.customer_models, self.task, self.gate)
                               if self.customer_models is not None else None)
        self.settings_file = profile / "model.json"
        self.tasks_dir = profile / "tasks"
        self.tasks_dir.mkdir(exist_ok=True)
        self.fixtures = read_json(APP / "fixtures" / "manifest.json")
        core_tools = {tool["id"]: (tool, run_role_tool) for tool in public_tools()}
        extra_tools = {tool["id"]: (tool, self.run_extra_role) for tool in role_tools_extra.public_tools()}
        if core_tools.keys() & extra_tools.keys():
            raise ValueError("Duplicate local role tool")
        self.role_tools = {**core_tools, **extra_tools}
        self.workflows = {}
        for tool in workflow_shop_image_review.public_tools():
            if tool["id"] in self.role_tools:
                raise ValueError("Duplicate local workflow")
            self.role_tools[tool["id"]] = (tool, None)
        for module in WORKFLOW_MODULES:
            for tool in module.public_tools():
                if tool["id"] in self.role_tools:
                    raise ValueError("Duplicate local workflow")
                self.role_tools[tool["id"]] = (tool, None)
                self.workflows[tool["id"]] = module.run_workflow
        for site, row in PLATFORMS.items():
            fixture = self.fixtures[site]
            if sha256((APP / "fixtures" / fixture["file"]).read_bytes()).hexdigest() != fixture["sha256"]:
                raise ValueError("Synthetic fixture hash mismatch")
        self.store = {}
        self.requests = {}
        for folder in sorted(self.tasks_dir.iterdir()):
            if not re.fullmatch(r"[0-9a-f]{32}", folder.name) or folder.is_symlink():
                continue
            path = folder / "task.json"
            if not path.is_file():
                continue
            task = read_json(path)
            if task["id"] != folder.name:
                raise ValueError("Stored task identity mismatch")
            if (task.get("copy_gate", {}).get("execution_kind") == visual_copy.TEST
                    and self.test_copy_executor is None):
                continue
            if (task.get("run", {}).get("workflow", {}).get("execution_kind") == poster_copy_executor.TEST
                    and self.poster_copy_kind != poster_copy_executor.TEST):
                continue
            if self.is_cloud_fixture_task(task) and self.cloud_fixture is None:
                continue
            if self.is_fresh_fixture_task(task) and not self.fresh_fixture_profile:
                continue
            foreground = task.get("agent_session") or {}
            if (foreground.get("scope") in {foreground_agent_bridge.SCOPE,FRESH_FOREGROUND}
                    and foreground["state"] in interactive_agent_bridge.ACTIVE):
                task["agent_session"] = {**foreground, "state":"blocked", "reason":"interrupted"}
                atomic_write(path, task)
            if self.is_agent_fixture_task(task):
                if self.agent_fixture is None:
                    continue
                agent = task.get("agent_session")
                if agent and agent["state"] in interactive_agent_bridge.ACTIVE:
                    task["agent_session"] = {**agent, "state": "blocked", "reason": "interrupted"}
                    atomic_write(path, task)
            if (task["status"] == "drafting" and task.get("copy_intake")
                    and task.get("run", {}).get("mode") == "awaiting_skill"):
                task["status"] = visual_copy.WAITING
                task["message"] = "上次提炼中断。可用原任务重试或恢复已留回执。"
                task["copy_intake"] = {**task["copy_intake"], "state": "interrupted", "layout_allowed": False}
                atomic_write(path, task)
            elif (task["status"] in BUSY and task.get("copy_gate")
                    and not task["copy_gate"]["layout_allowed"]):
                task["status"] = visual_copy.WAITING
                task["message"] = "待提炼。上次文案阶段中断，可用原编号重试。"
                atomic_write(path, task)
            elif task["status"] in BUSY:
                task["status"] = "interrupted"
                task["message"] = ("上次运行中断。本机任务不会自动重跑，可重新提交。"
                                   if task.get("kind") else
                                   "上次运行中断。已保存的结果仍可查看，任务不会自动重新调用模型。")
                atomic_write(path, task)
            self.store[task["id"]] = task
            self.requests[task["request_id"]] = task["id"]
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dt-poster")

    @staticmethod
    def is_cloud_fixture_task(task):
        result = task.get("result")
        pins = task.get("source", {}).get("role_packet_pins")
        return (task.get("test_scope") == poster_copy_executor.CLOUD_TEST
                or task.get("run", {}).get("workflow", {}).get("execution_kind") == poster_copy_executor.CLOUD_TEST
                or isinstance(pins, dict) and pins.get("evidence_scope") == poster_copy_executor.CLOUD_TEST
                or isinstance(result, dict) and result.get("evidence_scope") == poster_copy_executor.CLOUD_TEST)

    @staticmethod
    def is_agent_fixture_task(task):
        result = task.get("result")
        pins = task.get("source", {}).get("role_packet_pins")
        kind = poster_copy_executor.INTERACTIVE_TEST
        return (task.get("test_scope") == kind
                or task.get("run", {}).get("workflow", {}).get("execution_kind") == kind
                or isinstance(pins, dict) and pins.get("evidence_scope") == kind
                or isinstance(result, dict) and result.get("evidence_scope") == kind)

    @staticmethod
    def is_fresh_fixture_task(task):
        kind = poster_copy_executor.FRESH_TEST
        return (task.get("run", {}).get("workflow", {}).get("execution_kind") == kind
                or (task.get("source", {}).get("role_packet_pins") or {}).get("evidence_scope") == kind
                or (task.get("source", {}).get("foreground_visual") or {}).get("model_mode") == "local_fixture")

    def poster_copy_scope(self, settings):
        config = settings["connection"]
        if config["mode"] != CLOUD_MODE:
            return self.poster_copy_kind
        target = config["base_url"]
        if self.cloud_fixture is not None:
            return poster_copy_executor.CLOUD_TEST if target == self.cloud_fixture else None
        if urlsplit(target).hostname in {"127.0.0.1", "localhost", "::1"}:
            return None
        return poster_copy_executor.CLOUD

    def settings(self):
        if self.model_workspace is not None or self.customer_models is not None:
            return {"connection": validate_config({"mode": "demo"})}
        if self.settings_file.exists():
            return read_json(self.settings_file)
        return {"connection": validate_config({"mode": "demo"})}

    def save_settings(self, payload):
        if self.model_workspace is not None or self.customer_models is not None:
            raise APIError(409,"model_settings_managed","此工作台的模型连接由独立设置入口处理。")
        connection = validate_config(payload)
        key = payload.get("key")
        if key is not None and not isinstance(key, str):
            raise APIError(400, "invalid_key", "访问令牌格式无效。")
        require_fixed_backend_route(connection, key)
        if self.cloud_fixture is not None and (connection["mode"] != CLOUD_MODE
                                               or connection["base_url"] != self.cloud_fixture):
            raise APIError(409, "cloud_fixture_target_only", "隔离云接口模拟只能配置指定回环地址。")
        with self.model_gate:
            previous = self.settings()
            result = {"connection": connection, "revision": uuid4().hex}
            if connection["mode"] != "demo" and not payload.get("forget_key"):
                if key:
                    result["protected_key"] = base64.b64encode(protect_key(key)).decode("ascii")
                elif (previous["connection"]["mode"] == connection["mode"]
                      and previous["connection"]["base_url"] == connection["base_url"]
                      and previous.get("protected_key")):
                    result["protected_key"] = previous["protected_key"]
            atomic_write(self.settings_file, result)
            return public_model(result)

    def connection(self, settings):
        if self.model_workspace is not None or self.customer_models is not None:
            raise APIError(409,"model_settings_managed","此工作台的语言会话需要独立模型连接。")
        config = settings["connection"]
        require_fixed_backend_route(config)
        key = None
        if blob := settings.get("protected_key"):
            key = unprotect_key(base64.b64decode(blob, validate=True))
        require_fixed_backend_route(config, key)
        return config, key

    def session(self):
        with self.gate:
            saved = self.settings()
        return {"profile": self.root.name, "settings": public_model(saved), "copy_limits": public_limits(),
                "poster_copy_capability": self.poster_copy_scope(saved),
                "poster_agent_fixture": self.agent_fixture is not None,
                "model_workspace_preview": self.model_workspace is not None,
                "model_workspace_customer": self.customer_models is not None,
                "model_workspace_protocol_fixture": False,
                "model_workspace_v2": self.customer_models is not None,
                "ordinary_visual_chain": self.ordinary_visual_chain,
                "model_workspace_catalog_fixture": bool(self.customer_models and self.customer_models.fixture),
                "transcription": {"max_upload_bytes": MAX_UPLOAD_BYTES, "languages": LANGUAGES},
                "role_tools": [tool for tool, _ in self.role_tools.values()],
                "main_images": {"sites": list(PLATFORMS), "slots": list(main_images.SLOTS),
                                "max_file_bytes": MAX_IMAGE_BYTES},
                "long_detail": {"max_file_bytes": MAX_IMAGE_BYTES, "sites": {
                    site: [{"id": key, "label": fields[0], "image_required": fields[2],
                            "fact": long_detail.PH_TYPED_MODULES[key][0] if site == "PH"
                            and key in long_detail.PH_TYPED_MODULES else None}
                           for key, fields in modules.items()]
                    for site, modules in (("CN", long_detail.MODULES), ("PH", long_detail.PH_MODULES))
                }},
                "fixtures": {
            site: {"site": site, "title": self.fixtures[site]["title"],
                   "facts": self.fixtures[site]["subtitle"], "simulated": True,
                   "sku": self.fixtures[site]["sku"]}
            for site in PLATFORMS
        }}

    def token_plan_poster_status(self, task):
        source = task.get("source") or {}
        agent = task.get("agent_session") or {}
        new = self.fresh_foreground and not task.get("kind") and (
            agent.get("scope") == FRESH_FOREGROUND or fresh_poster_agent_bridge.eligible(task))
        if task.get("kind") or task.get("site") != "CN" or not new and source.get("sku") != foreground_agent_bridge.SKU:
            return None
        run = task.get("run") or {}
        workflow = run.get("workflow") or {}
        kind = workflow.get("execution_kind")
        if (kind in poster_copy_executor.WORKBENCH_MOCK_KINDS or kind == poster_copy_executor.TEST
                or run.get("simulated") is True or (task.get("agent_session") or {}).get("mock") is True):
            evidence = "simulation"
        elif kind == poster_copy_executor.CLOUD or run.get("provider") == CLOUD_MODE:
            evidence = "fixed_backend"
        elif workflow.get("copy_route") == "USER":
            evidence = "confirmed_copy"
        else:
            evidence = "local_task"
        agent = task.get("agent_session") or {}
        foreground = agent.get("scope") in {foreground_agent_bridge.SCOPE,FRESH_FOREGROUND}
        setup = fresh_poster_agent_bridge.eligible(task) if new else foreground_agent_bridge.eligible(task)
        captured = task.get("foreground_detail") if new else None
        detail = None
        if captured is not None:
            missing = [{"field":row["module"],"reason":row["field"]} for row in captured["missing"]]
            detail = {"requested":True,"ready":not missing,"missing":missing,"fields":{
                key:{"fact":row["fact"],"image_kind":row["image_kind"]} for key,row in captured["fields"].items()}}
        demo_ready = (self.model_workspace is not None and (detail is None or detail["ready"])
                      and (not agent or foreground and agent["state"] == "blocked"))
        ordinary_ready = self.ordinary_visual_chain and new and detail and detail["ready"] and not agent
        available = bool((demo_ready or ordinary_ready) and self.foreground_token_plan
                         and setup and self.agent_active is None)
        state = (agent["state"] if foreground else "waiting_for_detail_evidence" if detail and not detail["ready"]
                 else "ready_for_customer" if available else "disabled")
        return {"state":state,"evidence":evidence,"can_start":available,"real_acceptance_passed":False,
                "flow":"new_poster" if new else "reviewed_bava",
                "detail":detail,
                "mall_task_id":agent.get("mall_task_id") if foreground else None,
                "proof_scope":agent.get("proof_scope") if foreground else None,
                "reason":agent.get("reason") if foreground else None}

    def serialize(self, task):
        private = {"request_id", "request_hash", "revision_requests", "local_files", "token_plan_acceptance"}
        output = {k: v for k, v in task.items() if k not in private}
        if token_plan := self.token_plan_poster_status(task):
            output["token_plan_acceptance"] = token_plan
        if task.get("kind"):
            if task["status"] == "interrupted" and task.get("source", {}).get("tool_id")==workflow_live_rehearsal.CONTROL:
                output["message"]="上次处理中断。核对资料后，可用完全相同的资料和人员条件加入 retry_interrupted=true 进行本机重试。"
            if (task["status"] == "interrupted"
                    and task.get("source", {}).get("tool_id") == workflow_lead.NAMED_TOOL):
                output["message"] = "上次执行中断，任务不会自动重跑。原编号只查看原记录，请使用新任务编号重新提交。"
            output["artifacts"] = {
                kind: f"/api/tasks/{task['id']}/files/{kind}" for kind in task.get("local_files", {})
            }
            if task["status"]=="ready" and task["source"].get("combined_visual"):
                output["artifacts"].update({kind:f"/api/tasks/{task['id']}/files/{kind}"
                                            for kind in foreground_mall_combo.FILES})
            if (task["status"] == "ready" and task["source"].get("tool_id") == "shop-product-facts"
                    and task["result"].get("visual_delivery")):
                output["artifacts"].update({kind: f"/api/tasks/{task['id']}/files/{kind}" for kind in MALL_VISUAL_FILES})
            return output
        output["versions"] = [
            {**item, "files": {kind: "/api/tasks/" + task["id"] + "/versions/" + str(item["version"]) + "/" + kind
                               for kind in ARTIFACTS}}
            for item in task["versions"]
        ]
        return output

    def task(self, ident):
        task = self.store.get(ident)
        if (task is None or (self.is_cloud_fixture_task(task) and self.cloud_fixture is None)
                or (self.is_agent_fixture_task(task) and self.agent_fixture is None)
                or (self.is_fresh_fixture_task(task) and not self.fresh_fixture_profile)):
            raise APIError(404, "missing_task", "当前本机工作区没有此任务。")
        return task

    def ordinary_mall_visible(self, task):
        source = task["source"]
        if not source.get("ordinary_chain_mall"):
            return True
        binding = source["combined_visual"]["binding"]
        parent = self.store.get(binding["task_id"])
        agent = parent.get("agent_session", {}) if parent else {}
        return (agent.get("transport") == ORDINARY_VISUAL_TRANSPORT
                and agent.get("session_id") == binding["session_id"]
                and agent.get("state") == "completed" and agent.get("mall_task_id") == task["id"])

    def require_visible_task(self, task):
        if not self.ordinary_mall_visible(task):
            raise APIError(404, "visual_chain_receipt_pending", "尚无已确认的商城整链收件。")
        return task

    def save_task(self, task):
        task["updated_at"] = utc()
        atomic_write(self.tasks_dir / task["id"] / "task.json", task)

    def status(self, task, state, message, **extra):
        with self.gate:
            task.update(status=state, message=message, **extra)
            self.save_task(task)

    def request_id(self, value):
        if not isinstance(value, str) or not REQUEST_ID.fullmatch(value):
            raise APIError(400, "invalid_request_id", "提交标识无效，请刷新页面后重试。")
        return value

    def create(self, payload, *, reviewed_flow=False):
        key = self.request_id(payload.get("request_id"))
        site = payload.get("site")
        title = payload.get("title")
        facts = payload.get("facts")
        if not isinstance(site, str) or site not in PLATFORMS:
            raise APIError(400, "invalid_site", "请选择中国抖音或菲律宾 TikTok Shop。")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 46 or any(not c.isprintable() for c in title):
            raise APIError(400, "invalid_title", "请填写 1 至 46 个字符的单行商品名称。")
        if not isinstance(facts, str) or not facts.strip() or len(facts) > 2000:
            raise APIError(400, "invalid_facts", "请填写可用于海报的商品事实，最多 2000 字。")
        copy = None
        use_plan = reviewed_flow and "product_ref" in payload
        if use_plan and (not isinstance(payload["product_ref"], str) or not planned_poster.REFERENCE.fullmatch(payload["product_ref"])):
            raise APIError(400, "invalid_product_ref", "排版计划需要本任务的有效商品编号。")
        if reviewed_flow:
            if payload.get("fixture") is not None:
                raise APIError(400, "fixture_not_production_input", "请上传本机原图；内置样例仅用于演示。")
            if "confirmed_copy" in payload:
                copy = payload["confirmed_copy"]
                if not isinstance(copy, dict) or set(copy) != {"title", "subtitle"}:
                    raise APIError(400, "invalid_confirmed_copy", "请同时填写已确认的标题和副标题。")
                try:
                    for field, value in copy.items():
                        if normalize_copy(value, field) != value:
                            raise CopyConstraintError("已确认文案含首尾空格，请修正后原样提交。")
                except CopyConstraintError as error:
                    raise APIError(400, "invalid_confirmed_copy", str(error)) from None
        fixture = payload.get("fixture")
        if fixture is not None:
            if fixture != site or payload.get("image") is not None:
                raise APIError(400, "invalid_fixture", "请使用同站点样例，或重新选择本机素材。")
            row = self.fixtures[site]
            image = (APP / "fixtures" / row["file"]).read_bytes()
            source = {"kind": "synthetic_fixture", "simulated": True, "sku": row["sku"], "source": row["source"]}
        else:
            image = from_image_data(payload.get("image"))
            source = {"kind": "local_upload", "simulated": False}
        source["sha256"] = sha256(image).hexdigest()
        if use_plan:
            source["sku"] = payload["product_ref"]
        has_detail = "detail" in payload
        if has_detail and not (self.fresh_foreground and reviewed_flow and site=="CN" and use_plan
                               and copy is None and not source["simulated"] and source.get("sku") != foreground_agent_bridge.SKU):
            raise APIError(400,"visual_evidence_scope","组合生产仅用于本机新商品、商品编号和原图，确认文案留空。")
        submission = [site, title, facts, source]
        if reviewed_flow:
            submission.extend(("reviewed_poster_v1", copy))
        if has_detail:
            submission.extend(("foreground_detail",payload["detail"]))
        signature = sha256(json.dumps(submission, ensure_ascii=False, sort_keys=reviewed_flow).encode("utf-8")).hexdigest()
        with self.gate:
            if ident := self.requests.get(key):
                old = self.task(ident)
                if old["request_hash"] != signature:
                    raise APIError(409, "duplicate_conflict", "同一提交标识已用于另一份资料，请重新发起。")
                return self.serialize(old)
            settings = None
            if reviewed_flow:
                mode = "user_copy" if copy is not None else "awaiting_skill"
                if use_plan and copy is not None:
                    mode = planned_poster.MODE
                run = {"mode": mode, "provider": "local", "model": None,
                       "workflow": {"copy_route": "USER" if copy is not None else "WAITING",
                                    "model_called": False, "imagegen_called": False,
                                    "native_skill_execution_verified": False, "production_design_verified": False}}
            else:
                settings = self.settings()
                if source["simulated"]:
                    # A labeled built-in sample always uses the local demo.
                    settings = {"connection": validate_config({"mode": "demo"})}
                else:
                    require_current_cloud_consent(payload, settings)
                mode = settings["connection"]["mode"]
                run = {"mode": mode, "provider": mode, "simulated": mode == "demo",
                       "model": settings["connection"]["model"]}
            waiting = mode == "awaiting_skill"
            plan_needed = mode == planned_poster.MODE
            status = visual_copy.WAITING if waiting else (planned_poster.AWAITING_PLAN if plan_needed else "queued")
            message = {
                visual_copy.WAITING: ("资料已在本机保存。可从本任务打开本人确认窗。"
                                      if self.fresh_foreground and site=="CN" and use_plan and not source["simulated"]
                                      else "资料已在本机保存。当前没有可用的真实卖点 Skill 执行入口。"),
                planned_poster.AWAITING_PLAN: "原图和确认文案已保存，请提交该任务的排版计划。",
            }.get(status, "已加入本机队列。")
            ident = uuid4().hex
            path = self.tasks_dir / ident
            task = {"id": ident, "site": site, "title": title.strip(), "facts": facts.strip(),
                    "source": source, "run": run,
                    "status": status, "message": message,
                    "created_at": utc(), "updated_at": utc(), "request_id": key, "request_hash": signature,
                    "versions": [], "revision_requests": {}}
            if copy is not None:
                task["confirmed_copy"] = copy
            if plan_needed:
                task["plan_binding"] = planned_poster.binding(task)
            elif waiting and use_plan:
                task["copy_intake"] = {
                    "task_id": ident, "site": site, "product_ref": source["sku"],
                    "asset_sha256": source["sha256"], "target_version": 1,
                    "state": "executor_unavailable", "layout_allowed": False,
                }
            if has_detail:
                try:
                    with TemporaryDirectory(dir=self.tasks_dir,prefix=".visual-") as temporary:
                        staged = Path(temporary)
                        (staged/"product.png").write_bytes(image)
                        task["foreground_detail"] = foreground_detail_outputs.capture_inputs(
                            payload["detail"],task,staged,from_image_data)
                        if task["foreground_detail"]["missing"]:
                            task["message"] = "已保存本机任务。请补齐下方标出的事实和图片。"
                        staged.rename(path)
                except (ValueError,OSError,TypeError,KeyError) as error:
                    raise APIError(400,"invalid_detail_evidence",str(error) if isinstance(error,ValueError)
                                   else "商详证据未能保存，请核对图片与来源。") from None
            else:
                path.mkdir()
                (path/"product.png").write_bytes(image)
            self.save_task(task)
            self.store[ident] = task
            self.requests[key] = ident
            if not waiting and not plan_needed:
                self.executor.submit(self.make_poster, task, settings)
            return self.serialize(task)

    def create_transcription(self, payload):
        try:
            name, suffix, data, language = validate_upload(payload.get("media"), payload.get("language"))
        except MediaError as error:
            raise APIError(400, "invalid_media", str(error)) from None
        source = {"kind": "local_media", "file": "input" + suffix, "language": language,
                  "sha256": sha256(data).hexdigest(), "media_uploaded": False}
        return self.create_local(payload, "transcription", name, source, data=data)

    def create_main_images(self, payload):
        try:
            supplied, images = main_images.intake(payload, from_image_data)
        except main_images.MainImageError as error:
            raise APIError(400, "invalid_main_image_set", str(error)) from None
        source = {"tool_id": main_images.TOOL_ID, "assets": [meta for meta, _ in images]}
        label = "TikTok PH · Main images" if payload["site"] == "PH" else "抖音中国 · 商品主图套组"
        return self.create_local(payload, "role_tool", label, source,
                                 role_input=supplied, media_files=images)

    def create_long_detail(self, payload):
        try:
            supplied, images = long_detail.intake(payload, from_image_data)
        except long_detail.LongDetailError as error:
            raise APIError(400, "invalid_long_detail", str(error)) from None
        source = {"tool_id": long_detail.TOOL_ID, "assets": [meta for meta, _ in images]}
        label = "TikTok PH · Product details" if payload["site"] == "PH" else "抖音中国 · 商品长商详"
        return self.create_local(payload, "role_tool", label, source,
                                 role_input=supplied, media_files=images)

    def create_role(self, payload, *, agent_session=None, source_release=None, combination=None):
        tool_id, source = payload.get("tool_id"), payload.get("input")
        selected = self.role_tools.get(tool_id) if isinstance(tool_id, str) else None
        if selected is None:
            raise APIError(400, "invalid_role_tool", "请选择可用的本机岗位工具。")
        tool, _ = selected
        if payload.get("site") not in tool["sites"]:
            raise APIError(400, "wrong_tool_site", "该工具暂未接入所选站点。")
        if not isinstance(source, dict):
            raise APIError(400, "invalid_role_input", "请提供 JSON 对象作为工具输入。")
        media = payload.get("media", [])
        if not isinstance(media, list):
            raise APIError(400, "invalid_media", "请重新选择本机素材。")
        limit = tool.get("max_media_files", 0)
        if len(media) > limit or (not media and limit and tool.get("media_required", True)):
            raise APIError(400, "invalid_media_count", f"当前工具需选择 1 至 {limit} 个 MP4 文件。" if limit else
                           "当前工具只接收结构化输入。")
        if tool_id == workflow_ph_competitor_detail.TOOL:
            try:
                source, selected_media = workflow_ph_competitor_detail.intake(payload["site"], source, from_image_data)
            except (ValueError, TypeError, KeyError) as error:
                raise APIError(400, "invalid_ph_review_evidence", str(error)) from None
        elif tool_id == workflow_competitor_detail.TOOL:
            try:
                source, selected_media = workflow_competitor_detail.intake(payload["site"], source, from_image_data)
            except (workflow_competitor_detail.WorkflowError, workflow_competitor.WorkflowError) as error:
                raise APIError(400, "invalid_competitor_detail", str(error)) from None
        else:
            selected_media = self.role_media(media, tool.get("max_media_bytes", MAX_UPLOAD_BYTES))
        if tool_id == workflow_live_recording_review.TOOL:
            try:
                workflow_live_recording_review.validate_submission(
                    source, payload["site"], [metadata for metadata, _ in selected_media]
                )
            except workflow_live_recording_review.WorkflowError as error:
                raise APIError(400, "invalid_recording_review_evidence", str(error)) from None
        source_info = {"tool_id": tool_id}
        title = tool["label"]
        if tool_id == workflow_live_listening.RECEIVE:
            self.bind_listening_takeout(source)
        if tool_id == workflow_lead.NAMED_TOOL:
            try:
                job_id, role, upstream_id = workflow_lead.validate_named_request(payload["site"], source)
            except workflow_lead.WorkflowError as error:
                raise APIError(400, "invalid_named_job", str(error)) from None
            source_info.update(job_id=job_id, role=role)
            target=source.get("target_tool_id")
            if target is not None:
                with self.gate:
                    pins,_=self.bind_named_target(payload["site"],target,source["input"])
                source_info.update(named_target=target,named_target_pins=pins)
            if upstream_id is not None:
                source_info["upstream"] = self.named_upstream(payload["site"], upstream_id, require_ready=bool(target))
            title = f"{title} · {job_id}"
        if tool_id == workflow_live_team.TOOL:
            try:
                session_id, *_ = workflow_live_team.validate(payload["site"], source)
            except workflow_live_team.WorkflowError as error:
                raise APIError(400, "invalid_live_team_plan", str(error)) from None
            source_info["session_id"] = session_id
            title = f"{title} · {session_id}"
        if tool_id == workflow_lead_environment.TOOL:
            try:
                workflow_lead_environment.validate(payload["site"], source)
            except workflow_lead_environment.WorkflowError as error:
                raise APIError(400, "invalid_environment_review", str(error)) from None
            title = f"{title} · {source['case_id']}"
        if tool_id == workflow_lead_transfer.TOOL:
            try:
                workflow_lead_transfer.validate(payload["site"], source)
            except workflow_lead_transfer.WorkflowError as error:
                raise APIError(400, "invalid_cross_site_method_review", str(error)) from None
            title = f"{title} · {source['case_id']}"
        if tool_id == workflow_lead_quarter.TOOL:
            try:
                workflow_lead_quarter.validate(payload["site"], source)
            except workflow_lead_quarter.WorkflowError as error:
                raise APIError(400, "invalid_quarter_resource_review", str(error)) from None
            title = f"{title} · {source['case_id']}"
        if tool_id in workflow_visual_surfaces.TOOLS:
            with self.gate:
                _, pins = self.bind_visual_surface(payload["site"], tool_id, source)
                source_info.update(surface_upstream=pins, surface_key=source["key"], version=source["version"])
                title = f"{title} · {source['key']} v{source['version']}"
                return self.create_local(payload, "role_tool", title, source_info, role_input=source)
        if tool_id in workflow_media_library.TOOLS:
            with self.gate:
                _, pins, _ = self.bind_media_library(payload["site"], tool_id, source)
                source_info.update(media_upstream=pins)
                if source.get("mode") == "search":
                    title = f"{title} · {payload['site']} · {'历史检索' if source['history'] else '当前检索'}"
                else:
                    source_info.update(asset_id=source["asset_id"], version=source["version"], mode=source.get("mode"))
                    title = f"{title} · {source['asset_id']} v{source['version']}"
                return self.create_local(payload, "role_tool", title, source_info, role_input=source)
        if tool_id == workflow_creator_cohorts.TOOL:
            try:
                workflow_creator_cohorts.validate(payload["site"], source)
            except (workflow_creator_cohorts.WorkflowError, workflow_lead_weekly.WorkflowError,
                    workflow_live_rehearsal.WorkflowError) as error:
                raise APIError(400, "invalid_creator_cohorts", str(error)) from None
            source_info["creator_cohort_key"] = source["study_id"]
            title = f"{title} · {payload['site']} · {source['study_id']}"
            return self.create_local(payload, "role_tool", title, source_info, role_input=source)
        if tool_id in workflow_competitor_watch.TOOLS:
            site = payload["site"]
            with self.gate:
                key, pins, _ = self.bind_watch(site, tool_id, source)
                source_info.update(watch_key=key, watch_pins=pins, watch_id=source["watch_id"])
                suffix = source.get("view_id") or source.get("record_id") or "登记"
                version = source.get("revision") or source.get("version")
                if version is not None:
                    suffix += f" v{version}"
                title = f"{title} · {site} · {source['watch_id']} · {suffix}"
                return self.create_local(payload, "role_tool", title, source_info, role_input=source)
        if tool_id in workflow_campaign_reviews.TOOLS:
            site=payload["site"]
            with self.gate:
                linked=self.bind_campaign_review(site,tool_id,source)
                source_info.update(campaign_review_key=source["package_id"],campaign_review_pins=linked[0])
                title=f"{title} · {site} · {source['package_id']}"
                return self.create_local(payload,"role_tool",title,source_info,role_input=source)
        if tool_id in LOCAL_PACKET_MODULES:
            site=payload["site"]
            with self.gate:
                key,linked=self.bind_role_packet(site,tool_id,source,agent_session=agent_session)
                if source_release is not None and (self.foreground_active != agent_session or not agent_session
                        or linked[0]["source_hashes"].get("poster-chain-release.json") != source_release):
                    raise APIError(409,"mall_release_changed","本次会话锁定的已审源档已经变化。")
                if combination is not None:
                    if not source_release or not agent_session or self.foreground_active != agent_session:
                        raise APIError(409,"visual_delivery_scope","三份视觉结果仅在同一活跃客户会话接件。")
                    parent = self.task(source["poster_task_id"])
                    pin, _ = foreground_mall_combo.bound(self,parent,agent_session,combination,source_release)
                    source_info["combined_visual"] = pin
                    if parent.get("agent_session", {}).get("transport") == ORDINARY_VISUAL_TRANSPORT:
                        source_info["ordinary_chain_mall"] = True
                if agent_session is not None:
                    source_info["agent_session_id"] = agent_session
                    source_info["agent_role_protocol"] = mall_agent_worker.PROTOCOL
                source_info.update(role_packet_key=key,role_packet_pins=linked[0],
                                   playbook_id=source.get("playbook_id"))
                label=(source.get("job_id") or source.get("lookup_id") or
                       f"{source.get('playbook_id')} v{source.get('version')}")
                if tool_id in workflow_ph_competitor_cod.TOOLS:
                    label=f"{source['handoff_id']} v{source['version']}"
                if tool_id == workflow_ph_stock_readback.TOOL:
                    label=f"{source['job_id']} v{source['handoff_version']}"
                title=f"{title} · {site} · {label}"
                if tool_id == workflow_offline_review.SHOP and selected_media:
                    source_info["assets"] = [item for item, _ in selected_media]
                return self.create_local(payload,"role_tool",title,source_info,role_input=source,
                                         media_files=selected_media if tool_id == workflow_offline_review.SHOP else None)
        if tool_id == workflow_live_paid_slot.TOOL:
            site=payload["site"]
            try:
                workflow_live_paid_slot.verify_upload(source,[meta for meta,_ in selected_media])
            except (ValueError,KeyError,TypeError) as error:
                raise APIError(400,"invalid_paid_material",str(error)) from None
            with self.gate:
                linked=self.bind_paid_slot(site,source)
                source_info.update(paid_slot_key=source["plan_id"],paid_slot_pins=linked[0],
                                   assets=[meta for meta,_ in selected_media])
                title=f"{title} · {site} · {source['plan_id']}"
                return self.create_local(payload,"role_tool",title,source_info,role_input=source,media_files=selected_media)
        if tool_id == workflow_live_preflight.TOOL:
            site=payload["site"]
            with self.gate:
                linked=self.bind_preflight(site,source)
                source_info.update(preflight_key=source["review_id"],preflight_pins=linked[1])
                title=f"{title} · {site} · {source['review_id']}"
                return self.create_local(payload,"role_tool",title,source_info,role_input=source)
        if tool_id == workflow_competitor_supplier.TOOL:
            site=payload["site"]
            with self.gate:
                linked=self.bind_supplier_research(site,source)
                source_info.update(supplier_key=source["research_id"],supplier_revision=source["revision"],supplier_pins=linked[1])
                title=f"{title} · {site} · {source['research_id']} v{source['revision']}"
                return self.create_local(payload,"role_tool",title,source_info,role_input=source)
        if tool_id == workflow_competitor_dossier.TOOL:
            site=payload["site"]
            with self.gate:
                linked=self.bind_dossier(site,source)
                source_info.update(dossier_id=source["dossier_id"], dossier_revision=source["revision"], dossier_pins=linked[1])
                title=f"{title} · {site} · {source['dossier_id']} v{source['revision']}"
                return self.create_local(payload,"role_tool",title,source_info,role_input=source)
        if tool_id == workflow_competitor_bulletin.TOOL:
            site = payload["site"]
            with self.gate:
                linked = self.bind_bulletin(site, source)
                source_info.update(bulletin_key=source["bulletin_id"], bulletin_cadence=source["cadence"],
                                   bulletin_revision=source["revision"], bulletin_pins=linked[2])
                title = f"{title} · {site} · {source['cadence']} · {source['bulletin_id']} v{source['revision']}"
                return self.create_local(payload, "role_tool", title, source_info, role_input=source)
        if tool_id == workflow_lead_monthly.TOOL:
            site = payload["site"]
            with self.gate:
                linked = self.bind_monthly(site, source)
                source_info.update(monthly_key=source["review_id"], monthly_pins=linked[2])
                title = f"{title} · {site} · {source['review_id']}"
                return self.create_local(payload, "role_tool", title, source_info, role_input=source)
        if tool_id in workflow_lead_weekly.TOOLS:
            site = payload["site"]
            with self.gate:
                key, linked = self.bind_weekly(site, tool_id, source)
                source_info.update(weekly_request=key, weekly_pins=linked[2])
                identity = source.get("report_id") or source["record_id"]
                suffix = source.get("submission_id") or "周报"
                title = f"{title} · {site} · {identity} v{source['version']} · {suffix}"
                return self.create_local(payload, "role_tool", title, source_info, role_input=source)
        if tool_id in workflow_competitor_handoff.TOOLS:
            site=payload["site"]
            with self.gate:
                key,linked=self.bind_c10(site,tool_id,source)
                source_info.update(proposal_id=source["proposal_id"],version=source["version"],c10_request=key,upstream=linked[1])
                suffix=source.get("intake_id") or source.get("feedback_id") or "提案"
                title=f"{title} · {site} · {source['proposal_id']} v{source['version']} · {suffix}"
                return self.create_local(payload,"role_tool",title,source_info,role_input=source)
        if tool_id in workflow_portfolio_handoff.TOOLS:
            site=payload["site"]
            with self.gate:
                key,linked=self.bind_portfolio(site,tool_id,source)
                source_info.update(portfolio_id=source["portfolio_id"],version=source["version"],
                                   portfolio_request=key,upstream=linked[1])
                suffix=source.get("intake_id") or "发送"
                title=f"{title} · {site} · {source['portfolio_id']} v{source['version']} · {suffix}"
                return self.create_local(payload,"role_tool",title,source_info,role_input=source)
        if tool_id in workflow_live_rehearsal.TOOLS:
            site = payload["site"]
            with self.gate:
                if tool_id == workflow_live_rehearsal.CONTROL and "retry_interrupted" in source:
                    if source["retry_interrupted"] is not True:
                        raise APIError(400,"invalid_rehearsal_retry","明确重试时填写 retry_interrupted=true。")
                    payload = {**payload,"rehearsal_retry":True}
                    source = {k:v for k,v in source.items() if k!="retry_interrupted"}
                chain, linked = self.bind_live_rehearsal(site, tool_id, source)
                source_info.update(rehearsal_chain=chain, version=source["version"], upstream=linked[1])
                title = f"{title} · {site} · {chain} v{source['version']}"
                return self.create_local(payload, "role_tool", title, source_info, role_input=source)
        if tool_id in workflow_live_offline.TOOLS:
            with self.gate:
                _, pins = self.bind_live_offline(payload["site"], tool_id, source, enforce_latest=True)
                source_info.update(workflow_live_offline.chain_source(tool_id, source), upstream=pins)
                title = f"{title} · {source['key']} v{source['version']}"
                return self.create_local(payload, "role_tool", title, source_info, role_input=source)
        if tool_id in CAMPAIGN_INTAKES:
            market = payload["site"]
            module = CAMPAIGN_OFFERS[market]
            try:
                module.validate_intake(source)
            except module.WorkflowError as error:
                raise APIError(400, "invalid_campaign_intake", str(error)) from None
            snapshot, _, _ = self.campaign_review_source(market, source["upstream_task_id"])
            source_info["upstream"] = snapshot
        if tool_id in workflow_shop_image_review.TOOLS.values():
            with self.gate:
                self.bind_shop_image_review(payload["site"], source, current_only=True)
                title = f"{title} · {source['sku_id']} · {source['image_id']} v{source['version']} {source['mode']}"
                return self.create_local(payload, "role_tool", title, source_info, role_input=source)
        if tool_id == "shop-product-facts" and "visual_tasks" in source:
            source = dict(source)
            selectors = source.pop("visual_tasks")
            visual = self.shop_visual_source(payload["site"], source, selectors)
            source_info["visual_tasks"] = selectors
            source_info["visual_delivery"] = visual
        if selected_media:
            source_info["assets"] = [item for item, _ in selected_media]
        return self.create_local(payload, "role_tool", title, source_info,
                                 role_input=source, media_files=selected_media)

    def visual_surface_files(self, task):
        folder = self.tasks_dir / task["id"]
        try:
            data = {kind: bind_data_path(folder, folder / name).read_bytes()
                    for kind, name in task["local_files"].items()}
            workflow_visual_surfaces.verify(task, data)
            return data
        except (ValueError, OSError, TypeError, KeyError) as error:
            raise APIError(409, "surface_evidence_missing", "视觉交付文件或回执无法核验。") from error

    def visual_surface_source(self, site, task_id, tools):
        task = self.store.get(task_id)
        if (not task or task["site"] != site or task["status"] != "ready"
                or task["source"].get("tool_id") not in tools):
            raise APIError(409, "surface_source_mismatch", "需引用已完成的本站正确岗位任务。")
        data = self.visual_surface_files(task)
        return task["result"], data, {"id": task_id, "receipt_sha256": sha256(data["receipt"]).hexdigest()}

    def bind_visual_surface(self, site, tool, value):
        w, m = workflow_visual_surfaces, workflow_media_library
        try:
            w.validate(site, tool, value)
        except w.WorkflowError as error:
            raise APIError(400, "invalid_visual_surface", str(error)) from None
        bound, pins = {}, {}
        chain = [task["result"] for task in self.store.values()
                 if task["site"] == site and task["status"] == "ready"
                 and task["source"].get("surface_key") == value["key"]
                 and task["source"].get("tool_id") in w.BRIEFS]
        current = max((row["version"] for row in chain), default=0)
        if value["version"] < current:
            raise APIError(409, "surface_stale_version", f"已有 v{current} 简报；旧版可在任务历史取回。")
        if tool in w.BRIEFS:
            desk = w.BRIEFS[tool]
            record, pins["asset"] = self.media_registry_result(site, value["asset_task_id"], m.VISUAL)
            if (record["product_id"], record["facts"]["title"]) != (value["product_id"], value["title"]):
                raise APIError(409, "surface_sku_mismatch", "视觉素材、商品和标题不匹配。")
            state = m.available(record, value["as_of"], w.PURPOSE[desk])
            if state != "allowed_offline_reference":
                raise APIError(409, "surface_"+state, "本次素材用途无已确认的有效离线授权记录。")
            if any(t["site"] == site and t["status"] == "ready" and t["source"].get("mode") == "register"
                   and t["source"].get("tool_id") == m.VISUAL and t["source"].get("asset_id") == record["asset_id"]
                   and t["source"]["version"] > record["version"] for t in self.store.values()):
                raise APIError(409, "surface_stale_asset", "存在更新素材版本。")
            source = record["source"]
            latest_source, pins["frame"], _ = self.local_media_frame(site, source["source_task_id"], source["frame_id"])
            if source != latest_source or pins["frame"] != record["upstream"]["source"]:
                raise APIError(409, "surface_evidence_missing", "原件与登记证据不一致。")
            bound["asset"] = {"task_id": record["task_id"], "source": source, "rights": record["rights"],
                              "rights_purpose":w.PURPOSE[desk], "asset_id":record["asset_id"], "version":record["version"]}
            if prior := value["previous_task_id"]:
                before, _, pins["previous"] = self.visual_surface_source(site, prior, (tool,))
                frozen = ("site","key","product_id","slot","title","language","currency","session_ref")
                if (before["version"]+1 != value["version"]
                        or any(before[k]!=value[k] for k in frozen) or before["asset"] != bound["asset"]
                        or m._time(value["as_of"]) < m._time(before["as_of"])
                        or value["line"]==before["line"] or value["copy_ref"]==before["copy_ref"]):
                    raise APIError(409, "surface_revision_mismatch", "续版只可使用同场次同素材并变更有来源的运营文案。")
            elif current and any(row["tool_id"]!=tool for row in chain):
                raise APIError(409, "surface_source_mismatch", "该任务编号已绑定其他运营岗位。")
        elif tool == w.RENDER:
            brief, _, pins["brief"] = self.visual_surface_source(site, value["brief_task_id"], w.BRIEFS)
            if not w.matching(brief, value):
                raise APIError(409, "surface_sku_mismatch", "素材需求与任务编号、版本或 SKU 不符。")
            asset = brief["asset"]
            record, pins["asset"] = self.media_registry_result(site, asset["task_id"], m.VISUAL)
            source = asset["source"]
            if m.available(record, brief["as_of"], asset["rights_purpose"]) != "allowed_offline_reference":
                raise APIError(409, "surface_rights_pending", "运用范围或权利无法核验。")
            now, pins["frame"], blobs = self.local_media_frame(site, source["source_task_id"], source["frame_id"])
            if now != source or record["source"]!=source or pins["frame"]!=record["upstream"]["source"]:
                raise APIError(409, "surface_evidence_missing", "缺少已登记原件或素材发生变化。")
            if any(t["site"] == site and t["status"] == "ready" and t["source"].get("mode") == "register"
                   and t["source"].get("tool_id") == m.VISUAL and t["source"].get("asset_id") == record["asset_id"]
                   and t["source"]["version"] > record["version"] for t in self.store.values()):
                raise APIError(409, "surface_stale_asset", "存在更新素材版本。")
            bound.update(brief=brief, frame=blobs["frame"])
            if prior := value["previous_art_task_id"]:
                before, files, pins["previous_art"] = self.visual_surface_source(site, prior, (w.RENDER,))
                if (before["version"]+1!=value["version"] or before["key"]!=value["key"]
                        or before["product_id"]!=value["product_id"] or before["brief_task_id"]!=brief["previous_task_id"]
                        or before["slot"]!=brief["slot"] or before["asset"]!=asset):
                    raise APIError(409, "surface_revision_mismatch", "请输入同一编号的上一版原始产物。")
                bound["previous_bundle"] = files["bundle"]
        else:
            art, files, pins["art"] = self.visual_surface_source(site, value["art_task_id"], (w.RENDER,))
            if not w.matching(art, value) or w.SLOTS[art["slot"]] != w.RECEIPTS[tool]:
                raise APIError(409, "surface_receipt_mismatch", "接收岗位、编号、版本或 SKU 与交付不符。")
            bound.update(art=art, bundle=files["bundle"])
        return bound, pins

    def media_library_artifacts(self, task):
        folder = self.tasks_dir / task["id"]
        try:
            files = {key: bind_data_path(folder, folder / name).read_bytes()
                     for key, name in task["local_files"].items()}
            workflow_media_library.verify(task, files)
            return files
        except (ValueError, OSError, TypeError, KeyError) as error:
            raise APIError(409, "media_receipt_unverified", "素材产物或版本回执已变化。") from error

    def media_registry_result(self, site, ident, lib):
        task = self.store.get(ident)
        if (not task or task["site"] != site or task["status"] != "ready"
                or task["source"].get("tool_id") != lib or task["source"].get("mode") != "register"):
            raise APIError(409, "media_source_unverified", "请选择本站已完成且类型匹配的库登记。")
        files = self.media_library_artifacts(task)
        return task["result"], {"task_id": ident, "record_sha256": sha256(files["json"]).hexdigest(),
                                "receipt_sha256": sha256(files["receipt"]).hexdigest()}

    def local_media_frame(self, site, ident, frame_id):
        m = workflow_media_library
        try:
            evidence, refs = self.verified_live_offline_source(ident, workflow_teardown.TOOL_ID, site)
            matches = [r for r in evidence["frames"] if r["id"] == frame_id]
            if (not evidence["synthetic_material"] or len(matches) != 1
                    or not re.fullmatch(r"frame-[0-9]{3}\.jpg", matches[0]["file"])):
                raise m.WorkflowError("来源须为本站合成拆解中的确切帧。")
            selected = matches[0]
            task = self.store[ident]
            folder = self.tasks_dir / ident
            path = bind_data_path(folder, folder / "teardown" / selected["file"])
            picture = path.read_bytes()
            src, = task["source"]["assets"]
            original = bind_data_path(folder, folder / src["file"]).read_bytes()
            if (not picture.startswith(b"\xff\xd8\xff") or sha256(picture).hexdigest() != selected["sha256"]
                    or sha256(original).hexdigest() != refs["media_sha256"]):
                raise m.WorkflowError("原片或所选帧的哈希不符。")
            public = {"source_task_id": ident, "media_sha256": refs["media_sha256"], "frame_id": frame_id,
                      "frame_sha256": selected["sha256"], "actual_seconds": selected["actual_seconds"],
                      "publication_verified": False, "physical_product_verified": False}
            return public, {**refs, "frame_sha256": selected["sha256"]}, {"frame":picture,"video":original}
        except (OSError, TypeError, KeyError, ValueError) as error:
            raise APIError(409, "media_source_unverified", "原片、所选帧或拆解回执缺失或不符。") from error

    def verified_teardown_report_frames(self, ident, frames):
        folder = self.tasks_dir / ident
        pins = {}
        try:
            for frame in frames:
                name = frame["file"]
                if not re.fullmatch(r"frame-[0-9]{3}\.jpg", name):
                    raise ValueError("Unexpected frame path")
                jpeg = bind_data_path(folder, folder / "teardown" / name).read_bytes()
                if not jpeg.startswith(b"\xff\xd8\xff") or sha256(jpeg).hexdigest() != frame["sha256"]:
                    raise ValueError("Frame hash mismatch")
                pins[frame["id"]] = {k: frame[k] for k in ("actual_seconds", "sha256")}
        except (OSError, TypeError, KeyError, ValueError) as error:
            raise APIError(409, "teardown_report_frame_unverified", "引用帧缺失或其字节与原件清单不符。") from error
        return pins

    def bind_media_library(self, site, tool, value, *, check_latest=True):
        m = workflow_media_library
        try:
            m.validate(site, tool, value)
        except m.WorkflowError as error:
            raise APIError(400, "invalid_media_library", str(error)) from None
        lib = tool if tool in m.LIBRARIES else (m.DIRECTOR if tool == m.RECEIVE else m.VISUAL)
        rows = [t for t in self.store.values() if t["site"] == site and t["status"] == "ready"
                and t["source"].get("tool_id") == lib and t["source"].get("mode") == "register"]
        linked, pins, blobs = {}, {}, {}
        if value.get("mode") == "search":
            latest = {}
            for row in rows:
                source = row["source"]
                key, version = source["asset_id"], source["version"]
                if key not in latest or latest[key]["source"]["version"] < version:
                    latest[key] = row
            pool = rows if value["history"] else list(latest.values())
            results = []
            for row in sorted(pool, key=lambda row: (row["source"]["asset_id"], -row["source"]["version"])):
                record, proof = self.media_registry_result(site, row["id"], lib)
                if record["purpose"] != value["purpose"] or not set(value["tags"]) <= set(record["tags"]):
                    continue
                result = {key: record[key] for key in ("site","asset_id","version","product_id","title") if key in record}
                results.append({**result, "title":record["facts"]["title"],"registration_task_id":row["id"],
                                "rights_for_use":m.available(record,value["as_of"],value["purpose"]),
                                "tags":record["tags"],"template":record["template"]})
                pins[row["id"]] = proof
                if len(results) == 50:
                    break
            return {"records": results}, pins, blobs
        versions = [r["source"]["version"] for r in rows if r["source"]["asset_id"] == value["asset_id"]]
        if check_latest and versions and value["version"] < max(versions):
            raise APIError(409, "media_stale_version", f"已有 v{max(versions)}，旧版可在历史中取回。")
        if tool in m.LIBRARIES:
            if previous := value["previous_task_id"]:
                old, pins["previous"] = self.media_registry_result(site, previous, lib)
                if (old["asset_id"],old["product_id"],old["version"]+1) != (
                        value["asset_id"],value["product_id"],value["version"]):
                    raise APIError(409, "media_revision_mismatch", "上一版需是同对象连续版本。")
                if m._time(old["as_of"]) > m._time(value["as_of"]):
                    raise APIError(409, "media_revision_mismatch", "续版评估时间早于上一版。")
            linked["source"], pins["source"], blobs = self.local_media_frame(site,value["source_task_id"],value["frame_id"])
        else:
            registration, pins["registry"] = self.media_registry_result(site,value["asset_task_id"],lib)
            if not m.same(registration,value):
                raise APIError(409, "media_source_mismatch", "素材任务的站点、对象、编号或版本不匹配。")
            gate = m.available(registration,value["as_of"],value["purpose"])
            if gate != "allowed_offline_reference":
                raise APIError(409, f"media_{gate}", "登记中的权利、时间或用途不足，暂不能交接或渲染。")
            if tool == m.COVER and value["title"] != registration["facts"]["title"]:
                raise APIError(409, "media_title_mismatch", "封面标题须与该版的事实卡完全一致。")
            original = registration["source"]
            current, pins["source"], blobs = self.local_media_frame(site,original["source_task_id"],original["frame_id"])
            if current != original or pins["source"] != registration["upstream"]["source"]:
                raise APIError(409, "media_source_unverified", "原片或帧记录已经变化。")
            linked["registration"] = registration
        return linked, pins, blobs

    def verified_live_offline_source(self, ident, tool, site):
        task = self.store.get(ident)
        if (not task or task["site"] != site or task["status"] != "ready"
                or task["source"].get("tool_id") != tool):
            raise workflow_live_offline.WorkflowError("来源须为本站、指定类型的已完成任务。")
        try:
            files = {kind: self.local_artifact_bytes(ident, kind) for kind in ("json", "report", "receipt")}
            result = json.loads(files["json"])
            evidence = json.loads(files["receipt"])
            if result != task["result"] or result["site"] != site:
                raise ValueError("Result identity changed")
            pins = {"task_id": ident, "site": site, "tool_id": tool,
                    **{f"{k}_sha256": sha256(buf).hexdigest() for k, buf in files.items()}}
            if tool in workflow_live_offline.TOOLS:
                pass  # local_artifact_bytes verifies the owned receipt and result as a unit.
            elif tool == workflow_teardown.TOOL_ID:
                folder = self.tasks_dir / ident
                asset, = task["source"]["assets"]
                media_hash = sha256(bind_data_path(folder, folder / asset["file"]).read_bytes()).hexdigest()
                if (media_hash != asset["sha256"] or media_hash != result["source_sha256"]
                        or media_hash != evidence["source_sha256"]
                        or any(evidence["artifacts"][k]["sha256"] != pins[f"{k}_sha256"]
                               for k in ("json", "report"))):
                    raise ValueError("Clip or teardown receipts differ")
                pins["media_sha256"] = media_hash
            else:
                kind = "review_sha256" if tool == workflow_live.TOOL else "result_sha256"
                if evidence[kind] != pins["json_sha256"] or evidence["report_sha256"] != pins["report_sha256"]:
                    raise ValueError("Live evidence digests differ")
                if tool == workflow_live.TOOL:
                    saved_input = self.local_artifact_bytes(ident, "input")
                else:
                    folder = self.tasks_dir / ident
                    saved_input = bind_data_path(folder, folder / "role-input.json").read_bytes()
                input_hash = sha256(saved_input).hexdigest()
                if input_hash != evidence["input_sha256"]:
                    raise ValueError("Live evidence input changed")
                pins["input_sha256"] = input_hash
            return result, pins
        except (APIError, OSError, KeyError, TypeError, ValueError) as error:
            raise workflow_live_offline.WorkflowError("来源文件或哈希回执缺失、已改动或无法核对。") from error

    def watch_local_rows(self, site, watch, tool):
        return [t["result"] for t in self.store.values()
                if t["status"] == "ready" and t["site"] == site
                and t["source"].get("tool_id") == tool and t["source"].get("watch_id") == watch]

    def bind_watch(self, site, tool, source):
        try:
            return workflow_competitor_watch.bind(site, tool, source, self.bulletin_source,self.watch_local_rows)
        except (workflow_competitor_watch.WorkflowError, workflow_lead_weekly.WorkflowError,
                workflow_live_rehearsal.WorkflowError) as error:
            raise APIError(400, "invalid_watch_input", str(error)) from None

    def bulletin_source(self, ident, site, tool):
        task = self.store.get(ident)
        if (not task or task["site"] != site or task["status"] != "ready"
                or task["source"].get("tool_id") != tool):
            raise APIError(409, "bulletin_source_unavailable", "仅引用本站正确类型的本机留档。")
        self.require_visible_task(task)
        files = {k: self.local_artifact_bytes(ident, k) for k in ("json", "receipt")}
        return task["result"], {"task_id": ident, "result_sha256": sha256(files["json"]).hexdigest(),
                                "receipt_sha256": sha256(files["receipt"]).hexdigest()}

    def ph_certification_intake_source(self, ident):
        result, pin = self.bulletin_source(ident, "PH", workflow_admission.CERT)
        folder = self.tasks_dir / ident
        try:
            original = read_json(bind_data_path(folder, folder / "role-input.json"))
            if sha256(workflow_ph_cert_intake.encode(original)).hexdigest() != result["input_sha256"]:
                raise ValueError("Certification input changed")
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise APIError(409, "ph_cert_original_changed", "选品认证输入缺失或已变更。") from error
        return result, original, pin

    def ph_live_controller_source(self, ident):
        task = self.store.get(ident)
        if (not task or task["site"] != "PH" or task["status"] != "ready"
                or task["source"].get("tool_id") != workflow_live_recording_review.TOOL):
            raise APIError(409, "ph_recording_source_unavailable", "只能领取已完成的菲律宾直播证据。")
        folder = self.tasks_dir / ident
        try:
            data = self.local_artifact_bytes(ident, "json")
            result = json.loads(data)
            if result["scope"] != "synthetic_offline":
                raise ValueError("Source is outside this synthetic workflow")
            source = task["source"]["assets"][0]
            media = bind_data_path(folder, folder / source["file"])
            if (media.stat().st_size > workflow_live_recording_review.MAX_BYTES
                    or sha256(media.read_bytes()).hexdigest() != result["source_identity"]["recording_sha256"]):
                raise ValueError("Recording source bytes changed")
        except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
            raise APIError(409, "ph_recording_source_changed", "菲律宾合成直播证据原文件无法复核。") from error
        return result, {"task_id": ident, "result_sha256": sha256(data).hexdigest(),
                        "recording_sha256": source["sha256"]}

    def bind_campaign_review(self,site,tool,source):
        try:
            return workflow_campaign_reviews.bind(tool,site,source,self.bulletin_source)
        except (workflow_campaign_reviews.WorkflowError, workflow_live_rehearsal.WorkflowError,
                workflow_lead_weekly.WorkflowError) as error:
            raise APIError(400,"invalid_campaign_review",str(error)) from None

    def playbook_rows(self,site,playbook,tool):
        return [t["result"] for t in self.store.values() if t["status"]=="ready" and t["site"]==site
                and t["source"].get("tool_id")==tool and t["source"].get("playbook_id")==playbook]

    def teardown_library_ids(self,site,library_id):
        return [t["id"] for t in self.store.values()
                if t["site"]==site and t["status"]=="ready" and t["source"].get("tool_id")==workflow_teardown_library.TOOL
                and t["result"]["mode"]=="save" and t["result"]["library_id"]==library_id]

    def teardown_period_library(self,site,request):
        module=workflow_teardown_library
        source={**{k:request[k] for k in ("site","scope","currency","library_id","product_id","as_of","owner")},
                "mode":"find","lookup_id":request["job_id"],"include_history":True}
        pins,history=module.bind(site,module.TOOL,source,self.bulletin_source,self.media_registry_result,
                              self.verified_live_offline_source,self.local_media_frame,self.teardown_library_ids)
        return module.query(site,source,history),pins

    def teardown_handoff_history(self,site,handoff_id,tool):
        return [t["result"] for t in self.store.values()
                if t["status"]=="ready" and t["site"]==site and t["source"].get("tool_id")==tool
                and t["result"]["handoff_id"]==handoff_id]

    def director_preflight_history(self,site,packet_id,tool):
        return [t["result"] for t in self.store.values()
                if t["status"]=="ready" and t["site"]==site and t["source"].get("tool_id")==tool
                and t["result"]["packet_id"]==packet_id]

    def selection_lifecycle_history(self,site,case_id,tool):
        return [t["result"] for t in self.store.values()
                if t["status"]=="ready" and t["site"]==site and t["source"].get("tool_id")==tool
                and t["result"]["case_id"]==case_id]

    def shop_period_history(self,site,review_id,tool):
        return [t["result"] for t in self.store.values()
                if t["status"]=="ready" and t["site"]==site and t["source"].get("tool_id")==tool
                and t["result"]["review_id"]==review_id]

    def cn_card_current_listing(self,ident,site,as_of):
        result,pin=self.cn_listing_source(ident,site)
        for t in self.store.values():
            if (t["site"]!=site or t["status"]!="ready" or t["source"].get("tool_id")!="shop-product-facts"):
                continue
            row=t["result"].get("result",{})
            if (row.get("sku_id")==result["sku_id"] and
                    type(row.get("facts_version")) is int and row["facts_version"]>result["facts_version"]):
                _,newer=self.cn_listing_source(t["id"],site)
                if workflow_offline_review.absolute_time(newer["as_of"])<=as_of:
                    raise workflow_offline_review.WorkflowError("商品卡来源已存在更新的本站同 SKU 版本。")
        return result,pin

    def cn_card_image_source(self,ident,site):
        result,_=self.shop_image_history(site,ident,"review")
        task=self.store[ident]
        paths=self.shop_image_artifacts(task)
        return result,{"task_id":ident,"result_sha256":sha256(paths["json"]).hexdigest(),
                       "receipt_sha256":sha256(paths["receipt"]).hexdigest()}

    def live_program_history(self,site,tool,session_id):
        return [t["result"] for t in self.store.values() if t["status"]=="ready" and t["site"]==site
                and t["source"].get("tool_id")==tool and t["result"]["identity"]["session_id"]==session_id]

    def ph_listing_history(self,site,tool,template_id,locale,category):
        return [t["result"] for t in self.store.values() if t["status"]=="ready" and t["site"]==site
                and t["source"].get("tool_id")==tool and
                (t["result"]["template_id"],t["result"]["locale"],t["result"]["category"])==(template_id,locale,category)]

    def cn_listing_source(self,ident,site):
        t=self.store.get(ident)
        if not t or t["site"]!=site or t["status"]!="ready" or t["source"].get("tool_id")!="shop-product-facts":
            raise APIError(409,"listing_source_unavailable","须引用抖音正确类型的本机商品事实包。")
        try:
            data=self.local_artifact_bytes(ident,"json")
            obj=json.loads(data)
            if obj!=t["result"] or obj["site"]!=site or obj["scope"]!="synthetic_offline" or obj["task_id"]!=ident:
                raise ValueError("listing result mismatch")
            folder=self.tasks_dir/ident
            raw=bind_data_path(folder,folder/"role-input.json").read_bytes()
            submitted=json.loads(raw)
            fingerprint=json.dumps([t["kind"],t["site"],t["title"],t["source"],submitted],
                                   ensure_ascii=False,sort_keys=True,allow_nan=False).encode("utf-8")
            pack=submitted["facts_pack"]
            if (sha256(fingerprint).hexdigest()!=t["request_hash"] or
                    (pack["sku_id"],pack["version"])!=(obj["result"]["sku_id"],obj["result"]["facts_version"])):
                raise ValueError("listing source fingerprint mismatch")
            for key in ("as_of","expires_at"):
                row=datetime.fromisoformat(pack[key].replace("Z","+00:00"))
                if row.utcoffset() is None:
                    raise ValueError("listing evidence timing not explicit")
            return obj["result"],{"task_id":ident,"result_sha256":sha256(data).hexdigest(),
                                  "facts_version":obj["result"]["facts_version"],"role_input_sha256":sha256(raw).hexdigest(),
                                  "as_of":pack["as_of"],"expires_at":pack["expires_at"]}
        except (OSError,ValueError,KeyError,TypeError) as error:
            raise APIError(409,"listing_source_unavailable","本机事实包和任务不一致。") from error

    def lead_delivery_source(self,ident,site,tool):
        result,pin=self.bulletin_source(ident,site,tool)
        try:
            task=self.store[ident]
            files={k:self.local_artifact_bytes(ident,k) for k in ("json","report","receipt")}
            receipt=json.loads(files["receipt"])
            if "outputs_sha256" in receipt:
                declared=tuple(receipt["outputs_sha256"][Path(task["local_files"][k]).name]
                               for k in ("json","report"))
            else:
                declared=(receipt["result_sha256"],receipt["report_sha256"])
            if (json.loads(files["json"])!=result or receipt["site"]!=site
                    or ("task_id" in receipt and receipt["task_id"]!=ident)
                    or ("tool_id" in receipt and receipt["tool_id"]!=tool)
                    or tuple(sha256(files[k]).hexdigest() for k in ("json","report"))!=declared
                    or (sha256(files["json"]).hexdigest(),sha256(files["receipt"]).hexdigest())!=
                       (pin["result_sha256"],pin["receipt_sha256"])):
                raise ValueError("delivery bytes mismatch")
        except (OSError,ValueError,KeyError,TypeError) as error:
            raise APIError(409,"lead_delivery_source_mismatch","原件字节与该岗位的已保存回执不一致。") from error
        return result,pin

    def lead_delivery_history(self,site,batch_id):
        return [t["result"] for t in self.store.values() if t["status"]=="ready" and t["site"]==site
                and t["source"].get("tool_id")==workflow_lead_delivery.TOOL and t["result"]["batch_id"]==batch_id]

    def lead_program_approval(self,site,p,plan,pin):
        try:
            return workflow_lead_delivery.approval(site,p,plan,pin,self.bulletin_source,self.lead_delivery_history)
        except workflow_lead_delivery.WorkflowError as error:
            raise workflow_live_program.WorkflowError(str(error)) from None

    def verified_poster_delivery_source(self, site, ident, version, *, reviewed=False, agent_session=None):
        tool = workflow_poster_delivery
        task = self.store.get(ident)
        if not task or task["site"] != site or task.get("kind") or task["status"] != "ready":
            raise APIError(409, "poster_delivery_wrong_source", "仅可打包本站已保存的海报。")
        agent = task.get("agent_session", {})
        if (agent.get("transport") == ORDINARY_VISUAL_TRANSPORT and agent.get("state") != "completed"
                and agent_session != agent.get("session_id")):
            raise APIError(409, "visual_chain_receipt_pending", "请等待商品三件套的主会话确认。")
        if task["run"].get("workflow", {}).get("execution_kind") == poster_copy_executor.TEST:
            raise APIError(409, "poster_test_copy_source", "模拟接线样张只在测试流程内取件。")
        if self.is_agent_fixture_task(task):
            if (not self.agent_fixture or not reviewed or not agent_session
                    or task.get("agent_session", {}).get("session_id") != agent_session):
                raise APIError(409, "agent_handoff_only", "此模拟收件须由同一客户交互会话调用。")
        if self.is_cloud_fixture_task(task) and (self.cloud_fixture is None or not reviewed):
            raise APIError(409, "poster_cloud_mock_source", "模拟云模型样张只供隔离商城接件测试。")
        entry = next((row for row in task["versions"] if row["version"] == version), None)
        if entry is None:
            raise APIError(404, "poster_delivery_missing_version", "指定的海报版本不存在。")
        folder = self.tasks_dir / ident
        revision = folder / "artwork" / f"v{version}"
        fixed_files = {key: (revision / filename).relative_to(folder).as_posix()
                       for key, (filename, _) in ARTIFACTS.items()}
        if entry["files"] != fixed_files:
            raise APIError(409, "poster_delivery_source_changed", "海报的交付文件路径与版本不符。")
        paths = {n: revision / n for n in tool.ARTWORK}
        if reviewed:
            if task.get("run", {}).get("mode") != planned_poster.MODE:
                raise APIError(409, "poster_delivery_unreviewed", "商城收件须引用本机已审校的排版版本。")
            paths["poster-chain-release.json"] = revision / "poster-chain-release.json"
        if version > 1:
            paths.update({n: revision / n for n in tool.REVISION})
        paths["product.png"] = folder / "product.png"
        try:
            fixed = {n: bind_data_path(folder, path) for n, path in paths.items()}
            if sum(path.stat().st_size for path in fixed.values()) > 24 * 1024 * 1024:
                raise ValueError("Source bundle exceeds bound")
            blobs = {n: path.read_bytes() for n, path in fixed.items()}
            if reviewed:
                poster_copy_executor.verify_accepted(self.project, folder, task)
                for kind, name in (("png", "original.png"), ("psd", "layered.psd")):
                    planned_poster.verify_download(task, folder, entry, kind, blobs[name])
            return entry, task["source"], blobs
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise APIError(409, "poster_delivery_source_changed", "海报源档缺失或无法核对。") from error

    def verified_poster_archive_task(self, ident, site, tool):
        result, _ = self.bulletin_source(ident,site,tool)
        files = {key:self.local_artifact_bytes(ident,key) for key in ("json", "receipt", "bundle")}
        if len(files["bundle"]) > 24 * 1024 * 1024:
            raise APIError(409, "poster_archive_too_large", "The archived poster package exceeds the supported bound.")
        return result, files

    def director_version_source(self, ident, site):
        task = self.store.get(ident)
        if (not task or task["site"] != site or task["status"] != "ready"
                or task["source"].get("tool_id") != workflow_director.VERSIONS_TOOL):
            raise APIError(409, "director_version_source_unavailable", "只能引用本站已完成的编导多版本交付。")
        return self.local_artifact_bytes(ident, "json"), self.local_artifact_bytes(ident, "director_bundle")

    def ph_competitor_stock_source(self, ident, site):
        task = self.store.get(ident)
        if (not task or task["site"] != site or task["status"] != "ready"
                or task["source"].get("tool_id") != workflow_ph_stock_readback.SOURCE):
            raise APIError(409,"ph_competitor_stock_source_unavailable","需要已完成的本站竞品库存证据任务。")
        binary = self.local_artifact_bytes(ident,"json")
        original = task["result"]
        if json.loads(binary) != original or original["task_id"] != ident or original["site"] != site:
            raise APIError(409,"ph_competitor_stock_source_changed","已存竞品库存产物与原任务不符。")
        return original, {"task_id":ident, "result_sha256":sha256(binary).hexdigest()}

    def bind_role_packet(self,site,tool,source,*,agent_session=None):
        m=LOCAL_PACKET_MODULES[tool]
        try:
            if m is workflow_ph_creator_card:
                linked=m.bind(site,tool,source,self.bulletin_source)
                key=source["job_id"]
            elif m is workflow_live_program:
                linked=m.bind(site,tool,source,self.bulletin_source,self.live_program_history,self.lead_program_approval)
                key=m.request_key(tool,source)
            elif m is workflow_cn_sales_plans:
                if tool == m.PH_TALK_INT:
                    linked = m.bind_ph_talk_intake(site, source, self.bulletin_source, self.live_program_history)
                else:
                    linked = m.bind(site,tool,source,self.bulletin_source,self.cn_listing_source,self.live_program_history)
                key=source["job_id"]
            elif m is workflow_offline_planning:
                reviews=() if tool!=m.DIR else read_json(APP/"fixtures/cn-offline-reviewed-script.json")["approved_synthetic_reviews"]
                linked=m.bind(site,tool,source,self.bulletin_source,reviews)
                key=source["job_id"]
            elif m is workflow_offline_review:
                if tool==m.TEARDOWN:
                    linked=m.bind_teardown(site,source,self.teardown_period_library,self.bulletin_source)
                else:
                    linked=m.bind_shop(site,source,self.cn_card_current_listing,self.bulletin_source,self.cn_card_image_source)
                key=source["job_id"]
            elif m is workflow_teardown_handoff:
                linked=m.bind(site,tool,source,self.bulletin_source,self.teardown_period_library,
                              self.local_media_frame,self.teardown_handoff_history)
                key=m.lookup_key(tool,source)
            elif m is workflow_shoot_handoff:
                def shoot_artifact(ident, kind):
                    try:
                        return self.local_artifact_bytes(ident, kind)
                    except APIError as error:
                        raise m.WorkflowError("拍摄上游回执不可核实。") from error
                linked=m.bind(site,tool,source,shoot_artifact)
                key=m.request_key(source)
            elif m is workflow_director_preflight:
                linked=m.bind(site,tool,source,self.bulletin_source,
                              lambda ident,market:self.verified_live_offline_source(ident,workflow_teardown.TOOL_ID,market),
                              self.director_preflight_history)
                key=m.request_key(source)
            elif m is workflow_cn_selection_lifecycle:
                linked=m.bind(site,source,self.bulletin_source,self.selection_lifecycle_history)
                key=m.request_key(source)
            elif m is workflow_ph_competitor_cod:
                linked=m.bind(site,tool,source,self.bulletin_source)
                key=m.request_key(source)
            elif m is workflow_ad_lead_plan:
                def admission_artifact(ident, kind):
                    try:
                        return self.local_artifact_bytes(ident, kind)
                    except APIError as error:
                        raise m.WorkflowError("准入回执不可核实。") from error
                linked=m.bind(site,tool,source,admission_artifact)
                key=m.request_key(source)
            elif m is workflow_ad_mode_readback:
                linked=m.bind(site,tool,source,self.lead_delivery_source)
                key=source["job_id"]
            elif m is workflow_ad_creative_custody:
                def creative_artifact(ident, kind):
                    try:
                        return self.local_artifact_bytes(ident, kind)
                    except APIError as error:
                        raise m.WorkflowError("素材交接上游回执不可核实。") from error
                linked=m.bind(site,tool,source,creative_artifact)
                key=m.request_key(source)
            elif m is workflow_ph_stock_readback:
                linked=m.bind(site,source,self.ph_competitor_stock_source)
                key=m.request_key(source)
            elif m is workflow_competitor_compare_readiness:
                linked=m.bind(site,source)
                key=source["job_id"]
            elif m is workflow_admission:
                binder=m.bind_ad if tool==m.AD else m.bind_cert
                linked=binder(site,source)
                key=source["job_id"]
            elif m is workflow_ph_cert_intake:
                linked=m.bind(site,source,self.ph_certification_intake_source)
                key=source["job_id"]
            elif m is workflow_ph_live_controller:
                linked=m.bind(site,source,self.ph_live_controller_source)
                key=source["job_id"]
            elif m is workflow_local_insight:
                if tool==m.SEL:
                    linked=m.bind_selection(site,source,self.bulletin_source)
                elif tool==m.AD:
                    linked=m.bind_ad(site,source)
                else:
                    linked=m.bind_teardown(site,source,self.bulletin_source)
                key=f"{source['tracker_id']}:{source['version']}" if tool==m.TD else source["job_id"]
            elif m is workflow_shop_period:
                linked=m.bind(site,source,self.bulletin_source,self.shop_period_history)
                key=m.request_key(source)
            elif m is workflow_lead_delivery:
                if tool == m.OPERATING_TOOL:
                    linked=m.bind_operating(site,source,self.lead_delivery_source)
                    key=source["job_id"]
                else:
                    linked=m.bind(site,source,self.lead_delivery_source,self.lead_delivery_history,self.live_program_history)
                    key=m.request_key(source)
            elif m is workflow_ph_listing_library:
                linked=m.bind(site,tool,source,self.bulletin_source,self.ph_listing_history)
                key=m.request_key(tool,source)
            elif m is workflow_channel_alignment:
                linked=m.bind(site,tool,source)
                key=source["job_id"]
            elif m is workflow_ad_channel_intake:
                linked=m.bind(site,source,self.bulletin_source)
                key=source["job_id"]
            elif m is workflow_poster_delivery:
                if tool == m.SHOP_TAKEOUT:
                    linked = m.bind_shop_takeout(site, source, self.verified_poster_archive_task)
                else:
                    def load_poster(poster_site, ident, version, *, reviewed=False):
                        return self.verified_poster_delivery_source(poster_site, ident, version,
                            reviewed=reviewed, agent_session=agent_session)
                    linked = m.bind(site, source, load_poster, tool=tool)
                key = source["job_id"]
            elif m is workflow_teardown_report:
                if tool == m.PH_INTAKE:
                    linked = m.bind_ph_intake(site, source, self.bulletin_source)
                else:
                    linked = m.bind(site, source, self.verified_live_offline_source,
                                    self.verified_teardown_report_frames, tool=tool)
                key = source["job_id"]
            elif m is workflow_teardown_anchor:
                evidence = self.root / "private-fixtures" / "cn-bava-human-anchor.json"
                try:
                    fixture_bytes = bind_data_path(self.root, evidence).read_bytes()
                except (OSError, APIError):
                    raise m.WorkflowError("此路径需要授权人保管的本机历史锚点原件。") from None
                linked = m.bind(site, tool, source, self.bulletin_source, fixture_bytes)
                key = source["job_id"]
            elif m is workflow_director_version_intake:
                linked = m.bind(site, tool, source, self.director_version_source)
                key = m.request_key(source)
            elif m is workflow_director_teardown_methods:
                linked = m.bind(site, source, self.bulletin_source)
                key = source["job_id"]
            elif m is workflow_teardown_library:
                try:
                    linked=m.bind(site,tool,source,self.bulletin_source,self.media_registry_result,
                                  self.verified_live_offline_source,self.local_media_frame,self.teardown_library_ids)
                except workflow_live_offline.WorkflowError as error:
                    raise m.WorkflowError(str(error)) from None
                key=m.request_key(source)
            elif m is workflow_director_review:
                linked=m.bind(site,tool,source,self.bulletin_source,self.local_artifact_bytes)
                key=m.request_key(source)
            elif m is workflow_shop_stock_handoff:
                linked=m.bind(site,tool,source,self.bulletin_source)
                key=source["job_id"]
            elif m in (workflow_selection_trial, workflow_selection_screen, workflow_campaign_options,
                        workflow_ad_experiment, workflow_cn_warehouse, workflow_director_topic):
                linked=m.bind(site,tool,source,self.bulletin_source)
                key=m.request_key(source)
            else:
                linked=m.bind(site,tool,source,self.bulletin_source,self.playbook_rows)
                key=source["lookup_id"] if tool==m.FIND else f"{source['playbook_id']}:{source['version']}"
            return f"{tool}:{site}:{key}",linked
        except (m.WorkflowError,workflow_live_rehearsal.WorkflowError,workflow_lead_weekly.WorkflowError,
                workflow_live_offline.WorkflowError,workflow_teardown_library.WorkflowError) as error:
            raise APIError(400,"invalid_role_packet",str(error)) from None

    def paid_slot_control(self,ident,site):
        result,pin=self.live_rehearsal_source(ident,workflow_live_rehearsal.CONTROL,site,False)
        directory=self.tasks_dir/ident
        try:
            original=read_json(bind_data_path(directory,directory/"role-input.json"))
            if sha256(workflow_live_rehearsal.encode(original)).hexdigest()!=result["input_sha256"]:
                raise ValueError("original rehearsal digest")
        except (OSError,ValueError,TypeError,KeyError) as error:
            raise APIError(409,"paid_slot_control_input_unverified","费用原始回执无法核验。") from error
        return result,{**pin,"input_sha256":result["input_sha256"]},original

    def bind_paid_slot(self,site,source):
        try:
            return workflow_live_paid_slot.bind(site,source,self.paid_slot_control)
        except (workflow_live_paid_slot.WorkflowError, workflow_live_rehearsal.WorkflowError,
                workflow_lead_weekly.WorkflowError) as error:
            raise APIError(400,"invalid_live_paid_slot",str(error)) from None

    def bind_preflight(self,site,source):
        try:
            return workflow_live_preflight.bind(site,source,self.live_rehearsal_source)
        except (workflow_live_preflight.WorkflowError, workflow_live_rehearsal.WorkflowError,
                workflow_lead_weekly.WorkflowError) as error:
            raise APIError(400,"invalid_live_preflight",str(error)) from None

    def bind_supplier_research(self,site,source):
        try:
            return workflow_competitor_supplier.bind(site,source,self.bulletin_source)
        except (workflow_competitor_supplier.WorkflowError, workflow_live_rehearsal.WorkflowError,
                workflow_lead_weekly.WorkflowError) as error:
            raise APIError(400,"invalid_supplier_research",str(error)) from None

    def bind_dossier(self,site,source):
        try:
            return workflow_competitor_dossier.bind(site,source,self.bulletin_source)
        except (workflow_competitor_dossier.WorkflowError, workflow_lead_weekly.WorkflowError,
                workflow_live_rehearsal.WorkflowError) as error:
            raise APIError(400,"invalid_dossier_input",str(error)) from None

    def bind_bulletin(self, site, source):
        try:
            return workflow_competitor_bulletin.bind(site, source, self.bulletin_source)
        except (workflow_competitor_bulletin.WorkflowError, workflow_lead_weekly.WorkflowError,
                workflow_live_rehearsal.WorkflowError) as error:
            raise APIError(400, "invalid_bulletin_input", str(error)) from None

    def bind_monthly(self, site, source):
        try:
            return workflow_lead_monthly.bind(site, source, self.weekly_source)
        except (workflow_lead_monthly.WorkflowError, workflow_lead_weekly.WorkflowError,
                workflow_live_rehearsal.WorkflowError) as error:
            raise APIError(400, "invalid_monthly_input", str(error)) from None

    def weekly_artifacts(self, task):
        folder = self.tasks_dir / task["id"]
        try:
            files = {k: bind_data_path(folder, folder / name).read_bytes()
                     for k, name in workflow_lead_weekly.FILES.items()}
            workflow_lead_weekly.verify_saved(task, files)
            return files
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise APIError(409, "weekly_evidence_mismatch", "岗位来源或周报文件回执不一致。") from error

    def weekly_source(self, ident, site, tools):
        task = self.store.get(ident)
        if (not task or task["site"] != site or task["status"] != "ready"
                or task["source"].get("tool_id") not in tools):
            raise APIError(409, "weekly_source_unavailable", "仅可引用本站已保存的指定岗位或上一版周报。")
        files = self.weekly_artifacts(task)
        return task["result"], {"task_id": ident, "result_sha256": sha256(files["json"]).hexdigest(),
                                "receipt_sha256": sha256(files["receipt"]).hexdigest()}

    def bind_weekly(self, site, tool, source):
        try:
            return workflow_lead_weekly.bind(site, tool, source, self.weekly_source)
        except (workflow_lead_weekly.WorkflowError, workflow_live_rehearsal.WorkflowError) as error:
            raise APIError(400, "invalid_weekly_input", str(error)) from None

    def c10_artifacts(self, task):
        folder=self.tasks_dir/task["id"]
        try:
            bundle={k:bind_data_path(folder,folder/name).read_bytes() for k,name in workflow_competitor_handoff.FILES.items()}
            workflow_competitor_handoff.verify_saved(task,bundle)
            return bundle
        except (OSError,ValueError,TypeError,KeyError) as error:
            raise APIError(409,"competitor_handoff_evidence_mismatch","竞品交接回执或产物不可核验。") from error

    def c10_latest(self,site,key):
        return max((t["source"]["version"] for t in self.store.values() if t["site"]==site and
            t["source"].get("tool_id")==workflow_competitor_handoff.PROPOSE and
            t["source"].get("proposal_id")==key and t["status"] not in ("failed","interrupted")),default=0)

    def c10_source(self,ident,tool,site,latest):
        t=self.store.get(ident)
        if not t or t["site"]!=site or t["status"]!="ready" or t["source"].get("tool_id")!=tool:
            raise APIError(409,"competitor_handoff_source_unavailable","须引用本站对应岗位的已完成任务。")
        a=t["result"]
        if latest and a["version"]!=self.c10_latest(site,a["proposal_id"]):
            raise APIError(409,"competitor_handoff_stale","已存在新提案版本，旧版只供历史查询。")
        files=self.c10_artifacts(t)
        return a,{"task_id":ident,"result_sha256":sha256(files["json"]).hexdigest(),
                  "receipt_sha256":sha256(files["receipt"]).hexdigest()}

    def c10_review_source(self,ident,site):
        t=self.store.get(ident)
        if not t or t["site"]!=site or t["status"]!="ready" or t["source"].get("tool_id")!=workflow_competitor.TOOL_ID:
            raise APIError(409,"competitor_review_source_unavailable","须使用本站已完成的合成评价证据任务。")
        folder=self.tasks_dir/ident
        try:
            input_data=read_json(bind_data_path(folder,folder/"role-input.json"))
            encoded=json.dumps([t["kind"],site,t["title"],t["source"],input_data],ensure_ascii=False,sort_keys=True,allow_nan=False).encode("utf-8")
            files={k:self.local_artifact_bytes(ident,k) for k in ("json","report","receipt")}
            a=json.loads(files["json"]);r=json.loads(files["receipt"])
            if (sha256(encoded).hexdigest()!=t["request_hash"] or a.pop("source_input")!=input_data or a!=t["result"]
                or (a["site"],a["mode"],a["tool_id"])!=(site,"SYNTHETIC_OFFLINE",workflow_competitor.TOOL_ID)
                or r["result_sha256"]!=sha256(files["json"]).hexdigest() or r["report_sha256"]!=sha256(files["report"]).hexdigest()
                or r["source_input_serialized_sha256"]!=sha256(workflow_competitor._json_bytes(input_data)).hexdigest()
                or r["data_request_count"]!=0):
                raise ValueError("Source identity, mode or evidence content changed")
            return a,{"task_id":ident,"result_sha256":sha256(files["json"]).hexdigest(),
                      "receipt_sha256":sha256(files["receipt"]).hexdigest()}
        except (OSError,ValueError,TypeError,KeyError) as error:
            raise APIError(409,"competitor_review_evidence_mismatch","评价原件或合成来源无法核验。") from error

    def bind_c10(self,site,tool,value):
        m=workflow_competitor_handoff
        try:key=m.common(site,tool,value)
        except (m.WorkflowError,workflow_live_rehearsal.WorkflowError) as error:
            raise APIError(400,"invalid_competitor_handoff",str(error)) from None
        if tool==m.PROPOSE and value["version"]<self.c10_latest(site,value["proposal_id"]):
            raise APIError(409,"competitor_handoff_stale","已存在新提案版本。")
        try:return key,m.bind(site,tool,value,self.c10_source,self.c10_review_source)
        except (m.WorkflowError,workflow_live_rehearsal.WorkflowError) as error:
            raise APIError(409,"competitor_handoff_binding_mismatch",str(error)) from None

    def portfolio_artifacts(self, task):
        root=self.tasks_dir/task["id"]
        try:
            files={k:bind_data_path(root,root/name).read_bytes() for k,name in workflow_portfolio_handoff.FILES.items()}
            workflow_portfolio_handoff.verify_saved(task,files)
            return files
        except (ValueError,OSError,KeyError,TypeError) as error:
            raise APIError(409,"portfolio_evidence_mismatch","货盘输入、结果或回执无法核验。") from error

    def portfolio_latest(self, site, key):
        versions=[t["source"]["version"] for t in self.store.values() if t["site"]==site
                  and t["source"].get("tool_id")==workflow_portfolio_handoff.PRODUCE
                  and t["source"].get("portfolio_id")==key and t["status"] not in ("failed","interrupted")]
        return max(versions,default=0)

    def portfolio_source(self, ident, tool, site, latest):
        t=self.store.get(ident)
        if not t or t["site"]!=site or t["status"]!="ready" or t["source"].get("tool_id")!=tool:
            raise APIError(409,"portfolio_source_unavailable","须引用同站已完成的对应岗位本机产物。")
        a=t["result"]
        if latest and a["version"]!=self.portfolio_latest(site,a["portfolio_id"]):
            raise APIError(409,"portfolio_source_stale","已有更新货盘，旧版仅保留历史取件。")
        files=self.portfolio_artifacts(t)
        return a,{"task_id":ident,"result_sha256":sha256(files["json"]).hexdigest(),
                  "receipt_sha256":sha256(files["receipt"]).hexdigest()}

    def bind_portfolio(self, site, tool, value):
        m=workflow_portfolio_handoff
        try:
            key=m.common(site,tool,value)
        except (m.WorkflowError,workflow_live_rehearsal.WorkflowError) as error:
            raise APIError(400,"invalid_portfolio_contract",str(error)) from None
        if tool==m.PRODUCE and value["version"]<self.portfolio_latest(site,value["portfolio_id"]):
            raise APIError(409,"portfolio_source_stale","已有更新货盘，旧版仅保留历史取件。")
        try:
            return key,m.bind(site,tool,value,self.portfolio_source)
        except (m.WorkflowError,workflow_live_rehearsal.WorkflowError) as error:
            raise APIError(409,"portfolio_identity_mismatch",str(error)) from None

    def rehearsal_versions(self, site, tool, chain):
        return [t["source"]["version"] for t in self.store.values()
                if t["site"] == site and t["source"].get("tool_id") == tool
                and t["source"].get("rehearsal_chain") == chain and t["status"] not in ("failed","interrupted")]

    def live_rehearsal_source(self, ident, tool, site, latest):
        t = self.store.get(ident)
        if (not t or t["site"] != site or t["status"] != "ready" or t["source"].get("tool_id") != tool):
            raise APIError(409, "rehearsal_source_unverified", "须使用同站、对应岗位已完成的演练回执。")
        data = t["result"]
        if latest and data["version"] != max(self.rehearsal_versions(site,tool,data["chain"]),default=0):
            raise APIError(409, "rehearsal_source_stale", "已存在更新回执，旧版仅供历史取件。")
        files = self.live_rehearsal_artifacts(t)
        return data, {"task_id":ident, "result_sha256":sha256(files["json"]).hexdigest(),
                      "receipt_sha256":sha256(files["receipt"]).hexdigest()}

    def live_rehearsal_artifacts(self, task):
        folder = self.tasks_dir / task["id"]
        try:
            files = {kind: bind_data_path(folder, folder / name).read_bytes()
                     for kind, name in workflow_live_rehearsal.output_names(task.get("attempt")).items()}
            workflow_live_rehearsal.verify_artifacts(task,files)
            return files
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise APIError(409, "rehearsal_evidence_unverified", "演练文件和回执无法核验。") from error

    def bind_live_rehearsal(self, site, tool, value):
        m = workflow_live_rehearsal
        try:
            chain = m.common(site,tool,value)
        except m.WorkflowError as error:
            raise APIError(400,"invalid_rehearsal_input",str(error)) from None
        latest = max(self.rehearsal_versions(site,tool,chain),default=0)
        if value["version"] < latest:
            raise APIError(409,"rehearsal_stale_version",f"这条记录已到 v{latest}，旧版仅供历史取件。")
        try:
            return chain,m.bind(self.project,site,tool,value,self.live_rehearsal_source,self.campaign_review_source)
        except (m.WorkflowError,workflow_cn_campaign_offer.WorkflowError,workflow_ph_campaign_offer.WorkflowError) as error:
            raise APIError(409,"rehearsal_binding_mismatch",str(error)) from None

    def bind_live_offline(self, site, tool, value, *, enforce_latest=False):
        m = workflow_live_offline
        try:
            m.validate(site, tool, value)
        except m.WorkflowError as error:
            raise APIError(400, "invalid_live_offline", str(error)) from None
        chain_tool = m.PLAN if tool in m.INTAKES else tool
        language = value.get("language") if chain_tool == m.SOP else None
        if enforce_latest:
            related = [t for t in self.store.values() if t["site"] == site
                       and t["status"] not in ("failed", "interrupted")
                       and t["source"].get("tool_id") == chain_tool
                       and t["source"].get("chain_key") == value["key"]
                       and t["source"].get("language") == language]
            latest = max((t["source"]["version"] for t in related), default=0)
            if value["version"] < latest or (tool in m.INTAKES and latest and value["version"] != latest):
                raise APIError(409, "live_offline_stale", f"当前链路已到 v{latest}，旧版本仅可取回。")
        try:
            return m.bind(site, tool, value, self.verified_live_offline_source)
        except m.WorkflowError as error:
            raise APIError(409, "live_offline_evidence_unverified", str(error)) from error

    def live_offline_artifacts(self, task):
        folder = self.tasks_dir / task["id"]
        try:
            files = {k: bind_data_path(folder, folder / file).read_bytes()
                     for k, file in workflow_live_offline.OUTPUTS.items()}
            workflow_live_offline.verify_saved(task, files)
            return files
        except (OSError, TypeError, KeyError, ValueError) as error:
            raise APIError(409, "live_offline_receipt_unverified", "直播版本与回执或产物不符。") from error

    def shop_image_intake(self, site, ident, parameters):
        task = self.store.get(ident)
        if (not task or task["site"] != site or task["status"] != "ready"
                or task["source"].get("tool_id") != "shop-product-facts"
                or "visual_delivery" not in task["source"]):
            raise APIError(409, "shop_image_intake_missing", "请选择本工作台已完成且附有视觉包的本站商城收件任务。")
        folder = self.tasks_dir / ident
        supplied = read_json(bind_data_path(folder, folder / "role-input.json"))
        fingerprint = json.dumps([task["kind"], task["site"], task["title"], task["source"], supplied],
                                 ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
        delivery = task["source"]["visual_delivery"]
        if (sha256(fingerprint).hexdigest() != task["request_hash"]
                or task["result"] != json.loads(self.local_artifact_bytes(ident, "json"))
                or delivery != task["result"].get("visual_delivery")):
            raise workflow_shop_image_review.WorkflowError("收件输入、结果或图包来源已变化。")
        if delivery["listing"] != {k: parameters[k] for k in ("sku_id", "version")}:
            raise APIError(409, "shop_image_binding_mismatch", "所选 SKU 或版本与商城收件不符。")
        main = parameters["image_id"].startswith("P")
        group = delivery["main" if main else "detail"]
        try:
            package = self.local_artifact_bytes(group["task_id"], "bundle" if main else "detail_bundle")
        except (OSError, APIError) as error:
            raise workflow_shop_image_review.MissingImage("指定图片的本机归档缺失。") from error
        asset, data = workflow_shop_image_review.asset_from_manifest(delivery, parameters, package)
        if delivery != self.shop_visual_source(site, supplied, task["source"]["visual_tasks"]):
            raise workflow_shop_image_review.WorkflowError("视觉来源已变化，不能作为本次审核依据。")
        hashes = {row["id"]: row["sha256"] for key in ("main", "detail") for row in delivery[key]["files"]
                  if row["id"] != "description"}
        return {"product_id": supplied["facts_pack"]["product_id"], "asset": asset,
                "intake_task_id": ident, "manifest_sha256_by_id": hashes}, data

    def mall_visual_bytes(self, task, kind):
        ident, site = task["id"], task["site"]
        delivery = task.get("source", {}).get("visual_delivery")
        if not delivery or task["source"].get("tool_id") != "shop-product-facts" or task["status"] != "ready":
            raise APIError(404, "mall_visual_unavailable", "没有同版商城接件。")
        try:
            listing = delivery["listing"]
            selected, cover = self.shop_image_intake(site, ident, {**listing, "image_id": "P01"})
            if kind == "mall_front_original":
                return cover
            detail_id = delivery["detail"]["task_id"]
            detail = self.store[detail_id]["result"]
            bundle = self.local_artifact_bytes(detail_id, "detail_bundle")
            expected = detail["file_sha256"]["original.png"]
            with ZipFile(BytesIO(bundle)) as archive:
                matches = [entry for entry in archive.infolist() if entry.filename == "original.png"]
                if len(matches) != 1 or not 0 < matches[0].file_size <= 10_000_000:
                    raise ValueError("Unverified full-page PNG in source package.")
                original = archive.read(matches[0])
            if sha256(original).hexdigest() != expected:
                raise ValueError("Full-page PNG checksum mismatch.")
            with Image.open(BytesIO(original)) as image:
                if image.format != "PNG" or list(image.size) != detail["size"]:
                    raise ValueError("Full-page PNG dimensions mismatch.")
                image.verify()
            if kind == "mall_detail_original":
                return original
            producer_receipt = self.local_artifact_bytes(detail_id, "receipt")
            result = {
                "site": site, "scope": "synthetic_offline", "intake_task_id": ident,
                "intake_result_sha256": sha256(self.local_artifact_bytes(ident, "json")).hexdigest(),
                "product_id": selected["product_id"], "listing": listing,
                "front": selected["asset"],
                "detail": {
                    "task_id": detail_id, "bundle_sha256": delivery["detail"]["bundle_sha256"],
                    "producer_receipt_sha256": sha256(producer_receipt).hexdigest(),
                    "png_sha256": expected, "png_size": detail["size"],
                    "layout_source_sha256": detail["file_sha256"]["layout-source.json"],
                    "layered_psd_sha256": detail["file_sha256"]["layered.psd"],
                    "layer_count": detail["layer_count"], "native_text_count": detail["native_text_count"],
                    "sections": [{key: row[key] for key in ("group","module","status")} for row in detail["sections"]],
                },
                "physical_facts_verified": False, "physical_handset_verified": False,
                "native_photoshop_verified_by_mall": False, "upload_authorized": False,
            }
            return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        except (APIError, OSError, BadZipFile, KeyError, TypeError, ValueError) as error:
            raise APIError(409, "mall_visual_unverified", "商城接件、原图或版式回执不一致。") from error

    def shop_image_artifacts(self, task):
        folder = self.tasks_dir / task["id"]
        try:
            reads = {key: bind_data_path(folder, folder / name).read_bytes()
                     for key, name in workflow_shop_image_review.OUTPUTS.items()}
            workflow_shop_image_review.result_artifacts(task, reads)
            return reads
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise APIError(409, "shop_image_receipt_unverified", "图片或逐图审核回执缺失或已变化。") from error

    def shop_image_history(self, site, ident, mode):
        task = self.store.get(ident)
        if (not task or task["site"] != site or task["status"] != "ready"
                or task["source"].get("tool_id") != workflow_shop_image_review.TOOLS[site]
                or task["result"].get("mode") != mode):
            raise APIError(409, "shop_image_prior_missing", "请选择本工作台正确类型且已完成的逐图审核任务。")
        reads = self.shop_image_artifacts(task)
        return task["result"], reads["preview"]

    def bind_shop_image_review(self, site, parameters, *, current_only=False):
        module = workflow_shop_image_review
        try:
            module.validate(site, parameters)
        except module.WorkflowError as error:
            raise APIError(400, "invalid_shop_image_review", str(error)) from None
        try:
            if parameters["mode"] == "open":
                linked, data = self.shop_image_intake(site, parameters["intake_task_id"], parameters)
                if previous := parameters.get("revises_review_task_id"):
                    prior, _ = self.shop_image_history(site, previous, "review")
                    linked["revision"] = module.check_revision(prior, linked, parameters)
            else:
                inspection, data = self.shop_image_history(site, parameters["inspection_task_id"], "open")
                if (not module.same_listing_asset(inspection, parameters)
                        or inspection["asset"]["sha256"] != parameters["image_sha256"]):
                    raise APIError(409, "shop_image_binding_mismatch", "审核记录与打开的 SKU、版本、图片编号或哈希不符。")
                linked = {key: inspection[key] for key in
                          ("product_id", "asset", "intake_task_id", "manifest_sha256_by_id")}
                linked["inspection_task_id"] = inspection["task_id"]
                if "revision" in inspection:
                    linked["revision"] = inspection["revision"]
            if current_only:
                related = [task["result"] for task in self.store.values()
                           if task["site"] == site and task["status"] == "ready"
                           and task["source"].get("tool_id") == module.TOOLS[site]
                           and task["result"]["product_id"] == linked["product_id"]
                           and module.same_listing_asset(task["result"], parameters, version=False)]
                newest = max((row["version"] for row in related), default=parameters["version"])
                if parameters["version"] < newest:
                    raise APIError(409, "shop_image_stale_version", f"已有 v{newest}，旧版仅可从任务历史查看。")
                if parameters["mode"] == "open":
                    closed = {row["revision"]["previous_review_task_id"] for row in related
                              if row.get("revision") and row.get("revision_resolution")
                              and all(state == "closed" for state in row["revision_resolution"].values())}
                    returns = [row for row in related if row["status"] == "returned_for_revision"
                               and row["version"] < parameters["version"] and row["task_id"] not in closed]
                    if returns:
                        latest = max(returns, key=lambda row: (row["version"], row["task_id"]))
                        if parameters.get("revises_review_task_id") != latest["task_id"]:
                            raise APIError(409, "shop_image_revision_link_required", "请引用最新退回回执再打开返修版本。")
            return linked, data
        except module.MissingImage as error:
            raise APIError(409, "shop_image_missing", str(error)) from error
        except (BadZipFile, OSError, TypeError, KeyError, ValueError) as error:
            message = str(error) if isinstance(error, module.WorkflowError) else "逐图审核无法验证来源和文件。"
            raise APIError(409, "shop_image_evidence_unverified", message) from error

    def shop_visual_source(self, site, parameters, selectors):
        if site not in PLATFORMS or parameters.get("site") != site:
            raise APIError(400, "invalid_visual_site", "视觉资料收件只接受本站任务。")
        if (not isinstance(selectors, dict) or set(selectors) != {"main", "detail"}
                or any(not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident)
                       for ident in selectors.values())):
            raise APIError(400, "invalid_visual_refs", "请选择两份已完成的同站视觉任务。")
        try:
            expect = parameters["expect"]
            facts = parameters["facts_pack"]
            binding = {"sku_id": expect["sku_id"], "version": expect["version"]}
            modules = long_detail.MODULES if site == "CN" else long_detail.PH_MODULES
            ids = [f"P{i:02d}" for i in range(1, 6)] + [f"S{i:02d}" for i in range(1, len(modules) + 1)]
            if facts["image_ids"] != ids or expect["image_ids"] != ids:
                raise ValueError("Expected ordered main images and complete same-site detail sections.")
            def upstream(ident, expected_tool, package):
                with self.gate:
                    task = self.store.get(ident)
                    if (not task or task["site"] != site or task["status"] != "ready"
                            or task["source"].get("tool_id") != expected_tool):
                        raise ValueError("Visual source is not a completed same-site task.")
                    report = self.local_artifact_bytes(ident, "json")
                    data = json.loads(report)
                    receipt = json.loads(self.local_artifact_bytes(ident, "receipt"))
                    if (data != task["result"] or any(data[key] != value for key, value in receipt.items())
                            or data.get("copy_execution", {}).get("kind") == visual_copy.TEST
                            or data["site"] != site or data["task_id"] != ident
                            or data["product_id"] != facts["product_id"]
                            or (data["listing"] if site == "CN" else data["market"]["listing"]) != binding):
                        raise ValueError("Visual source does not match the product, SKU, version or recorded result.")
                    return data, self.local_artifact_bytes(ident, package)
            main = upstream(selectors["main"], main_images.TOOL_ID, "bundle")
            detail = upstream(selectors["detail"], long_detail.TOOL_ID, "detail_bundle")
            return visual_market.shop_visual_delivery(*main, *detail, main_images.SLOTS, modules)
        except (APIError, BadZipFile, OSError, KeyError, TypeError, ValueError) as error:
            raise APIError(409, "visual_delivery_unverified", "视觉资料未收件，请核对同站商品、SKU、版本、清单顺序、文件与描述来源。") from error

    def campaign_review_source(self, site, ident):
        with self.gate:
            task = self.store.get(ident)
            if (not task or task["site"] != site or task["status"] != "ready"
                    or task["source"].get("tool_id") != CAMPAIGN_OFFERS[site].REVIEW):
                raise APIError(409, site.lower()+"_campaign_source_unavailable", "须选择已完成的本站活动价审任务。")
            original_raw, original, json_raw, receipt_raw = self.campaign_offer_artifacts(task, original=True)
            return ({"task_id": ident, "review_input_file_sha256": sha256(original_raw).hexdigest(),
                     "result_sha256": sha256(json_raw).hexdigest(), "receipt_sha256": sha256(receipt_raw).hexdigest()},
                    original, task["result"])

    def campaign_offer_artifacts(self, task, original=False):
        folder = self.tasks_dir / task["id"]
        site = task["site"]
        module = CAMPAIGN_OFFERS[site]
        if task["local_files"] != module.OUTPUTS:
            raise APIError(409, site.lower()+"_campaign_evidence_mismatch", "活动价审或商城收件产物缺失。")
        try:
            result_bytes = bind_data_path(folder, folder / task["local_files"]["json"]).read_bytes()
            receipt_bytes = bind_data_path(folder, folder / task["local_files"]["receipt"]).read_bytes()
            result = task["result"]
            if (result != json.loads(result_bytes) or result["site"] != site or result["task_id"] != task["id"]
                    or result["tool_id"] != task["source"]["tool_id"]
                    or json.loads(receipt_bytes) != module.receipt_for(result, result_bytes)):
                raise ValueError("Campaign evidence changed")
            if not original:
                return {"json": result_bytes, "receipt": receipt_bytes}
            raw = bind_data_path(folder, folder / "role-input.json").read_bytes()
            data = json.loads(raw)
            fingerprint = json.dumps([task["kind"], task["site"], task["title"], task["source"], data],
                                     ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
            if sha256(fingerprint).hexdigest() != task["request_hash"]:
                raise ValueError("Campaign source changed")
            return raw, data, result_bytes, receipt_bytes
        except (APIError, OSError, ValueError, TypeError, KeyError):
            raise APIError(409, site.lower()+"_campaign_evidence_mismatch", "活动价审或商城收件凭据不完整或已变化。") from None

    def bind_named_target(self,site,target,p):
        identity=p.get("target") if isinstance(p,dict) and target in workflow_channel_alignment.TOOLS else p
        if not isinstance(p,dict) or not isinstance(identity,dict) or identity.get("site")!=site or (
                p.get("scope",p.get("data_origin"))!="synthetic_offline"):
            raise APIError(400,"invalid_named_target_input","具名岗位只接收本站可声明的合成输入。")
        runner=self.workflows[target]
        if target==workflow_campaign.TOOL_ID:
            if set(p)!={"site","scope","case_ids"}:
                raise APIError(400,"invalid_named_target_input","活动合成派单须显式登记站点及案例编号范围。")
            def perform(folder,progress):
                return runner(self.project,site,target,{"case_ids":p["case_ids"]},folder,{},progress)
            return {},perform
        if target==workflow_media_library.VISUAL:
            if p.get("mode")!="search" or p.get("history") is not False:
                raise APIError(400,"invalid_named_target_input","具名视觉入口仅检索登记中的当前版本。")
            linked=self.bind_media_library(site,target,p)
            def perform(folder,progress):
                return runner(site,target,p,folder,linked,self.engine,self.node,progress)
            return linked[1],perform
        if target==workflow_teardown_library.TOOL and p.get("mode")!="find":
            raise APIError(400,"invalid_named_target_input","具名拆解入口仅查询已封存的元素库。")
        if target in LOCAL_PACKET_MODULES:
            _,linked=self.bind_role_packet(site,target,p)
            def perform(folder,progress):
                return runner(self.project,site,target,p,folder,{},progress,linked=linked)
            return linked[0],perform
        def perform(folder,progress):
            return runner(self.project,site,target,p,folder,{},progress)
        return {},perform

    def named_upstream(self, site, job_id, *, require_ready=False):
        message = f"本站上游任务 {job_id} 尚无可核实的完成回执，未派发下游。"
        with self.gate:
            ident = self.requests.get(workflow_lead.named_request_key(site, job_id))
            parent = self.store.get(ident)
            if (not parent or parent["site"] != site or parent["status"] != "ready"
                    or parent["source"].get("tool_id") != workflow_lead.NAMED_TOOL
                    or parent["source"].get("job_id") != job_id
                    or ((require_ready or "named_target" in parent["source"])
                        and parent["result"].get("dependency_ready") is not True)):
                raise APIError(409, "upstream_not_completed", message)
            try:
                artifacts = self.named_completion_artifacts(parent)
                psource=parent["source"]
                if "named_target" in psource:
                    folder=self.tasks_dir/ident
                    saved=read_json(bind_data_path(folder,folder/"role-input.json"))
                    signature=json.dumps([parent["kind"],site,parent["title"],psource,saved],
                                         ensure_ascii=False,sort_keys=True,allow_nan=False).encode("utf-8")
                    if sha256(signature).hexdigest()!=parent["request_hash"]:
                        raise ValueError("upstream named input changed")
                    pins,_=self.bind_named_target(site,psource["named_target"],saved["input"])
                    if pins!=psource["named_target_pins"]:
                        raise ValueError("upstream named evidence changed")
            except (APIError,ValueError,KeyError,TypeError,OSError):
                raise APIError(409, "upstream_not_completed", message) from None
            return {"job_id": job_id, "workbench_task_id": ident,
                    "receipt_sha256": sha256(artifacts["receipt"]).hexdigest()}

    def named_completion_artifacts(self, task):
        """Verify and return the exact bytes from a completed local named dispatch."""
        folder = self.tasks_dir / task["id"]
        dispatch = folder / "named-dispatch"

        def read_evidence(path):
            return bind_data_path(folder, dispatch / path).read_bytes()

        try:
            source = task["source"]
            result=task["result"]
            target=source.get("named_target")
            if target is None:
                worker_paths = NAMED_WORKER_FILES[source["role"].split("-", 1)[1]]
            else:
                worker_paths=result["worker_files"]
                prefix=f"worker/{task['id']}/"
                if (result["worker_tool_id"]!=target or result["route_pins"]!=source["named_target_pins"]
                        or target!=workflow_lead.NAMED_ROUTES.get(source["role"].split("-",1)[1])
                        or set(worker_paths)!={"json","receipt","report"} or any(
                            not x.startswith(prefix) or ".." in Path(x).parts or "\\\\" in x for x in worker_paths.values())):
                    raise ValueError("Named typed artifact scope differs")
            expected_files = {
                "json": "named-dispatch/result.json",
                "receipt": "named-dispatch/receipt.json",
                "report": "named-dispatch/" + worker_paths["report"],
            }
            if task["local_files"] != expected_files:
                raise ValueError("Unexpected completion artifact paths")
            result = task["result"]
            expected_receipt = {key: val for key, val in result.items() if key != "worker_result"}
            result_bytes = read_evidence("result.json")
            receipt_bytes = read_evidence("receipt.json")
            receipt = json.loads(receipt_bytes)
            if (json.loads(result_bytes) != result or receipt != expected_receipt
                    or receipt["state"] != "completed" or receipt["site"] != task["site"]
                    or receipt["job_id"] != source["job_id"] or receipt["role"] != source["role"]
                    or receipt["workbench_task_id"] != task["id"]):
                raise ValueError("Completion identity or wrapper mismatch")
            worker_files = {kind: read_evidence(path) for kind, path in worker_paths.items()}
            worker_hashes = {kind: sha256(data).hexdigest() for kind, data in worker_files.items()}
            if target is not None:
                worker_result=json.loads(worker_files["json"])
                if (worker_result!=result["worker_result"] or worker_result["tool_id"]!=target
                        or worker_result["site"]!=task["site"] or worker_result.get("task_id",task["id"])!=task["id"]):
                    raise ValueError("Named worker output and envelope identity differ")
            if (worker_hashes != receipt["artifact_sha256"]
                    or sha256(read_evidence("queue.sqlite")).hexdigest() != receipt["queue_sha256"]):
                raise ValueError("Completion evidence mismatch")
            return {"json": result_bytes, "receipt": receipt_bytes, "report": worker_files["report"]}
        except (APIError, OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
            raise APIError(409, "named_evidence_unavailable",
                           "本机岗位结果的回执、队列或产物缺失或已变化，暂不可取件。请核对原任务文件。") from None

    def role_media(self, media, limit):
        selected = []
        total = 0
        filenames = set()
        for index, item in enumerate(media):
            try:
                name, suffix, data, _ = validate_upload(item, "auto")
            except MediaError as error:
                raise APIError(400, "invalid_media", str(error)) from None
            if suffix != ".mp4" or name.casefold() in filenames:
                raise APIError(400, "invalid_media", "请选择文件名互不相同的 MP4 素材。")
            total += len(data)
            if total > min(limit, MAX_UPLOAD_BYTES):
                raise APIError(400, "media_limit", "所选素材总大小超过此工具上限。请减少素材或缩短视频。")
            filenames.add(name.casefold())
            selected.append(({"name": name, "file": "assets/asset-" + str(index) + ".mp4",
                              "sha256": sha256(data).hexdigest()}, data))
        return selected

    def schedule_visual_copy(self, task, parameters):
        gate = task["copy_gate"]
        if gate["binding"] is None:
            other_rounds = [other["copy_gate"] for other in self.store.values()
                            if other["id"] != task["id"] and other["site"] == task["site"]
                            and other.get("copy_gate")
                            and other["copy_gate"].get("execution_kind") == gate["execution_kind"]]
            if any(row["binding"] is not None and not row["layout_allowed"] for row in other_rounds):
                self.status(task, visual_copy.WAITING, "待提炼。上一轮尚未完成，本任务暂未分配 A/B。")
                return self.serialize(task)
            completed = sum(row["layout_allowed"] for row in other_rounds)
            try:
                gate["binding"] = visual_copy.bind(self.project, task, parameters, "B" if completed % 2 else "A")
            except OSError:
                self.status(task, visual_copy.WAITING, "待提炼。本地 Skill 文件不可用。")
                return self.serialize(task)
        if gate["execution_kind"] != visual_copy.TEST or self.test_copy_executor is None:
            self.status(task, visual_copy.WAITING, "待提炼。当前工作台尚无可用的真实 Skill 执行入口。")
        else:
            self.status(task, "queued", "已加入隔离文案测试队列。")
            self.executor.submit(self.run_local, task)
        return self.serialize(task)

    def create_local(self, payload, kind, title, source, *, data=None, role_input=None, media_files=()):
        request_id = self.request_id(payload.get("request_id"))
        site = payload.get("site")
        if not isinstance(site, str) or site not in PLATFORMS:
            raise APIError(400, "invalid_site", "请选择中国抖音或菲律宾 TikTok Shop。")
        tool = source.get("tool_id")
        requires_copy = tool in visual_copy.SHAPES and visual_copy.missing(tool, role_input)
        if source.get("tool_id") == workflow_lead.NAMED_TOOL:
            request_id = workflow_lead.named_request_key(site, source["job_id"])
        elif tool == workflow_live_team.TOOL:
            request_id = f"live-team:{site}:{source['session_id']}"
        elif tool in workflow_visual_surfaces.BRIEFS:
            request_id = f"visual-surface-brief:{site}:{source['surface_key']}:{source['version']}"
        elif tool in workflow_media_library.LIBRARIES and "asset_id" in source:
            request_id = f"media-library:{site}:{tool}:{source['asset_id']}:{source['version']}"
        elif tool == workflow_creator_cohorts.TOOL:
            request_id = f"creator-cohorts:{site}:{source['creator_cohort_key']}"
        elif tool in workflow_competitor_watch.TOOLS:
            request_id = source["watch_key"]
        elif tool in workflow_campaign_reviews.TOOLS:
            request_id=f"campaign-review:{site}:{tool}:{source['campaign_review_key']}"
        elif tool in LOCAL_PACKET_MODULES:
            request_id=source["role_packet_key"]
        elif tool == workflow_live_paid_slot.TOOL:
            request_id=f"paid-slot:{site}:{source['paid_slot_key']}"
        elif tool == workflow_live_preflight.TOOL:
            request_id=f"preflight:{site}:{source['preflight_key']}"
        elif tool == workflow_competitor_supplier.TOOL:
            request_id=f"supplier-research:{site}:{source['supplier_key']}:{source['supplier_revision']}"
        elif tool == workflow_competitor_dossier.TOOL:
            request_id=f"dossier:{site}:{source['dossier_id']}:{source['dossier_revision']}"
        elif tool == workflow_competitor_bulletin.TOOL:
            request_id = f"bulletin:{site}:{source['bulletin_cadence']}:{source['bulletin_key']}:{source['bulletin_revision']}"
        elif tool == workflow_lead_monthly.TOOL:
            request_id = f"monthly-review:{site}:{source['monthly_key']}"
        elif tool in workflow_lead_weekly.TOOLS:
            request_id = source["weekly_request"]
        elif tool in workflow_competitor_handoff.TOOLS:
            request_id = source["c10_request"]
        elif tool in workflow_portfolio_handoff.TOOLS:
            request_id = source["portfolio_request"]
        elif tool in workflow_live_rehearsal.TOOLS:
            request_id = (f"live-rehearsal:{site}:{tool}:{source['rehearsal_chain']}:"
                          f"{source['version']}")
        elif tool in workflow_live_offline.TOOLS:
            request_id = (f"live-offline:{site}:{tool}:{source['chain_key']}:"
                          f"{source.get('language') or 'all'}:{source['version']}")
        try:
            encoded = json.dumps([kind, site, title, source, role_input],
                                 ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError, RecursionError):
            raise APIError(400, "invalid_local_input", "输入不是有效的本机任务资料。") from None
        if len(encoded) > 1024 * 1024:
            raise APIError(400, "large_role_input", "本机工具的结构化输入不得超过 1 MiB。")
        signature = sha256(encoded).hexdigest()
        with self.gate:
            if ident := self.requests.get(request_id):
                existing = self.task(ident)
                if existing["request_hash"] != signature:
                    raise APIError(409, "duplicate_conflict", "同一提交标识已用于另一份资料，请重新发起。")
                if tool == workflow_live_rehearsal.CONTROL and payload.get("rehearsal_retry") is True:
                    if existing["status"] == "interrupted":
                        return self.retry_rehearsal(existing, role_input)
                    if existing["status"] == "failed":
                        raise APIError(409,"rehearsal_retry_failed","业务失败的版本不会作为中断重试；请保留原证据。")
                if requires_copy and existing["status"] == visual_copy.WAITING:
                    return self.schedule_visual_copy(existing, role_input)
                return self.serialize(existing)
            if tool == workflow_live_rehearsal.CONTROL and payload.get("rehearsal_retry") is True:
                raise APIError(409,"rehearsal_retry_without_origin","没有同编号的中断任务，重试未创建新任务。")
            ident = uuid4().hex
            folder = self.tasks_dir / ident
            folder.mkdir()
            if data is not None:
                with (folder / source["file"]).open("xb") as stream:
                    stream.write(data)
            if media_files:
                (folder / "assets").mkdir()
                for item, contents in media_files:
                    with (folder / item["file"]).open("xb") as stream:
                        stream.write(contents)
            if role_input is not None:
                atomic_write(folder / "role-input.json", role_input)
            task = {"id": ident, "kind": kind, "site": site, "title": title, "source": source,
                    "status": "queued", "message": "已加入本机队列。", "created_at": utc(), "updated_at": utc(),
                    "request_id": request_id, "request_hash": signature, "local_files": {}}
            if tool == workflow_live_rehearsal.CONTROL:
                task["attempt"]=1
            if requires_copy:
                # The existing replacement adapter is confined to the PH test profile.
                use_test_copy = site == "PH" and self.test_copy_executor is not None
                task.update(status=visual_copy.WAITING, message="待提炼。",
                            copy_gate={"binding": None, "layout_allowed": False, "missing_fields": requires_copy,
                                       "execution_kind": visual_copy.TEST if use_test_copy else None})
            self.save_task(task)
            self.store[ident] = task
            self.requests[request_id] = ident
            if requires_copy:
                return self.schedule_visual_copy(task, role_input)
            self.executor.submit(self.run_local, task)
            return self.serialize(task)

    def retry_rehearsal(self, task, value):
        # Called with self.gate, after exact business fingerprint and current upstream checks.
        folder = self.tasks_dir / task["id"]
        try:
            persisted = read_json(bind_data_path(folder,folder/"role-input.json"))
        except (OSError,ValueError,APIError):
            raise APIError(409,"rehearsal_retry_evidence_unverified","保存的重试输入无法核验。") from None
        if persisted != value or task["local_files"]:
            raise APIError(409,"rehearsal_retry_evidence_unverified","已保存输入或完成产物与重试条件不符。")
        attempt=task.get("attempt",1)
        while (folder/f"attempt-{attempt+1}").exists():
            attempt+=1
        history=[*task.get("interrupted_attempts",[]),
                 {"attempt":task.get("attempt"),"interrupted_at":task["updated_at"],"input_sha256":task["request_hash"]}]
        self.status(task,"queued","已用原任务号排队重试；保留中断尝试。",attempt=attempt+1,interrupted_attempts=history)
        self.executor.submit(self.run_local,task)
        return self.serialize(task)

    def bind_listening_takeout(self, parameters):
        try:
            ident = workflow_live_listening.receive_input(parameters)["source_task_id"]
            with self.gate:
                prior = self.task(ident)
                data = self.local_artifact_bytes(ident, "json")
                return workflow_live_listening.receive(prior, data, parameters)
        except (ValueError, KeyError, APIError) as error:
            raise APIError(409, "listening_takeout_mismatch",
                           "原听审任务、版本或文件哈希不符，领取未发生。") from error

    def run_extra_role(self, project, site, tool_id, parameters, task_id):
        return role_tools_extra.run_role_tool(project, site, tool_id, parameters, task_id,
                                              task_dir=self.tasks_dir / task_id)

    def run_local(self, task):
        folder = self.tasks_dir / task["id"]
        source = task["source"]
        agent_execution = None
        def progress(message):
            self.status(task, "drafting", message)

        try:
            if task["kind"] == "transcription":
                progress("正在核对本机媒体。")
                media = bind_data_path(folder, folder / source["file"])
                if sha256(media.read_bytes()).hexdigest() != source["sha256"]:
                    raise MediaError("本机媒体已变化，请重新提交。")
                result, paths = run_transcription(self.project, folder, media, source["language"], progress)
            else:
                tool_id = source["tool_id"]
                parameters = read_json(bind_data_path(folder, folder / "role-input.json"))
                fingerprint = json.dumps([task["kind"], task["site"], task["title"], source, parameters],
                                         ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
                if sha256(fingerprint).hexdigest() != task["request_hash"]:
                    raise RoleToolError("保存的岗位输入已变化，请重新提交。")
                workflow = self.workflows.get(tool_id)
                render_artwork = ARTWORK_RENDERERS.get(tool_id)
                if workflow or render_artwork or tool_id in workflow_shop_image_review.TOOLS.values():
                    progress("正在核对本机工作流的输入。")
                    assets = {}
                    for item in source.get("assets", []):
                        path = bind_data_path(folder, folder / item["file"])
                        if sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                            raise MediaError("本机素材与提交时不同，请重新选择。")
                        assets[item["name"]] = path
                    if tool_id in workflow_shop_image_review.TOOLS.values():
                        try:
                            with self.gate:
                                linked, data = self.bind_shop_image_review(task["site"], parameters)
                        except APIError as error:
                            raise workflow_shop_image_review.WorkflowError(str(error)) from None
                        result, paths = workflow_shop_image_review.save_review(self.project, task["site"], folder, parameters, linked, data)
                    elif render_artwork:
                        receipt = None
                        if gate := task.get("copy_gate"):
                            try:
                                if self.test_copy_executor is None or gate["binding"] is None:
                                    raise ValueError("No trusted copy executor is configured.")
                                binding = gate["binding"]
                                if binding != visual_copy.bind(self.project, task, parameters, binding["route"]):
                                    raise ValueError("Copy binding is stale.")
                                if gate["execution_kind"] != visual_copy.TEST:
                                    raise ValueError("A production round cannot run through the test executor.")
                                saved_receipt = folder / "copy-receipt.json"
                                if saved_receipt.is_file():
                                    receipt = read_json(bind_data_path(folder, saved_receipt))
                                else:
                                    receipt = self.test_copy_executor(json.loads(json.dumps(binding)),
                                                                      json.loads(json.dumps(parameters)), dict(assets))
                                parameters = visual_copy.fill(parameters, binding, receipt)
                                if not saved_receipt.is_file():
                                    atomic_write(saved_receipt, receipt)
                            except (TypeError, ValueError, OSError):
                                self.status(task, visual_copy.WAITING, "待提炼。未收到与本任务匹配的隔离测试回执。")
                                return
                            gate = {**gate, "layout_allowed": True,
                                    "receipt_sha256": visual_copy.layout_plan(receipt, None)["sha256"]}
                            self.status(task, "drafting", "隔离测试回执已核验，正在验证设计接收。", copy_gate=gate)
                        result, paths = render_artwork(self.project, folder, task, parameters, assets,
                                                       self.engine, self.node, progress, copy_execution=receipt)
                    elif tool_id == workflow_lead.NAMED_TOOL:
                        upstream = source.get("upstream")
                        if upstream is not None:
                            try:
                                current = self.named_upstream(task["site"], upstream["job_id"],
                                                               require_ready="named_target" in source)
                            except APIError as error:
                                raise workflow_lead.WorkflowError(str(error)) from None
                            if current != upstream:
                                raise workflow_lead.WorkflowError("上游完成回执已变化，未派发下游。")
                        if "named_target" in source:
                            try:
                                with self.gate:
                                    target=source["named_target"]
                                    pins,selected=self.bind_named_target(task["site"],target,parameters["input"])
                                    if pins!=source["named_target_pins"]:
                                        raise workflow_lead.WorkflowError("队列等待期间原证据发生变化。")
                                    def launch(worker,notify):
                                        try:
                                            return selected(worker,notify)
                                        except WORKFLOW_ERRORS as error:
                                            raise workflow_lead.WorkflowError(str(error)) from None
                                    result,paths=workflow(self.project,task["site"],tool_id,parameters,folder,
                                                          assets,progress,upstream=upstream,route=(pins,launch))
                            except APIError as error:
                                raise workflow_lead.WorkflowError(str(error)) from None
                        else:
                            result, paths = workflow(self.project, task["site"], tool_id, parameters, folder,
                                                     assets, progress, upstream=upstream)
                    elif tool_id in workflow_visual_surfaces.TOOLS:
                        try:
                            with self.gate:
                                bound, pins = self.bind_visual_surface(task["site"], tool_id, parameters)
                        except APIError as error:
                            raise workflow_visual_surfaces.WorkflowError(str(error)) from None
                        if pins != source["surface_upstream"]:
                            raise workflow_visual_surfaces.WorkflowError("排队期间来源已变化。")
                        result, paths = workflow(task["site"], tool_id, parameters, folder,
                                                 bound, self.engine, self.node, progress)
                    elif tool_id in workflow_media_library.TOOLS:
                        try:
                            with self.gate:
                                bound = self.bind_media_library(task["site"], tool_id, parameters, check_latest=False)
                        except APIError as error:
                            raise workflow_media_library.WorkflowError(str(error)) from None
                        if bound[1] != source["media_upstream"]:
                            raise workflow_media_library.WorkflowError("素材来源在排队期间改变，请重新核对。")
                        result, paths = workflow(task["site"], tool_id, parameters,
                                                                     folder, bound, self.engine,self.node,progress)
                    elif tool_id in workflow_competitor_watch.TOOLS:
                        try:
                            with self.gate:
                                linked = self.bind_watch(task["site"], tool_id, parameters)
                                if linked[1] != source["watch_pins"]:
                                    raise workflow_competitor_watch.WorkflowError("排队期间监控范围或观测来源有变化。")
                                result, paths = workflow(self.project, task["site"], tool_id, parameters, folder,
                                                         assets, None, linked=linked)
                        except APIError as error:
                            raise workflow_competitor_watch.WorkflowError(str(error)) from None
                    elif tool_id in workflow_campaign_reviews.TOOLS:
                        try:
                            with self.gate:
                                linked=self.bind_campaign_review(task["site"],tool_id,parameters)
                                if linked[0]!=source["campaign_review_pins"]:
                                    raise workflow_campaign_reviews.WorkflowError("等待处理期间活动证据已变化。")
                                result,paths=workflow(self.project,task["site"],tool_id,parameters,folder,assets,None,linked=linked)
                        except APIError as error:
                            raise workflow_campaign_reviews.WorkflowError(str(error)) from None
                    elif tool_id in LOCAL_PACKET_MODULES:
                        m=LOCAL_PACKET_MODULES[tool_id]
                        try:
                            with self.gate:
                                _,linked=self.bind_role_packet(task["site"],tool_id,parameters,
                                    agent_session=source.get("agent_session_id"))
                                if linked[0]!=source["role_packet_pins"]:
                                    raise m.WorkflowError("排队期间工作回执来源变化。")
                                scoped_role = source.get("agent_role_protocol") == mall_agent_worker.PROTOCOL
                                if scoped_role:
                                    if (tool_id != workflow_poster_delivery.SHOP_RECEIVE or assets
                                            or source.get("agent_session_id") != self.agent_active
                                            or not (self.agent_fixture or self.foreground_active == self.agent_active
                                                    and self.foreground_active is not None)):
                                        raise m.WorkflowError("商城 Agent 的任务范围不符。")
                                    parent = self.task(parameters["poster_task_id"])
                                    if parent.get("agent_session", {}).get("state") != "receiving":
                                        raise m.WorkflowError("客户会话未等待商城的独立返回。")
                                    snapshot = self.verified_poster_delivery_source(task["site"], parameters["poster_task_id"],
                                        parameters["version"], reviewed=True, agent_session=source["agent_session_id"])
                                else:
                                    result,paths=workflow(self.project,task["site"],tool_id,parameters,folder,assets,None,linked=linked)
                            if scoped_role:
                                combined = source.get("combined_visual")
                                attachments = None
                                if combined:
                                    pin, blobs = foreground_mall_combo.bound(self,parent,source["agent_session_id"],
                                        combined["source_tasks"],combined["poster_release_sha256"])
                                    if pin != combined:
                                        raise m.WorkflowError("商城组合收件源包已变化。")
                                    attachments = pin, blobs
                                result, paths, agent_execution = mall_agent_worker.run(self.project, task, parameters,
                                                                                      folder, snapshot, linked, attachments)
                                with self.gate:
                                    _, still_bound = self.bind_role_packet(task["site"],tool_id,parameters,
                                        agent_session=source["agent_session_id"])
                                    if still_bound[0] != linked[0] or self.agent_active != source["agent_session_id"]:
                                        raise m.WorkflowError("商城 Agent 执行期间，父任务或会话已变化。")
                                    if combined:
                                        recheck, _ = foreground_mall_combo.bound(self,parent,source["agent_session_id"],
                                            combined["source_tasks"],combined["poster_release_sha256"])
                                        if recheck != combined:
                                            raise m.WorkflowError("已审三组源文件在收件前发生变化。")
                                        combined_doc = foreground_mall_combo.receipt(task,result["bundle_sha256"],
                                                                                     agent_execution,combined)
                                        atomic_write(folder/"combined-visual-receipt.json",combined_doc)
                        except APIError as error:
                            raise m.WorkflowError(str(error)) from None
                    elif tool_id == workflow_live_listening.RECEIVE:
                        try:
                            linked = self.bind_listening_takeout(parameters)
                        except APIError as error:
                            raise workflow_live_listening.WorkflowError(str(error)) from None
                        result, paths = workflow(self.project, task["site"], tool_id, parameters,
                                                 folder, assets, progress, linked=linked)
                    elif tool_id == workflow_live_paid_slot.TOOL:
                        try:
                            with self.gate:
                                linked=self.bind_paid_slot(task["site"],parameters)
                                if linked[0]!=source["paid_slot_pins"]:
                                    raise workflow_live_paid_slot.WorkflowError("排队期间同场投流来源变化。")
                                result,paths=workflow(self.project,task["site"],tool_id,parameters,folder,assets,None,linked=linked)
                        except APIError as error:
                            raise workflow_live_paid_slot.WorkflowError(str(error)) from None
                    elif tool_id == workflow_live_preflight.TOOL:
                        try:
                            with self.gate:
                                linked=self.bind_preflight(task["site"],parameters)
                                if linked[1]!=source["preflight_pins"]:
                                    raise workflow_live_preflight.WorkflowError("排队期间开播前证据有变化。")
                                result,paths=workflow(self.project,task["site"],tool_id,parameters,folder,assets,None,linked=linked)
                        except APIError as error:
                            raise workflow_live_preflight.WorkflowError(str(error)) from None
                    elif tool_id == workflow_competitor_supplier.TOOL:
                        try:
                            with self.gate:
                                linked=self.bind_supplier_research(task["site"],parameters)
                                if linked[1]!=source["supplier_pins"]:
                                    raise workflow_competitor_supplier.WorkflowError("排队期间货源研究证据有变化。")
                                result,paths=workflow(self.project,task["site"],tool_id,parameters,folder,assets,None,linked=linked)
                        except APIError as error:
                            raise workflow_competitor_supplier.WorkflowError(str(error)) from None
                    elif tool_id == workflow_competitor_dossier.TOOL:
                        try:
                            with self.gate:
                                linked=self.bind_dossier(task["site"],parameters)
                                if linked[1]!=source["dossier_pins"]:
                                    raise workflow_competitor_dossier.WorkflowError("排队期间两店两品的来源有变化。")
                                result,paths=workflow(self.project,task["site"],tool_id,parameters,folder,assets,None,linked=linked)
                        except APIError as error:
                            raise workflow_competitor_dossier.WorkflowError(str(error)) from None
                    elif tool_id == workflow_competitor_bulletin.TOOL:
                        try:
                            with self.gate:
                                linked = self.bind_bulletin(task["site"], parameters)
                                if linked[2] != source["bulletin_pins"]:
                                    raise workflow_competitor_bulletin.WorkflowError("排队期间竞品情报证据变化。")
                                result, paths = workflow(self.project, task["site"], tool_id, parameters, folder,
                                                         assets, None, linked=linked)
                        except APIError as error:
                            raise workflow_competitor_bulletin.WorkflowError(str(error)) from None
                    elif tool_id == workflow_lead_monthly.TOOL:
                        try:
                            with self.gate:
                                linked = self.bind_monthly(task["site"], parameters)
                                if linked[2] != source["monthly_pins"]:
                                    raise workflow_lead_monthly.WorkflowError("排队期间月报周来源变化。")
                                result, paths = workflow(self.project, task["site"], tool_id, parameters, folder,
                                                         assets, None, linked=linked)
                        except APIError as error:
                            raise workflow_lead_monthly.WorkflowError(str(error)) from None
                    elif tool_id in workflow_lead_weekly.TOOLS:
                        try:
                            with self.gate:
                                _, linked = self.bind_weekly(task["site"], tool_id, parameters)
                                if linked[2] != source["weekly_pins"]:
                                    raise workflow_lead_weekly.WorkflowError("排队期间周报证据变化。")
                                result, paths = workflow(self.project, task["site"], tool_id, parameters, folder,
                                                         assets, None, linked=linked)
                        except APIError as error:
                            raise workflow_lead_weekly.WorkflowError(str(error)) from None
                    elif tool_id in workflow_competitor_handoff.TOOLS:
                        try:
                            with self.gate:
                                _,linked=self.bind_c10(task["site"],tool_id,parameters)
                                if linked[1]!=source["upstream"]:
                                    raise workflow_competitor_handoff.WorkflowError("竞品上游凭据变化。")
                                result,paths=workflow(self.project,task["site"],tool_id,parameters,folder,assets,None,linked=linked)
                        except APIError as error:
                            raise workflow_competitor_handoff.WorkflowError(str(error)) from None
                    elif tool_id in workflow_portfolio_handoff.TOOLS:
                        try:
                            with self.gate:
                                _,linked=self.bind_portfolio(task["site"],tool_id,parameters)
                                if linked[1]!=source["upstream"]:
                                    raise workflow_portfolio_handoff.WorkflowError("货盘上游凭据变化，未签收。")
                                result,paths=workflow(self.project,task["site"],tool_id,parameters,folder,assets,None,linked=linked)
                        except APIError as error:
                            raise workflow_portfolio_handoff.WorkflowError(str(error)) from None
                    elif tool_id in workflow_live_rehearsal.TOOLS:
                        # Keep the newest-evidence check and creation of this immutable receipt in one local gate.
                        try:
                            with self.gate:
                                _, linked = self.bind_live_rehearsal(task["site"],tool_id,parameters)
                                if linked[1] != source["upstream"]:
                                    raise workflow_live_rehearsal.WorkflowError("上游凭据已变化，未生成演练结果。")
                                result, paths = workflow(self.project,task["site"],tool_id,parameters,folder,assets,None,
                                                         linked=linked,attempt=task.get("attempt"))
                        except APIError as error:
                            raise workflow_live_rehearsal.WorkflowError(str(error)) from None
                    elif tool_id in workflow_live_offline.TOOLS:
                        try:
                            with self.gate:
                                linked = self.bind_live_offline(task["site"], tool_id, parameters)
                        except APIError as error:
                            raise workflow_live_offline.WorkflowError(str(error)) from None
                        if linked[1] != source["upstream"]:
                            raise workflow_live_offline.WorkflowError("本机上游凭据已变化，应重新核对。")
                        result, paths = workflow(self.project, task["site"], tool_id, parameters,
                                                 folder, assets, progress, linked=linked)
                    elif tool_id in CAMPAIGN_INTAKES:
                        module = CAMPAIGN_OFFERS[task["site"]]
                        try:
                            parent = self.campaign_review_source(task["site"], parameters["upstream_task_id"])
                        except APIError as error:
                            raise module.WorkflowError(str(error)) from None
                        if parent[0] != source["upstream"]:
                            raise module.WorkflowError("上游活动凭据变化，商城未签收。")
                        result, paths = workflow(self.project, task["site"], tool_id, parameters, folder,
                                                 assets, progress, upstream=parent)
                    else:
                        result, paths = workflow(self.project, task["site"], tool_id, parameters, folder,
                                                 assets, progress)
                else:
                    progress("正在执行已有岗位的本机检查。")
                    _, runner = self.role_tools[tool_id]
                    result = runner(self.project, task["site"], tool_id, parameters, task["id"])
                    if "visual_tasks" in source:
                        try:
                            current = self.shop_visual_source(task["site"], parameters, source["visual_tasks"])
                        except APIError as error:
                            raise RoleToolError(str(error)) from None
                        if current != source["visual_delivery"]:
                            raise RoleToolError("上游视觉资料已变化，商城未签收。")
                        result["visual_delivery"] = current
                    target = folder / "role-result.json"
                    atomic_write(target, result)
                    paths = {"json": target}
            verified = {}
            for kind, path in paths.items():
                local = bind_data_path(folder, path)
                if kind not in LOCAL_FILES or not local.is_file() or not local.stat().st_size:
                    raise ValueError("Invalid local result")
                verified[kind] = local.relative_to(folder).as_posix()
            details = {"agent_execution":agent_execution} if agent_execution is not None else {}
            self.status(task, "ready", "结果已保存在本机。", result=result, local_files=verified, **details)
        except WORKFLOW_ERRORS as error:
            self.status(task, "failed", str(error), local_files={})
        except Exception as error:
            atomic_write(folder / "failure.json", {"type": type(error).__name__, "utc": utc()})
            self.status(task, "failed", "本机任务未完成。请核对输入、依赖或工作区后再试。", local_files={})

    def combined_visual_bytes(self, ident, kind):
        with self.gate:
            task = self.task(ident)
            source = task.get("source",{})
            stored = source.get("combined_visual")
            if task["status"]!="ready" or not stored or kind not in foreground_mall_combo.FILES:
                raise APIError(404,"visual_combination_missing","未找到已收件的同版视觉组合。")
            try:
                bind = stored["binding"]
                parent = self.task(bind["task_id"])
                if parent["agent_session"]["session_id"] != bind["session_id"]:
                    raise ValueError()
                proof = self.local_artifact_bytes(ident,"receipt")
                poster = self.local_artifact_bytes(ident,"bundle")
                if json.loads(proof)["source_release_sha256"]!=stored["poster_release_sha256"]:
                    raise ValueError()
                pin, archives = foreground_mall_combo.bound(
                    self,parent,bind["session_id"],stored["source_tasks"],stored["poster_release_sha256"])
                if pin != stored:
                    raise ValueError()
                folder = self.tasks_dir/ident
                p = bind_data_path(folder,folder/"combined-visual-receipt.json")
                receipt = p.read_bytes()
                if json.loads(receipt)!=foreground_mall_combo.receipt(task,sha256(poster).hexdigest(),task["agent_execution"],pin):
                    raise ValueError()
            except (OSError,KeyError,TypeError,ValueError,APIError) as error:
                raise APIError(409,"visual_combination_unverified","海报、主图或商详任一同版源包无法冷取核验。") from error
            if kind=="mall_combined_receipt":
                return receipt
            return archives["main" if kind=="mall_main_bundle" else "detail"]

    def local_artifact_bytes(self, ident, kind):
        with self.gate:
            task = self.task(ident)
            if (task.get("copy_gate", {}).get("execution_kind") == visual_copy.TEST
                    and self.test_copy_executor is None):
                raise APIError(409, "isolated_copy_test_only", "该产物只供隔离文案测试使用。")
            if kind in MALL_VISUAL_FILES:
                return self.mall_visual_bytes(task, kind)
            if kind in foreground_mall_combo.FILES:
                return self.combined_visual_bytes(ident,kind)
            path = task.get("local_files", {}).get(kind) if task.get("status") == "ready" else None
            if not path:
                raise APIError(404, "missing_file", "当前任务没有该交付文件。")
            tool_id = task.get("source", {}).get("tool_id")
            if tool_id == workflow_director.VERSIONS_TOOL:
                folder = self.tasks_dir / ident
                try:
                    if path != workflow_director.VERSIONS_FILES[kind]:
                        raise ValueError("Incorrect director artifact path")
                    artifact = bind_data_path(folder, folder / path).read_bytes()
                    if kind == "director_bundle":
                        if sha256(artifact).hexdigest() != task["result"]["bundle_sha256"]:
                            raise ValueError("Unverified director package")
                    elif json.loads(artifact) != task["result"]:
                        raise ValueError("Unverified director result")
                except (OSError, TypeError, ValueError, KeyError):
                    raise APIError(409, "director_package_mismatch", "多版本交付文件缺失或与留档回执不符。") from None
                return artifact
            if tool_id in ARTWORK_RENDERERS and kind in ("preview", "bundle", "detail_bundle"):
                main = tool_id == main_images.TOOL_ID
                if kind == "preview":
                    expected_path = "main-images/preview.png" if main else "long-detail/preview.png"
                    expected_hash = (task["result"].get("preview_sha256") if main else
                                     task["result"].get("file_sha256", {}).get("preview.png"))
                elif kind == ("bundle" if main else "detail_bundle"):
                    expected_path = "main-images/main-images.zip" if main else "long-detail/long-detail.zip"
                    expected_hash = task["result"].get("bundle_sha256")
                else:
                    raise APIError(404, "missing_file", "该视觉任务没有这个交付文件。")
                folder = self.tasks_dir / ident
                try:
                    if path != expected_path:
                        raise ValueError("Incorrect artwork path")
                    artifact = bind_data_path(folder, folder / expected_path).read_bytes()
                    if not artifact or sha256(artifact).hexdigest() != expected_hash:
                        raise ValueError("Unverified artwork bytes")
                except (OSError, ValueError) as error:
                    raise APIError(409, "visual_media_evidence_mismatch", "视觉交付文件缺失或与留档回执不符。") from error
                return artifact
            if tool_id in workflow_shop_image_review.TOOLS.values():
                return self.shop_image_artifacts(task)[kind]
            if tool_id == workflow_competitor_price_band.TOOL:
                folder=self.tasks_dir/ident
                try:
                    files={k:bind_data_path(folder,folder/name).read_bytes() for k,name in workflow_competitor_price_band.FILES.items()}
                    workflow_competitor_price_band.verify_saved(task,files)
                    return files[kind]
                except (OSError,TypeError,KeyError,ValueError) as error:
                    raise APIError(409,"price_band_evidence_mismatch","价格观察、产物及回执无法核验。") from error
            if tool_id == workflow_campaign_sensitivity.TOOL:
                folder=self.tasks_dir/ident
                try:
                    files={k:bind_data_path(folder,folder/name).read_bytes() for k,name in workflow_campaign_sensitivity.FILES.items()}
                    workflow_campaign_sensitivity.verify_saved(task,files)
                    return files[kind]
                except (OSError,TypeError,KeyError,ValueError) as error:
                    raise APIError(409,"campaign_forecast_evidence_mismatch","活动预测文件或回执无法核验。") from error
            if tool_id == workflow_creator_cohorts.TOOL:
                folder = self.tasks_dir / ident
                try:
                    files = {k: bind_data_path(folder, folder / name).read_bytes()
                             for k, name in workflow_creator_cohorts.FILES.items()}
                    workflow_creator_cohorts.verify_saved(task, files)
                    return files[kind]
                except (OSError, ValueError, TypeError, KeyError) as error:
                    raise APIError(409, "creator_cohort_evidence_mismatch", "达人分析或回执无法核验。") from error
            if tool_id in workflow_competitor_watch.TOOLS:
                folder = self.tasks_dir / ident
                try:
                    files = {k: bind_data_path(folder, folder / name).read_bytes()
                             for k, name in workflow_competitor_watch.FILES.items()}
                    workflow_competitor_watch.verify_saved(task, files)
                    return files[kind]
                except (OSError, ValueError, TypeError, KeyError) as error:
                    raise APIError(409, "monitor_watch_evidence_mismatch", "监控结果或本机来源无法核验。") from error
            if tool_id in workflow_campaign_reviews.TOOLS:
                folder=self.tasks_dir/ident
                try:
                    files={k:bind_data_path(folder,folder/name).read_bytes()
                           for k,name in workflow_campaign_reviews.FILES.items()}
                    workflow_campaign_reviews.verify_saved(task,files)
                    return files[kind]
                except (OSError,ValueError,TypeError,KeyError) as error:
                    raise APIError(409,"campaign_review_evidence_mismatch","活动结果或收件回执无法核验。") from error
            if tool_id in LOCAL_PACKET_MODULES:
                folder=self.tasks_dir/ident
                m=LOCAL_PACKET_MODULES[tool_id]
                try:
                    files={k:bind_data_path(folder,folder/name).read_bytes() for k,name in m.FILES.items()}
                    m.verify_saved(task,files)
                    if task["source"].get("agent_role_protocol") == mall_agent_worker.PROTOCOL:
                        mall_agent_worker.verify_cold(folder, task, files)
                    return files[kind]
                except (OSError,ValueError,TypeError,KeyError) as error:
                    raise APIError(409,"role_packet_evidence_mismatch","本机工作回执无法核验。") from error
            if tool_id in workflow_live_listening.TOOLS:
                folder = self.tasks_dir / ident
                try:
                    files = {k: bind_data_path(folder, folder / name).read_bytes()
                             for k, name in workflow_live_listening.FILES.items()}
                    workflow_live_listening.verify_saved(task, files)
                    return files[kind]
                except (OSError, ValueError, TypeError, KeyError) as error:
                    raise APIError(409, "listening_artifact_mismatch", "听审文件无法通过同版核验。") from error
            if tool_id == workflow_live_recording_review.TOOL:
                folder = self.tasks_dir / ident
                try:
                    if kind != "json" or path != workflow_live_recording_review.FILE:
                        raise ValueError("Invalid local recording artifact")
                    data = bind_data_path(folder, folder / path).read_bytes()
                    workflow_live_recording_review.verify_saved(task, data)
                    return data
                except (OSError, ValueError, KeyError, TypeError) as error:
                    raise APIError(409, "recording_review_evidence_mismatch", "本机录屏审核文件无法核验。") from error
            if tool_id == workflow_live_paid_slot.TOOL:
                folder=self.tasks_dir/ident
                try:
                    files={k:bind_data_path(folder,folder/name).read_bytes() for k,name in workflow_live_paid_slot.artifact_names("video" in task["local_files"]).items()}
                    workflow_live_paid_slot.verify_saved(task,files)
                    return files[kind]
                except (OSError,ValueError,TypeError,KeyError) as error:
                    raise APIError(409,"live_paid_slot_evidence_mismatch","投流证据回执无法核验。") from error
            if tool_id == workflow_live_preflight.TOOL:
                folder=self.tasks_dir/ident
                try:
                    files={k:bind_data_path(folder,folder/name).read_bytes() for k,name in workflow_live_preflight.FILES.items()}
                    workflow_live_preflight.verify_saved(task,files)
                    return files[kind]
                except (OSError,ValueError,TypeError,KeyError) as error:
                    raise APIError(409,"live_preflight_evidence_mismatch","开播前核查回执无法核验。") from error
            if tool_id == workflow_competitor_supplier.TOOL:
                folder=self.tasks_dir/ident
                try:
                    files={k:bind_data_path(folder,folder/name).read_bytes()
                           for k,name in workflow_competitor_supplier.FILES.items()}
                    workflow_competitor_supplier.verify_saved(task,files)
                    return files[kind]
                except (OSError,ValueError,TypeError,KeyError) as error:
                    raise APIError(409,"supplier_research_evidence_mismatch","候选和匿名货源结果无法核验。") from error
            if tool_id == workflow_competitor_dossier.TOOL:
                folder=self.tasks_dir/ident
                try:
                    files={k:bind_data_path(folder,folder/name).read_bytes() for k,name in workflow_competitor_dossier.FILES.items()}
                    workflow_competitor_dossier.verify_saved(task,files)
                    return files[kind]
                except (OSError,ValueError,TypeError,KeyError) as error:
                    raise APIError(409,"dossier_evidence_mismatch","两店两品档案无法核验。") from error
            if tool_id == workflow_competitor_bulletin.TOOL:
                folder = self.tasks_dir / ident
                try:
                    files = {k: bind_data_path(folder, folder / name).read_bytes()
                             for k, name in workflow_competitor_bulletin.FILES.items()}
                    workflow_competitor_bulletin.verify_saved(task, files)
                    return files[kind]
                except (OSError, ValueError, TypeError, KeyError) as error:
                    raise APIError(409, "bulletin_evidence_mismatch", "竞品情报结果或回执无法核验。") from error
            if tool_id == workflow_lead_monthly.TOOL:
                folder = self.tasks_dir / ident
                try:
                    files = {k: bind_data_path(folder, folder / name).read_bytes()
                             for k, name in workflow_lead_monthly.FILES.items()}
                    workflow_lead_monthly.verify_saved(task, files)
                    return files[kind]
                except (OSError, ValueError, TypeError, KeyError) as error:
                    raise APIError(409, "monthly_evidence_mismatch", "月报结果或回执无法核验。") from error
            if tool_id in workflow_lead_weekly.TOOLS:
                return self.weekly_artifacts(task)[kind]
            if tool_id in workflow_competitor_handoff.TOOLS:
                return self.c10_artifacts(task)[kind]
            if tool_id in workflow_portfolio_handoff.TOOLS:
                return self.portfolio_artifacts(task)[kind]
            if tool_id in workflow_live_rehearsal.TOOLS:
                return self.live_rehearsal_artifacts(task)[kind]
            if tool_id in workflow_live_offline.TOOLS:
                return self.live_offline_artifacts(task)[kind]
            if tool_id in workflow_media_library.TOOLS:
                return self.media_library_artifacts(task)[kind]
            if tool_id in workflow_visual_surfaces.TOOLS:
                return self.visual_surface_files(task)[kind]
            if tool_id == workflow_lead.NAMED_TOOL:
                return self.named_completion_artifacts(task)[kind]
            if tool_id in CAMPAIGN_FILES:
                return self.campaign_offer_artifacts(task)[kind]
            if tool_id in workflow_finance_offline.TOOLS:
                folder = self.tasks_dir / ident
                try:
                    files = {k:bind_data_path(folder, folder / name).read_bytes()
                             for k, name in workflow_finance_offline.OUTPUTS.items()}
                    workflow_finance_offline.verify_artifacts(task, files)
                    return files[kind]
                except (OSError, TypeError, KeyError, ValueError) as error:
                    raise APIError(409, "finance_evidence_mismatch", "合成财务回执或取件文件不可核验。") from error
            if tool_id in workflow_offline_schedule.TOOLS:
                folder = self.tasks_dir / ident
                try:
                    files = {k:bind_data_path(folder, folder / name).read_bytes()
                             for k, name in workflow_offline_schedule.OUTPUTS.items()}
                    workflow_offline_schedule.verify_artifacts(task, files)
                    return files[kind]
                except (OSError, TypeError, KeyError, ValueError) as error:
                    raise APIError(409, "offline_schedule_evidence_mismatch", "排期回执或取件文件不可核验。") from error
            if tool_id == workflow_ph_competitor_detail.TOOL:
                folder = self.tasks_dir / ident
                try:
                    files = {k:bind_data_path(folder, folder / name).read_bytes()
                             for k, name in workflow_ph_competitor_detail.OUTPUTS.items()}
                    workflow_ph_competitor_detail.verify_artifacts(task, files)
                    return files[kind]
                except (OSError, TypeError, KeyError, ValueError) as error:
                    raise APIError(409, "ph_review_evidence_mismatch", "菲律宾标注或 OCR 取件证据不可核验。") from error
            folder = self.tasks_dir / ident
            return bind_data_path(folder, folder / path).read_bytes()

    def make_poster(self, task, settings):
        task_dir = self.tasks_dir / task["id"]
        try:
            self.status(task, "drafting", "正在按对应站点的海报流程准备文案。")
            def render_accepted_copy(copy, run_details):
                self.status(task, "rendering", "文案已验证，正在导出 PNG 和分层 PSD。",
                            run={**task["run"], **run_details})
                render_task = {**task, "task_id": task["id"], "sku": task["source"].get("sku")}
                return render_initial(task_dir, render_task, copy, self.engine, self.node)

            if task["run"]["mode"] == "user_copy":
                result, run_details = run_user_poster_workflow(
                    project=self.project, task_dir=task_dir, task=task, accept=render_accepted_copy,
                )
                message = "已原样使用确认文案并保存 PNG 和分层 PSD。当前为本地模板预览，正式设计与商品一致性待核。"
            else:
                if self.model_workspace is not None and task["source"].get("kind") == "synthetic_fixture":
                    config, key = validate_config({"mode": "demo"}), None
                else:
                    config, key = self.connection(settings)
                result, run_details = run_poster_workflow(
                    project=self.project, profile=self.root, task_dir=task_dir, task=task,
                    config=config, key=key, draft=draft_copy, accept=render_accepted_copy,
                )
                message = "海报已保存在本机。"
            self.status(task, "ready", message, versions=[result], run={**task["run"], **run_details})
        except (ConnectorError, PosterError, CopyConstraintError, WorkflowError) as error:
            self.status(task, "failed", str(error))
        except Exception:
            self.status(task, "failed", "本机任务处理失败。请检查项目路径、引擎和工作区后重新提交。")

    def update_foreground_detail(self,ident,payload):
        if set(payload)!={"detail"}:
            raise APIError(400,"wrong_detail_payload","仅提交四项明确商详资料。")
        with self.gate:
            task=self.task(ident)
            agent=task.get("agent_session") or {}
            if (not self.fresh_foreground or not fresh_poster_agent_bridge.eligible(task)
                    or agent.get("state") in interactive_agent_bridge.ACTIVE or "foreground_detail" not in task):
                raise APIError(409,"detail_edition_not_available","仅本机待提炼的新商品可修改此前选择的商详资料。")
            try:
                captured=foreground_detail_outputs.capture_inputs(payload["detail"],task,self.tasks_dir/ident,from_image_data)
            except (ValueError,OSError,TypeError,KeyError) as error:
                raise APIError(400,"invalid_detail_evidence",str(error) if isinstance(error,ValueError)
                               else "商详证据未能保存，请核对图片与来源。") from None
            self.status(task,task["status"],"商详资料已在本机保存。",foreground_detail=captured)
            return self.serialize(task)

    def start_ordinary_visual_chain(self, ident, payload):
        if not self.ordinary_visual_chain:
            raise APIError(403, "visual_chain_unavailable", "请从客户模型工作台打开商品任务。")
        if (set(payload) != {"approved", "account_id", "model_id", "task_version", "revision"}
                or payload["approved"] is not True or type(payload["task_version"]) is not int
                or any(not isinstance(payload[key], str) for key in ("account_id", "model_id", "revision"))):
            raise APIError(400, "visual_chain_explicit_only", "请从当前商品任务确认发起。")
        with self.gate:
            task = self.task(ident)
            if (payload["task_version"], payload["revision"]) != task_binding(task):
                raise APIError(409, "task_changed", "本机商品任务已有变更，请刷新后核对。")
            status = self.token_plan_poster_status(task)
            if (not status or not status["can_start"] or status["flow"] != "new_poster"
                    or not status["detail"] or not status["detail"]["ready"] or task.get("agent_session")):
                raise APIError(409, "visual_chain_not_available", "请核对尚未提炼文案的商品任务及完整商详资料。")
            try:
                child = self.customer_models.operation_child("language", payload["account_id"], payload["model_id"], visual_session=True)
            except WorkspaceError as error:
                raise APIError(409, error.code, error.message) from None
            child.operation_gate.release()
            session = uuid4().hex
            runner = OrdinaryVisualChain(self, task, session, atomic_write, payload["account_id"], payload["model_id"], child)
            self.status(task, "drafting", "正在使用你确认的模型，商品原图留在本机。",
                        agent_session={"scope": FRESH_FOREGROUND, "session_id": session, "state": "starting",
                                       "transport": ORDINARY_VISUAL_TRANSPORT, "mock": bool(self.customer_models.fixture)})
            self.agent_active = self.foreground_active = session
            threading.Thread(target=runner.run, name="dt-ordinary-visual-chain", daemon=True).start()
            return self.serialize(task)

    def start_token_plan_foreground(self, ident, payload):
        if self.model_workspace is None:
            raise APIError(403, "token_plan_direct_disabled", "当前工作台已停用 Token Plan 直连，请从模型连接选择支持的方式。")
        if not self.foreground_token_plan:
            raise APIError(404,"foreground_disabled","当前工作台未启用本机本人点验。")
        if set(payload) != {"start"} or payload["start"] is not True:
            raise APIError(400,"foreground_explicit_only","请打开本机确认窗。")
        with self.gate:
            task = self.task(ident)
            status = self.token_plan_poster_status(task)
            if not status or not status["can_start"]:
                if status and status.get("detail") and not status["detail"]["ready"]:
                    raise APIError(409,"visual_material_missing","请补齐页面列出的商详事实和图片，再发起模型。")
                raise APIError(409,"foreground_task_not_available","请核对本任务的本机原图、已核事实、商品编号和当前状态。")
            selection = None
            if self.model_workspace is not None:
                if status["flow"] != "new_poster":
                    raise APIError(409,"prototype_new_task_only","此预览请使用新本机商品任务。")
                selection = self.model_workspace.chosen("language")
                if (not selection or selection.get("runnable") is not True
                        or not isinstance(selection.get("model_id"), str)
                        or selection["model_id"] not in AUTHORIZED_MOCKS.get(selection.get("provider"), ())):
                    raise APIError(409,"model_language_required","请在模型连接分配支持工具调用的本机演示语言模型。")
            session = uuid4().hex
            if status["flow"] == "new_poster":
                runner = fresh_poster_agent_bridge.NewPosterSession(self,task,session,atomic_write)
                if selection:
                    runner.local_demo_only = True
                    runner.demo_model_id = selection["model_id"]
                    runner.record["model_assignment"] = {
                        key:selection[key] for key in ("account_id","model_id","provider","plan")}
                state, scope = "drafting", FRESH_FOREGROUND
                message = ("正在使用所选本机演示模型。商品原图留在本机。" if selection
                           else "等待本人在本机确认窗发起。新商品原图留在本机。")
            else:
                _, _, snapshot = self.verified_poster_delivery_source("CN",ident,1,reviewed=True)
                runner = foreground_agent_bridge.ForegroundSession(self,task,session,atomic_write,snapshot)
                state, scope = "ready", foreground_agent_bridge.SCOPE
                message = "已打开本人会话。原已审文件保留。"
            self.status(task,state,message,agent_session={"scope":scope,"session_id":session,"state":"starting"})
            self.agent_active = self.foreground_active = session
            threading.Thread(target=runner.run,name="dt-foreground-agent-broker",daemon=True).start()
            return self.serialize(task)

    def start_interactive_agent(self, ident, payload):
        if self.agent_fixture is None:
            raise APIError(404, "agent_fixture_unavailable", "当前实例不启用交互模拟。")
        if payload not in ({}, {"resume_reviewed": True}) or (payload and payload["resume_reviewed"] is not True):
            raise APIError(400, "agent_start_payload", "客户会话只收原任务启动确认。")
        with self.gate:
            task = self.task(ident)
            if (self.agent_active is not None or task["site"] != "CN" or task.get("kind")
                    or not task.get("copy_intake") or task["source"].get("kind") != "local_upload"
                    or task["copy_intake"]["target_version"] != 1):
                raise APIError(409, "agent_task_not_available", "当前任务不能启动隔离会话。")
            checkpoint = None
            scope = {"session_id":uuid4().hex,"state":"starting","mock":True}
            if payload:
                # Test-only continuation from a verified saved producer, with a fresh main session.
                old = task.get("agent_session") or {}
                if (not self.is_agent_fixture_task(task) or task["status"] != "ready"
                        or old.get("state") != "completed" or old.get("resumed_from")
                        or len(task["versions"]) != 1 or not task["versions"][0].get("plan_release")):
                    raise APIError(409, "agent_checkpoint_unavailable", "缺已完成的首版交互检查点。")
                self.verified_poster_delivery_source("CN", ident, 1, reviewed=True, agent_session=old["session_id"])
                folder = self.tasks_dir / ident
                try:
                    prior = bind_data_path(folder, folder/"customer-agent-session.json").read_bytes()
                    if len(prior) > 32000:
                        raise ValueError()
                    seal = json.loads(prior)
                    if (seal.get("session_id"), seal.get("task_id"), seal.get("version"), seal.get("state"),
                            seal.get("scope"), seal.get("accepted_tool_calls"), seal.get("model_turns")) != (
                            old["session_id"], ident, 1, "completed", poster_copy_executor.INTERACTIVE_TEST, 2, 3):
                        raise ValueError()
                    if seal["events"][-1]["event"] != "model_acknowledged_returned_result":
                        raise ValueError()
                    archive = folder/"prior-customer-agent-session.json"
                    if archive.resolve() != archive:
                        raise ValueError()
                    with archive.open("xb") as evidence:
                        evidence.write(prior)
                except (OSError, ValueError, TypeError, KeyError, IndexError):
                    raise APIError(409, "agent_checkpoint_unavailable", "无法核对保存的会话检查点。") from None
                checkpoint = {"prior_session_id":old["session_id"],"prior_receipt_sha256":sha256(prior).hexdigest(),
                              "copy_receipt_sha256":task["run"]["workflow"]["copy_receipt_sha256"]}
                scope["resumed_from"] = checkpoint["prior_session_id"]
                self.status(task, "ready", "隔离会话从已审校首版启动交接检查。", agent_session=scope)
            else:
                if (task["status"] != visual_copy.WAITING or task.get("confirmed_copy") or task.get("agent_session")):
                    raise APIError(409, "agent_task_not_available", "只能发起本实例一个待提炼的 CN 原图任务。")
                self.status(task, "drafting", "客户已启动隔离交互会话。",
                            test_scope=poster_copy_executor.INTERACTIVE_TEST, agent_session=scope,
                            copy_intake={**task["copy_intake"],"state":"running","layout_allowed":False})
            self.agent_active = scope["session_id"]
            worker = interactive_agent_bridge.CustomerSession(self, task, scope["session_id"], atomic_write, checkpoint)
            threading.Thread(target=worker.run, name="dt-customer-agent-broker", daemon=True).start()
            return self.serialize(task)

    def execute_poster_copy(self, ident, payload):
        with self.gate:
            task = self.task(ident)
            if (task.get("kind") or task.get("agent_session") or not task.get("copy_intake")
                    or task.get("confirmed_copy")):
                raise APIError(409, "poster_copy_not_pending", "当前任务没有待提炼的文案。")
            settings = self.settings()
            kind = self.poster_copy_scope(settings)
            if kind is None:
                raise APIError(409, "poster_copy_executor_unavailable", "请选择并保存可用的客户云端模型配置。")
            cloud = kind in poster_copy_executor.CLOUD_KINDS
            allowed = {"cloud_consent"} if cloud else set()
            if not isinstance(payload, dict) or set(payload) - allowed:
                raise APIError(400, "poster_copy_payload", "此操作只使用本机任务和当前配置确认。")
            if cloud:
                require_current_cloud_consent(payload, settings)
            if task["status"] == "drafting":
                return self.serialize(task)
            if task["status"] != visual_copy.WAITING:
                raise APIError(409, "poster_copy_not_pending", "当前任务没有待提炼的文案。")
            copy_intake = {key: value for key, value in task["copy_intake"].items() if key != "error_code"}
            details = {"copy_intake": {**copy_intake, "state": "running", "layout_allowed": False}}
            if kind == poster_copy_executor.CLOUD_TEST:
                details["test_scope"] = kind
            if cloud:
                target = public_model(settings)

                def runner(binding, prompt):
                    return self.run_cloud_poster_copy(kind, target, binding, prompt)
            else:
                target = None
                runner = self.poster_copy_runner
            self.status(task, "drafting", "正在等待已配置的文案执行回执。", **details)
            self.executor.submit(self.run_reviewed_poster_copy, task, kind, runner, target)
            return self.serialize(task)

    def run_cloud_poster_copy(self, kind, target, binding, prompt):
        # Serialize model-setting edits with the specific consent-checked send.
        # Never fall back to a different model, endpoint, or local demo.
        with self.model_gate:
            settings = self.settings()
            if public_model(settings) != target or self.poster_copy_scope(settings) != kind:
                raise ConnectorError("cloud_settings_changed", "云端配置已变更。请重新确认后再发送。")
            config, key = self.connection(settings)
            result = draft_copy(config, key, prompt["site"], prompt["title"], prompt["facts"], prompt["guidance"])
        if result.get("simulated") is not False or result.get("response_validated") is not True:
            raise ConnectorError("invalid_model_response", "无法核对结构化云端文案。")
        return {"binding": binding, "copy": {field: result[field] for field in LAYER_FIELDS.values()},
                "invocation_id": uuid4().hex}

    def run_reviewed_poster_copy(self, task, kind, runner, target):
        folder = self.tasks_dir / task["id"]
        try:
            state = self.root / f"poster-copy-reviewed-{task['site']}-{kind}.sqlite"
            copy, receipt = poster_copy_executor.run(self.project, folder, task, state, kind, runner, atomic_write)
            run = {**task["run"], "mode": planned_poster.MODE, "workflow": {**task["run"]["workflow"], **receipt}}
            if target is not None:
                run.update(provider=CLOUD_MODE, model=target["model"],
                           simulated=kind == poster_copy_executor.CLOUD_TEST)
            updated = {**task, "confirmed_copy": copy, "run": run,
                       "copy_intake": {**task["copy_intake"], "state": "accepted", "layout_allowed": True}}
            updated["plan_binding"] = planned_poster.binding(updated)
            updated.update(status=planned_poster.AWAITING_PLAN,
                           message="文案回执已绑定本任务。核对并提交排版计划后才可出件。")
            with self.gate:
                self.save_task(updated)
                self.store[task["id"]] = updated
        except Exception as error:
            intake = {**task["copy_intake"], "state": "rejected", "layout_allowed": False}
            message = "文案回执未通过校验。可确认当前配置后用原任务重试。"
            if isinstance(error, ConnectorError):
                intake["error_code"] = error.code
                message = str(error)
            self.status(task, visual_copy.WAITING, message, copy_intake=intake)

    def planned_artwork(self, ident, action, payload, *, agent_plan_session=None, agent_layout_call=None):
        with self.gate:
            task = self.task(ident)
            agent = task.get("agent_session") or {}
            fresh = agent.get("scope") == FRESH_FOREGROUND
            if fresh:
                locked = self.fresh_foreground and agent["session_id"] == self.foreground_active
                can_plan = (action=="layout-plan" and agent_plan_session==agent["session_id"]
                            and bool(agent_layout_call) and agent["state"]=="drafting")
                can_review = (action=="layout-review" and agent["state"]=="waiting_for_review"
                              and task.get("plan_gate",{}).get("agent_layout_call"))
                if not locked or not (can_plan or can_review):
                    raise APIError(409,"foreground_layout_only","新商品排版来自同一模型会话的工具调用，当前仅接本人审校。")
            if self.is_agent_fixture_task(task):
                state = task.get("agent_session", {}).get("state")
                if (action in {"layout-plan","layout-review"} and state != "waiting_for_review") or action == "layout-edit":
                    raise APIError(409, "agent_handoff_proof_required", "同一交互会话须验证卖点结果标识后才可审校首版。")
            if task.get("run", {}).get("mode") != planned_poster.MODE:
                raise APIError(409, "not_planned_poster", "当前任务没有受审排版流程。")
            folder = self.tasks_dir / ident
            try:
                poster_copy_executor.verify_accepted(self.project, folder, task)
                if action == "layout-plan":
                    if task["status"] not in {planned_poster.AWAITING_PLAN, planned_poster.AWAITING_REVIEW}:
                        raise PosterError("当前阶段不能更改首版排版计划。")
                    planned_poster.build(task, folder, payload)
                    path = folder / "poster-layout-plan.json"
                    if path.resolve() != path:
                        raise PosterError("排版计划路径无效。")
                    atomic_write(path, payload)
                    self.status(task, planned_poster.AWAITING_REVIEW,
                                "排版计划已锁定。请核对待出件原图、文案和版式。",
                                plan_gate={"plan_sha256": planned_poster.signature(payload),
                                           "review_summary": payload,
                                           **({"agent_layout_call":agent_layout_call} if fresh else {})})
                elif action == "layout-review":
                    if task["status"] != planned_poster.AWAITING_REVIEW:
                        raise PosterError("须先保存并核对当前任务的首版排版计划。")
                    approval = planned_poster.approve_input(task, payload)
                    plan = planned_poster.read_plan(task, folder)
                    layout = planned_poster.build(task, folder, plan)
                    self.status(task, "rendering", "按已审校的排版计划导出本机 PNG 和 PSD。")
                    self.executor.submit(self.render_planned_artwork, task, layout, approval)
                else:
                    key = self.request_id(payload.get("request_id"))
                    fingerprint = planned_poster.signature(payload)
                    if prior := task["revision_requests"].get(key):
                        if prior != fingerprint:
                            raise PosterError("该请求号已用于另一项修改。")
                        return self.serialize(task)
                    if task["status"] != "ready" or not task["versions"]:
                        raise PosterError("只有已经出件的当前版可提交单层改版。")
                    element, changes = planned_poster.edit_input(task, folder, payload)
                    base = task["versions"][-1]["version"]
                    proof = read_json(folder / "artwork" / f"v{base}" / "poster-chain-release.json")
                    approval = proof["initial_review"]
                    if planned_poster.signature(approval) != task["versions"][0]["plan_release"]["review_sha256"]:
                        raise PosterError("首版审校回执不匹配。")
                    task["revision_requests"][key] = fingerprint
                    self.status(task, "revising", "按已核对的单层修改生成下一版。")
                    self.executor.submit(self.render_planned_artwork, task, None, approval, payload, element, changes)
            except (OSError, KeyError, ValueError, TypeError) as error:
                message = str(error) if isinstance(error, PosterError) else "任务材料或审校记录无法核实。"
                raise APIError(409, "invalid_poster_plan_evidence", message) from None
            return self.serialize(task)

    def render_planned_artwork(self, task, layout, approval, edit=None, element=None, changes=None):
        folder = self.tasks_dir / task["id"]
        try:
            def finalize(result):
                poster_copy_executor.verify_accepted(self.project, folder, task)
                return planned_poster.finalize(task, folder, result, approval, edit=edit)
            if edit is None:
                result = render_initial(folder, task, task["confirmed_copy"], self.engine, self.node,
                                        approved_layout=layout, finalize=finalize)
            else:
                result = render_revision(folder, edit["base_version"], element, None, self.engine, self.node,
                                         reviewed_changes=changes, finalize=finalize)
            self.status(task, "ready", f"按已审校的计划保存 v{result['version']}。", versions=[*task["versions"], result])
        except Exception as error:
            message = str(error) if isinstance(error, PosterError) else "本机排版或审校核验失败。原版本保留。"
            if edit is not None:
                with self.gate:
                    task["revision_requests"].pop(edit["request_id"], None)
            state = {"fresh_render_failed":True} if (task.get("agent_session") or {}).get("scope") == FRESH_FOREGROUND else {}
            self.status(task, "ready" if edit else planned_poster.AWAITING_REVIEW, message, **state)

    def revise(self, ident, payload):
        key = self.request_id(payload.get("request_id"))
        base = payload.get("base_version")
        element = payload.get("element_ref")
        text = payload.get("text")
        if type(base) is not int or not isinstance(element, str) or not isinstance(text, str) or not text.strip():
            raise APIError(400, "invalid_revision", "请填写文字修改内容并确认目标编号。")
        payload_hash = sha256(json.dumps([base, element, text], ensure_ascii=False).encode("utf-8")).hexdigest()
        with self.gate:
            task = self.task(ident)
            if task.get("kind"):
                raise APIError(400, "not_editable", "当前任务没有可编辑的海报版本。")
            if task.get("run", {}).get("mode") == planned_poster.MODE:
                raise APIError(409, "planned_review_required", "计划稿请提交绑定当前版的单层修改审校。")
            if expected := task["revision_requests"].get(key):
                if expected != payload_hash:
                    raise APIError(409, "duplicate_conflict", "该修改标识已用于另一项修改。")
                return self.serialize(task)
            if task["status"] in BUSY or not task["versions"]:
                raise APIError(409, "not_editable", "请等待当前任务结束或先生成首版。")
            current = task["versions"][-1]
            if base != current["version"]:
                raise APIError(409, "stale_version", f"底版已过期，请切换到 v{current['version']}。")
            if element not in {e["id"] for e in current["selected_elements"]}:
                raise APIError(400, "wrong_element", "请从当前作品选择一个文字编号。")
            try:
                field = LAYER_FIELDS[element.rsplit("/", 1)[-1]]
                text = normalize_copy(text, field)
            except CopyConstraintError as error:
                raise APIError(400, "copy_layout_limit", str(error)) from None
            task["revision_requests"][key] = payload_hash
            self.status(task, "revising", "正在生成独立的新版本，已保存版本仍可查看。")
            self.executor.submit(self.make_revision, task, base, element, text)
            return self.serialize(task)

    def make_revision(self, task, base, element, text):
        try:
            result = render_revision(self.tasks_dir / task["id"], base, element, text, self.engine, self.node)
            self.status(task, "ready", f"v{result['version']} 已保存在本机。", versions=[*task["versions"], result])
        except PosterError as error:
            self.status(task, "ready", str(error))
        except Exception:
            self.status(task, "ready", "本机修改未完成，已保留可查看版本。请核对任务再试。")

    def artifact(self, ident, version, kind):
        with self.gate:
            task = self.task(ident)
            entry = next((x for x in task.get("versions", []) if x["version"] == version), None)
            if entry is None:
                raise APIError(404, "missing_version", "该版本尚未生成。")
            filename, mime = ARTIFACTS[kind]
            expected = Path("artwork") / f"v{version}" / filename
            if Path(entry["files"][kind]) != expected:
                raise APIError(404, "missing_file", "本机结果文件不可用。")
            task_root = self.tasks_dir / ident
            path = bind_data_path(task_root, task_root / expected)
            if task.get("run", {}).get("mode") == planned_poster.MODE:
                try:
                    data = path.read_bytes()
                    poster_copy_executor.verify_accepted(self.project, task_root, task)
                    planned_poster.verify_download(task, task_root, entry, kind, data)
                except (ValueError, KeyError, TypeError, OSError):
                    raise APIError(409, "poster_plan_download_mismatch", "该版与审校回执无法核对。") from None
                path = data
            return path, mime, f"{entry['piece']}-v{version}-{filename}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    @property
    def app(self):
        return self.server.workbench

    def allowed_origin(self):
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected:
            raise APIError(403, "invalid_host", "请使用启动器给出的本机地址。")
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + expected:
            raise APIError(403, "invalid_origin", "请求必须来自本机工作台。")

    def send_bytes(self, data, mime, status=200, name=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; frame-ancestors 'none'; object-src 'none'; img-src 'self' blob: data:; base-uri 'none'")
        if name:
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        self.wfile.write(data)

    def as_json(self, body, status=200):
        self.send_bytes(json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def payload(self, limit=MAX_BODY):
        if self.headers.get("X-DT-Local") != "1" or self.headers.get_content_type() != "application/json":
            raise APIError(403, "invalid_request", "请求必须由本机工作台提交。")
        try:
            length = int(self.headers["Content-Length"])
            if not 2 <= length <= limit or self.headers.get("Transfer-Encoding"):
                raise ValueError
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise APIError(400, "invalid_payload", "请求数据格式无效或超出长度限制。") from None

    def guarded(self, action):
        try:
            self.allowed_origin()
            action()
        except (APIError, TaskModelError) as error:
            self.as_json({"error": {"code": error.code, "message": str(error)}}, error.status)
        except ConnectorError as error:
            self.as_json({"error": {"code": error.code, "message": str(error)}}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except FileNotFoundError:
            self.as_json({"error": {"code": "not_found", "message": "请求的本机文件不存在。"}}, 404)
        except Exception:
            self.as_json({"error": {"code": "internal_error", "message": "本机程序未完成请求。"}}, 500)

    def do_GET(self):
        self.guarded(self.get)

    def get(self):
        url = urlsplit(self.path)
        path = url.path
        if path in STATIC_FILES:
            file, mime = STATIC_FILES[path]
            self.send_bytes((APP / "web" / file).read_bytes(), mime)
        elif path == "/api/session":
            self.as_json(self.app.session())
        elif path == "/api/model-workspace" and self.app.customer_models is not None:
            self.as_json(self.app.customer_models.snapshot())
        elif path == "/api/model-workspace" and self.app.model_workspace is not None:
            self.as_json(self.app.model_workspace.snapshot())
        elif self.app.provider_tasks and (provider_route := PROVIDER_TASK_PATH.fullmatch(path)):
            ident, operation, action = provider_route.groups()
            if action == "output":
                data, mime, name = self.app.provider_tasks.output(ident, operation)
                self.send_bytes(data, mime, name=name)
            elif action is None:
                self.as_json(self.app.provider_tasks.summary(ident))
            else:
                raise APIError(405, "method_not_allowed", "请在工作台确认查询。")
        elif path == "/api/tasks":
            with self.app.gate:
                tasks = sorted(self.app.store.values(), key=lambda task: task["created_at"], reverse=True)
                self.as_json({"tasks": [self.app.serialize(task) for task in tasks if self.app.ordinary_mall_visible(task)]})
        elif local_match := LOCAL_FILE.fullmatch(path):
            ident, kind = local_match.groups()
            task = self.app.require_visible_task(self.app.task(ident))
            content = self.app.local_artifact_bytes(ident, kind)
            mime, name = LOCAL_FILES[kind]
            if kind in foreground_mall_combo.FILES and parse_qs(url.query).get("download") != ["1"]:
                name = None
            tool = task["source"].get("tool_id")
            if (tool == workflow_media_library.COVER and kind in ("preview", "bundle")
                    or tool == workflow_media_library.RECEIVE and kind == "bundle"):
                attachment = parse_qs(url.query).get("download") == ["1"]
                name = {"preview": "video-cover.png",
                        "bundle": ("video-cover-source.zip" if tool == workflow_media_library.COVER
                                   else "director-media-bundle.zip")}[kind] if attachment else None
            elif tool in workflow_visual_surfaces.TOOLS and kind in ("preview", "bundle"):
                attachment = parse_qs(url.query).get("download") == ["1"]
                name = {"preview":"visual-surface.png","bundle":"visual-surface.zip"}[kind] if attachment else None
            elif tool in ARTWORK_RENDERERS and kind in ("preview", "bundle", "detail_bundle"):
                attachment = parse_qs(url.query).get("download") == ["1"]
                name = ({"preview": "main-images-preview.png" if tool == main_images.TOOL_ID else "long-detail-preview.png",
                         "bundle": "main-images.zip", "detail_bundle": "long-detail.zip"}[kind] if attachment else None)
            elif tool == workflow_director.VERSIONS_TOOL and kind == "director_bundle":
                name = "director-versions.zip" if parse_qs(url.query).get("download") == ["1"] else None
            elif tool in workflow_poster_delivery.TOOLS and kind == "bundle":
                attachment = parse_qs(url.query).get("download") == ["1"]
                name = "poster-source.zip" if attachment else None
            elif tool == workflow_ph_competitor_detail.TOOL and kind == "evidence_bundle":
                attachment = parse_qs(url.query).get("download") == ["1"]
                name = "ph-reviewed-evidence.zip" if attachment else None
            self.send_bytes(content, mime, name=name)
        elif match := TASK_PATH.fullmatch(path):
            ident, action, version, kind = match.groups()
            if action is None:
                with self.app.gate:
                    task = self.app.require_visible_task(self.app.task(ident))
                    self.as_json({"task": self.app.serialize(task)})
            elif version is not None:
                local, mime, name = self.app.artifact(ident, int(version), kind)
                attachment = parse_qs(url.query).get("download") == ["1"] or kind == "psd"
                self.send_bytes(local if isinstance(local, bytes) else local.read_bytes(), mime, name=name if attachment else None)
            else:
                raise APIError(405, "method_not_allowed", "请在工作台提交修改。")
        else:
            raise APIError(404, "not_found", "找不到对应的本机功能。")

    def do_POST(self):
        self.guarded(self.post)

    def post(self):
        path = urlsplit(self.path).path
        limit = {"/api/tasks/transcription": MAX_MEDIA_BODY, "/api/tasks/role": MAX_WORKFLOW_BODY,
                 main_images.ENDPOINT: main_images.MAX_BODY,
                 long_detail.ENDPOINT: long_detail.MAX_BODY}.get(path, MAX_BODY)
        if self.app.fresh_foreground and (path=="/api/tasks/poster"
                or re.fullmatch(r"/api/tasks/[0-9a-f]{32}/detail-evidence",path)):
            limit = MAX_FOREGROUND_VISUAL_BODY
        if self.app.provider_tasks and (provider_route := PROVIDER_TASK_PATH.fullmatch(path)):
            if self.headers.get("Origin") != "http://" + self.headers.get("Host", ""):
                raise APIError(403, "model_workspace_origin", "请从本机任务页发起。")
            ident, operation, action = provider_route.groups()
            data = self.payload(12288)
            try:
                if action is None:
                    result = self.app.provider_tasks.start(ident, data)
                elif action == "poll":
                    result = self.app.provider_tasks.poll(ident, operation, data)
                else:
                    raise APIError(405, "method_not_allowed", "请在工作台取回本机产物。")
            except WorkspaceError as error:
                raise APIError(400, error.code, error.message) from None
            self.as_json({"operation": result}, 202)
            return
        if self.app.customer_models is not None:
            if path in {"/api/model-workspace/native-connect", "/api/model-workspace/native-disconnect"}:
                raise APIError(403, "token_plan_direct_disabled", "当前工作台已停用 Token Plan 直连。")
            model_action = re.fullmatch(r"/api/model-workspace/(open|catalog|manual|assign|close|remove|dispatch-test)", path)
            if model_action:
                if self.headers.get("Origin") != "http://" + self.headers.get("Host", ""):
                    raise APIError(403, "model_workspace_origin", "请从本机模型连接页面发起。")
                data = self.payload(4096)
                try:
                    result = (self.app.customer_models.dispatch(data) if model_action[1] == "dispatch-test"
                              else self.app.customer_models.mutate(model_action[1], data))
                except WorkspaceError as error:
                    raise APIError(400, error.code, error.message) from None
                self.as_json(result)
                return
        workspace = re.fullmatch(r"/api/model-workspace/(connect|assign|manual|remove)", path)
        if workspace and self.app.model_workspace is not None:
            if self.headers.get("Origin") != "http://" + self.headers.get("Host",""):
                raise APIError(403,"model_workspace_origin","请在本机模型连接页面操作。")
            try:
                result = self.app.model_workspace.change(workspace[1],self.payload(8192))
            except WorkspaceError as error:
                raise APIError(400,error.code,error.message) from None
            self.as_json(result)
            return
        if visual_route := re.fullmatch(r"/api/tasks/([0-9a-f]{32})/ordinary-visual-chain", path):
            if self.headers.get("Origin") != "http://" + self.headers.get("Host", ""):
                raise APIError(403, "foreground_origin_required", "请从本机工作台任务页发起。")
            self.as_json({"task": self.app.start_ordinary_visual_chain(visual_route[1], self.payload(4096))}, 202)
            return
        foreground = re.fullmatch(r"/api/tasks/([0-9a-f]{32})/token-plan-foreground", path)
        if foreground:
            if self.headers.get("Origin") != "http://" + self.headers.get("Host", ""):
                raise APIError(403,"foreground_origin_required","请从本机工作台任务页本人发起。")
            self.as_json({"task":self.app.start_token_plan_foreground(foreground[1],self.payload(64))},202)
            return
        if (self.app.model_workspace is not None or self.app.customer_models is not None) and (path in {"/api/settings", "/api/model/test"}
                or re.fullmatch(r"/api/tasks/[0-9a-f]{32}/(agent-session|execute-copy)", path)):
            raise APIError(403,"preview_model_path_blocked","请通过模型连接与商品任务的当前入口发起。")
        payload = self.payload(limit)
        if path == "/api/settings":
            self.as_json({"settings": self.app.save_settings(payload)})
        elif path == "/api/model/test":
            with self.app.gate:
                settings = self.app.settings()
                require_current_cloud_consent(payload, settings)
            self.as_json({"connection": test_connection(*self.app.connection(settings))})
        elif path == "/api/tasks/poster":
            self.as_json({"task": self.app.create(payload, reviewed_flow=True)}, 202)
        elif path == "/api/tasks":
            self.as_json({"task": self.app.create(payload)}, 202)
        elif path == "/api/tasks/transcription":
            self.as_json({"task": self.app.create_transcription(payload)}, 202)
        elif path == "/api/tasks/role":
            self.as_json({"task": self.app.create_role(payload)}, 202)
        elif path == main_images.ENDPOINT:
            self.as_json({"task": self.app.create_main_images(payload)}, 202)
        elif path == long_detail.ENDPOINT:
            self.as_json({"task": self.app.create_long_detail(payload)}, 202)
        elif match := re.fullmatch(r"/api/tasks/([0-9a-f]{32})/detail-evidence",path):
            if self.headers.get("Origin") != "http://" + self.headers.get("Host",""):
                raise APIError(403,"foreground_origin_required","请在本机原任务补录。")
            self.as_json({"task":self.app.update_foreground_detail(match[1],payload)})
        elif match := re.fullmatch(r"/api/tasks/([0-9a-f]{32})/agent-session", path):
            self.as_json({"task": self.app.start_interactive_agent(match[1], payload)}, 202)
        elif match := re.fullmatch(r"/api/tasks/([0-9a-f]{32})/execute-copy", path):
            self.as_json({"task": self.app.execute_poster_copy(match[1], payload)}, 202)
        elif match := re.fullmatch(r"/api/tasks/([0-9a-f]{32})/(layout-plan|layout-review|layout-edit)", path):
            self.as_json({"task": self.app.planned_artwork(match[1], match[2], payload)}, 202)
        elif (match := TASK_PATH.fullmatch(path)) and match.group(2) == "revise":
            self.as_json({"task": self.app.revise(match.group(1), payload)}, 202)
        else:
            raise APIError(404, "not_found", "找不到对应的本机功能。")


def main(*, poster_copy_runner=None, poster_copy_kind=None, cloud_fixture=None, agent_fixture=None,
         agent_role_fixture=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--node", default=shutil.which("node"))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--foreground-token-plan", action="store_true")
    parser.add_argument("--customer-model-workspace", action="store_true")
    parser.add_argument("--customer-protocol-fixture")
    parser.add_argument("--account-catalog-fixture")
    option = parser.parse_args()
    if not option.node:
        parser.error("Node runtime not found. Provide its local path.")
    project = option.project.resolve(strict=True)
    engine = option.engine.resolve(strict=True)
    root = resolve_profile(option.profile)
    preview = root.name.startswith("bava-tokenplan-new-test-design-")
    customer_test = root.name.startswith("bava-tokenplan-new-test-customer-")
    customer_live = root.name.startswith("bava-tokenplan-new-customer-")
    if not option.foreground_token_plan or (not (customer_live or customer_test) if option.customer_model_workspace else not preview):
        parser.error("Use a dedicated preview or customer workspace profile and the matching foreground flags.")
    if option.customer_protocol_fixture is not None:
        parser.error("The old Token Plan substitute route is disabled in this checkout.")
    if option.account_catalog_fixture is not None:
        if not option.customer_model_workspace or not customer_test:
            parser.error("The catalogue fixture requires a separate customer test profile.")
        try:
            fixture_url(option.account_catalog_fixture)
        except ValueError as error:
            parser.error(str(error))
    root.mkdir(parents=True, exist_ok=True)
    with profile_lock(root):
        verify_profile_path(root)
        app = Workbench(root, project, engine, option.node, poster_copy_runner=poster_copy_runner,
                        poster_copy_kind=poster_copy_kind, cloud_fixture=cloud_fixture, agent_fixture=agent_fixture,
                        agent_role_fixture=agent_role_fixture, foreground_token_plan=option.foreground_token_plan,
                        customer_model_workspace=option.customer_model_workspace,
                        account_catalog_fixture=option.account_catalog_fixture)
        if app.model_workspace is not None or app.customer_models is not None:
            # The web service remains local. An explicitly approved native child owns any model transport.
            def loopback_only(event, args):
                if event == "socket.getaddrinfo" and args[0] not in (None, "localhost", "127.0.0.1", "::1"):
                    raise PermissionError("Workstation preview allows only loopback network access.")
                if event == "socket.connect" and (not isinstance(args[1], tuple) or args[1][0] not in ("127.0.0.1", "::1")):
                    raise PermissionError("Workstation preview allows only loopback network access.")
            sys.addaudithook(loopback_only)
        try:
            with ThreadingHTTPServer(("127.0.0.1", option.port), Handler) as svr:
                svr.workbench = app
                url = f"http://127.0.0.1:{svr.server_port}"
                print(url, flush=True)
                if option.open_browser:
                    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
                try:
                    svr.serve_forever()
                except KeyboardInterrupt:
                    pass
        finally:
            if app.customer_models is not None:
                app.customer_models.close()
            app.executor.shutdown(wait=True)


if __name__ == "__main__":
    main()

