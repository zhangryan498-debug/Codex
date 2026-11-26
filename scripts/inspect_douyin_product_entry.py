"""Inspect one cached Douyin detail response, offline; never infer absence from empty fields."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit


def _id(value):
    return str(value) if not isinstance(value, bool) and isinstance(value, (str, int)) and re.fullmatch(r"[0-9]+", str(value)) else None


def _schema(value, name, aweme_id, product_id, promotion_id):
    try:
        uri = urlsplit(value)
        query = parse_qs(uri.query, keep_blank_values=True)
    except (TypeError, ValueError, AttributeError):
        return None, "malformed_schema"
    internal = name == "internal_flow_schema"
    host = "product_inner_feed" if internal else "ec_goods_detail"
    context_key = "video_id" if internal else "item_id"
    if uri.scheme != "sslocal" or uri.netloc != host or uri.path != "/" or uri.fragment:
        return None, "unsupported_schema"
    # item_id in this verified goods schema is the VIDEO ID, never the product ID.
    expected = {context_key: aweme_id, "promotion_id": promotion_id}
    for key in ["item_id", "video_id", "aweme_id", "product_id"]:
        if key in query:
            expected[key] = product_id if key == "product_id" else aweme_id
    if any(query.get(key) != [value] for key, value in expected.items()):
        return None, "schema_identity_conflict_or_missing_context"
    return {"field": name, "base": f"sslocal://{host}/", "promotion_id": promotion_id,
            "context_parameter": context_key, "context_aweme_id": aweme_id,
            "raw_value_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}, None


def inspect_payload(payload, *, source_sha256=None):
    """Return API-level evidence for a single response; source hash is of the original bytes."""
    wrapped = isinstance(payload, dict) and "response" in payload
    response = payload.get("response") if wrapped else payload
    base = "/response/data/aweme_detail" if wrapped else "/data/aweme_detail"
    result = {"source_sha256": source_sha256, "aweme_id": None, "api_presence": "unknown",
              "presence_gate_passed": False, "product_bindings": [], "issues": [],
              "status_flags": {}, "ui_seen": False, "ui_clicked": False,
              "ui_note": "API结构证据；未亲见公开页面或点击跳转",
              "flag_note": "状态原值不是商品入口证明；不解释with_fusion_goods业务语义",
              "scope": "single_aweme_detail.anchor_info.extra_direct_rows_only"}
    if not isinstance(response, dict) or response.get("code") != 0:
        result["issues"].append("successful_single_detail_response_required")
        return result
    data = response.get("data")
    aweme = data.get("aweme_detail") if isinstance(data, dict) else None
    if not isinstance(aweme, dict) or not _id(aweme.get("aweme_id")):
        result["issues"].append("single_aweme_detail_required")
        return result
    aweme_id = result["aweme_id"] = _id(aweme["aweme_id"])
    status = aweme.get("status") if isinstance(aweme.get("status"), dict) else {}
    result["status_flags"] = {key: {"pointer": base + "/status/" + key, "value": status.get(key)}
                              for key in ["with_goods", "with_fusion_goods"]}
    requested = (payload.get("params") or {}).get("videoId") if wrapped and isinstance(payload.get("params"), dict) else None
    if (status.get("aweme_id") is not None and _id(status["aweme_id"]) != aweme_id) or (requested is not None and _id(requested) != aweme_id):
        result["issues"].append("aweme_context_conflict")
        return result
    anchor = aweme.get("anchor_info")
    extra = anchor.get("extra") if isinstance(anchor, dict) else None
    extra_pointer = base + "/anchor_info/extra"
    result["source_extra_pointer"] = extra_pointer
    result["extra_was_json_string"] = isinstance(extra, str)
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except ValueError:
            result["issues"].append("malformed_anchor_extra_json")
            return result
    # shortcut: only directly verified extra rows; validate other layouts before adding support.
    if isinstance(extra, list):
        rows = [(f"/{i}", row) for i, row in enumerate(extra)]
    elif isinstance(extra, dict):
        rows = [("", extra)]
    else:
        result["issues"].append("missing_or_unsupported_anchor_extra")
        return result
    for row_pointer, row in rows:
        if not isinstance(row, dict):
            result["issues"].append({"decoded_row_pointer": row_pointer, "reason": "row_not_object"})
            continue
        product_id, promotion_id = _id(row.get("product_id")), _id(row.get("promotion_id"))
        reasons, schemas = [], []
        if not product_id or not promotion_id:
            reasons.append("direct_row_product_and_promotion_ids_required")
        if any(key in row and _id(row[key]) != aweme_id for key in ["aweme_id", "video_id"]):
            reasons.append("row_aweme_context_conflict")
        for name in ["product_detail_schema", "jump_schema", "internal_flow_schema"]:
            if row.get(name) in (None, ""):
                continue
            schema, error = _schema(row[name], name, aweme_id, product_id, promotion_id)
            if error:
                reasons.append(name + ":" + error)
            else:
                schemas.append({**schema, "decoded_schema_pointer": row_pointer + "/" + name})
        if not any(s["field"] in {"product_detail_schema", "jump_schema"} for s in schemas):
            reasons.append("verified_product_detail_schema_required")
        if reasons:
            result["issues"].append({"decoded_row_pointer": row_pointer, "reasons": reasons})
            continue
        result["product_bindings"].append({"aweme_id": aweme_id, "product_id": product_id,
            "promotion_id": promotion_id, "source_extra_pointer": extra_pointer,
            "decoded_row_pointer": row_pointer, "decoded_product_id_pointer": row_pointer + "/product_id",
            "decoded_promotion_id_pointer": row_pointer + "/promotion_id", "schemas": schemas,
            "binding_basis": "same_aweme_anchor_direct_product_row_and_consistent_schema",
            "uri_item_id_is_video_context_not_product": True})
    if result["product_bindings"]:
        result["api_presence"], result["presence_gate_passed"] = "confirmed_present", True
    return result


def inspect_file(path):
    path = Path(path)
    raw = path.read_bytes()
    result = inspect_payload(json.loads(raw.decode("utf-8-sig")), source_sha256=hashlib.sha256(raw).hexdigest())
    return {"source_file": str(path.resolve()), **result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    source, output = args.input.resolve(), args.output.resolve()
    if source == output or (output.exists() and source.samefile(output)):
        parser.error("output must not overwrite input (including aliases)")
    try:
        result = inspect_file(source)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
