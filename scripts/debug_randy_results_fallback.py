#!/usr/bin/env python3
from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import pandas as pd
import requests


TABLES = [
    "Results_Display.csv",
    "Resolved_SASA_Summary.csv",
]


def base_url() -> str:
    raw = os.environ.get("RANDY_BACKUP_BASE_URL") or os.environ.get("RANDY_ARCHIVE_BASE_URL") or ""
    raw = raw.strip().rstrip("/")
    if raw.endswith("/backup"):
        return raw
    return f"{raw}/backup" if raw else ""


def hunter_url() -> str:
    return (os.environ.get("WARHEAD_HUNTER_BASE_URL") or "http://127.0.0.1:5070").strip().rstrip("/")


def headers() -> dict[str, str]:
    token = (
        os.environ.get("RANDY_BACKUP_TOKEN", "").strip()
        or os.environ.get("RANDY_ARCHIVE_TOKEN", "").strip()
        or os.environ.get("PROTAC_BACKUP_TOKEN", "").strip()
    )
    out = {"User-Agent": "warhead-hunter-randy-results-debug/1.0"}
    if token:
        out["Authorization"] = f"Bearer {token}"
    return out


def get_json(url: str) -> tuple[int, dict[str, Any]]:
    try:
        resp = requests.get(url, headers=headers(), timeout=20)
        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text[:300]}
        return resp.status_code, payload if isinstance(payload, dict) else {"payload": payload}
    except Exception as exc:
        return 0, {"error": str(exc)}


def head(url: str, auth: bool = False) -> int:
    try:
        resp = requests.head(url, headers=headers() if auth else {}, timeout=20, allow_redirects=False)
        return resp.status_code
    except Exception:
        return 0


def table_candidates(name: str, detail: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def add(path: str) -> None:
        path = str(path or "").strip().lstrip("/")
        if path and path not in out:
            out.append(path)

    available = detail.get("available_tables")
    if isinstance(available, dict):
        add(str(available.get(name) or ""))
    tables = detail.get("tables")
    if isinstance(tables, dict) and isinstance(tables.get(name), dict):
        add(str(tables[name].get("relative_path") or ""))

    add(name)
    add(f"TARGET_RESULTS/{name}")
    add(f"job_files/{name}")
    add(f"job_files/TARGET_RESULTS/{name}")
    return out


def download_table(job_id: str, rel: str) -> tuple[int, pd.DataFrame | None]:
    url = f"{base_url()}/hunter-job/{quote(job_id, safe='')}/file/{quote(rel, safe='/')}"
    try:
        resp = requests.get(url, headers=headers(), timeout=30)
        if resp.status_code != 200:
            return resp.status_code, None
        sep = "\t" if rel.lower().endswith(".tsv") else ","
        return resp.status_code, pd.read_csv(io.BytesIO(resp.content), sep=sep, dtype=str).fillna("")
    except Exception:
        return 0, None


def first_value(row: pd.Series, *names: str) -> str:
    lowered = {str(k).lower(): k for k in row.index}
    for name in names:
        key = name if name in row.index else lowered.get(name.lower())
        if key is not None:
            value = str(row.get(key) or "").strip()
            if value and value.lower() != "nan":
                return value
    return ""


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/debug_randy_results_fallback.py <job_id>")
        return 2

    job_id = sys.argv[1].strip()
    if not base_url():
        print("FAIL: set RANDY_BACKUP_BASE_URL or RANDY_ARCHIVE_BASE_URL")
        return 2

    print(f"RANDY base: {base_url()}")
    print(f"Token configured: {'yes' if 'Authorization' in headers() else 'no'}")
    print(f"Warhead Hunter base: {hunter_url()}")

    detail_url = f"{base_url()}/hunter-job/{quote(job_id, safe='')}"
    status, detail = get_json(detail_url)
    print(f"\nJob detail: HTTP {status}, ok={detail.get('ok')}")
    print(f"Archive layout: {detail.get('archive_layout') or 'not reported'}")
    print(f"Available tables: {detail.get('available_tables') or detail.get('tables') or 'none reported'}")
    if status != 200 or not detail.get("ok"):
        return 1

    chosen_rel = ""
    chosen_df: pd.DataFrame | None = None
    for table in TABLES:
        for rel in table_candidates(table, detail):
            code, df = download_table(job_id, rel)
            rows = len(df.index) if df is not None else 0
            print(f"Table candidate {rel}: HTTP {code}, rows={rows}")
            if df is not None and not df.empty:
                chosen_rel = rel
                chosen_df = df
                break
        if chosen_df is not None:
            break

    if chosen_df is None:
        print("FAIL: no readable RANDY table found")
    else:
        print(f"PASS: readable table path: {chosen_rel}")

    results_code = head(f"{hunter_url()}/results/{quote(job_id, safe='')}")
    print(f"/results/{job_id}: HTTP {results_code}")

    if chosen_df is not None and not chosen_df.empty:
        row = chosen_df.iloc[0]
        pdb = first_value(row, "pdb_id", "pdb").lower()
        chain = (first_value(row, "Chain", "chain") or "A").upper()
        ligand = (first_value(row, "Warhead", "Ligand_Resolved", "ligand", "Ligand") or "").upper()
        resid = first_value(row, "Residue_ID", "residue_id", "resid", "Variant")
        if pdb and chain and ligand:
            qs = f"?{urlencode({'resid': resid})}" if resid else ""
            endpoints = [
                f"/api/sdf/{job_id}/{pdb}/{chain}/{ligand}{qs}",
                f"/api/pdb/{job_id}/{pdb}_{chain}_{ligand}.pdb",
                f"/api/svg/{job_id}/{pdb}/{chain}/{ligand}{qs}",
                f"/api/svg-plain/{job_id}/{pdb}/{chain}/{ligand}{qs}",
            ]
            print("\nFirst-row asset endpoints:")
            for endpoint in endpoints:
                print(f"{endpoint}: HTTP {head(hunter_url() + endpoint)}")
        else:
            print("First row does not expose pdb/chain/ligand fields for asset checks.")

    unsafe_url = f"{base_url()}/hunter-job/{quote(job_id, safe='')}/file/../app.py"
    unsafe_code = head(unsafe_url, auth=True)
    print(f"\nUnsafe traversal check: HTTP {unsafe_code} (expected not 200)")

    if chosen_df is None or results_code == 404 or unsafe_code == 200:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
