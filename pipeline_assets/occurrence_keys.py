from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def normalize_target(value: Any) -> str:
    return str(value or "").strip()


def normalize_pdb(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_chain(value: Any) -> str:
    return str(value or "").strip().upper()


def normalize_ligand(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip().strip("'").strip('"').strip()
        if inner:
            text = inner
    return text.upper()


def normalize_residue_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        as_float = float(text)
        if as_float.is_integer():
            return str(int(as_float))
    except Exception:
        pass
    return text


OccurrenceKey = Tuple[str, str, str, str, str]
AssetKey = Tuple[str, str, str, str]


def occurrence_key(
    target: Any,
    pdb_id: Any,
    chain: Any,
    ligand: Any,
    residue_id: Any,
) -> OccurrenceKey:
    return (
        normalize_target(target),
        normalize_pdb(pdb_id),
        normalize_chain(chain),
        normalize_ligand(ligand),
        normalize_residue_id(residue_id),
    )


def asset_key(pdb_id: Any, chain: Any, ligand: Any, residue_id: Any) -> AssetKey:
    return (
        normalize_pdb(pdb_id),
        normalize_chain(chain),
        normalize_ligand(ligand),
        normalize_residue_id(residue_id),
    )


def occurrence_key_from_row(row: Dict[str, Any]) -> OccurrenceKey:
    return occurrence_key(
        row.get("Target") or row.get("target"),
        row.get("pdb_id") or row.get("pdb"),
        row.get("Chain") or row.get("chain"),
        row.get("Warhead") or row.get("Ligand") or row.get("ligand") or row.get("Ligand_Resolved"),
        row.get("Residue_ID") or row.get("residue_id") or row.get("resid"),
    )


def asset_key_from_occurrence(key: OccurrenceKey) -> AssetKey:
    return (key[1], key[2], key[3], key[4])


def parse_war_pdb_filename(name: str) -> Optional[Tuple[str, str, str]]:
    path = Path(name)
    stem = path.stem
    parts = stem.split("_")
    if len(parts) < 3:
        return None
    pdb_id = normalize_pdb(parts[0])
    chain_token = str(parts[1]).strip()
    ligand = normalize_ligand("_".join(parts[2:]))
    if not pdb_id or not chain_token or not ligand:
        return None
    return pdb_id, chain_token, ligand
