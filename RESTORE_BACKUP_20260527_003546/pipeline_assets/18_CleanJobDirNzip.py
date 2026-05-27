#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
18_CleanJobDirNzip.py
----------------------------------------
Safe post-processing helper for Warhead Hunter job directories.

Core design goals:
  - default to dry-run
  - preserve uncertain artifacts
  - trace downstream usage before classifying files
  - build a curated public bundle for API/docs workflows
  - avoid destructive cleanup unless explicitly requested
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]

NEVER_DELETE_NAMES = {
    "job.log",
    "job_metadata.json",
    "input.csv",
    "Protein_Data.csv",
    "job_result_manifest.json",
    "cleanup_report.md",
    "cleanup_deleted_files.json",
}

FINAL_DELIVERABLE_NAMES = {
    "Results_Display.csv",
    "Resolved_SASA_Summary.csv",
    "Resolved_SASA_Summary.tsv",
    "Ligand_Metadata.csv",
    "Warhead_SASA_summary.csv",
    "Warhead_SASA_atoms.csv",
    "3DSASAmapped.csv",
    "Ligand_3D_Atoms_with_SASA.csv",
    "Ligand_3D_Atoms.csv",
    "Ligand_MCS_Map.csv",
    "Ligand_MCS_SASA_ALL_ATOMS.csv",
    "Ligand_PDB_Index.csv",
}

PIPELINE_SCRIPT_NAMES = {
    "1_GRABBER.py",
    "2_SQchk.py",
    "3_PDBmkr.py",
    "4_PDBfxr.py",
    "5_PDBcln.py",
    "6_SASA.py",
    "7_metadata.py",
    "8_scaffold.py",
    "9_2Dmapping.py",
    "10_2DmappingExtraction.py",
    "11_mcsMatcher.py",
    "12_Results.py",
    "13_mcsSASA_svg.py",
    "14_SVGmkr.py",
    "15_ResultsMerged.py",
    "16_ResultsDisplay.py",
    "17_obabelSDF.py",
    "18_CleanJobDirNzip.py",
    "NON_LIGAND_CODES.py",
}

PIPELINE_ASSET_NAMES = {
    "Components-smiles-stereo-oe.smi",
}

REBUILDABLE_INTERMEDIATE_NAMES = {
    "filtered_data.csv",
    "chain_similarity.csv",
}

DEBUG_OR_FAILURE_NAMES = {
    "Dropped_Ion_Rows.csv",
    "Ligand_Metadata_Failures.csv",
    "Skip4.txt",
}

ARCHIVE_ONLY_NAMES = {
    "5CharMAP.csv",
    "ChainRenameMAP.csv",
}

DELETE_SUFFIXES = (".tmp", ".bak", ".old", ".cache")
HIDDEN_DELETE_NAMES = {".ds_store"}
SAFE_SKIP_DIRS = {".git", "__pycache__"}

PUBLIC_BUNDLE_KEEP_NAMES = {
    "Protein_Data.csv",
    "job_metadata.json",
    "job_result_manifest.json",
    "cleanup_report.md",
}

VERIFIED_FOLDER_POLICIES = {
    "TARGET_RESULTS/": "Final result collection directory assembled by 12_Results.py and consumed by result-serving helpers.",
    "TARGET_RESULTS/WAR_PDB/": "Bound PDB directory is served by the gallery/API and handoff routes.",
    "WAR_PDB/": "Bound PDB directory is served by the gallery/API and Results_Display generation.",
    "TARGET_RESULTS/MCS_Output/": "Mapped ligand outputs are served by SVG/SDF API routes and result views.",
    "MCS_Output/": "Mapped ligand outputs are served by SVG/SDF API routes and result views.",
    "TARGET_RESULTS/LIGAND_SDF/": "Ligand SDF directory is served by /api/sdf helpers.",
    "LIGAND_SDF/": "Ligand SDF directory is served by /api/sdf helpers.",
    "Target_Table/": "Target_Table is consumed by downstream result-generation scripts such as 11_mcsMatcher.py.",
}

STRONG_GALLERY_PREFIXES = (
    "TARGET_RESULTS/",
    "TARGET_RESULTS/WAR_PDB/",
    "WAR_PDB/",
    "TARGET_RESULTS/MCS_Output/",
    "MCS_Output/",
    "TARGET_RESULTS/LIGAND_SDF/",
    "LIGAND_SDF/",
)

TRACE_SOURCE_FILES = [
    Path("app.py"),
    Path("routes.py"),
    *sorted(Path("api").glob("*.py")),
    *sorted(Path("templates").glob("*.html")),
    *sorted(Path("static/js").glob("*.js")),
    *sorted(Path("static/css").glob("*.css")),
    Path("pipeline_assets/16_ResultsDisplay.py"),
    Path("pipeline_assets/15_ResultsMerged.py"),
    Path("pipeline_assets/12_Results.py"),
    Path("pipeline_assets/11_mcsMatcher.py"),
]

