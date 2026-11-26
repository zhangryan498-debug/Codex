"""Bind, attach and cold-read the three local visual outputs from one customer session."""
from hashlib import sha256
from interactive_agent_runtime import digest
import foreground_detail_outputs
from poster_copy_executor import FRESH_TEST

FILES = {
    "mall_main_bundle": ("application/zip","main-images.zip"),
    "mall_detail_bundle": ("application/zip","long-detail.zip"),
    "mall_combined_receipt": ("application/json; charset=utf-8","mall-combined-receipt.json"),
}
SOURCE_KINDS = {"main":"bundle","detail":"detail_bundle"}


def bound(bench, task, session, selectors, release):
    sku = task["source"]["sku"]
    binding = {"session_id":session,"task_id":task["id"],"version":1,"product_id":sku,"sku_id":sku}
    actual_mode = task["run"]["workflow"]["execution_kind"]
    expected = {"sku_id":sku,"product_id":sku,"asset_sha256":task["source"]["sha256"],
                "evidence_sha256":foreground_detail_outputs.digest(task["foreground_detail"]),
                "model_mode":"local_fixture" if actual_mode==FRESH_TEST else "customer_foreground"}
    proof = foreground_detail_outputs.verify_outputs(bench,binding,selectors,expected_context=expected)
    archives = {key:bench.local_artifact_bytes(ident,SOURCE_KINDS[key]) for key,ident in selectors.items()}
    if set(archives) != set(SOURCE_KINDS) or not all(0<len(data)<=64*1024*1024 for data in archives.values()):
        raise ValueError("Visual source bundles missing or oversized")
    seal = {"binding":binding,"source_tasks":selectors,"archive_sha256":{key:sha256(data).hexdigest()
            for key,data in archives.items()},"visual_verification_sha256":digest(proof),"poster_release_sha256":release}
    return seal, archives


def receipt(task, poster_sha, execution, seal):
    return {"scope": "local_mall_visual_receipt", "mall_task_id":task["id"], "site":task["site"],
            **seal, "poster_bundle_sha256":poster_sha, "mall_agent_receipt_sha256":execution["receipt_sha256"],
            "evidence_scope":task["source"]["role_packet_pins"].get("evidence_scope"),
            "publish_authorized":False, "human_signoff_recorded":False}
