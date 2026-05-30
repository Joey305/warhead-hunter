#!/usr/bin/env python3
from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:5070"


def find_results_display(job_dir: Path) -> Path | None:
    for candidate in [
        job_dir / "Results_Display.csv",
        job_dir / "TARGET_RESULTS" / "Results_Display.csv",
    ]:
        if candidate.exists():
            return candidate
    return None


def norm_resid(value: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    return s


def row_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        str(row.get("pdb_id") or row.get("pdb") or "").strip().lower(),
        str(row.get("Chain") or row.get("chain") or "A").strip().upper(),
        str(row.get("Ligand_Resolved") or row.get("Warhead") or row.get("ligand") or row.get("Ligand") or "").strip().upper(),
        norm_resid(row.get("Residue_ID") or row.get("residue_id") or row.get("resid") or ""),
    )


def residue_lookup(job_dir: Path) -> dict[tuple[str, str, str], str]:
    lookup: dict[tuple[str, str, str], str] = {}
    for filename in ["Ligand_3D_Atoms.csv", "Resolved_SASA_Summary.csv"]:
        path = None
        for candidate in [job_dir / filename, job_dir / "TARGET_RESULTS" / filename]:
            if candidate.exists():
                path = candidate
                break
        if not path:
            continue
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                pdb, chain, ligand, resid = row_key(row)
                if pdb and chain and ligand and resid:
                    lookup.setdefault((pdb, chain, ligand), resid)
    return lookup


def head_status(url: str) -> int:
    proc = subprocess.run(
        ["curl", "-I", "-L", "-s", "-o", "/dev/null", "-w", "%{http_code}", url],
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        return int((proc.stdout or "0").strip() or "0")
    except ValueError:
        return 0


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        print("Usage: python scripts/debug_live_gallery_endpoints.py <job_id> [base_url]")
        return 2

    job_id = argv[1]
    base_url = (argv[2] if len(argv) == 3 else BASE_URL).rstrip("/")
    job_dir = ROOT / "jobs" / job_id
    results_path = find_results_display(job_dir)
    if not results_path:
        print("FAIL: Results_Display.csv missing")
        return 1

    with results_path.open(newline="", encoding="utf-8", errors="replace") as handle:
        rows = list(csv.DictReader(handle))

    residues = residue_lookup(job_dir)
    failures = []
    checked = 0
    for row in rows:
        pdb, chain, ligand, resid = row_key(row)
        if not resid:
            resid = residues.get((pdb, chain, ligand), "")
        if not (pdb and chain and ligand):
            failures.append((pdb, chain, ligand, resid, "bad row key", 0))
            continue
        qs = f"?{urlencode({'resid': resid})}" if resid else ""
        sdf_url = f"{base_url}/api/sdf/{quote(job_id)}/{quote(pdb)}/{quote(chain)}/{quote(ligand)}{qs}"
        protein_qs = urlencode({k: v for k, v in {"ligand": ligand, "resid": resid}.items() if v})
        protein_url = f"{base_url}/api/protein/{quote(job_id)}/{quote(pdb)}/{quote(chain)}"
        if protein_qs:
            protein_url += f"?{protein_qs}"
        sdf_status = head_status(sdf_url)
        protein_status = head_status(protein_url)
        checked += 1
        if sdf_status != 200:
            failures.append((pdb, chain, ligand, resid, "sdf", sdf_status))
        if protein_status != 200:
            failures.append((pdb, chain, ligand, resid, "protein", protein_status))

    print(f"job_id: {job_id}")
    print(f"base_url: {base_url}")
    print(f"display rows checked: {checked}")
    print(f"endpoint failures: {len(failures)}")
    for pdb, chain, ligand, resid, endpoint, status in failures[:20]:
        print(f"  {endpoint} {status}: {pdb} {chain} {ligand} resid={resid}")

    if failures:
        print("FAIL")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