GENERATED_REFERENCE_NAMES = {
    "Results_Display.csv",
    "summary.json",
    "job_result_manifest.json",
    "Resolved_SASA_Summary.csv",
    "Resolved_SASA_Summary.tsv",
    "Warhead_SASA_summary.csv",
    "Warhead_SASA_atoms.csv",
    "Ligand_Metadata.csv",
    "Ligand_3D_Atoms.csv",
    "Ligand_3D_Atoms_with_SASA.csv",
    "Ligand_MCS_Map.csv",
    "Ligand_MCS_SASA_ALL_ATOMS.csv",
}


@dataclass
class DownstreamEvidence:
    source: str
    file: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "source": self.source,
            "file": self.file,
            "reason": self.reason,
        }


@dataclass
class FileRecord:
    relative_path: str
    name: str
    size_bytes: int
    modified_at: str
    extension: str
    kind: str
    category: str
    included_in_public_zip: bool
    reason: str
    downstream_used: bool
    downstream_evidence: List[DownstreamEvidence] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def classify_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdb":
        return "pdb"
    if suffix == ".cif":
        return "cif"
    if suffix == ".sdf":
        return "sdf"
    if suffix == ".svg":
        return "svg"
    if suffix in {".csv", ".tsv"}:
        return "csv"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".json":
        return "json"
    if suffix == ".log":
        return "log"
    if suffix == ".py":
        return "script"
    return "other"


def source_kind_for_path(path: Path) -> str:
    posix = path.as_posix()
    if posix == "app.py" or posix == "routes.py":
        return "route"
    if posix.startswith("api/"):
        return "api"
    if posix.startswith("templates/"):
        return "template"
    if posix.startswith("static/js/"):
        return "static_js"
    if posix.startswith("static/css/"):
        return "static_css"
    if posix.startswith("pipeline_assets/"):
        return "pattern"
    return "pattern"


def looks_completed(job_dir: Path) -> Tuple[bool, str]:
    meta = read_json(job_dir / "job_metadata.json") or {}
    if str(meta.get("status", "")).lower() == "completed":
        return True, "job_metadata.json status=completed"

    candidates = [
        job_dir / "TARGET_RESULTS" / "Results_Display.csv",
        job_dir / "Results_Display.csv",
        job_dir / "TARGET_RESULTS" / "Resolved_SASA_Summary.csv",
        job_dir / "TARGET_RESULTS" / "Resolved_SASA_Summary.tsv",
        job_dir / "TARGET_RESULTS" / "MCS_Output",
    ]
    for path in candidates:
        if path.exists():
            return True, f"found result artifact: {path.relative_to(job_dir)}"
    return False, "no completion marker found"


def resolve_job_dirs(args) -> List[Path]:
    if args.all_completed:
        root = Path(args.jobs_root).resolve()
        out: List[Path] = []
        if not root.exists():
            return out
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            completed, _reason = looks_completed(child)
            if completed:
                out.append(child.resolve())
        return out

    if args.job_dir:
        return [Path(args.job_dir).resolve()]

    if args.job_id:
        return [(Path(args.jobs_root) / args.job_id).resolve()]

    raise SystemExit("Provide --job-id, --job-dir, or --all-completed")


def is_hidden_or_cache(rel_parts: Tuple[str, ...]) -> bool:
    return any(part.startswith(".") for part in rel_parts) or any(part in SAFE_SKIP_DIRS for part in rel_parts)


def collect_repo_trace_texts() -> Dict[str, Tuple[str, str]]:
    texts: Dict[str, Tuple[str, str]] = {}
    for rel_path in TRACE_SOURCE_FILES:
        full = REPO_ROOT / rel_path
        if not full.exists() or not full.is_file():
            continue
        texts[rel_path.as_posix()] = (source_kind_for_path(rel_path), read_text(full))
    return texts


