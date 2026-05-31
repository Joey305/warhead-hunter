#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict

import requests


def base_url() -> str:
    return os.environ.get("WARHEAD_HUNTER_BASE_URL", "https://warheadhunter.com").rstrip("/")


def get_json(url: str) -> tuple[Dict[str, Any] | None, float, int]:
    started = time.perf_counter()
    response = requests.get(url, timeout=30, headers={"Accept": "application/json"})
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    try:
      payload = response.json()
    except Exception:
      payload = None
    return payload, elapsed_ms, response.status_code


def head(url: str) -> int:
    response = requests.head(url, timeout=30)
    if response.status_code in {405, 501}:
        response = requests.get(url, timeout=30, stream=True)
    return response.status_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect results startup artifact index performance.")
    parser.add_argument("job_id", help="Warhead Hunter job id")
    args = parser.parse_args()

    root = base_url()
    first_url = f"{root}/api/results/{args.job_id}/artifact-index?debug=1"
    second_url = first_url

    payload1, elapsed1, status1 = get_json(first_url)
    payload2, elapsed2, status2 = get_json(second_url)

    print(f"artifact_index_url: {first_url}")
    print(f"first_call_status: {status1}")
    print(f"first_call_ms: {elapsed1:.1f}")
    print(f"second_call_status: {status2}")
    print(f"second_call_ms: {elapsed2:.1f}")

    if not payload1 or not payload1.get("ok"):
        print("artifact_index_ok: false")
        if payload1 is not None:
            print(json.dumps(payload1, indent=2)[:1200])
        return 1

    print("artifact_index_ok: true")
    print(f"cache_hit_second_call: {bool(payload2 and payload2.get('cache', {}).get('hit'))}")
    print(f"row_count: {payload1.get('row_count')}")
    print(f"first_renderable_index: {payload1.get('first_renderable_index')}")
    print(f"generated_ms: {payload1.get('generated_ms')}")
    print("summary:")
    print(json.dumps(payload1.get("summary", {}), indent=2))

    rows = payload1.get("rows") or []
    first_renderable = next((row for row in rows if row.get("hard_renderable")), None)
    if not first_renderable:
        print("hard_gate_verdict: no renderable rows")
        return 1

    print("first_renderable_row:")
    print(json.dumps({
        "index": first_renderable.get("index"),
        "pdb": first_renderable.get("pdb"),
        "chain": first_renderable.get("chain"),
        "ligand": first_renderable.get("ligand"),
        "resid": first_renderable.get("resid"),
        "protein": first_renderable.get("protein"),
        "sdf": first_renderable.get("sdf"),
        "svg": first_renderable.get("svg"),
    }, indent=2))

    checks = {
        "protein": first_renderable.get("protein", {}).get("url"),
        "sdf": first_renderable.get("sdf", {}).get("url"),
        "svg": first_renderable.get("svg", {}).get("exposed_url"),
        "svg_plain": first_renderable.get("svg", {}).get("plain_url"),
    }
    for key, rel_url in checks.items():
        if not rel_url:
            continue
        status = head(f"{root}{rel_url}")
        print(f"{key}_status: {status}")

    print("startup_probe_shape: artifact-index plus first-card endpoint checks expected; no exhaustive pre-render card probing should remain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
