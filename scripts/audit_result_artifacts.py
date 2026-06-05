#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
JOBS_ROOT = ROOT / "jobs"
Axx_RE = re.compile(r"^A\d\d$")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def norm_str(value: Any, *, upper: bool = False, lower: bool = False) -> str:
    s = str(value or "").strip()
    if upper:
        return s.upper()
    if lower:
        return s.lower()
    return s


def norm_resid(value: Any) -> str:
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


def resolve_job_dir(job_id: Optional[str], job_dir: Optional[str]) -> Path:
    if job_dir:
        return Path(job_dir).resolve()
    if not job_id:
        raise SystemExit("Provide --job-id or --job-dir")
    return (JOBS_ROOT / job_id).resolve()


def read_csv(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, dtype=str).fillna("")
    except Exception:
        return None


def first_existing(paths: List[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None


def find_results_display(job_dir: Path) -> Optional[Path]:
    return first_existing([
        job_dir / "TARGET_RESULTS" / "Results_Display.csv",
        job_dir / "Results_Display.csv",
    ])


def find_5charmap(job_dir: Path) -> Optional[Path]:
    return first_existing([
        job_dir / "5CharMAP.csv",
        job_dir / "TARGET_RESULTS" / "5CharMAP.csv",
    ])


def find_artifact_roots(job_dir: Path, suffix: str) -> List[Path]:
    roots = []
    seen = set()
    for path in [
        job_dir / "MCS_Output" / suffix,
        job_dir / "TARGET_RESULTS" / "MCS_Output" / suffix,
    ]:
        if path.exists() and path.is_dir():
            resolved = str(path.resolve())
            if resolved not in seen:
                roots.append(path)
                seen.add(resolved)
    return roots


def build_alias_map(job_dir: Path) -> Dict[tuple[str, str], str]:
    fp = find_5charmap(job_dir)
    if not fp:
        return {}
    df = read_csv(fp)
    if df is None or df.empty:
        return {}
    cols = {c.lower(): c for c in df.columns}
    pdb_col = cols.get("pdb") or cols.get("pdb_id")
    ligx_col = cols.get("ligandx") or cols.get("ligand_x") or cols.get("ligand")
    lig5_col = cols.get("ligand5")
    if not pdb_col or not ligx_col or not lig5_col:
        return {}
    out = {}
    for _, row in df.iterrows():
        pdb = norm_str(row.get(pdb_col), lower=True)
        ligx = norm_str(row.get(ligx_col), upper=True)
        lig5 = norm_str(row.get(lig5_col), upper=True)
        if pdb and ligx and lig5:
            out[(pdb, ligx)] = lig5
    return out


def candidate_aliases(row: Dict[str, Any], ligand5: str) -> List[str]:
    aliases = []
    for key in ["Warhead", "Warhead_5", "Ligand_Resolved", "Ligand5_Resolved", "ligand", "Ligand"]:
        val = norm_str(row.get(key), upper=True)
        if val and val not in aliases:
            aliases.append(val)
    if ligand5 and ligand5 not in aliases:
        aliases.append(ligand5)
    return aliases


def find_best_artifact(roots: List[Path], exts: List[str], pdb: str, chain: str, aliases: List[str], resid: str) -> Optional[Path]:
    for alias in aliases:
        if resid:
            for root in roots:
                for ext in exts:
                    fp = root / f"{pdb}_{chain}_{alias}_{resid}{ext}"
                    if fp.exists():
                        return fp
        for root in roots:
            for ext in exts:
                hits = sorted(root.glob(f"{pdb}_{chain}_{alias}_*{ext}"))
                if hits:
                    return hits[0]
    return None


def load_artifact_index(job_id: str) -> Dict[str, Any]:
    try:
        from app import app
        client = app.test_client()
        resp = client.get(f"/api/results/{job_id}/artifact-index")
        if resp.status_code == 200:
            return resp.get_json(silent=True) or {}
    except Exception:
        pass
    return {}


def route_statuses(job_id: str, pdb: str, chain: str, ligand: str, resid: str) -> Dict[str, Any]:
    out = {"api_sdf_status": "", "api_svg_status": "", "api_ligand_props_status": ""}
    try:
        from app import app
        client = app.test_client()
    except Exception as exc:
        out["api_sdf_status"] = f"route_error:{exc}"
        out["api_svg_status"] = f"route_error:{exc}"
        out["api_ligand_props_status"] = f"route_error:{exc}"
        return out

    def call(path: str, expect_json: bool = False) -> str:
        resp = client.get(path)
        detail = ""
        if expect_json:
            data = resp.get_json(silent=True) or {}
            if data.get("ok") is False and data.get("error"):
                detail = str(data.get("error"))
            elif data.get("source"):
                detail = f"source={data.get('source')}"
        return f"{resp.status_code}{':' + detail if detail else ''}"

    q = f"?resid={resid}" if resid else ""
    out["api_sdf_status"] = call(f"/api/sdf/{job_id}/{pdb}/{chain}/{ligand}{q}")
    out["api_svg_status"] = call(f"/api/svg/{job_id}/{pdb}/{chain}/{ligand}{q}")
    out["api_ligand_props_status"] = call(
        f"/api/ligand_props/{job_id}/{ligand}?pdb_id={pdb}&chain={chain}&resid={resid}",
        expect_json=True,
    )
    return out


def first_missing_stage(record: Dict[str, Any]) -> tuple[str, str]:
    if not record["display_row_exists"]:
        return "Results_Display", "display row missing"
    if not record["artifact_index_entry_exists"]:
        return "artifact_index", "display row omitted from artifact index"
    if not record["sdf_file_exists"]:
        return "MCS_SDF", "SDF artifact missing"
    if not record["svg_file_exists"]:
        return "MCS_SVG", "SVG artifact missing"
    if not str(record["api_sdf_status"]).startswith("200"):
        return "api_sdf", "SDF route not resolvable"
    if not str(record["api_svg_status"]).startswith("200"):
        return "api_svg", "SVG route not resolvable"
    if not str(record["api_ligand_props_status"]).startswith("200"):
        return "api_ligand_props", "ligand properties route failed"
    if ":Ligand properties unavailable" in str(record["api_ligand_props_status"]):
        return "ligand_props", "properties unavailable for row"
    return "none", "artifacts and routes resolve"


def build_report(job_dir: Path, *, only_mapped: bool = False) -> Dict[str, Any]:
    job_id = job_dir.name
    display_fp = find_results_display(job_dir)
    display = read_csv(display_fp) if display_fp else None
    if display is None or display.empty:
        raise SystemExit(f"Results_Display.csv not found for {job_id}")

    alias_map = build_alias_map(job_dir)
    artifact_index = load_artifact_index(job_id)
    index_rows = artifact_index.get("rows") or []
    index_lookup = {
        (
            norm_str(row.get("pdb"), lower=True),
            norm_str(row.get("chain"), upper=True),
            norm_str(row.get("ligand"), upper=True),
            norm_resid(row.get("resid")),
        ): row
        for row in index_rows
    }

    sdf_roots = find_artifact_roots(job_dir, "MCS_SDF")
    svg_roots = find_artifact_roots(job_dir, "MCS_SVG")

    rows: List[Dict[str, Any]] = []
    for _, display_row in display.iterrows():
        row = display_row.to_dict()
        pdb = norm_str(row.get("pdb_id") or row.get("pdb"), lower=True)
        chain = norm_str(row.get("Chain") or row.get("chain") or "A", upper=True)
        ligandx = norm_str(row.get("Warhead") or row.get("Ligand_Resolved") or row.get("ligand"), upper=True)
        resid = norm_resid(row.get("Residue_ID") or row.get("resid"))
        ligand5 = norm_str(row.get("Warhead_5") or row.get("Ligand5_Resolved"), upper=True)
        if not ligand5:
            ligand5 = alias_map.get((pdb, ligandx), "")

        mapped = bool(Axx_RE.match(ligandx) and ligand5)
        if only_mapped and not mapped:
            continue

        aliases = candidate_aliases(row, ligand5)
        sdf_path = find_best_artifact(sdf_roots, [".sdf"], pdb, chain, aliases, resid)
        svg_path = find_best_artifact(svg_roots, ["_exposed.svg", "_plain.svg"], pdb, chain, aliases, resid)
        index_row = index_lookup.get((pdb, chain, ligandx, resid))
        statuses = route_statuses(job_id, pdb, chain, ligandx, resid)

        record = {
            "pdb_id": pdb,
            "chain": chain,
            "residue_id": resid,
            "ligandX": ligandx,
            "ligand5": ligand5,
            "display_row_exists": True,
            "artifact_index_entry_exists": bool(index_row),
            "artifact_index_svg_ok": bool(index_row and ((index_row.get("svg") or {}).get("ok"))),
            "artifact_index_sdf_ok": bool(index_row and ((index_row.get("sdf") or {}).get("ok"))),
            "sdf_file_exists": bool(sdf_path),
            "sdf_filename": sdf_path.name if sdf_path else "",
            "svg_file_exists": bool(svg_path),
            "svg_filename": svg_path.name if svg_path else "",
            "api_sdf_status": statuses["api_sdf_status"],
            "api_svg_status": statuses["api_svg_status"],
            "api_ligand_props_status": statuses["api_ligand_props_status"],
            "artifact_index_svg_url": ((index_row or {}).get("svg") or {}).get("exposed_url", ""),
            "artifact_index_sdf_url": ((index_row or {}).get("sdf") or {}).get("url", ""),
            "route_sdf": f"/api/sdf/{job_id}/{pdb}/{chain}/{ligandx}" + (f"?resid={resid}" if resid else ""),
            "route_svg": f"/api/svg/{job_id}/{pdb}/{chain}/{ligandx}" + (f"?resid={resid}" if resid else ""),
        }
        stage, reason = first_missing_stage(record)
        record["first_missing_stage"] = stage
        record["likely_reason"] = reason
        rows.append(record)

    return {
        "job_id": job_id,
        "job_dir": str(job_dir),
        "results_display": str(display_fp) if display_fp else "",
        "5charmap": str(find_5charmap(job_dir) or ""),
        "artifact_index_ok": bool(artifact_index.get("ok")),
        "mapped_rows": sum(1 for row in rows if row.get("ligand5")),
        "rows": rows,
    }


def print_table(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print("No matching rows.")
        return
    df = pd.DataFrame(rows)
    cols = [
        "pdb_id",
        "chain",
        "residue_id",
        "ligandX",
        "ligand5",
        "sdf_file_exists",
        "svg_file_exists",
        "api_sdf_status",
        "api_svg_status",
        "api_ligand_props_status",
        "first_missing_stage",
    ]
    print(df[cols].to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit result artifact routability for a Warhead Hunter job.")
    parser.add_argument("--job-id")
    parser.add_argument("--job-dir")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--only-mapped", action="store_true")
    args = parser.parse_args()

    job_dir = resolve_job_dir(args.job_id, args.job_dir)
    report = build_report(job_dir, only_mapped=args.only_mapped)

    if args.json:
        print(json.dumps(report, indent=2))
        return

    print(f"job_id={report['job_id']} rows={len(report['rows'])} mapped_rows={report['mapped_rows']}")
    print(f"results_display={report['results_display']}")
    print(f"5charmap={report['5charmap']}")
    print_table(report["rows"])


if __name__ == "__main__":
    main()