def collect_job_reference_texts(job_dir: Path) -> Dict[str, Tuple[str, str]]:
    refs: Dict[str, Tuple[str, str]] = {}
    for fp in sorted(job_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(job_dir).as_posix()
        if rel.startswith("bundles/") or rel.startswith("_archive/"):
            continue
        if any(part in SAFE_SKIP_DIRS for part in fp.relative_to(job_dir).parts):
            continue
        suffix = fp.suffix.lower()
        if suffix in {".html", ".htm"}:
            refs[rel] = ("generated_html", read_text(fp))
        elif fp.name in GENERATED_REFERENCE_NAMES or suffix in {".csv", ".tsv", ".json"}:
            refs[rel] = ("csv_reference" if suffix in {".csv", ".tsv"} else "json_reference", read_text(fp))
    return refs


def dedupe_evidence(items: Iterable[DownstreamEvidence]) -> List[DownstreamEvidence]:
    seen = set()
    out: List[DownstreamEvidence] = []
    for item in items:
        key = (item.source, item.file, item.reason)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def gather_text_search_evidence(
    rel: str,
    name: str,
    source_texts: Dict[str, Tuple[str, str]],
    *,
    skip_self: bool = False,
) -> List[DownstreamEvidence]:
    evidence: List[DownstreamEvidence] = []
    needles = [needle for needle in (rel, name) if needle]
    for source_file, (source_kind, text) in source_texts.items():
        if skip_self and source_file == rel:
            continue
        for needle in needles:
            if needle in text:
                reason = f"Referenced by source text via '{needle}'."
                evidence.append(DownstreamEvidence(source_kind, source_file, reason))
                break
        if len(evidence) >= 10:
            break
    return evidence


def folder_policy_evidence(rel: str) -> List[DownstreamEvidence]:
    evidence: List[DownstreamEvidence] = []
    for prefix, reason in VERIFIED_FOLDER_POLICIES.items():
        if rel.startswith(prefix):
            evidence.append(DownstreamEvidence("folder_policy", prefix.rstrip("/"), reason))
    return evidence


def specific_filename_evidence(name: str) -> List[DownstreamEvidence]:
    reasons = {
        "Results_Display.csv": "Used by gallery/result views as the primary display manifest.",
        "Resolved_SASA_Summary.csv": "Used by route helpers and downstream result display generation.",
        "Resolved_SASA_Summary.tsv": "Used by route helpers and downstream result display generation.",
        "Ligand_Metadata.csv": "Used by ligand property helpers and Results_Display generation.",
        "Warhead_SASA_atoms.csv": "Used by SASA overlay/atom mapping helpers and downstream merges.",
        "Ligand_MCS_Map.csv": "Used by SVG selection and downstream atom mapping helpers.",
        "Ligand_MCS_SASA_ALL_ATOMS.csv": "Used by SASA API helpers.",
    }
    if name in reasons:
        return [DownstreamEvidence("pattern", name, reasons[name])]
    return []


def compute_cleaned_pdb_exists(job_dir: Path) -> bool:
    for fp in job_dir.rglob("*.pdb"):
        rel = fp.relative_to(job_dir).as_posix()
        if rel.startswith("_archive/") or rel.startswith("bundles/"):
            continue
        if rel.startswith("TARGET_RESULTS/") or rel.startswith("WAR_PDB/") or rel.startswith("Target_Table/") or rel.startswith("MCS_Output/"):
            return True
    return False


def build_downstream_context(job_dir: Path, trace_downstream: bool) -> Dict[str, object]:
    repo_texts = collect_repo_trace_texts() if trace_downstream else {}
    job_texts = collect_job_reference_texts(job_dir) if trace_downstream else {}
    return {
        "repo_texts": repo_texts,
        "job_texts": job_texts,
        "cleaned_pdb_exists": compute_cleaned_pdb_exists(job_dir),
    }


def trace_downstream_usage(job_dir: Path, fp: Path, ctx: Dict[str, object]) -> List[DownstreamEvidence]:
    rel = fp.relative_to(job_dir).as_posix()
    name = fp.name
    evidence: List[DownstreamEvidence] = []

    evidence.extend(folder_policy_evidence(rel))
    evidence.extend(specific_filename_evidence(name))
    evidence.extend(gather_text_search_evidence(rel, name, ctx["repo_texts"]))  # type: ignore[index]
    evidence.extend(gather_text_search_evidence(rel, name, ctx["job_texts"], skip_self=True))  # type: ignore[index]

    if rel in {"job_result_manifest.json", "cleanup_report.md"}:
        evidence.append(DownstreamEvidence("pattern", rel, "Job-level manifest/report is exposed to API clients and packaging workflows."))

    return dedupe_evidence(evidence)


def should_treat_as_final_bundle_candidate(rel: str, kind: str, downstream_used: bool) -> bool:
    if kind in {"pdb", "sdf", "svg"} and downstream_used:
        return True
    if kind == "html" and downstream_used:
        return True
    if kind == "csv" and (rel.endswith("Results_Display.csv") or rel.endswith("Resolved_SASA_Summary.csv") or rel.endswith("Resolved_SASA_Summary.tsv")):
        return True
    if rel.endswith("Warhead_SASA_summary.csv") or rel.endswith("Warhead_SASA_atoms.csv"):
        return True
    if rel.endswith("Ligand_Metadata.csv") or rel.endswith("Ligand_3D_Atoms.csv") or rel.endswith("Ligand_3D_Atoms_with_SASA.csv"):
        return True
    if rel.endswith("Ligand_PDB_Index.csv") or rel.endswith("Ligand_MCS_Map.csv") or rel.endswith("Ligand_MCS_SASA_ALL_ATOMS.csv"):
        return True
    if any(rel.startswith(prefix) for prefix in STRONG_GALLERY_PREFIXES) and kind in {"pdb", "sdf", "svg", "csv", "html", "json"}:
        return True
    return False


def classify_record(
    job_dir: Path,
    fp: Path,
    *,
    include_raw: bool,
    current_public_bundle_name: str,
    current_archive_bundle_name: str,
    ctx: Dict[str, object],
) -> FileRecord:
    rel = fp.relative_to(job_dir).as_posix()
    name = fp.name
    suffix = fp.suffix.lower()
    kind = classify_kind(fp)
    size_bytes = fp.stat().st_size
    modified_at = datetime.fromtimestamp(fp.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    rel_parts = fp.relative_to(job_dir).parts

    evidence = trace_downstream_usage(job_dir, fp, ctx)
    downstream_used = bool(evidence)
    cleaned_pdb_exists = bool(ctx["cleaned_pdb_exists"])

    category = "UNKNOWN_KEEP"
    reason = "Unrecognized artifact pattern preserved conservatively."
    include_in_public_zip = False

    if name.lower() in HIDDEN_DELETE_NAMES:
        category = "DELETE_CANDIDATE"
        reason = "Hidden system file."
    elif is_hidden_or_cache(rel_parts):
        category = "CACHE_TEMP"
        reason = "Hidden/cache artifact."
    elif suffix in DELETE_SUFFIXES or name.endswith(DELETE_SUFFIXES):
        category = "DELETE_CANDIDATE"
        reason = "Temporary or backup file suffix."
    elif rel.startswith("bundles/"):
        if name == current_public_bundle_name or name == current_archive_bundle_name:
            category = "KEEP"
            reason = "Current generated bundle artifact."
        else:
            category = "DELETE_CANDIDATE"
            reason = "Older generated bundle candidate."
    elif rel.startswith("_archive/"):
        category = "ARCHIVE_ONLY"
        reason = "Archived copy of intermediate or execution asset."
    elif name in NEVER_DELETE_NAMES:
        category = "KEEP"
        reason = "Core job metadata or cleanup bookkeeping artifact."
        include_in_public_zip = name in PUBLIC_BUNDLE_KEEP_NAMES
    elif name in PIPELINE_SCRIPT_NAMES or name in PIPELINE_ASSET_NAMES:
        category = "ARCHIVE_ONLY"
        reason = "Copied pipeline execution asset."
    elif kind == "cif":
        if downstream_used:
            category = "KEEP"
            reason = "Coordinate CIF appears to be referenced downstream and is preserved."
        elif cleaned_pdb_exists:
            category = "ARCHIVE_ONLY"
            reason = "Coordinate CIF is not referenced downstream and cleaned PDB outputs exist."
        else:
            category = "UNKNOWN_KEEP"
            reason = "Coordinate CIF preserved because cleaned downstream replacements were not confirmed."
        include_in_public_zip = False
    elif name == "CIFdata.csv":
        if downstream_used:
            category = "KEEP"
            reason = "CIFdata.csv appears to be referenced downstream."
        else:
            category = "UNKNOWN_KEEP"
            reason = "CIFdata.csv preserved conservatively until rebuildability and downstream use are confirmed."
    elif name in FINAL_DELIVERABLE_NAMES:
        category = "BUNDLE"
        reason = "Final scientific deliverable or gallery/API-facing result table."
        include_in_public_zip = True
    elif should_treat_as_final_bundle_candidate(rel, kind, downstream_used):
        category = "BUNDLE"
        reason = "Final downstream-facing structural, tabular, or viewer output."
        include_in_public_zip = kind != "cif"
    elif name in REBUILDABLE_INTERMEDIATE_NAMES:
        category = "UNKNOWN_KEEP" if downstream_used else "REBUILDABLE_INTERMEDIATE"
        reason = "Intermediate table retained conservatively." if downstream_used else "Intermediate table appears rebuildable from earlier pipeline stages."
    elif name in ARCHIVE_ONLY_NAMES:
        category = "UNKNOWN_KEEP" if downstream_used else "ARCHIVE_ONLY"
        reason = "Alias/provenance table is still referenced downstream." if downstream_used else "Mapping/provenance table useful for archive but not required in public bundle."
    elif name in DEBUG_OR_FAILURE_NAMES or suffix == ".err":
        category = "DEBUG_LOG"
        reason = "Debug, skip-list, or failure-oriented output."
    elif kind == "log":
        category = "KEEP" if name == "job.log" else "DEBUG_LOG"
        reason = "Execution log."
    elif kind == "json":
        category = "KEEP" if downstream_used else "UNKNOWN_KEEP"
        reason = "JSON artifact is referenced downstream." if downstream_used else "JSON artifact preserved conservatively."
    elif kind == "csv":
        category = "KEEP" if downstream_used else "UNKNOWN_KEEP"
        reason = "CSV artifact is referenced downstream." if downstream_used else "CSV artifact preserved conservatively."
    elif kind == "html":
        category = "BUNDLE" if downstream_used else "UNKNOWN_KEEP"
        reason = "Generated HTML viewer/page appears downstream-facing." if downstream_used else "HTML artifact preserved conservatively."
        include_in_public_zip = downstream_used
    elif kind in {"pdb", "sdf", "svg"}:
        category = "BUNDLE" if downstream_used else "UNKNOWN_KEEP"
        reason = "Prepared structure/map artifact is referenced downstream." if downstream_used else "Prepared structure/map artifact preserved conservatively."
        include_in_public_zip = downstream_used

    if include_raw and category in {"ARCHIVE_ONLY", "REBUILDABLE_INTERMEDIATE"} and kind != "cif":
        include_in_public_zip = True

    return FileRecord(
        relative_path=rel,
        name=name,
        size_bytes=size_bytes,
        modified_at=modified_at,
        extension=suffix,
        kind=kind,
        category=category,
        included_in_public_zip=include_in_public_zip,
        reason=reason,
        downstream_used=downstream_used,
        downstream_evidence=evidence,
    )


def scan_job(
    job_dir: Path,
    *,
    include_raw: bool,
    public_bundle_name: str,
    archive_bundle_name: str,
    trace_downstream: bool,
    verbose: bool = False,
) -> List[FileRecord]:
    ctx = build_downstream_context(job_dir, trace_downstream)
    records: List[FileRecord] = []
    for fp in sorted(job_dir.rglob("*")):
        if not fp.is_file():
            continue
        rec = classify_record(
            job_dir,
            fp,
            include_raw=include_raw,
            current_public_bundle_name=public_bundle_name,
            current_archive_bundle_name=archive_bundle_name,
            ctx=ctx,
        )
        records.append(rec)
        if verbose:
            flag = "used" if rec.downstream_used else "unused"
            print(f"[scan] {rec.category:24s} {flag:6s} {rec.relative_path}")
    return records


def bundle_paths(job_dir: Path, job_id: str, bundle_name: Optional[str] = None) -> Tuple[Path, Path]:
    bundles_dir = job_dir / "bundles"
    public_name = bundle_name or f"{job_id}_warhead_hunter_public_results.zip"
    archive_name = f"{job_id}_warhead_hunter_archive_full.zip"
    return bundles_dir / public_name, bundles_dir / archive_name


def build_manifest(
    job_dir: Path,
    job_id: str,
    records: List[FileRecord],
    public_zip: Optional[Path],
    archive_zip: Optional[Path],
    warnings: List[str],
) -> Dict:
    created_at = ""
    meta = read_json(job_dir / "job_metadata.json") or {}
    if meta:
        created_at = str(meta.get("created_at") or "")

    counts = Counter(rec.category for rec in records)
    return {
        "job_id": job_id,
        "created_at": created_at,
        "cleaned_at": now_iso(),
        "source": "18_CleanJobDirNzip.py",
        "job_dir": str(job_dir),
        "counts": {
            "total_files": len(records),
            "public_bundle_files": sum(1 for rec in records if rec.included_in_public_zip),
            "archive_only_files": counts.get("ARCHIVE_ONLY", 0),
            "delete_candidates": counts.get("DELETE_CANDIDATE", 0),
            "unknown_keep": counts.get("UNKNOWN_KEEP", 0),
            "downstream_used_files": sum(1 for rec in records if rec.downstream_used),
            "unused_files": sum(1 for rec in records if not rec.downstream_used),
        },
        "files": [
            {
                "relative_path": rec.relative_path,
                "name": rec.name,
                "size_bytes": rec.size_bytes,
                "modified_at": rec.modified_at,
                "extension": rec.extension,
                "kind": rec.kind,
                "category": rec.category,
                "included_in_public_zip": rec.included_in_public_zip,
                "reason": rec.reason,
                "downstream_used": rec.downstream_used,
                "downstream_evidence": [ev.to_dict() for ev in rec.downstream_evidence],
            }
            for rec in records
        ],
        "bundles": {
            "public_zip": str(public_zip.relative_to(job_dir)) if public_zip else "",
            "archive_zip": str(archive_zip.relative_to(job_dir)) if archive_zip else "",
        },
        "warnings": warnings,
    }


def write_manifest(job_dir: Path, manifest: Dict) -> Path:
    out = job_dir / "job_result_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=False), encoding="utf-8")
    return out


def can_delete_with_flags(rec: FileRecord) -> bool:
    if rec.category == "DELETE_CANDIDATE":
        return True
    if rec.kind == "cif" and not rec.downstream_used and rec.category in {"ARCHIVE_ONLY", "REBUILDABLE_INTERMEDIATE"}:
        return True
    return False


def build_group_rows(records: List[FileRecord]) -> List[Tuple[str, List[FileRecord]]]:
    groups: Dict[str, List[FileRecord]] = defaultdict(list)
    for rec in records:
        parts = rec.relative_path.split("/")
        key = parts[0] if len(parts) > 1 else rec.relative_path
        groups[key].append(rec)
    return sorted(groups.items(), key=lambda item: item[0].lower())


def write_cleanup_report(
    job_dir: Path,
    manifest: Dict,
    records: List[FileRecord],
    total_size_before: int,
    public_zip: Optional[Path],
    archive_zip: Optional[Path],
    archived_count: int,
    deleted: List[Dict],
    warnings: List[str],
) -> Path:
    public_size = public_zip.stat().st_size if public_zip and public_zip.exists() else 0
    archive_size = archive_zip.stat().st_size if archive_zip and archive_zip.exists() else 0
    unknown = [f for f in manifest["files"] if f["category"] == "UNKNOWN_KEEP"]
    delete_candidates = [f for f in manifest["files"] if f["category"] == "DELETE_CANDIDATE"]

    lines = [
        f"# Cleanup Report — {manifest['job_id']}",
        "",
        "## Summary",
        f"- Cleaned at: `{manifest['cleaned_at']}`",
        f"- Total size before: `{total_size_before}` bytes",
        f"- Public ZIP: `{manifest['bundles']['public_zip']}` ({public_size} bytes)" if public_zip else "- Public ZIP: not created",
        f"- Archive ZIP: `{manifest['bundles']['archive_zip']}` ({archive_size} bytes)" if archive_zip else "- Archive ZIP: not created",
        f"- Files included in public ZIP: `{manifest['counts']['public_bundle_files']}`",
        f"- Archive-only files detected: `{manifest['counts']['archive_only_files']}`",
        f"- Archived copies created: `{archived_count}`",
        f"- Delete candidates: `{len(delete_candidates)}`",
        f"- Unknown files preserved: `{len(unknown)}`",
        f"- Downstream-used files: `{manifest['counts']['downstream_used_files']}`",
        f"- Unused files: `{manifest['counts']['unused_files']}`",
        "",
        "## Warnings",
    ]
    if warnings:
        lines.extend([f"- {w}" for w in warnings])
    else:
        lines.append("- None")

    lines.extend([
        "",
        "## Downstream Usage Audit",
    ])
    for group_name, group_records in build_group_rows(records):
        used = any(rec.downstream_used for rec in group_records)
        in_zip = any(rec.included_in_public_zip for rec in group_records)
        can_archive = any(rec.category in {"ARCHIVE_ONLY", "REBUILDABLE_INTERMEDIATE", "DEBUG_LOG"} for rec in group_records)
        can_delete = any(can_delete_with_flags(rec) for rec in group_records)
        evidence_lines: List[str] = []
        seen_evidence = set()
        for rec in group_records:
            for ev in rec.downstream_evidence[:3]:
                key = (ev.source, ev.file, ev.reason)
                if key in seen_evidence:
                    continue
                seen_evidence.add(key)
                evidence_lines.append(f"{ev.source}: {ev.file} — {ev.reason}")
            if len(evidence_lines) >= 4:
                break
        lines.append(f"- `{group_name}`")
        lines.append(f"  used by gallery/API: {'yes' if used else 'no'}")
        lines.append(f"  included in public ZIP: {'yes' if in_zip else 'no'}")
        lines.append(f"  can archive: {'yes' if can_archive else 'no'}")
        lines.append(f"  can delete with explicit flags: {'yes' if can_delete else 'no'}")
        if evidence_lines:
            lines.append("  evidence:")
            for item in evidence_lines:
                lines.append(f"  - {item}")
        else:
            lines.append("  evidence: none found")

    lines.extend([
        "",
        "## Delete Candidates",
    ])
    if delete_candidates:
        lines.extend([f"- `{item['relative_path']}` — {item['reason']}" for item in delete_candidates[:80]])
    else:
        lines.append("- None")

    lines.extend([
        "",
        "## Unknown Files Preserved",
    ])
    if unknown:
        lines.extend([f"- `{item['relative_path']}` — {item['reason']}" for item in unknown[:80]])
    else:
        lines.append("- None")

    lines.extend([
        "",
        "## Next Recommended Cleanup Rules",
        "- Review UNKNOWN_KEEP artifacts after a real completed job audit and promote stable patterns only when route/template/API dependencies are confirmed.",
        "- Keep actual `.cif` coordinate files out of public bundles by default unless a downstream dependency is proven.",
        "- Prefer `--safe-package` in automation; reserve `--apply` cleanup for deliberate review.",
    ])

    out = job_dir / "cleanup_report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def build_bundle_readme(job_id: str, manifest: Dict) -> str:
    return "\n".join([
        f"Warhead Hunter Public Result Bundle — {job_id}",
        "",
        "This bundle contains cleaned/prepared, job-derived outputs intended for downstream inspection.",
        "Typical contents include final CSV summaries, cleaned ligand-bound PDB files, ligand SDF files,",
        "SVG atom maps, and job-level metadata relevant to API-driven retrieval workflows.",
        "",
        "This bundle intentionally excludes copied pipeline scripts, raw coordinate CIF files,",
        "and rebuildable intermediate clutter unless explicitly requested.",
        f"Generated from job_result_manifest.json at {manifest['cleaned_at']}.",
        "",
    ])


def make_public_zip(
    job_dir: Path,
    job_id: str,
    records: List[FileRecord],
    manifest_path: Path,
    report_path: Optional[Path],
    out_zip: Path,
) -> Path:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    selected = [rec for rec in records if rec.included_in_public_zip or rec.name in PUBLIC_BUNDLE_KEEP_NAMES]

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", build_bundle_readme(job_id, read_json(manifest_path) or {}))
        zf.write(manifest_path, arcname="job_result_manifest.json")
        if report_path and report_path.exists():
            zf.write(report_path, arcname="cleanup_report.md")

        for rec in selected:
            src = job_dir / rec.relative_path
            if not src.exists() or not src.is_file():
                continue
            if rec.relative_path.startswith("bundles/") or rec.relative_path.startswith("_archive/"):
                continue
            zf.write(src, arcname=rec.relative_path)
    return out_zip


def copy_archives(job_dir: Path, records: List[FileRecord], verbose: bool = False) -> int:
    archive_root = job_dir / "_archive"
    count = 0
    for rec in records:
        if rec.category not in {"ARCHIVE_ONLY", "REBUILDABLE_INTERMEDIATE", "DEBUG_LOG"}:
            continue
        src = job_dir / rec.relative_path
        if not src.exists() or not src.is_file():
            continue
        dst = archive_root / rec.relative_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
        if verbose:
            print(f"[archive-copy] {rec.relative_path} -> {dst.relative_to(job_dir)}")
    return count


def make_archive_zip(job_dir: Path, records: List[FileRecord], out_zip: Path) -> Path:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for rec in records:
            if rec.category in {"DELETE_CANDIDATE", "CACHE_TEMP"}:
                continue
            if rec.relative_path.startswith("bundles/"):
                continue
            src = job_dir / rec.relative_path
            if src.exists() and src.is_file():
                zf.write(src, arcname=rec.relative_path)
    return out_zip


def delete_candidates(job_dir: Path, records: List[FileRecord], *, allow_delete_cif: bool, verbose: bool = False) -> List[Dict]:
    deleted: List[Dict] = []
    for rec in records:
        eligible = rec.category == "DELETE_CANDIDATE"
        eligible_cif = (
            allow_delete_cif
            and rec.kind == "cif"
            and not rec.downstream_used
            and rec.category in {"ARCHIVE_ONLY", "REBUILDABLE_INTERMEDIATE"}
        )
        if not (eligible or eligible_cif):
            continue
        src = job_dir / rec.relative_path
        if not src.exists() or not src.is_file():
            continue
        size = src.stat().st_size
        src.unlink()
        deleted.append({
            "relative_path": rec.relative_path,
            "size": size,
            "reason": rec.reason,
            "timestamp": now_iso(),
        })
        if verbose:
            print(f"[delete] {rec.relative_path}")

    for d in sorted(job_dir.rglob("*"), reverse=True):
        if d.is_dir() and d.name in {"__pycache__"}:
            try:
                shutil.rmtree(d)
            except Exception:
                pass
        elif d.is_dir():
            try:
                next(d.iterdir())
            except StopIteration:
                if d != job_dir:
                    try:
                        d.rmdir()
                    except Exception:
                        pass
            except Exception:
                pass
    return deleted


def write_deleted_log(job_dir: Path, deleted: List[Dict]) -> Optional[Path]:
    if not deleted:
        return None
    out = job_dir / "cleanup_deleted_files.json"
    out.write_text(json.dumps(deleted, indent=2), encoding="utf-8")
    return out


def print_filtered_records(records: List[FileRecord], *, title: str, predicate) -> None:
    matches = [rec for rec in records if predicate(rec)]
    print(title)
    if not matches:
        print("  - none")
        return
    for rec in matches:
        print(f"  - {rec.relative_path} [{rec.category}]")
        if rec.downstream_evidence:
            for ev in rec.downstream_evidence[:3]:
                print(f"      * {ev.source}: {ev.file} — {ev.reason}")


def summarize(
    job_id: str,
    manifest: Dict,
    records: List[FileRecord],
    public_zip: Optional[Path],
    report_path: Optional[Path],
    archived_count: int,
    deleted_count: int,
    warnings: List[str],
    args,
) -> Dict:
    print(f"job_id: {job_id}")
    print(f"total files scanned: {manifest['counts']['total_files']}")
    print(f"files included in public zip: {manifest['counts']['public_bundle_files']}")
    print(f"files archived: {archived_count}")
    print(f"delete candidates: {manifest['counts']['delete_candidates']}")
    print(f"unknown preserved: {manifest['counts']['unknown_keep']}")
    print(f"downstream-used files: {manifest['counts']['downstream_used_files']}")
    print(f"unused files: {manifest['counts']['unused_files']}")
    print(f"public zip path: {public_zip if public_zip else 'not created'}")
    print(f"cleanup report path: {report_path if report_path else 'not created'}")
    if warnings:
        print("warnings:")
        for item in warnings:
            print(f"  - {item}")

    if args.show_gallery_required:
        print_filtered_records(
            records,
            title="gallery/API required files",
            predicate=lambda rec: rec.downstream_used and rec.category in {"KEEP", "BUNDLE"},
        )
    if args.show_unused:
        print_filtered_records(
            records,
            title="unused files",
            predicate=lambda rec: not rec.downstream_used,
        )
    if args.show_cif:
        print_filtered_records(
            records,
            title=".cif audit",
            predicate=lambda rec: rec.kind == "cif" or rec.name == "CIFdata.csv",
        )

    return {
        "job_id": job_id,
        "scanned": manifest["counts"]["total_files"],
        "public_bundle_files": manifest["counts"]["public_bundle_files"],
        "archived": archived_count,
        "deleted": deleted_count,
        "unknown_keep": manifest["counts"]["unknown_keep"],
        "warnings": len(warnings),
    }


def process_job(job_dir: Path, args) -> Dict:
    job_dir = job_dir.resolve()
    if not job_dir.exists():
        raise FileNotFoundError(f"Job directory not found: {job_dir}")

    job_id = args.job_id or job_dir.name
    public_zip_path, archive_zip_path = bundle_paths(job_dir, job_id, args.bundle_name)
    records = scan_job(
        job_dir,
        include_raw=args.include_raw,
        public_bundle_name=public_zip_path.name,
        archive_bundle_name=archive_zip_path.name,
        trace_downstream=args.trace_downstream,
        verbose=args.verbose,
    )
    total_size_before = sum(rec.size_bytes for rec in records)

    warnings: List[str] = []
    completed, reason = looks_completed(job_dir)
    if not completed:
        warnings.append(f"Job does not clearly look completed: {reason}")

    manifest = build_manifest(job_dir, job_id, records, public_zip_path, archive_zip_path if args.archive_intermediates else None, warnings)

    effective_safe_package = args.safe_package
    effective_apply = args.apply
    effective_make_manifest = bool(effective_safe_package or args.make_manifest or args.make_zip or effective_apply)
    effective_make_zip = bool(effective_safe_package or args.make_zip or effective_apply)
    effective_dry_run = bool(args.dry_run or not (effective_make_manifest or effective_make_zip or effective_apply))

    archived_count = 0
    deleted: List[Dict] = []
    manifest_path = None
    report_path = None
    public_zip_written = None
    archive_zip_written = None

    if effective_dry_run:
        if args.archive_intermediates and not effective_apply:
            warnings.append("Archive copies were requested but not applied because --apply was not provided.")
        if args.delete_rebuildable and not effective_apply:
            warnings.append("Deletion was requested but not applied because --apply was not provided.")
        if args.allow_delete_cif and not (args.apply and args.delete_rebuildable):
            warnings.append("CIF deletion was requested but not applied because destructive flags were not provided.")
        warnings.append("Dry-run mode: no manifest, report, or ZIP files were written.")
    else:
        if effective_make_manifest:
            manifest_path = write_manifest(job_dir, manifest)
        report_path = write_cleanup_report(job_dir, manifest, records, total_size_before, None, None, 0, [], warnings)

        if effective_make_zip:
            if manifest_path is None:
                manifest_path = write_manifest(job_dir, manifest)
            public_zip_written = make_public_zip(job_dir, job_id, records, manifest_path, report_path, public_zip_path)

        if args.archive_intermediates and effective_apply:
            archived_count = copy_archives(job_dir, records, verbose=args.verbose)
            archive_zip_written = make_archive_zip(job_dir, records, archive_zip_path)

        if args.delete_rebuildable and effective_apply:
            deleted = delete_candidates(job_dir, records, allow_delete_cif=args.allow_delete_cif, verbose=args.verbose)
            write_deleted_log(job_dir, deleted)

        report_path = write_cleanup_report(job_dir, manifest, records, total_size_before, public_zip_written, archive_zip_written, archived_count, deleted, warnings)
        if effective_make_manifest:
            manifest = build_manifest(job_dir, job_id, records, public_zip_path if public_zip_written else None, archive_zip_path if archive_zip_written else None, warnings)
            write_manifest(job_dir, manifest)

    return summarize(job_id, manifest, records, public_zip_written, report_path, archived_count, len(deleted), warnings, args)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--job-id")
    p.add_argument("--job-dir")
    p.add_argument("--jobs-root", default="jobs")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--make-zip", action="store_true")
    p.add_argument("--make-manifest", action="store_true")
    p.add_argument("--archive-intermediates", action="store_true")
    p.add_argument("--delete-rebuildable", action="store_true")
    p.add_argument("--include-raw", action="store_true")
    p.add_argument("--exclude-raw", action="store_true")
    p.add_argument("--bundle-name")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--safe-package", action="store_true")
    p.add_argument("--all-completed", action="store_true")
    p.add_argument("--trace-downstream", action="store_true", default=True)
    p.add_argument("--allow-delete-cif", action="store_true")
    p.add_argument("--show-gallery-required", action="store_true")
    p.add_argument("--show-unused", action="store_true")
    p.add_argument("--show-cif", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.include_raw and args.exclude_raw:
        raise SystemExit("Use only one of --include-raw or --exclude-raw")
    args.include_raw = bool(args.include_raw and not args.exclude_raw)

    job_dirs = resolve_job_dirs(args)
    if not job_dirs:
        print("No completed jobs found to process." if args.all_completed else "No job directory found.")
        return 1

    global_stats = []
    for job_dir in job_dirs:
        try:
            summary = process_job(job_dir, args)
            global_stats.append(summary)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
        except Exception as exc:
            print(f"ERROR processing {job_dir}: {exc}", file=sys.stderr)
            return 2
        if len(job_dirs) > 1:
            print("-" * 72)

    if len(global_stats) > 1:
        print("GLOBAL SUMMARY")
        print(f"jobs processed: {len(global_stats)}")
        print(f"files scanned: {sum(x['scanned'] for x in global_stats)}")
        print(f"public bundle files: {sum(x['public_bundle_files'] for x in global_stats)}")
        print(f"archived: {sum(x['archived'] for x in global_stats)}")
        print(f"deleted: {sum(x['deleted'] for x in global_stats)}")
        print(f"unknown preserved: {sum(x['unknown_keep'] for x in global_stats)}")
        print(f"warnings: {sum(x['warnings'] for x in global_stats)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
