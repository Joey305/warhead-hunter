from __future__ import annotations

import csv
import json
import mimetypes
import os
import re
import sqlite3
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, unquote

from flask import Flask, abort, jsonify, request, send_file
from werkzeug.utils import secure_filename

APP = Flask(__name__)


def load_env_file() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env_file()

BACKUP_TOKEN = os.environ.get("PROTAC_BACKUP_TOKEN", "").strip()
BACKUP_DIR = Path(os.environ.get("PROTAC_BACKUP_DIR", str(Path(__file__).resolve().parents[1] / "data"))).expanduser()
HUNTER_JOBS_DIR = Path(
    os.environ.get(
        "WARHEAD_HUNTER_JOBS_DIR",
        "/home/jxs794/PROTAC_BUILDER/warhead_hunter/hunter_jobs",
    )
).expanduser()

DB_PATH = BACKUP_DIR / "protac_backup.sqlite3"
JSONL_PATH = BACKUP_DIR / "protac_events.jsonl"
EVENTS_CSV_PATH = BACKUP_DIR / "protac_events.csv"
COMPONENTS_CSV_PATH = BACKUP_DIR / "protac_components.csv"
LINKER_USAGE_CSV_PATH = BACKUP_DIR / "protac_linker_library_usage.csv"
HUNTER_HANDOFF_CSV_PATH = BACKUP_DIR / "warhead_hunter_handoffs.csv"
HUNTER_ARCHIVES_CSV_PATH = BACKUP_DIR / "warhead_hunter_job_archives.csv"

SAFE_RESULT_SUFFIXES = {".pdb", ".sdf", ".svg", ".csv", ".tsv", ".json", ".log", ".txt", ".zip"}
SAFE_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "_randy_backup"}
SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,80}$")

PDB_ASSET_RE = re.compile(r"^(?P<pdb>[0-9A-Za-z]{4})_(?P<chain>[A-Za-z0-9])_(?P<ligand>[A-Za-z0-9]{2,16})\.pdb$", re.I)
SDF_SVG_ASSET_RE = re.compile(
    r"^(?P<pdb>[0-9A-Za-z]{4})_(?P<chain>[A-Za-z0-9])_(?P<ligand>[A-Za-z0-9]{2,16})_(?P<resid>[A-Za-z0-9_.-]+)(?P<tag>_plain|_exposed)?\.(?P<ext>sdf|svg)$",
    re.I,
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require_auth() -> Tuple[bool, Tuple[str, int] | None]:
    if not BACKUP_TOKEN:
        return False, ("Receiver token is not configured.", 500)
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {BACKUP_TOKEN}":
        return False, ("Unauthorized.", 401)
    return True, None


def safe_component(value: Any, default: str = "") -> str:
    s = str(value or default).strip()
    s = s.replace("\\", "_").replace("/", "_")
    s = s.replace("..", "_")
    return s


def safe_job_id(value: Any) -> str:
    s = safe_component(value)
    if not s or not SAFE_JOB_ID_RE.fullmatch(s):
        raise ValueError("Missing or invalid job_id")
    return s


def safe_under(base: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def init_storage() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    HUNTER_JOBS_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS protac_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at_utc TEXT NOT NULL,
                event_type TEXT,
                source TEXT,
                endpoint TEXT,
                status TEXT,
                protac_smiles TEXT,
                job_id TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.commit()

    csv_headers = {
        EVENTS_CSV_PATH: [
            "received_at_utc", "event_type", "source", "endpoint", "status",
            "protac_smiles", "job_id", "built", "failed",
        ],
        COMPONENTS_CSV_PATH: [
            "received_at_utc", "event_id", "event_type", "source", "endpoint", "status",
            "run_id", "row_number", "protac_name", "client_ip",
            "warhead_smiles", "linker_smiles", "ligase_smiles", "protac_smiles",
        ],
        LINKER_USAGE_CSV_PATH: [
            "received_at_utc", "event_id", "source", "endpoint", "status", "run_id",
            "client_ip", "filename", "rows_total", "built", "failed", "name_col", "smiles_col", "extra",
        ],
        HUNTER_HANDOFF_CSV_PATH: [
            "received_at_utc", "event_id", "job_id", "pdb", "chain", "warhead", "resid",
            "remote_dir", "saved_file_count", "pdb_file", "sdf_file", "svg_files",
            "source", "client_ip",
        ],
        HUNTER_ARCHIVES_CSV_PATH: [
            "received_at_utc", "event_id", "job_id", "source", "status",
            "remote_dir", "archive_file", "archive_size_bytes", "extracted_file_count", "client_ip",
        ],
    }
    for path, header in csv_headers.items():
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(header)


def extract(payload: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def payload_int(payload: Dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            return int(float(value))
        except Exception:
            continue
    return 0


def store_event(payload: Dict[str, Any]) -> int:
    init_storage()
    received_at = now_utc()
    event_type = extract(payload, "event_type", "type", "event")
    source = extract(payload, "source")
    endpoint = extract(payload, "endpoint")
    status = extract(payload, "status")
    protac_smiles = extract(payload, "protac_smiles", "smiles", "generated_smiles")
    job_id = extract(payload, "job_id", "jobId")
    built = payload_int(payload, "built")
    failed = payload_int(payload, "failed")
    payload_json = json.dumps(payload, sort_keys=True)

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO protac_events (
                received_at_utc, event_type, source, endpoint, status,
                protac_smiles, job_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (received_at, event_type, source, endpoint, status, protac_smiles, job_id, payload_json),
        )
        event_id = int(cur.lastrowid)
        conn.commit()

    with JSONL_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"received_at_utc": received_at, "event_id": event_id, "payload": payload}) + "\n")

    with EVENTS_CSV_PATH.open("a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow([received_at, event_type, source, endpoint, status, protac_smiles, job_id, built, failed])

    if event_type in {"protac_component_record", "legacy_protac_components"}:
        append_component_csv(received_at, event_id, event_type, payload)
    if event_type == "linker_library_usage":
        append_linker_usage_csv(received_at, event_id, payload)
    if event_type == "hunter_job_materialized":
        append_hunter_handoff_csv(received_at, event_id, payload)
    if event_type == "hunter_job_archived":
        append_hunter_archive_csv(received_at, event_id, payload)

    return event_id


def append_component_csv(received_at: str, event_id: int, event_type: str, payload: Dict[str, Any]) -> None:
    with COMPONENTS_CSV_PATH.open("a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow([
            received_at, event_id, event_type, extract(payload, "source"), extract(payload, "endpoint"),
            extract(payload, "status"), extract(payload, "run_id"), extract(payload, "row_number"),
            extract(payload, "protac_name", "name"), extract(payload, "client_ip"),
            extract(payload, "warhead_smiles", "target_smiles"), extract(payload, "linker_smiles"),
            extract(payload, "ligase_smiles", "e3_smiles"), extract(payload, "protac_smiles", "smiles"),
        ])


def append_linker_usage_csv(received_at: str, event_id: int, payload: Dict[str, Any]) -> None:
    with LINKER_USAGE_CSV_PATH.open("a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow([
            received_at, event_id, extract(payload, "source"), extract(payload, "endpoint"),
            extract(payload, "status"), extract(payload, "run_id"), extract(payload, "client_ip"),
            extract(payload, "filename"), payload_int(payload, "rows_total", "total_rows"),
            payload_int(payload, "built"), payload_int(payload, "failed"), extract(payload, "name_col"),
            extract(payload, "smiles_col"), extract(payload, "extra"),
        ])


