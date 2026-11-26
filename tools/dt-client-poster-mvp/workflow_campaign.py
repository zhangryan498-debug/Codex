"""Run the existing campaign arithmetic on the reviewed, public synthetic cases."""
import builtins
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType


class WorkflowError(ValueError):
    pass


TOOL_ID = "campaign-ledger-replay"
SUITE_ID = "DT_FRESH_COMPOSITE_B7E91D_20261111"
LABEL = "活动价格与优惠账本回放"
SOURCE = "work/fresh-composite-blind-20261111"
MARKETS = {"CN": ("douyin_cn", "CNY"), "PH": ("tiktok_ph", "PHP")}
EXAMPLES = {
    "CN": {"case_ids": ["CN-62A4D7", "CN-9F017B", "CN-8D21AF", "CN-7B905E"]},
    "PH": {"case_ids": ["PH-146D7A", "PH-02E8C1", "PH-6A72F0", "PH-884D21"]},
}
PINS = {
    SOURCE + "/adapter/engine.py": "c077daf67c32e0b1e78c3ae5a80bc41ddd9398ae6687875d2e2d8ced9abe48cb",
    SOURCE + "/public_input/CN_raw_cases.json": "39bf176a6c32a754acf043ccd564ee59bf2debfe7c2a489313278de08df0659c",
    SOURCE + "/public_input/PH_raw_cases.json": "dff7d2c62d94e0aaab30e4886bf3d0298dc2855c58e24f46d41f5867647b695d",
    SOURCE + "/public_input/public_rules.json": "00ffcd2141225a29d1b2c67ec59ae3aedb50cce9f756cb0a29174d2f7f4335f1",
    SOURCE + "/public_input/execution_contract.json": "fadcd67831b71eb8cbe8e066c2ff89a4ba258ab74561e01c7df86dc04222a1b0",
    "scripts/campaign_ledger_semantics_v3.py": "32bfc31859d6c695770f49bca321d380d63386cca2b74d69db985f155fcb0f56",
    "scripts/campaign_ledger_semantics_v4.py": "b889ba1a9b34210d0ae1a5e08b2f4c182e0ed8a106490b50d9c867b65ad298bf",
}
DECISIONS = {"ALLOW": "模拟账本通过", "REJECT": "模拟方案拒绝", "HOLD": "补证后复核"}
LIMITS = [
    "费用、优惠、活动状态和底线均来自 2026-11-11 冻结合成案例，历史规则仅用于回放。",
    "仅接受本项目预置案例编号。案例未涉及的经营数据和当前平台资格仍待核。",
    "未执行接收岗价审绑定，无可消费的价格授权回执。",
    "活动报名、改价、投放、发布和实店权限均未执行。",
]


def public_tools():
    return [{
        "id": TOOL_ID,
        "label": LABEL,
        "role_suffix": "campaign-ops",
        "sites": list(MARKETS),
        "input_example": {"case_ids": []},
        "input_examples": {
            site: {"case_ids": list(example["case_ids"])}
            for site, example in EXAMPLES.items()
        },
        "description": "用项目的冻结模拟输入重算买家实付、商家费用与净额，并复核活动状态和价格底线。case_ids 留空时回放本站全部 12 例。",
        "max_media_files": 0,
    }]


def _verified(project, relative_path):
    path = (project / relative_path).resolve()
    if not path.is_relative_to(project):
        raise WorkflowError("回放依赖超出项目目录。")
    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise WorkflowError(f"缺少活动回放依赖 {relative_path}。") from exc
    digest = hashlib.sha256(blob).hexdigest()
    if digest != PINS[relative_path]:
        raise WorkflowError(f"活动回放依赖和冻结版本不一致，已停止。{relative_path}")
    return path, blob


