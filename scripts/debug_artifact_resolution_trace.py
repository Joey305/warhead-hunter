#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import quote, urlencode

import requests


DEFAULT_BASE_URL = "https://warheadhunter.com"


def endpoint(base_url: str, path: str, query: dict[str, str] | None = None) -> str:
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        clean = {k: v for k, v in query.items() if v}
        if clean:
            url += f"?{urlencode(clean)}"
    return url


def head_status(url: str) -> int:
    try:
        response = requests.head(url, timeout=20, allow_redirects=False)
        if response.status_code == 405:
            response = requests.get(url, timeout=20, stream=True)
        return response.status_code
    except Exception:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--pdb", required=True)
    parser.add_argument("--chain", required=True)
    parser.add_argument("--ligand", required=True)
    parser.add_argument("--resid", default="")
    args = parser.parse_args()

    base_url = (os.environ.get("WARHEAD_HUNTER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    pdb = args.pdb.strip().lower()
    chain = args.chain.strip().upper()
    ligand = args.ligand.strip().upper()
    resid = str(args.resid or "").strip()

    debug_url = endpoint(
        base_url,
        f"/api/debug/artifact-resolution/{quote(args.job_id)}/{quote(pdb)}/{quote(chain)}",
        {"ligand": ligand, "resid": resid, "debug": "1"},
    )
    debug_resp = requests.get(debug_url, timeout=30)
    if debug_resp.status_code != 200:
        print(f"base_url: {base_url}")
        print(f"job_id: {args.job_id}")
        print(f"debug endpoint status: {debug_resp.status_code}")
        print("FAIL: debug endpoint is unavailable on this deployment")
        return 1
    payload = debug_resp.json()

    checks = payload.get("checks") or {}
    resolved = payload.get("resolved") or {}
    hard_gate = payload.get("hard_gate") or {}

    print(f"base_url: {base_url}")
    print(f"job_id: {args.job_id}")
    print(f"Expected PDB filename: {payload.get('expected', {}).get('pdb_filename', '')}")
    print(f"Expected SDF filename: {payload.get('expected', {}).get('sdf_filename', '')}")
    print(
        "Expected SVG filenames: "
        f"{payload.get('expected', {}).get('svg_exposed_filename', '')} | "
        f"{payload.get('expected', {}).get('svg_plain_filename', '')}"
    )
    print(f"/api/pdb status: {checks.get('pdb_exact_endpoint', {}).get('status', 0)}")
    print(f"/api/protein status: {checks.get('protein_endpoint', {}).get('status', 0)}")
    print(f"/api/sdf status: {checks.get('sdf_endpoint', {}).get('status', 0)}")
    print(f"/api/svg status: {checks.get('svg_endpoint', {}).get('status', 0)}")
    print(f"/api/svg-plain status: {checks.get('svg_plain_endpoint', {}).get('status', 0)}")

    residue_url = endpoint(
        base_url,
        f"/api/jobs/{quote(args.job_id)}/sasa/residue_for_ligand",
        {"pdb_id": pdb, "chain": chain, "ligand": ligand},
    )
    residue_status = head_status(residue_url)
    print(f"SASA residue status: {residue_status}")
    print(
        "resolved source root: "
        f"{resolved.get('pdb', {}).get('source', '')} "
        f"{resolved.get('pdb', {}).get('relative_path', '')}"
    )
    print(f"hard gate verdict: renderable={hard_gate.get('renderable', False)}")

    pdb_status = checks.get("pdb_exact_endpoint", {}).get("status", 0)
    protein_status = checks.get("protein_endpoint", {}).get("status", 0)
    sdf_status = checks.get("sdf_endpoint", {}).get("status", 0)
    if pdb_status == 200 and protein_status != 200:
        print("route mismatch warning: exact PDB works but /api/protein fails")
        return 1
    if sdf_status == 200 and protein_status != 200:
        print("FAIL: SDF works but protein fails for this ligand")
        return 1
    if not hard_gate.get("renderable", False):
        print("FAIL: hard gate remains false")
        return 1
    print("PASS: hard gate is satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