def append_hunter_handoff_csv(received_at: str, event_id: int, payload: Dict[str, Any]) -> None:
    files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
    with HUNTER_HANDOFF_CSV_PATH.open("a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow([
            received_at, event_id, extract(payload, "job_id"), extract(payload, "pdb"), extract(payload, "chain"),
            extract(payload, "warhead"), extract(payload, "resid"), extract(payload, "remote_dir", "stored_dir"),
            payload_int(payload, "saved_file_count"),
            files.get("pdb", "") if isinstance(files, dict) else "",
            files.get("sdf", "") if isinstance(files, dict) else "",
            "|".join(files.get("svg", [])) if isinstance(files, dict) and isinstance(files.get("svg"), list) else "",
            extract(payload, "source"), extract(payload, "client_ip"),
        ])


def append_hunter_archive_csv(received_at: str, event_id: int, payload: Dict[str, Any]) -> None:
    with HUNTER_ARCHIVES_CSV_PATH.open("a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow([
            received_at, event_id, extract(payload, "job_id"), extract(payload, "source"),
            extract(payload, "status"), extract(payload, "remote_dir", "stored_dir"),
            extract(payload, "archive_file"), payload_int(payload, "archive_size_bytes"),
            payload_int(payload, "extracted_file_count"), extract(payload, "client_ip"),
        ])


