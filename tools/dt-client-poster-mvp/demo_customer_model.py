"""Offline demonstration of the customer's existing foreground tool protocol."""
from copy import deepcopy
import json
from uuid import uuid4

from copy_constraints import normalize_copy
from foreground_detail_outputs import CARD_OPTIONS, OPTIONS
from fresh_poster_contract import DETAIL, HEIGHT, LAYOUT, MAIN_CARDS, SELL, VISUAL_SECTIONS, WIDTH
from interactive_agent_runtime import HASH, ID, SHOP


def _fits(text, style):
    try:
        return normalize_copy(text, style) == text
    except ValueError:
        return False


def _selling_copy(facts, product):
    # Work with approved phrases as entered, preserving negation and units.
    title_options = [fact for fact in facts if _fits(fact, "title")]
    subtitle_options = [fact for fact in facts if _fits(fact, "subtitle")]
    if not title_options and _fits(product, "title") and any(fact.startswith(product + " ") for fact in facts):
        # A separately submitted name can serve as the title when a fact begins with that full name.
        title_options = [product]
    if not title_options or not subtitle_options:
        return None
    title = product if product in title_options else title_options[0]
    subtitle = next((fact for fact in subtitle_options if fact != title), subtitle_options[0])
    return {"title": title, "subtitle": subtitle}


def _layout():
    def rect(x, y, width, height, **style):
        return {"x": x, "y": y, "width": width, "height": height, **style}

    return {
        "B01": rect(0, 0, WIDTH, HEIGHT, fill="#E8F0EE"),
        "M01": rect(32, 40, 656, 288, fill="#FDFDFC"),
        "T01": rect(56, 72, 608, 111, fill="#12332F", fontSize=34),
        "T02": rect(56, 195, 608, 100, fill="#24433F", fontSize=24),
        "I01": rect(62, 365, 596, 455),
    }


def _reply(status, **details):
    return "stop", {"role": "assistant", "content": json.dumps({"status": status, **details},
                                                               ensure_ascii=False)}


def _token(value, validator):
    return isinstance(value, str) and validator.fullmatch(value) is not None