def _load_code(path, source, local_dependencies=None):
    # Bind the v4 module's v3 import without modifying process-wide Python import state.
    module = ModuleType(path.stem)
    module.__file__ = str(path)
    if local_dependencies:
        def load_dependency(name, *args, **kwargs):
            if name in local_dependencies:
                return local_dependencies[name]
            return builtins.__import__(name, *args, **kwargs)

        module.__dict__["__builtins__"] = {**vars(builtins), "__import__": load_dependency}
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def _write_json(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _report(result):
    lines = [
        f"# {LABEL}",
        "",
        f'站点 {result["site"]}，{result["currency"]} 最小货币单位。'
        f'本次重新计算 {len(result["cases"])} 个冻结模拟案例。',
        "",
        "| 案例 | 建议 | 底线证明 | 当前净额 | 当前下界 | 当前上界 | 待补字段 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in result["cases"]:
        numbers = row["numbers"]
        amounts = [numbers.get(key) for key in ("net_minor", "net_lower_minor", "net_upper_minor")]
        values = ["未知" if value is None else str(value) for value in amounts]
        pending = "、".join(row["pending_fields"]) or "无"
        case = row["case_id"]
        label = DECISIONS[row["decision"]]
        status = row["floor_status"]
        lines.append(f"| {case} | {label} | {status} | {' | '.join(values)} | {pending} |")
    lines.extend(["", "## 适用范围", "", *[f"- {item}" for item in LIMITS], ""])
    return "\n".join(lines)


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress):
    if tool_id != TOOL_ID or site not in MARKETS:
        raise WorkflowError("此活动回放仅支持抖音中国和 TikTok 菲律宾。")
    if assets:
        raise WorkflowError("此活动回放无需上传媒体。")
    if not isinstance(payload, dict) or set(payload) != {"case_ids"}:
        raise WorkflowError("仅接受 case_ids 字段。用空数组回放本站全部案例。")
    requested = payload["case_ids"]
    if (not isinstance(requested, list) or len(requested) > 12 or
            any(not isinstance(case, str) for case in requested) or
            len(set(requested)) != len(requested)):
        raise WorkflowError("case_ids 必须是最多 12 个不同案例编号组成的数组。")

    project = Path(project).resolve()
    market, currency = MARKETS[site]
    case_path = f"{SOURCE}/public_input/{site}_raw_cases.json"
    rule_path = SOURCE + "/public_input/public_rules.json"
    contract_path = SOURCE + "/public_input/execution_contract.json"
    engine_path = SOURCE + "/adapter/engine.py"
    v3_path = "scripts/campaign_ledger_semantics_v3.py"
    v4_path = "scripts/campaign_ledger_semantics_v4.py"
    destinations = (case_path, rule_path, contract_path, engine_path, v3_path, v4_path)
    progress("正在核对活动案例、规则和计算器的冻结版本。")
    verified = {name: _verified(project, name) for name in destinations}
    raw, rules, contract = (json.loads(verified[name][1]) for name in destinations[:3])
    if (any(doc["suite_id"] != SUITE_ID for doc in (raw, rules, contract)) or
            contract["version"] != "1.0" or raw["market"] != market or market not in contract["scope"]):
        raise WorkflowError("冻结活动数据的站点、套件或合同版本不一致。")
    available = {case["case_id"]: case for case in raw["cases"]}
    if (len(available) != 12 or len(raw["cases"]) != 12 or
            any(case["market"] != market or case["currency"] != currency for case in available.values())):
        raise WorkflowError("活动案例站点、币种或编号不一致。")
    if any(case_id not in available for case_id in requested):
        raise WorkflowError("case_ids 包含未知编号或其他站点的编号。")

    engine = _load_code(*verified[engine_path])
    v3 = _load_code(*verified[v3_path])
    policy = _load_code(*verified[v4_path], {v3.__name__: v3})
    cards = rules["rule_cards"]
    rows = []
    progress("正在重算当前价格及费用，并校验价格底线与缺失字段。")
    for case_id in requested or available:
        case = available[case_id]
        try:
            calculated = engine.calculate_case(case)
            proof = policy.normalize(case, calculated, cards)
        except (ValueError, AssertionError, KeyError, TypeError) as exc:
            raise WorkflowError(f"案例 {case_id} 未通过离线计算或账本校验。") from exc
        if proof["scope"] != "synthetic_offline" or proof["release_status"] != "not_authorized":
            raise WorkflowError(f"案例 {case_id} 缺少离线授权标记。")
        rows.append({
            **proof,
            "simulation_disposition": DECISIONS[proof["decision"]],
            "currency": currency,
            "source_case": case,
        })

    result = {
        "tool_id": TOOL_ID,
        "site": site,
        "market": market,
        "currency": currency,
        "scope": "synthetic_offline",
        "suite_id": SUITE_ID,
        "release_status": "not_authorized",
        "campaign_started": False,
        "price_handoff_status": "not_evaluated",
        "decision_counts": {key: sum(row["decision"] == key for row in rows) for key in DECISIONS},
        "amount_coverage": dict(Counter(row["current_amount_status"] for row in rows)),
        "cases": rows,
        "limitations": LIMITS,
    }
    task_dir = Path(task_dir)
    files = {
        "json": task_dir / "campaign-ledger.json",
        "receipt": task_dir / "campaign-ledger-receipt.json",
        "report": task_dir / "campaign-ledger.md",
    }
    _write_json(files["json"], result)
    files["report"].write_text(_report(result), encoding="utf-8")
    _write_json(files["receipt"], {
        "tool_id": TOOL_ID,
        "site": site,
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": result["scope"],
        "release_status": result["release_status"],
        "suite_id": SUITE_ID,
        "case_ids": [row["case_id"] for row in rows],
        "case_evaluation_times": {
            row["case_id"]: row["source_case"]["evaluation_time"] for row in rows
        },
        "project_inputs_and_code_sha256": {name: PINS[name] for name in destinations},
        "outputs_sha256": {
            files[kind].name: hashlib.sha256(files[kind].read_bytes()).hexdigest()
            for kind in ("json", "report")
        },
        "actual_price_gate_receiver_called": False,
        "calls": {"model": 0, "shop": 0, "external_api": 0},
    })
    progress("活动离线账本和核查回执已保存。")
    return result, files
