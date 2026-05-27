from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def normalize_resid(value: Any) -> str:
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


def normalize_sdf_key(pdb: Any, chain: Any, ligand: Any, resid: Any = "") -> Tuple[str, str, str, str]:
    return (
        str(pdb or "").strip().lower(),
        str(chain or "").strip().upper(),
        str(ligand or "").strip().upper(),
        normalize_resid(resid),
    )


def row_sdf_key(row: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return normalize_sdf_key(
        row.get("pdb_id") or row.get("pdb"),
        row.get("Chain") or row.get("chain") or "A",
        row.get("Ligand_Resolved") or row.get("Warhead") or row.get("ligand") or row.get("Ligand"),
        row.get("Residue_ID") or row.get("residue_id") or row.get("resid") or "",
    )


def expected_mcs_sdf_filename(pdb: Any, chain: Any, ligand: Any, resid: Any) -> str:
    pdb_n, chain_n, ligand_n, resid_n = normalize_sdf_key(pdb, chain, ligand, resid)
    return f"{pdb_n}_{chain_n}_{ligand_n}_{resid_n}.sdf"


def mcs_sdf_roots(job_dir: Path) -> List[Path]:
    return [
        job_dir / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF",
        job_dir / "MCS_Output" / "MCS_SDF",
    ]


def legacy_sdf_roots(job_dir: Path) -> List[Path]:
    return [
        job_dir / "TARGET_RESULTS" / "LIGAND_SDF",
        job_dir / "LIGAND_SDF",
    ]


def all_sdf_roots(job_dir: Path) -> List[Path]:
    return mcs_sdf_roots(job_dir) + legacy_sdf_roots(job_dir)


def _is_under(base: Path, path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(base.resolve())
    except AttributeError:
        base_s = str(base.resolve())
        path_s = str(path.resolve())
        return path_s == base_s or path_s.startswith(base_s + "/")
    except Exception:
        return False


def _sample_sdfs(roots: Iterable[Path], limit: int = 20) -> List[str]:
    files: List[str] = []
    for root in roots:
        if not root.exists():
            continue
        for fp in sorted(root.rglob("*.sdf")):
            files.append(str(fp))
            if len(files) >= limit:
                return files
    return files


def _iter_sdfs(roots: Iterable[Path], job_dir: Path) -> List[Path]:
    files: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for fp in sorted(root.rglob("*.sdf")):
            if _is_under(job_dir, fp):
                files.append(fp.resolve())
    return files


def _mcs_match_parts(path: Path, pdb: str, chain: str, ligand: str) -> Optional[str]:
    parts = path.stem.split("_")
    if len(parts) < 4:
        return None
    if parts[0].lower() != pdb.lower():
        return None
    if parts[1].upper() != chain.upper():
        return None
    if parts[2].upper() != ligand.upper():
        return None
    return normalize_resid("_".join(parts[3:]))


def infer_residue_from_results(job_dir: Path, pdb: Any, chain: Any, ligand: Any) -> str:
    pdb_n, chain_n, ligand_n, _ = normalize_sdf_key(pdb, chain, ligand)
    for path in [
        job_dir / "Results_Display.csv",
        job_dir / "TARGET_RESULTS" / "Results_Display.csv",
        job_dir / "Resolved_SASA_Summary.csv",
        job_dir / "TARGET_RESULTS" / "Resolved_SASA_Summary.csv",
        job_dir / "Ligand_3D_Atoms.csv",
        job_dir / "TARGET_RESULTS" / "Ligand_3D_Atoms.csv",
    ]:
        if not path.exists():
            continue
        try:
            with path.open(newline="", encoding="utf-8", errors="replace") as handle:
                for row in csv.DictReader(handle):
                    rpdb, rchain, rligand, rresid = row_sdf_key(row)
                    if (rpdb, rchain, rligand) == (pdb_n, chain_n, ligand_n) and rresid:
                        return rresid
        except Exception:
            continue
    return ""


def _diagnostics(
    job_dir: Path,
    roots: List[Path],
    pdb: str,
    chain: str,
    ligand: str,
    resid: str,
    candidates: List[Path],
    searched_paths: List[str],
    ambiguous: bool = False,
) -> Dict[str, Any]:
    return {
        "expected_keys": {"pdb": pdb, "chain": chain, "ligand": ligand, "residue_id": resid},
        "searched_directories": [str(root) for root in roots],
        "searched_paths": searched_paths,
        "expected_prefix": f"{pdb}_{chain}_{ligand}_",
        "query_resid": resid,
        "mcs_sdf_dir_exists": any(root.exists() for root in mcs_sdf_roots(job_dir)),
        "legacy_sdf_dir_exists": any(root.exists() for root in legacy_sdf_roots(job_dir)),
        "sample_sdf_files": _sample_sdfs(roots),
        "matching_candidates": [str(fp) for fp in candidates[:20]],
        "ambiguous": ambiguous,
    }


def resolve_sdf_path(
    job_dir: Path,
    pdb: Any,
    chain: Any,
    ligand: Any,
    resid: Any = "",
) -> Tuple[Optional[Path], Dict[str, Any]]:
    pdb_n, chain_n, ligand_n, resid_n = normalize_sdf_key(pdb, chain, ligand, resid)
    roots = all_sdf_roots(job_dir)
    searched_paths: List[str] = []

    query_resid = resid_n
    inferred_resid = "" if query_resid else infer_residue_from_results(job_dir, pdb_n, chain_n, ligand_n)
    effective_resid = query_resid or inferred_resid

    candidates = []
    if effective_resid:
        expected_name = expected_mcs_sdf_filename(pdb_n, chain_n, ligand_n, effective_resid)
        for root in mcs_sdf_roots(job_dir):
            candidate = root / expected_name
            searched_paths.append(str(candidate))
            if candidate.exists() and _is_under(job_dir, candidate):
                return candidate.resolve(), _diagnostics(
                    job_dir, roots, pdb_n, chain_n, ligand_n, effective_resid, [candidate], searched_paths
                )

    for fp in _iter_sdfs(mcs_sdf_roots(job_dir), job_dir):
        file_resid = _mcs_match_parts(fp, pdb_n, chain_n, ligand_n)
        if file_resid is None:
            continue
        if query_resid and file_resid != normalize_resid(query_resid):
            continue
        candidates.append(fp)

    searched_paths.extend(str(root / f"{pdb_n}_{chain_n}_{ligand_n}_*.sdf") for root in mcs_sdf_roots(job_dir))
    candidates = sorted(set(candidates))
    if len(candidates) == 1:
        return candidates[0], _diagnostics(job_dir, roots, pdb_n, chain_n, ligand_n, effective_resid, candidates, searched_paths)
    if len(candidates) > 1:
        return candidates[0], _diagnostics(
            job_dir, roots, pdb_n, chain_n, ligand_n, effective_resid, candidates, searched_paths, ambiguous=True
        )

    # Legacy fallback: older repaired jobs may have residue-less LIGAND_SDF files.
    legacy_name = f"{pdb_n}_{chain_n}_{ligand_n}.sdf"
    legacy_candidates: List[Path] = []
    for root in legacy_sdf_roots(job_dir):
        candidate = root / legacy_name
        searched_paths.append(str(candidate))
        if candidate.exists() and _is_under(job_dir, candidate):
            legacy_candidates.append(candidate.resolve())
    if legacy_candidates:
        return sorted(legacy_candidates)[0], _diagnostics(
            job_dir, roots, pdb_n, chain_n, ligand_n, effective_resid, legacy_candidates, searched_paths
        )

    return None, _diagnostics(job_dir, roots, pdb_n, chain_n, ligand_n, effective_resid, candidates, searched_paths)