class DemoCustomerModel:
    """Local fixture for existing tool calls. The selected model ID is a UI test label."""

    simulated = True

    def __init__(self, boot, model_id):
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("demo_missing_selected_model")
        self.model = model_id
        keys = ("session_id", "task_id", "version", "product", "facts")
        self.boot = {key: deepcopy(boot[key]) for key in keys}
        self.combined = "detail_facts" in boot
        if self.combined:
            self.boot["detail_facts"] = deepcopy(boot["detail_facts"])
        self.order = (SELL, LAYOUT, DETAIL, SHOP) if self.combined else (SELL, LAYOUT, SHOP)
        self.binding = {key: boot[key] for key in ("task_id", "version")}
        self.calls = []
        self.results = []
        self.terminated = False
        self.tools = None
        self.opening = None

    def _read_latest(self, messages):
        expected = {**self.boot, "original_image_stays_local": True}
        if (not isinstance(messages, list) or len(messages) != 2 + 2 * len(self.calls)
                or any(not isinstance(row, dict) for row in messages)
                or messages[0].get("role") != "system"
                or messages[1].get("role") != "user"
                or json.loads(messages[1]["content"]) != expected):
            raise ValueError("demo_history_mismatch")
        if self.opening is None:
            self.opening = deepcopy(messages[:2])
        elif self.opening != messages[:2]:
            raise ValueError("demo_history_mismatch")
        for index, issued in enumerate(self.calls):
            assistant, tool = messages[2 + 2 * index:4 + 2 * index]
            if (assistant != issued or tool.get("role") != "tool"
                    or tool.get("tool_call_id") != issued["tool_calls"][0]["id"]):
                raise ValueError("demo_tool_call_mismatch")
            result = json.loads(tool["content"])
            if (not isinstance(result, dict) or result.get("task_id") != self.binding["task_id"]
                    or type(result.get("version")) is not int or result["version"] != self.binding["version"]):
                raise ValueError("demo_wrong_tool_binding")
            if index < len(self.results):
                if self.results[index] != result:
                    raise ValueError("demo_tool_history_changed")
            else:
                self.results.append(result)

    def _blocked(self, reason):
        self.terminated = True
        return _reply("blocked", code=reason)

    def _checked_result(self, prior):
        current = self.results[prior]
        if (current.get("status") != "ok" or not _token(current.get("nonce"), ID)
                or any(current["nonce"] == earlier.get("nonce") for earlier in self.results[:prior])):
            return False
        stage = self.order[prior]
        if stage == SELL:
            sent = json.loads(self.calls[prior]["tool_calls"][0]["function"]["arguments"])
            return (current.get("copy") == {field: sent[field] for field in ("title", "subtitle")}
                    and isinstance(current.get("skill"), str) and bool(current["skill"])
                    and _token(current.get("skill_sha256"), HASH)
                    and _token(current.get("copy_receipt_sha256"), HASH))
        if stage == LAYOUT:
            return (_token(current.get("release_sha256"), HASH)
                    and _token(current.get("layout_skill_receipt_sha256"), HASH)
                    and current.get("local_approval") is True
                    and current.get("dimensions_px") == [WIDTH, HEIGHT]
                    and current.get("editable_native_text_layers") == 2)
        if stage == DETAIL:
            return current.get("same_sku_v1") is True and _token(current.get("visual_receipt_sha256"), HASH)
        return (_token(current.get("receipt_task_id"), ID) and _token(current.get("receipt_sha256"), HASH)
                and current.get("release_sha256") == self.results[1]["release_sha256"]
                and (not self.combined or current.get("same_sku_v1") is True
                     and _token(current.get("visual_receipt_sha256"), HASH)))

    def _arguments(self, stage):
        if stage == SELL:
            return _selling_copy(self.boot["facts"], self.boot["product"])
        if stage == LAYOUT:
            return {"selling_point_result_id": self.results[0]["nonce"], "objects": _layout()}
        poster = self.results[1]
        if stage == DETAIL:
            facts = self.boot["detail_facts"]
            if (set(facts) != set(VISUAL_SECTIONS)
                    or not all(_fits(text, "subtitle") for text in facts.values())):
                return None
            copy = self.results[0]["copy"]
            subtitles = {"feature": copy["title"], "advantage": copy["subtitle"], "benefit": facts["fit"],
                         "proof": facts["quality"], "scene": facts["use"]}
            return {"layout_result_id": poster["nonce"],
                    "cards": {slot: {"title": CARD_OPTIONS[slot], "subtitle": subtitles[slot]}
                              for slot in MAIN_CARDS},
                    "modules": {section: {"title": OPTIONS[section], "body": facts[section]}
                                for section in VISUAL_SECTIONS}}
        args = {"layout_result_id": poster["nonce"], "release_sha256": poster["release_sha256"], "decision": "accept"}
        if self.combined:
            args["visual_result_id"] = self.results[2]["nonce"]
        return args

    def __call__(self, messages, tools):
        if self.terminated:
            return self._blocked("demo_session_already_ended")
        try:
            names = tuple(tool["function"]["name"] for tool in tools)
            if names != self.order or self.tools is not None and self.tools != tools:
                return self._blocked("demo_tool_manifest_mismatch")
            if self.tools is None:
                self.tools = deepcopy(tools)
            self._read_latest(messages)
            if self.results and not self._checked_result(len(self.results) - 1):
                return self._blocked("demo_tool_result_rejected")
            step = len(self.calls)
            if step == len(self.order):
                self.terminated = True
                return _reply("completed", result_nonce=self.results[-1]["nonce"])
            stage = self.order[step]
            args = self._arguments(stage)
            if args is None:
                return self._blocked("demo_evidence_or_copy_missing")
        except (KeyError, TypeError, ValueError, IndexError):
            return self._blocked("demo_invalid_history_or_input")
        response = {"role": "assistant", "content": "", "reasoning_content": "local_workbench_demo_model",
                    "tool_calls": [{"type": "function", "id": f"demo-{uuid4().hex}", "index": 0,
                                    "function": {"name": stage,
                                                 "arguments": json.dumps({**self.binding, **args},
                                                                         ensure_ascii=False)}}]}
        self.calls.append(deepcopy(response))
        return "tool_calls", response

