from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Iterable


WAR_PDB_ALLOWED_PREFIXES = ("TARGET_RESULTS/WAR_PDB", "WAR_PDB")


def _clean_text(value) -> str:
    return str(value or "").strip().replace("\\", "/")


def is_under(base: Path, path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(base.resolve())
    except Exception:
        return False


def relative_to_job_dir(job_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(job_dir.resolve()).as_posix()
    except Exception:
        return ""


def _normalize_prefixes(prefixes: Iterable[str]) -> list[tuple[str, list[str]]]:
    normalized: list[tuple[str, list[str]]] = []
    for prefix in prefixes:
        text = _clean_text(prefix).strip("/")
        if not text:
            continue
        normalized.append((text, text.lower().split("/")))
    return normalized


def extract_relative_artifact_path(stored_path, allowed_prefixes: Iterable[str]) -> str:
    text = _clean_text(stored_path)
    if not text:
        return ""

    prefixes = _normalize_prefixes(allowed_prefixes)
    pure = PurePosixPath(text)
    if not pure.is_absolute() and ".." not in pure.parts:
        candidate = "/".join(part for part in pure.parts if part not in {"", "."})
        candidate_lower = candidate.lower()
        for prefix, _prefix_parts in prefixes:
            prefix_lower = prefix.lower()
            if candidate_lower == prefix_lower or candidate_lower.startswith(prefix_lower + "/"):
                return candidate

    parts = [part for part in text.split("/") if part and part != "."]
    lower_parts = [part.lower() for part in parts]
    for _prefix, prefix_parts in prefixes:
        prefix_len = len(prefix_parts)
        for idx in range(0, len(parts) - prefix_len + 1):
            if lower_parts[idx : idx + prefix_len] != prefix_parts:
                continue
            candidate_parts = parts[idx:]
            if ".." in candidate_parts:
                continue
            return "/".join(candidate_parts)
    return ""


def resolve_job_artifact(
    job_dir: Path,
    artifact_relative_path,
    *,
    allowed_prefixes: Iterable[str],
) -> Path | None:
    text = _clean_text(artifact_relative_path)
    if not text:
        return None

    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts:
        return None

    rel = "/".join(part for part in pure.parts if part not in {"", "."})
    rel_lower = rel.lower()
    prefixes = [prefix for prefix, _parts in _normalize_prefixes(allowed_prefixes)]
    if not any(rel_lower == prefix.lower() or rel_lower.startswith(prefix.lower() + "/") for prefix in prefixes):
        return None

    resolved = (job_dir / Path(*pure.parts)).resolve()
    if not is_under(job_dir, resolved):
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    return resolved


def canonical_war_pdb_relative_path(job_dir: Path, actual_path: Path) -> str:
    resolved = actual_path.resolve()
    target_root = (job_dir / "TARGET_RESULTS" / "WAR_PDB").resolve()
    raw_root = (job_dir / "WAR_PDB").resolve()

    if is_under(target_root, resolved):
        return relative_to_job_dir(job_dir, resolved)

    if is_under(raw_root, resolved):
        subpath = resolved.relative_to(raw_root)
        mirrored = (job_dir / "TARGET_RESULTS" / "WAR_PDB" / subpath).resolve()
        if mirrored.exists() and mirrored.is_file() and is_under(job_dir, mirrored):
            return relative_to_job_dir(job_dir, mirrored)
        return relative_to_job_dir(job_dir, resolved)

    if is_under(job_dir, resolved):
        return relative_to_job_dir(job_dir, resolved)
    return ""
