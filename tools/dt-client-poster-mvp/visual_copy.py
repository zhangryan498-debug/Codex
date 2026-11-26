"""Bind pending PH visual copy jobs to a verified, process-local test adapter."""
from copy import deepcopy
from hashlib import sha256
import json

from copy_constraints import normalize_copy
from poster_workflow import _SKILLS
import visual_market

WAITING = "awaiting_selling_points"
TEST = "isolated_copy_test"
SHAPES = {
    "main-image-set": ("cards", "slot", ("title", "subtitle")),
    "long-product-detail": ("modules", "module", ("title", "body")),
}


def blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def missing(tool, parameters):
    group, identity, fields = SHAPES[tool]
    return {row[identity]: [field for field in fields if blank(row.get(field))]
            for row in parameters[group]
            if any(blank(row.get(field)) for field in fields)}


def bind(project, task, parameters, route):
    name = _SKILLS[route]
    skill = project / ".agents" / "skills" / name / "SKILL.md"
    market = parameters.get("market") or {}
    return {
        "task_id": task["id"], "site": task["site"], "product_id": parameters["product_id"],
        "listing": market.get("listing") if market else parameters.get("listing"), "input_sha256": task["request_hash"],
        "tool_id": task["source"]["tool_id"], "route": route,
        "skill": name, "skill_sha256": sha256(skill.read_bytes()).hexdigest(),
    }


def fill(parameters, binding, receipt):
    if (not isinstance(receipt, dict) or set(receipt) != {"kind", "binding", "copy"}
            or receipt["kind"] != TEST
            or json.dumps(receipt["binding"], sort_keys=True, allow_nan=False)
            != json.dumps(binding, sort_keys=True, allow_nan=False)):
        raise ValueError("Copy receipt is not from this task, input, or isolated executor.")
    updates = receipt["copy"]
    required = missing(binding["tool_id"], parameters)
    if not isinstance(updates, dict) or set(updates) != set(required):
        raise ValueError("Copy receipt does not match the missing elements.")
    result = deepcopy(parameters)
    group, identity, _ = SHAPES[binding["tool_id"]]
    for row in result[group]:
        key = row[identity]
        if key not in required:
            continue
        values = updates[key]
        if not isinstance(values, dict) or set(values) != set(required[key]):
            raise ValueError("Replacement copy can only fill missing fields.")
        for field, value in values.items():
            style = "subtitle" if field == "body" else field
            if normalize_copy(value, style) != value:
                raise ValueError("Replacement copy failed existing layout constraints.")
            visual_market.check_ph_copy(value)
            row[field] = value
    return result


def layout_plan(execution, explicit_hash):
    if execution is None:
        return {"route": "USER", "sha256": explicit_hash}
    raw = json.dumps(execution, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return {"route": execution["binding"]["route"], "sha256": sha256(raw).hexdigest(),
            "execution_kind": TEST, "skill_executed": False}
