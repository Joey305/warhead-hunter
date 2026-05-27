#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import socket
import sys
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:5070"
REQUIRED_KEYS = [
    "QED",
    "MW",
    "LogP",
    "TPSA",
    "HBA",
    "HBD",
    "Rotatable_Bonds",
    "Ring_Count",
    "Aromatic_Rings",
    "Lipinski_Pass",
    "Veber_Pass",
    "Ghose_Pass",
    "Muegge_Pass",
    "Egan_Pass",
]


def find_file(job_dir: Path, name: str) -> Path | None:
    for candidate in [job_dir / name, job_dir / "TARGET_RESULTS" / name]:
        if candidate.exists():
            return candidate
    return None


def read_csv_rows(path: Path | None) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def norm_text(value: object, upper: bool = False, lower: bool = False) -> str:
    s = str(value or "").strip()
    if not s or s.lower() == "nan":
        return ""
    if upper:
        return s.upper()
    if lower:
        return s.lower()
    return s


def norm_resid(value: object) -> str:
    s = norm_text(value)
    if not s:
        return ""
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    return s


def metadata_lookup(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        lig = norm_text(row.get("Ligand") or row.get("Warhead"), upper=True)
        if lig and lig not in out:
            out[lig] = row
    return out


def summary_lookup(rows: list[dict[str, str]]) -> dict[tuple[str, str, str, str], dict[str, str]]:
    out: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in rows:
        key = (
            norm_text(row.get("pdb_id") or row.get("pdb"), lower=True),
            norm_text(row.get("Chain") or row.get("chain") or "A", upper=True),
            norm_text(row.get("Ligand_Resolved") or row.get("Warhead") or row.get("ligand") or row.get("Ligand"), upper=True),
            norm_resid(row.get("Residue_ID") or row.get("residue_id") or row.get("resid")),
        )
        if all(key):
            out.setdefault(key, row)
    return out


def metadata_has_props(row: dict[str, str] | None) -> bool:
    if not row:
        return False
    return any(norm_text(row.get(key)) for key in REQUIRED_KEYS)


def route_url(base_url: str, job_id: str, row: dict[str, str], smiles: str) -> str:
    pdb = norm_text(row.get("pdb_id") or row.get("pdb"), lower=True)
    chain = norm_text(row.get("Chain") or row.get("chain") or "A", upper=True)
    lig = norm_text(row.get("Ligand_Resolved") or row.get("Warhead") or row.get("ligand") or row.get("Ligand"), upper=True)
    resid = norm_resid(row.get("Residue_ID") or row.get("residue_id") or row.get("resid"))
    qs = {}
    if pdb:
        qs["pdb_id"] = pdb
    if chain:
        qs["chain"] = chain
    if resid:
        qs["resid"] = resid
    if smiles:
        qs["smiles"] = smiles
    return f"{base_url}/api/ligand_props/{quote(job_id)}/{quote(lig)}?{urlencode(qs)}"


def server_up(base_url: str) -> bool:
    try:
        host_port = base_url.split("://", 1)[-1].split("/", 1)[0]
        host, port = host_port.split(":")
        with socket.create_connection((host, int(port)), timeout=1.5):
            return True
    except Exception:
        return False


def fetch_json(url: str) -> tuple[int, dict]:
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body)
    except Exception as exc:
        return 0, {"ok": False, "error": repr(exc)}


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        print("Usage: python scripts/debug_ligand_props_contract.py <job_id> [base_url]")
        return 2

    job_id = argv[1]
    base_url = (argv[2] if len(argv) == 3 else DEFAULT_BASE_URL).rstrip("/")
    job_dir = ROOT / "jobs" / job_id
    if not job_dir.exists():
        print(f"FAIL: job not found: {job_dir}")
        return 1

    results_path = find_file(job_dir, "Results_Display.csv")
    summary_path = find_file(job_dir, "Resolved_SASA_Summary.csv")
    meta_path = find_file(job_dir, "Ligand_Metadata.csv")
    fail_path = find_file(job_dir, "Ligand_Metadata_Failures.csv")

    results_rows = read_csv_rows(results_path)
    summary_rows = read_csv_rows(summary_path)
    meta_rows = read_csv_rows(meta_path)
    fail_rows = read_csv_rows(fail_path)

    meta_by_lig = metadata_lookup(meta_rows)
    summary_by_key = summary_lookup(summary_rows)

    print(f"job_id: {job_id}")
    print(f"results_display: {results_path.relative_to(ROOT) if results_path else 'MISSING'} rows={len(results_rows)}")
    print(f"resolved_summary: {summary_path.relative_to(ROOT) if summary_path else 'MISSING'} rows={len(summary_rows)}")
    print(f"ligand_metadata: {meta_path.relative_to(ROOT) if meta_path else 'MISSING'} rows={len(meta_rows)}")
    print(f"ligand_metadata_failures: {fail_path.relative_to(ROOT) if fail_path else 'MISSING'} rows={len(fail_rows)}")
    if meta_rows:
        print(f"ligand_metadata columns: {list(meta_rows[0].keys())}")

    missing_smiles = []
    missing_props = []
    api_failures = []
    artifact_ok = 0

    live_api = server_up(base_url)
    print(f"live_api: {'yes' if live_api else 'no'} ({base_url})")

    for row in results_rows:
        pdb = norm_text(row.get("pdb_id") or row.get("pdb"), lower=True)
        chain = norm_text(row.get("Chain") or row.get("chain") or "A", upper=True)
        lig = norm_text(row.get("Ligand_Resolved") or row.get("Warhead") or row.get("ligand") or row.get("Ligand"), upper=True)
        resid = norm_resid(row.get("Residue_ID") or row.get("residue_id") or row.get("resid"))
        key = (pdb, chain, lig, resid)

        summary_row = summary_by_key.get(key, {})
        smiles = norm_text(row.get("SMILES")) or norm_text(summary_row.get("SMILES")) or norm_text(summary_row.get("Parent_SMILES"))
        meta_row = meta_by_lig.get(lig)

        if not smiles:
            missing_smiles.append(key)

        if metadata_has_props(meta_row):
            artifact_ok += 1
        elif smiles:
            artifact_ok += 1
        else:
            missing_props.append(key)

        if live_api:
            url = route_url(base_url, job_id, row, smiles)
            status, data = fetch_json(url)
            if status != 200:
                api_failures.append((key, status, data))
                continue
            if any(data.get(k) in (None, "", "None") for k in REQUIRED_KEYS):
                api_failures.append((key, status, data))

    print(f"artifact-backed or fallback-computable rows: {artifact_ok}/{len(results_rows)}")
    print(f"rows missing smiles: {len(missing_smiles)}")
    print(f"rows missing props contract: {len(missing_props)}")
    if missing_smiles:
        print("first 10 rows missing smiles:")
        for item in missing_smiles[:10]:
            print(f"  {item}")
    if missing_props:
        print("first 10 rows missing artifact/fallback props:")
        for item in missing_props[:10]:
            print(f"  {item}")

    if live_api:
        print(f"api failures: {len(api_failures)}")
        for key, status, data in api_failures[:10]:
            present = [k for k in REQUIRED_KEYS if data.get(k) not in (None, "", "None")]
            print(f"  {key} status={status} keys_present={present} error={data.get('error')}")
    else:
        print("api failures: skipped (server not running)")

    ok = bool(results_rows) and not missing_props and (not live_api or not api_failures)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