def rows_from_query(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    init_storage()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def summarize_events() -> Dict[str, Any]:
    rows = rows_from_query("SELECT * FROM protac_events ORDER BY id DESC")
    by_event_type: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    by_endpoint: Dict[str, int] = {}
    linker_usage = {"runs": 0, "rows_total": 0, "built": 0, "failed": 0, "by_filename": {}, "by_source": {}, "by_endpoint": {}}
    hunter_handoffs = {"count": 0, "saved_files": 0, "by_source": {}, "by_job": {}}
    hunter_archives = {"count": 0, "extracted_files": 0, "by_source": {}, "by_job": {}}
    latest_events = []
    total_from_usage = 0
    generated_protac_events = 0

    for row in rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except Exception:
            payload = {}
        event_type = row.get("event_type") or ""
        source = row.get("source") or ""
        endpoint = row.get("endpoint") or ""
        by_event_type[event_type] = by_event_type.get(event_type, 0) + 1
        if source:
            by_source[source] = by_source.get(source, 0) + 1
        if endpoint:
            by_endpoint[endpoint] = by_endpoint.get(endpoint, 0) + 1
        if event_type == "usage_log" and str(row.get("status") or payload.get("status") or "").lower() in {"ok", "success", ""}:
            total_from_usage += payload_int(payload, "built")
        if event_type == "generated_protac":
            generated_protac_events += 1
        if event_type == "linker_library_usage":
            linker_usage["runs"] += 1
            linker_usage["rows_total"] += payload_int(payload, "rows_total", "total_rows")
            linker_usage["built"] += payload_int(payload, "built")
            linker_usage["failed"] += payload_int(payload, "failed")
            fn = extract(payload, "filename") or "unknown"
            linker_usage["by_filename"][fn] = linker_usage["by_filename"].get(fn, 0) + 1
        if event_type == "hunter_job_materialized":
            hunter_handoffs["count"] += 1
            hunter_handoffs["saved_files"] += payload_int(payload, "saved_file_count")
            jid = extract(payload, "job_id") or "unknown"
            hunter_handoffs["by_job"][jid] = hunter_handoffs["by_job"].get(jid, 0) + 1
        if event_type == "hunter_job_archived":
            hunter_archives["count"] += 1
            hunter_archives["extracted_files"] += payload_int(payload, "extracted_file_count")
            jid = extract(payload, "job_id") or "unknown"
            hunter_archives["by_job"][jid] = hunter_archives["by_job"].get(jid, 0) + 1

        if len(latest_events) < 20:
            latest_events.append({
                "id": row.get("id"), "received_at_utc": row.get("received_at_utc"),
                "event_type": event_type, "source": source, "endpoint": endpoint,
                "status": row.get("status") or "", "job_id": row.get("job_id") or "",
                "protac_smiles": row.get("protac_smiles") or "", "built": payload_int(payload, "built"),
                "failed": payload_int(payload, "failed"),
            })

    total = total_from_usage or generated_protac_events
    return {
        "ok": True,
        "source": "randy_backup_receiver",
        "runtime_data_dir": str(BACKUP_DIR),
        "canonical_log_file": str(DB_PATH),
        "total": total,
        "local_actions": total,
        "generated_protac_events": generated_protac_events,
        "template_downloads": by_event_type.get("template_download", 0),
        "linker_library_runs": linker_usage["runs"],
        "linker_library_usage": linker_usage,
        "hunter_handoffs": hunter_handoffs,
        "hunter_archives": hunter_archives,
        "by_event_type": by_event_type,
        "by_source": by_source,
        "by_endpoint": by_endpoint,
        "latest_events": latest_events,
    }


@APP.get("/healthz")
def healthz():
    init_storage()
    return jsonify({"ok": True, "service": "protac-backup-receiver"})


@APP.post("/backup/protac-event")
def backup_protac_event():
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Expected JSON object payload."}), 400
    event_id = store_event(payload)
    return jsonify({"ok": True, "event_id": event_id})


@APP.post("/backup/protac-events")
def backup_protac_events():
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    payload = request.get_json(silent=True)
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return jsonify({"ok": False, "error": "Expected JSON object with events array."}), 400
    event_ids = []
    skipped = 0
    for event in events:
        if not isinstance(event, dict):
            skipped += 1
            continue
        event_ids.append(store_event(event))
    return jsonify({"ok": True, "count": len(event_ids), "event_ids": event_ids, "skipped": skipped})


@APP.get("/backup/summary")
def backup_summary():
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    return jsonify(summarize_events())


def parse_limit(default: int = 100, maximum: int = 1000) -> int:
    try:
        return max(1, min(int(request.args.get("limit", default)), maximum))
    except Exception:
        return default


@APP.get("/backup/events")
def backup_events():
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    rows = rows_from_query("SELECT * FROM protac_events ORDER BY id DESC LIMIT ?", (parse_limit(),))
    for row in rows:
        try:
            row["payload"] = json.loads(row.pop("payload_json") or "{}")
        except Exception:
            row["payload"] = {}
    return jsonify({"ok": True, "count": len(rows), "events": rows})


@APP.get("/backup/protacs")
@APP.get("/backup/components")
def backup_components():
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    rows = rows_from_query(
        """
        SELECT id, received_at_utc, event_type, source, endpoint, status, job_id, payload_json
        FROM protac_events
        WHERE event_type IN ('legacy_protac_components', 'protac_component_record')
        ORDER BY id DESC LIMIT ?
        """,
        (parse_limit(),),
    )
    items = []
    for row in rows:
        payload = json.loads(row.pop("payload_json") or "{}")
        items.append({**row, **{
            "run_id": payload.get("run_id", ""),
            "row_number": payload.get("row_number", ""),
            "protac_name": payload.get("protac_name", payload.get("name", "")),
            "warhead_smiles": payload.get("warhead_smiles", payload.get("target_smiles", "")),
            "linker_smiles": payload.get("linker_smiles", ""),
            "ligase_smiles": payload.get("ligase_smiles", payload.get("e3_smiles", "")),
            "protac_smiles": payload.get("protac_smiles", payload.get("smiles", "")),
        }})
    return jsonify({"ok": True, "count": len(items), "components": items})


@APP.get("/backup/linker-libraries")
def backup_linker_libraries():
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    rows = rows_from_query(
        "SELECT id, received_at_utc, source, endpoint, status, payload_json FROM protac_events WHERE event_type='linker_library_usage' ORDER BY id DESC LIMIT ?",
        (parse_limit(),),
    )
    items = []
    for row in rows:
        payload = json.loads(row.pop("payload_json") or "{}")
        items.append({**row, **{
            "run_id": payload.get("run_id", ""),
            "filename": payload.get("filename", ""),
            "rows_total": payload_int(payload, "rows_total", "total_rows"),
            "built": payload_int(payload, "built"),
            "failed": payload_int(payload, "failed"),
            "client_ip": payload.get("client_ip", ""),
        }})
    return jsonify({"ok": True, "count": len(items), "linker_libraries": items})


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def _safe_extract_zip(zip_path: Path, dest_dir: Path) -> List[Dict[str, Any]]:
    extracted: List[Dict[str, Any]] = []
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/").lstrip("/")
            parts = [p for p in name.split("/") if p and p not in {".", ".."}]
            if not parts:
                continue
            rel = Path(*parts)
            out_path = (dest_dir / rel).resolve()
            if not safe_under(dest_dir, out_path):
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, out_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append({"relative_path": rel.as_posix(), "size_bytes": out_path.stat().st_size})
    return extracted


def _job_storage_root(job_id: str) -> Path:
    return (HUNTER_JOBS_DIR / safe_job_id(job_id)).resolve()


def _stored_job_search_roots(job_dir: Path) -> List[Path]:
    return [p for p in [job_dir / "job_files", job_dir] if p.exists() and p.is_dir()]


def _find_first_recursive(roots: List[Path], patterns: List[str]) -> Path | None:
    matches: List[Path] = []
    for root in roots:
        for pattern in patterns:
            matches.extend(root.rglob(pattern))
    matches = sorted({p.resolve() for p in matches if p.exists() and p.is_file()}, key=lambda p: str(p))
    return matches[0] if matches else None


def _find_all_recursive(roots: List[Path], patterns: List[str]) -> List[Path]:
    matches: List[Path] = []
    for root in roots:
        for pattern in patterns:
            matches.extend(root.rglob(pattern))
    return sorted({p.resolve() for p in matches if p.exists() and p.is_file()}, key=lambda p: str(p))


def _locate_stored_handoff_files(job_id: str, pdb: str, chain: str, warhead: str, resid: str) -> Tuple[Path, Path, List[Path]]:
    job_dir = _job_storage_root(job_id)
    roots = _stored_job_search_roots(job_dir)
    if not roots:
        raise FileNotFoundError(f"No stored job files found for {job_id} under {job_dir}")
    pdb = safe_component(pdb).lower()
    chain = safe_component(chain).upper()
    warhead = safe_component(warhead).upper()
    resid = safe_component(resid)

    pdb_file = _find_first_recursive(roots, [f"{pdb}_{chain}_{warhead}.pdb"])
    if pdb_file is None:
        raise FileNotFoundError(f"Stored PDB not found: {pdb}_{chain}_{warhead}.pdb")

    sdf_patterns = [f"{pdb}_{chain}_{warhead}_{resid}.sdf"] if resid else []
    sdf_patterns.extend([f"{pdb}_{chain}_{warhead}_*.sdf", f"{pdb}_{chain}_{warhead}.sdf"])
    sdf_file = _find_first_recursive(roots, sdf_patterns)
    if sdf_file is None:
        raise FileNotFoundError(f"Stored SDF not found for {pdb}_{chain}_{warhead} resid={resid}")

    svg_patterns = [f"{pdb}_{chain}_{warhead}_{resid}_*.svg"] if resid else []
    svg_patterns.extend([f"{pdb}_{chain}_{warhead}_*_*.svg", f"{pdb}_{chain}_{warhead}*.svg"])
    svg_files = _find_all_recursive(roots, svg_patterns)
    return pdb_file, sdf_file, svg_files


@APP.post("/backup/hunter-job-files")
def backup_hunter_job_files():
    """Receive one selected PDB/SDF/SVG handoff over HTTPS and persist it on RANDY."""
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    init_storage()

    try:
        job_id = safe_job_id(request.form.get("job_id"))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    remote_dir = _job_storage_root(job_id)
    if not safe_under(HUNTER_JOBS_DIR, remote_dir):
        return jsonify({"ok": False, "error": "Unsafe job directory."}), 400
    remote_dir.mkdir(parents=True, exist_ok=True)

    try:
        manifest = json.loads(request.form.get("manifest_json") or "{}")
        if not isinstance(manifest, dict):
            manifest = {}
    except Exception:
        manifest = {}

    saved_files: List[Dict[str, Any]] = []
    for upload in request.files.getlist("files"):
        if not upload or not upload.filename:
            continue
        filename = secure_filename(upload.filename)
        if not filename:
            continue
        dst = (remote_dir / filename).resolve()
        if not safe_under(remote_dir, dst):
            continue
        upload.save(str(dst))
        saved_files.append({"filename": filename, "size_bytes": dst.stat().st_size})

    if not saved_files:
        return jsonify({"ok": False, "error": "No files were uploaded."}), 400

    files_by_ext = {"pdb": [], "sdf": [], "svg": []}
    for item in saved_files:
        suffix = Path(item["filename"]).suffix.lower().lstrip(".")
        if suffix in files_by_ext:
            files_by_ext[suffix].append(item["filename"])

    manifest_out = {
        **manifest,
        "job_id": job_id,
        "pdb": safe_component(request.form.get("pdb")) or manifest.get("pdb", ""),
        "chain": safe_component(request.form.get("chain")) or manifest.get("chain", ""),
        "warhead": safe_component(request.form.get("warhead")) or manifest.get("warhead", ""),
        "resid": safe_component(request.form.get("resid")) or manifest.get("resid", ""),
        "stored_at_utc": now_utc(),
        "stored_dir": str(remote_dir),
        "saved_files": saved_files,
        "files": {
            "pdb": files_by_ext["pdb"][0] if files_by_ext["pdb"] else "",
            "sdf": files_by_ext["sdf"][0] if files_by_ext["sdf"] else "",
            "svg": files_by_ext["svg"],
        },
    }
    (remote_dir / "manifest.json").write_text(json.dumps(manifest_out, indent=2, sort_keys=True), encoding="utf-8")

    payload = {
        "event_type": "hunter_job_materialized",
        "source": safe_component(request.form.get("source"), "protac-builder-button") or "protac-builder-button",
        "endpoint": "backup_hunter_job_files",
        "status": "ok",
        "job_id": job_id,
        "pdb": manifest_out.get("pdb", ""),
        "chain": manifest_out.get("chain", ""),
        "warhead": manifest_out.get("warhead", ""),
        "resid": manifest_out.get("resid", ""),
        "remote_dir": str(remote_dir),
        "saved_file_count": len(saved_files),
        "saved_files": saved_files,
        "files": manifest_out["files"],
        "client_ip": _client_ip(),
    }
    event_id = store_event(payload)
    return jsonify({"ok": True, "event_id": event_id, "remote_dir": str(remote_dir), "saved_files": saved_files, "manifest": "manifest.json"})


@APP.post("/backup/hunter-job-archive")
def backup_hunter_job_archive():
    """Receive a full Warhead Hunter job ZIP and extract it persistently on RANDY."""
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    init_storage()

    try:
        job_id = safe_job_id(request.form.get("job_id"))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    upload = request.files.get("archive") or request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"ok": False, "error": "Missing archive file field named 'archive'."}), 400

    job_dir = _job_storage_root(job_id)
    if not safe_under(HUNTER_JOBS_DIR, job_dir):
        return jsonify({"ok": False, "error": "Unsafe job directory."}), 400
    archives_dir = job_dir / "archives"
    extract_dir = job_dir / "job_files"
    archives_dir.mkdir(parents=True, exist_ok=True)

    filename = secure_filename(upload.filename) or f"{job_id}_warhead_hunter_full_job.zip"
    if not filename.lower().endswith(".zip"):
        filename += ".zip"
    archive_path = (archives_dir / filename).resolve()
    if not safe_under(archives_dir, archive_path):
        return jsonify({"ok": False, "error": "Unsafe archive filename."}), 400
    upload.save(str(archive_path))

    extracted_files = _safe_extract_zip(archive_path, extract_dir)
    manifest = {
        "job_id": job_id,
        "stored_at_utc": now_utc(),
        "stored_dir": str(job_dir),
        "archive_file": str(archive_path),
        "archive_size_bytes": archive_path.stat().st_size,
        "extract_dir": str(extract_dir),
        "extracted_file_count": len(extracted_files),
        "source": safe_component(request.form.get("source"), "warhead-hunter-job-runner") or "warhead-hunter-job-runner",
    }
    (job_dir / "job_archive_manifest.json").write_text(json.dumps({**manifest, "extracted_files_sample": extracted_files[:100]}, indent=2, sort_keys=True), encoding="utf-8")

    payload = {
        "event_type": "hunter_job_archived",
        "source": manifest["source"],
        "endpoint": "backup_hunter_job_archive",
        "status": safe_component(request.form.get("status"), "completed") or "completed",
        "job_id": job_id,
        "remote_dir": str(job_dir),
        "archive_file": str(archive_path),
        "archive_size_bytes": archive_path.stat().st_size,
        "extract_dir": str(extract_dir),
        "extracted_file_count": len(extracted_files),
        "client_ip": _client_ip(),
    }
    event_id = store_event(payload)
    return jsonify({"ok": True, "event_id": event_id, **manifest})


@APP.post("/backup/hunter-job-materialize")
def backup_hunter_job_materialize():
    """Materialize a selected PDB/SDF/SVG set from a previously backed-up full job."""
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    init_storage()

    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Expected JSON or form payload."}), 400
    try:
        job_id = safe_job_id(data.get("job_id"))
        pdb = safe_component(data.get("pdb")).lower()
        chain = safe_component(data.get("chain")).upper()
        warhead = safe_component(data.get("warhead")).upper()
        resid = safe_component(data.get("resid"))
        source = safe_component(data.get("source"), "protac-builder-button") or "protac-builder-button"
        pdb_file, sdf_file, svg_files = _locate_stored_handoff_files(job_id, pdb, chain, warhead, resid)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404

    job_dir = _job_storage_root(job_id)
    materialized_dir_name = f"handoff_{pdb}_{chain}_{warhead}_{resid}" if resid else f"handoff_{pdb}_{chain}_{warhead}"
    remote_dir = (job_dir / materialized_dir_name).resolve()
    if not safe_under(job_dir, remote_dir):
        return jsonify({"ok": False, "error": "Unsafe materialized directory."}), 400
    remote_dir.mkdir(parents=True, exist_ok=True)

    copied: List[Dict[str, Any]] = []
    for src in [pdb_file, sdf_file, *svg_files]:
        dst = (remote_dir / src.name).resolve()
        if not safe_under(remote_dir, dst):
            continue
        shutil.copy2(src, dst)
        copied.append({"filename": dst.name, "size_bytes": dst.stat().st_size})

    files_out = {"pdb": pdb_file.name, "sdf": sdf_file.name, "svg": [p.name for p in svg_files]}
    manifest = {
        "job_id": job_id, "pdb": pdb, "chain": chain, "warhead": warhead, "resid": resid,
        "stored_at_utc": now_utc(), "stored_dir": str(remote_dir), "files": files_out,
        "saved_files": copied,
        "source_paths": {"pdb": str(pdb_file), "sdf": str(sdf_file), "svg": [str(p) for p in svg_files]},
        "local_lookup_error": data.get("local_lookup_error", ""),
    }
    (remote_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    payload = {
        "event_type": "hunter_job_materialized", "source": source, "endpoint": "backup_hunter_job_materialize",
        "status": "ok", "job_id": job_id, "pdb": pdb, "chain": chain, "warhead": warhead, "resid": resid,
        "remote_dir": str(remote_dir), "saved_file_count": len(copied), "saved_files": copied,
        "files": files_out, "client_ip": _client_ip(),
    }
    event_id = store_event(payload)
    return jsonify({"ok": True, "event_id": event_id, "remote_dir": str(remote_dir), "saved_files": copied, "files": files_out, "manifest": "manifest.json"})


def _job_file_roots(job_dir: Path) -> List[Path]:
    roots = []
    for candidate in [job_dir / "job_files", job_dir / "archives", job_dir]:
        if candidate.exists() and candidate.is_dir():
            roots.append(candidate)
    return roots


def _root_label(job_dir: Path, root: Path) -> str:
    try:
        if root.resolve() == (job_dir / "job_files").resolve():
            return "job_files"
        if root.resolve() == (job_dir / "archives").resolve():
            return "archives"
    except Exception:
        pass
    return "job"


def _archive_rel(job_dir: Path, fp: Path, root: Path) -> str:
    rel = fp.resolve().relative_to(root.resolve()).as_posix()
    label = _root_label(job_dir, root)
    if label == "job_files":
        return f"job_files/{rel}"
    if label == "archives":
        return f"archives/{rel}"
    return rel


def _load_archive_manifest(job_dir: Path) -> Dict[str, Any]:
    for name in ["job_archive_manifest.json", "manifest.json"]:
        fp = job_dir / name
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
    return {}


def _normalize_archive_request_path(value: str) -> Tuple[Optional[str], Optional[str]]:
    raw = str(value or "").strip()
    decoded = raw
    for _ in range(3):
        nxt = unquote(decoded)
        if nxt == decoded:
            break
        decoded = nxt
    decoded = decoded.replace("\\", "/").strip()
    if not decoded:
        return None, "Missing relative path."
    if decoded.startswith("/") or re.match(r"^[A-Za-z]:", decoded):
        return None, "Absolute paths are not allowed."
    parts = [p for p in decoded.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        return None, "Path traversal is not allowed."
    if not parts:
        return None, "Missing relative path."
    return Path(*parts).as_posix(), None


def _candidate_archive_paths(requested_rel: str, suffix: str = "") -> List[str]:
    paths: List[str] = []

    def add(path: str) -> None:
        path = path.replace("\\", "/").lstrip("/")
        if path and path not in paths:
            paths.append(path)

    add(requested_rel)
    if not requested_rel.startswith("job_files/"):
        add(f"job_files/{requested_rel}")
    if not requested_rel.startswith("archives/"):
        add(f"archives/{requested_rel}")

    table_like = "/" not in requested_rel
    if table_like:
        add(f"TARGET_RESULTS/{requested_rel}")
        add(f"job_files/TARGET_RESULTS/{requested_rel}")
        add(f"archives/TARGET_RESULTS/{requested_rel}")
    elif not requested_rel.startswith("TARGET_RESULTS/") and not requested_rel.startswith("job_files/TARGET_RESULTS/"):
        add(f"TARGET_RESULTS/{requested_rel}")
        add(f"job_files/TARGET_RESULTS/{requested_rel}")
        add(f"archives/TARGET_RESULTS/{requested_rel}")

    if requested_rel.startswith("TARGET_RESULTS/"):
        tail = requested_rel[len("TARGET_RESULTS/"):]
        add(tail)
        add(f"job_files/{tail}")
        add(f"job_files/TARGET_RESULTS/{tail}")
        add(f"archives/{tail}")
        add(f"archives/TARGET_RESULTS/{tail}")
    if requested_rel.startswith("job_files/"):
        tail = requested_rel[len("job_files/"):]
        add(tail)
        add(f"archives/{tail}")
        if tail.startswith("TARGET_RESULTS/"):
            add(tail[len("TARGET_RESULTS/"):])
            add(f"TARGET_RESULTS/{tail[len('TARGET_RESULTS/'):]}")
            add(f"archives/{tail[len('TARGET_RESULTS/'):]}") 
            add(f"archives/TARGET_RESULTS/{tail[len('TARGET_RESULTS/'):]}") 
    if requested_rel.startswith("archives/"):
        tail = requested_rel[len("archives/"):]
        add(tail)
        add(f"job_files/{tail}")
        if tail.startswith("TARGET_RESULTS/"):
            add(tail[len("TARGET_RESULTS/"):])
            add(f"TARGET_RESULTS/{tail[len('TARGET_RESULTS/'):]}")
            add(f"job_files/{tail[len('TARGET_RESULTS/'):]}") 
            add(f"job_files/TARGET_RESULTS/{tail[len('TARGET_RESULTS/'):]}") 

    # Prefer same-extension candidates only when the caller supplied a suffix.
    if suffix:
        paths = [p for p in paths if Path(p).suffix.lower() in {"", suffix.lower()}]
    return paths


def _safe_zip_member_path(name: str) -> Optional[str]:
    raw = str(name or "").replace("\\", "/").lstrip("/")
    if not raw or raw.endswith("/"):
        return None
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return None
    if any(part in SAFE_SKIP_DIRS or part.startswith(".") for part in parts):
        return None
    rel = Path(*parts).as_posix()
    if Path(rel).suffix.lower() not in SAFE_RESULT_SUFFIXES:
        return None
    return rel


def _archive_zip_paths(job_dir: Path) -> List[Path]:
    archives_dir = (job_dir / "archives").resolve()
    if not archives_dir.is_dir() or not safe_under(job_dir, archives_dir):
        return []
    return sorted(
        p.resolve()
        for p in archives_dir.glob("*.zip")
        if p.is_file() and safe_under(archives_dir, p.resolve())
    )


def _zip_member_records(job_dir: Path, limit: int = 50000) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for zip_path in _archive_zip_paths(job_dir):
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for info in zf.infolist():
                    if len(records) >= limit:
                        return records
                    rel = _safe_zip_member_path(info.filename)
                    if not rel:
                        continue
                    records.append({
                        "name": Path(rel).name,
                        "relative_path": rel,
                        "path_in_root": rel,
                        "root": "archive_zip",
                        "source": "archive_zip_member",
                        "zip_name": zip_path.name,
                        "zip_member": rel,
                        "zip_member_original": info.filename,
                        "size_bytes": int(getattr(info, "file_size", 0) or 0),
                        "suffix": Path(rel).suffix.lower(),
                    })
        except Exception:
            continue
    return records


def _zip_index_summary(job_dir: Path) -> Dict[str, Any]:
    records = _zip_member_records(job_dir, limit=100000)
    names = {rec["name"] for rec in records}
    rels = [str(rec.get("relative_path") or "") for rec in records]
    return {
        "available": bool(_archive_zip_paths(job_dir)),
        "zip_count": len(_archive_zip_paths(job_dir)),
        "safe_file_count": len(records),
        "tables_present": sorted([
            name for name in [
                "Results_Display.csv",
                "Resolved_SASA_Summary.csv",
                "Warhead_SASA_atoms.csv",
                "Ligand_3D_Atoms.csv",
                "Ligand_3D_Atoms_with_SASA.csv",
                "Ligand_Metadata.csv",
            ]
            if name in names
        ]),
        "has_mcs_svg": any("MCS_Output/MCS_SVG/" in rel or "MCS_Output/MCS_SVGS/" in rel for rel in rels),
        "has_mcs_sdf": any("MCS_Output/MCS_SDF/" in rel for rel in rels),
        "has_war_pdb": any("/WAR_PDB/" in f"/{rel}" for rel in rels),
    }


def _zip_member_matches(job_dir: Path, requested_rel: str, candidates: List[str], limit: int = 20) -> List[Dict[str, Any]]:
    wanted = [c.replace("\\", "/").lstrip("/") for c in candidates if c]
    wanted.extend([requested_rel])
    wanted = list(dict.fromkeys(wanted))
    wanted_no_job_files = []
    for w in wanted:
        if w.startswith("job_files/"):
            wanted_no_job_files.append(w[len("job_files/"):])
        elif w.startswith("archives/"):
            wanted_no_job_files.append(w[len("archives/"):])
        else:
            wanted_no_job_files.append(w)
    wanted_names = {Path(w).name.lower() for w in wanted if Path(w).name}
    out: List[Tuple[int, str, Dict[str, Any]]] = []
    for rec in _zip_member_records(job_dir, limit=100000):
        rel = str(rec.get("zip_member") or rec.get("relative_path") or "")
        low = rel.lower()
        rel_with_job_files = f"job_files/{rel}".lower()
        name = Path(rel).name.lower()
        priority: Optional[int] = None
        if low in {w.lower() for w in wanted_no_job_files} or rel_with_job_files in {w.lower() for w in wanted}:
            priority = 0
        elif any(low.endswith("/" + w.lower()) for w in wanted_no_job_files if "/" in w):
            priority = 1
        elif name in wanted_names:
            priority = 2
        if priority is not None:
            out.append((priority, low, rec))
    return [rec for _priority, _low, rec in sorted(out, key=lambda item: (item[0], item[1]))[:limit]]


def _materialize_zip_member(job_dir: Path, rec: Dict[str, Any]) -> Optional[Path]:
    member = _safe_zip_member_path(str(rec.get("zip_member") or rec.get("relative_path") or ""))
    if not member:
        return None
    zip_name = Path(str(rec.get("zip_name") or "")).name
    zip_path = (job_dir / "archives" / zip_name).resolve()
    archives_dir = (job_dir / "archives").resolve()
    if not zip_name or not safe_under(archives_dir, zip_path) or not zip_path.is_file():
        return None
    target_rel = member[len("job_files/"):] if member.startswith("job_files/") else member
    dst = (job_dir / "job_files" / target_rel).resolve()
    if not safe_under(job_dir / "job_files", dst):
        return None
    if dst.exists() and dst.is_file() and dst.suffix.lower() in SAFE_RESULT_SUFFIXES:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            open_name = str(rec.get("zip_member_original") or member)
            with zf.open(open_name, "r") as src, tmp.open("wb") as out:
                shutil.copyfileobj(src, out)
        if dst.exists():
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(dst)
        return dst if dst.exists() else None
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def _path_from_archive_rel(job_dir: Path, archive_rel: str) -> Optional[Path]:
    rel, error = _normalize_archive_request_path(archive_rel)
    if error or not rel:
        return None
    if rel.startswith("job_files/"):
        candidate = (job_dir / rel).resolve()
        if safe_under(job_dir, candidate) and candidate.is_file() and candidate.suffix.lower() in SAFE_RESULT_SUFFIXES:
            return candidate
        return None
    if rel.startswith("archives/"):
        candidate = (job_dir / rel).resolve()
        if safe_under(job_dir, candidate) and candidate.is_file() and candidate.suffix.lower() in SAFE_RESULT_SUFFIXES:
            return candidate
        return None
    for root in [job_dir, job_dir / "job_files", job_dir / "archives"]:
        if not root.exists():
            continue
        candidate = (root / rel).resolve()
        if safe_under(job_dir, candidate) and safe_under(root, candidate) and candidate.is_file() and candidate.suffix.lower() in SAFE_RESULT_SUFFIXES:
            return candidate
    return None


def _manifest_path_matches(job_dir: Path, requested_rel: str) -> List[str]:
    manifest = _load_archive_manifest(job_dir)
    sample = manifest.get("extracted_files_sample")
    matches: List[str] = []
    if not isinstance(sample, list):
        return matches
    wanted_name = Path(requested_rel).name.lower()
    wanted_rel = requested_rel.lower()
    for item in sample:
        rel = ""
        if isinstance(item, dict):
            rel = str(item.get("relative_path") or item.get("path") or "")
        elif isinstance(item, str):
            rel = item
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel:
            continue
        archive_rel = f"job_files/{rel}" if not rel.startswith("job_files/") else rel
        if archive_rel.lower() == wanted_rel or rel.lower() == wanted_rel:
            matches.insert(0, archive_rel)
        elif Path(rel).name.lower() == wanted_name:
            matches.append(archive_rel)
    return matches


def _nearby_archive_matches(job_dir: Path, requested_rel: str, limit: int = 12) -> List[str]:
    name = Path(requested_rel).name.lower()
    suffix = Path(requested_rel).suffix.lower()
    if not name and not suffix:
        return []
    matches = []
    for rec in _safe_file_records(job_dir, limit=5000):
        rec_name = str(rec.get("name") or "").lower()
        if rec_name == name or (suffix and rec_name.endswith(suffix) and name.split(".")[0] in rec_name):
            matches.append(str(rec.get("relative_path") or ""))
        if len(matches) >= limit:
            break
    return matches


def resolve_hunter_archive_file(job_id: str, requested_path: str) -> Dict[str, Any]:
    job_dir = _job_storage_root(job_id)
    rel, error = _normalize_archive_request_path(requested_path)
    checked: List[str] = []
    if error or not rel:
        return {
            "ok": False,
            "status": 400,
            "error": error or "Unsafe path.",
            "job_id": job_id,
            "requested_path": requested_path,
            "candidate_paths": checked,
        }

    suffix = Path(rel).suffix.lower()
    candidates = []
    candidates.extend(_candidate_archive_paths(rel, suffix=suffix))
    candidates.extend(_manifest_path_matches(job_dir, rel))

    allowed_search_suffixes = SAFE_RESULT_SUFFIXES
    allowed_search_basenames = {
        "Results_Display.csv",
        "Resolved_SASA_Summary.csv",
        "Resolved_SASA_Summary.tsv",
        "Warhead_SASA_atoms.csv",
        "Ligand_3D_Atoms_with_SASA.csv",
        "3DSASAmapped.csv",
        "Ligand_Metadata.csv",
        "Protein_Data.csv",
    }
    if Path(rel).name in allowed_search_basenames or suffix in allowed_search_suffixes:
        candidates.extend(_nearby_archive_matches(job_dir, rel, limit=20))

    seen = set()
    for candidate_rel in candidates:
        if candidate_rel in seen:
            continue
        seen.add(candidate_rel)
        checked.append(candidate_rel)
        fp = _path_from_archive_rel(job_dir, candidate_rel)
        if fp:
            return {
                "ok": True,
                "status": 200,
                "job_id": job_id,
                "requested_path": rel,
                "relative_path": fp.resolve().relative_to(job_dir.resolve()).as_posix(),
                "candidate_paths": checked,
                "path": fp,
                "source": "extracted_job_files",
            }

    zip_matches = _zip_member_matches(job_dir, rel, checked or candidates, limit=20)
    for rec in zip_matches:
        checked.append(str(rec.get("zip_member") or rec.get("relative_path") or ""))
        fp = _materialize_zip_member(job_dir, rec)
        if fp:
            return {
                "ok": True,
                "status": 200,
                "job_id": job_id,
                "requested_path": rel,
                "relative_path": fp.resolve().relative_to(job_dir.resolve()).as_posix(),
                "candidate_paths": checked,
                "zip_member": rec.get("zip_member") or rec.get("relative_path"),
                "zip_name": rec.get("zip_name", ""),
                "source": "zip_materialized_to_job_files",
                "path": fp,
            }

    return {
        "ok": False,
        "status": 404,
        "error": "File not found",
        "job_id": job_id,
        "requested_path": rel,
        "candidate_paths": checked,
        "has_job_files": (job_dir / "job_files").is_dir(),
        "has_manifest": any((job_dir / name).exists() for name in ["job_archive_manifest.json", "manifest.json"]),
        "has_archives": (job_dir / "archives").is_dir(),
        "nearby_matches": _nearby_archive_matches(job_dir, rel, limit=12),
        "zip_matches": [
            {
                "relative_path": str(rec.get("zip_member") or rec.get("relative_path") or ""),
                "zip_name": rec.get("zip_name", ""),
            }
            for rec in zip_matches[:12]
        ],
    }


def _safe_file_records(job_dir: Path, limit: int = 10000) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    roots = _job_file_roots(job_dir)
    for root in roots:
        for fp in sorted(root.rglob("*")):
            if len(records) >= limit:
                return records
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in SAFE_RESULT_SUFFIXES:
                continue
            try:
                rel = fp.resolve().relative_to(root.resolve()).as_posix()
            except Exception:
                continue
            if any(part in SAFE_SKIP_DIRS or part.startswith(".") for part in rel.split("/")):
                continue
            # Avoid surfacing full archives in the normal file list unless explicitly requested.
            if fp.suffix.lower() == ".zip":
                continue
            archive_rel = _archive_rel(job_dir, fp, root)
            records.append({
                "name": fp.name,
                "relative_path": archive_rel,
                "path_in_root": rel,
                "root": _root_label(job_dir, root),
                "size_bytes": fp.stat().st_size,
                "suffix": fp.suffix.lower(),
            })
    # Deduplicate by relative path + name; prefer job_files.
    seen = set()
    out = []
    for rec in records:
        key = rec["relative_path"]
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def _path_for_relative(job_dir: Path, relative_path: str) -> Optional[Path]:
    resolved = resolve_hunter_archive_file(job_dir.name, relative_path)
    return resolved.get("path") if resolved.get("ok") else None


def _first_table_path(job_dir: Path, names: List[str]) -> Optional[Path]:
    name_lowers = {n.lower() for n in names}
    for rec in _safe_file_records(job_dir):
        if rec["name"].lower() in name_lowers:
            fp = _path_for_relative(job_dir, rec["relative_path"])
            if fp:
                return fp
    return None


def _target_name_from_job(job_dir: Path) -> str:
    fp = _first_table_path(job_dir, ["Protein_Data.csv", "input.csv"])
    if not fp:
        return ""
    try:
        with fp.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            row = next(csv.DictReader(handle), {}) or {}
        return str(row.get("protein") or row.get("target_name") or "").strip()
    except Exception:
        return ""


def _available_artifact_dirs(job_dir: Path) -> Dict[str, List[str]]:
    specs = {
        "sdf_dirs": [
            "MCS_Output/MCS_SDF",
            "TARGET_RESULTS/MCS_Output/MCS_SDF",
            "job_files/MCS_Output/MCS_SDF",
            "job_files/TARGET_RESULTS/MCS_Output/MCS_SDF",
            "archives/MCS_Output/MCS_SDF",
            "archives/TARGET_RESULTS/MCS_Output/MCS_SDF",
            "TARGET_RESULTS/LIGAND_SDF",
            "job_files/TARGET_RESULTS/LIGAND_SDF",
            "archives/TARGET_RESULTS/LIGAND_SDF",
        ],
        "svg_dirs": [
            "MCS_Output/MCS_SVG",
            "TARGET_RESULTS/MCS_Output/MCS_SVG",
            "job_files/MCS_Output/MCS_SVG",
            "job_files/TARGET_RESULTS/MCS_Output/MCS_SVG",
            "archives/MCS_Output/MCS_SVG",
            "archives/TARGET_RESULTS/MCS_Output/MCS_SVG",
            "MCS_Output/MCS_SVGS",
            "job_files/MCS_Output/MCS_SVGS",
            "archives/MCS_Output/MCS_SVGS",
        ],
        "pdb_dirs": [
            "WAR_PDB",
            "TARGET_RESULTS/WAR_PDB",
            "job_files/WAR_PDB",
            "job_files/TARGET_RESULTS/WAR_PDB",
            "archives/WAR_PDB",
            "archives/TARGET_RESULTS/WAR_PDB",
        ],
    }
    out: Dict[str, List[str]] = {}
    for key, rels in specs.items():
        out[key] = []
        for rel in rels:
            directory = (job_dir / rel).resolve() if rel.startswith("job_files/") else None
            if directory is None:
                direct = (job_dir / rel).resolve()
                wrapped = (job_dir / "job_files" / rel).resolve()
                directory = direct if direct.is_dir() else wrapped
            if directory and directory.is_dir() and safe_under(job_dir, directory):
                archive_rel = directory.relative_to(job_dir.resolve()).as_posix()
                if archive_rel not in out[key]:
                    out[key].append(archive_rel)
    zip_records = _zip_member_records(job_dir, limit=100000)
    out["pdb_files_sample"] = [
        str(rec.get("relative_path") or "")
        for rec in zip_records
        if str(rec.get("relative_path") or "").lower().endswith(".pdb")
    ][:25]
    return out


def _file_url(job_id: str, relative_path: str) -> str:
    return f"/backup/hunter-job/{quote(job_id, safe='')}/file/{quote(relative_path, safe='/')}"


def _scan_hunter_job_options(job_id: str, job_dir: Path) -> List[Dict[str, Any]]:
    files = _safe_file_records(job_dir) + _zip_member_records(job_dir, limit=100000)
    pdb_lookup: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    options: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

    for rec in files:
        m = PDB_ASSET_RE.match(rec["name"])
        if m:
            key = (m.group("pdb").lower(), m.group("chain").upper(), m.group("ligand").upper())
            pdb_lookup[key] = {"pdb_file": rec["name"], "pdb_path": rec["relative_path"], "pdb_url": _file_url(job_id, rec["relative_path"])}

    for rec in files:
        m = SDF_SVG_ASSET_RE.match(rec["name"])
        if not m:
            continue
        pdb = m.group("pdb").lower()
        chain = m.group("chain").upper()
        ligand = m.group("ligand").upper()
        resid = str(m.group("resid") or "").strip()
        ext = m.group("ext").lower()
        tag = (m.group("tag") or "").lower().lstrip("_")
        key = (pdb, chain, ligand, resid)
        item = options.setdefault(key, {
            "key": f"{pdb}_{chain}_{ligand}_{resid}",
            "pdb": pdb,
            "chain": chain,
            "ligand": ligand,
            "warhead": ligand,
            "resid": resid,
            "pdb_file": "",
            "pdb_path": "",
            "pdb_url": "",
            "sdf": "",
            "sdf_path": "",
            "sdf_url": "",
            "svg_plain": "",
            "svg_plain_path": "",
            "svg_plain_url": "",
            "svg_exposed": "",
            "svg_exposed_path": "",
            "svg_exposed_url": "",
        })
        if ext == "sdf":
            item["sdf"] = rec["name"]
            item["sdf_path"] = rec["relative_path"]
            item["sdf_url"] = _file_url(job_id, rec["relative_path"])
        elif ext == "svg" and tag == "plain":
            item["svg_plain"] = rec["name"]
            item["svg_plain_path"] = rec["relative_path"]
            item["svg_plain_url"] = _file_url(job_id, rec["relative_path"])
        elif ext == "svg" and tag == "exposed":
            item["svg_exposed"] = rec["name"]
            item["svg_exposed_path"] = rec["relative_path"]
            item["svg_exposed_url"] = _file_url(job_id, rec["relative_path"])

    for key, item in options.items():
        pdb_info = pdb_lookup.get((key[0], key[1], key[2]), {})
        item.update({k: v for k, v in pdb_info.items() if v})

    return sorted(options.values(), key=lambda x: (x.get("pdb", ""), x.get("chain", ""), x.get("ligand", ""), x.get("resid", "")))


@APP.get("/backup/hunter-jobs")
def backup_hunter_jobs():
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    limit = parse_limit(default=100, maximum=500)
    init_storage()
    jobs = []
    for job_dir in sorted([p for p in HUNTER_JOBS_DIR.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        archive_manifest = {}
        for name in ["job_archive_manifest.json", "manifest.json"]:
            fp = job_dir / name
            if fp.exists():
                try:
                    archive_manifest = json.loads(fp.read_text(encoding="utf-8"))
                    break
                except Exception:
                    archive_manifest = {}
        jobs.append({
            "job_id": job_dir.name,
            "remote_dir": str(job_dir),
            "file_count": len(_safe_file_records(job_dir, limit=100000)),
            "option_count": len(_scan_hunter_job_options(job_dir.name, job_dir)),
            "manifest": archive_manifest,
            "detail_url": f"/backup/hunter-job/{job_dir.name}",
        })
    return jsonify({"ok": True, "count": len(jobs), "jobs": jobs})


@APP.get("/backup/hunter-job/<job_id>")
def backup_hunter_job_detail(job_id: str):
    """Return a machine-readable index for one archived Warhead Hunter job."""
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    init_storage()
    try:
        job_id = safe_job_id(job_id)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    job_dir = _job_storage_root(job_id)
    if not safe_under(HUNTER_JOBS_DIR, job_dir) or not job_dir.is_dir():
        return jsonify({"ok": False, "error": "Hunter job not found", "job_id": job_id}), 404

    files = _safe_file_records(job_dir, limit=parse_limit(default=5000, maximum=20000))
    tables = {}
    for table_name in [
        "Results_Display.csv",
        "Resolved_SASA_Summary.csv",
        "Warhead_SASA_atoms.csv",
        "Ligand_3D_Atoms_with_SASA.csv",
        "3DSASAmapped.csv",
        "Ligand_Metadata.csv",
        "Protein_Data.csv",
        "job_metadata.json",
        "job_result_manifest.json",
    ]:
        match = next((rec for rec in files if rec["name"].lower() == table_name.lower()), None)
        if match:
            tables[table_name] = {
                "name": match["name"],
                "relative_path": match["relative_path"],
                "url": _file_url(job_id, match["relative_path"]),
                "size_bytes": match["size_bytes"],
            }

    archive_manifest = _load_archive_manifest(job_dir)
    available_tables = {
        table_name: info["relative_path"]
        for table_name, info in tables.items()
    }
    archive_layout = {
        "has_job_files": (job_dir / "job_files").is_dir(),
        "has_archives": bool(_archive_zip_paths(job_dir)),
        "has_archives_tree": (job_dir / "archives").is_dir(),
        "zip_count": len(_archive_zip_paths(job_dir)),
        "has_manifest": any((job_dir / name).exists() for name in ["job_archive_manifest.json", "manifest.json"]),
        "layout_roots": [label for label, path in [("", job_dir), ("job_files", job_dir / "job_files"), ("archives", job_dir / "archives")] if path.is_dir()],
    }

    options = _scan_hunter_job_options(job_id, job_dir)
    return jsonify({
        "ok": True,
        "job_id": job_id,
        "source": "randy_hunter_job_archive",
        "remote_dir": str(job_dir),
        "target_name": _target_name_from_job(job_dir),
        "tables": tables,
        "available_tables": available_tables,
        "available_artifacts": _available_artifact_dirs(job_dir),
        "archive_layout": archive_layout,
        "zip_index": _zip_index_summary(job_dir),
        "options": options,
        "option_count": len(options),
        "files": [{**rec, "url": _file_url(job_id, rec["relative_path"])} for rec in files],
        "file_count": len(files),
        "manifest": archive_manifest,
    })


@APP.get("/backup/hunter-job/<job_id>/file/<path:relative_path>")
def backup_hunter_job_file(job_id: str, relative_path: str):
    """Serve one safe archived job file by relative path."""
    ok, error = require_auth()
    if not ok:
        message, status_code = error
        return jsonify({"ok": False, "error": message}), status_code
    init_storage()
    try:
        job_id = safe_job_id(job_id)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    job_dir = _job_storage_root(job_id)
    if not safe_under(HUNTER_JOBS_DIR, job_dir) or not job_dir.is_dir():
        return jsonify({"ok": False, "error": "Hunter job not found", "job_id": job_id}), 404

    resolved = resolve_hunter_archive_file(job_id, relative_path)
    if not resolved.get("ok"):
        status = int(resolved.get("status") or 404)
        payload = {k: v for k, v in resolved.items() if k not in {"status", "path"}}
        if request.method == "HEAD":
            return "", status
        return jsonify(payload), status

    fp = resolved["path"]

    mimetype = mimetypes.guess_type(fp.name)[0]
    if fp.suffix.lower() == ".pdb":
        mimetype = "chemical/x-pdb"
    elif fp.suffix.lower() == ".sdf":
        mimetype = "chemical/x-mdl-sdfile"
    elif fp.suffix.lower() == ".svg":
        mimetype = "image/svg+xml"
    elif fp.suffix.lower() in {".csv", ".tsv"}:
        mimetype = "text/csv" if fp.suffix.lower() == ".csv" else "text/tab-separated-values"
    return send_file(fp, mimetype=mimetype, as_attachment=False, download_name=fp.name)


if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=8787)
