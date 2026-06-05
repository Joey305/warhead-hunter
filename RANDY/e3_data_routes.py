from __future__ import annotations

import io
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Blueprint, abort, current_app, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_E3_DATA_DIR = PROJECT_ROOT
DEFAULT_E3_DB_PATH = PROJECT_ROOT / "Ligases" / "Ligase_Recruiter.db"
DEFAULT_E3_ELIAH_DB_PATH = PROJECT_ROOT / "Ligases" / "eliah.db"
DEFAULT_E3_ASSET_ROOT = PROJECT_ROOT / "Ligases"
DEFAULT_E3_TABLE_ROOT = PROJECT_ROOT / "Ligase_Table"
DEFAULT_E3_SHIPMENT_DB_PATH = Path(
    os.environ.get("E3_SHIPMENT_DB_PATH", "/home/jxs794/PROTAC_BUILDER/data/e3_shipments.db")
).expanduser()

MAX_QUERY_ROWS = int(os.environ.get("E3_MAX_QUERY_ROWS", "50000"))
SAFE_SQL_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
UNSAFE_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|VACUUM|ATTACH|DETACH|PRAGMA)\b",
    re.IGNORECASE,
)
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _token() -> str:
    return (
        os.environ.get("E3_RANDY_TOKEN", "").strip()
        or os.environ.get("RANDY_E3_TOKEN", "").strip()
        or os.environ.get("RANDY_BACKUP_TOKEN", "").strip()
        or os.environ.get("PROTAC_BACKUP_TOKEN", "").strip()
    )


def _data_dir() -> Path:
    return Path(os.environ.get("E3_DATA_DIR", str(DEFAULT_E3_DATA_DIR))).expanduser()


def _db_path() -> Path:
    return Path(os.environ.get("E3_DB_PATH", str(DEFAULT_E3_DB_PATH))).expanduser()


def _eliah_db_path() -> Path:
    return Path(os.environ.get("E3_ELIAH_DB_PATH", str(DEFAULT_E3_ELIAH_DB_PATH))).expanduser()


def _asset_root() -> Path:
    return Path(os.environ.get("E3_ASSET_ROOT", str(DEFAULT_E3_ASSET_ROOT))).expanduser()


def _table_root() -> Path:
    return Path(os.environ.get("E3_TABLE_ROOT", str(DEFAULT_E3_TABLE_ROOT))).expanduser()


def _shipment_db_path() -> Path:
    return Path(os.environ.get("E3_SHIPMENT_DB_PATH", str(DEFAULT_E3_SHIPMENT_DB_PATH))).expanduser()


def _safe_under(base: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def _validate_ligase_name(ligase: str) -> str:
    ligase = str(ligase or "").strip()
    if not ligase or not SAFE_NAME_RE.fullmatch(ligase):
        abort(400, description="Invalid ligase name.")
    return ligase


def _resolve_ligase_dir(ligase: str) -> Path:
    ligase = _validate_ligase_name(ligase)
    asset_root = _asset_root().resolve()
    if not asset_root.is_dir():
        abort(404, description=f"Asset root not found: {asset_root}")

    exact = (asset_root / ligase).resolve()
    if exact.is_dir() and _safe_under(asset_root, exact):
        return exact

    ligase_lower = ligase.lower()
    for item in asset_root.iterdir():
        if item.is_dir() and item.name.lower() == ligase_lower and _safe_under(asset_root, item):
            return item.resolve()

    abort(404, description=f"Ligase not found: {ligase}")


def _validate_relative_filename(filename: str, allowed_exts: set[str]) -> str:
    filename = str(filename or "").strip()
    if not filename:
        abort(400, description="Missing filename.")
    rel = Path(filename)
    if rel.is_absolute() or ".." in rel.parts:
        abort(400, description="Invalid filename.")
    if any(part in {"", "."} for part in rel.parts):
        abort(400, description="Invalid filename.")
    if rel.suffix.lower() and rel.suffix.lower() not in allowed_exts:
        abort(400, description=f"Unsupported file type: {rel.suffix}")
    return rel.as_posix()


def _candidate_names(filename: str, extension: str) -> list[str]:
    rel_name = _validate_relative_filename(filename, {extension})
    basename = Path(rel_name).name
    stem = Path(basename).stem
    suffix = Path(basename).suffix.lower()
    if suffix and suffix != extension:
        abort(400, description=f"Unsupported file type: {suffix}")

    normalized_stem = stem if suffix else basename
    core_no_variant = re.sub(r"_\d+$", "", normalized_stem)
    candidates = [f"{normalized_stem}{extension}"]
    if core_no_variant != normalized_stem:
        candidates.append(f"{core_no_variant}{extension}")
    candidates.append(f"{core_no_variant}_1{extension}")
    return list(dict.fromkeys(candidates))


def _find_variant_file(folder: Path, filename: str, extension: str) -> Path:
    if not folder.is_dir():
        abort(404, description=f"Folder not found: {folder.name}")

    seen: set[str] = set()
    for candidate_name in _candidate_names(filename, extension):
        if candidate_name in seen:
            continue
        seen.add(candidate_name)
        candidate = (folder / candidate_name).resolve()
        if candidate.is_file() and _safe_under(folder, candidate):
            return candidate

    requested = Path(filename).name
    stem = Path(requested).stem if requested.lower().endswith(extension) else requested
    core_no_variant = re.sub(r"_\d+$", "", stem)
    variant_re = re.compile(rf"^{re.escape(core_no_variant)}_(\d+){re.escape(extension)}$", re.IGNORECASE)
    for candidate in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if candidate.is_file() and variant_re.fullmatch(candidate.name) and _safe_under(folder, candidate):
            return candidate.resolve()

    abort(404, description=f"File not found: {requested}")


def _resolve_sdf_folders(ligase_dir: Path) -> list[Path]:
    folders = [folder for folder in [ligase_dir / "SDF_4Download", ligase_dir / "SDF"] if folder.is_dir()]
    if folders:
        return folders
    abort(404, description=f"No SDF folder found for {ligase_dir.name}.")


def _iter_asset_files(ligase_dir: Path, asset_type: str) -> list[tuple[str, Path]]:
    key = str(asset_type or "").strip().lower()
    options = {
        "pdb": ("PDB", [("PDB", ligase_dir / "PDB", {".pdb"})]),
        "pdbs": ("pdb", [("PDB", ligase_dir / "PDB", {".pdb"})]),
        "sdf": ("SDF", [("SDF_4Download", ligase_dir / "SDF_4Download", {".sdf"}), ("SDF", ligase_dir / "SDF", {".sdf"})]),
        "sdfs": ("sdf", [("SDF_4Download", ligase_dir / "SDF_4Download", {".sdf"}), ("SDF", ligase_dir / "SDF", {".sdf"})]),
        "all": ("all", [("PDB", ligase_dir / "PDB", {".pdb"}), ("SDF_4Download", ligase_dir / "SDF_4Download", {".sdf"}), ("SDF", ligase_dir / "SDF", {".sdf"})]),
        "structures": ("all", [("PDB", ligase_dir / "PDB", {".pdb"}), ("SDF_4Download", ligase_dir / "SDF_4Download", {".sdf"}), ("SDF", ligase_dir / "SDF", {".sdf"})]),
    }
    if key not in options:
        abort(400, description="asset_type must be one of: pdb, pdbs, sdf, sdfs, all, structures")

    _, folders = options[key]
    files: list[tuple[str, Path]] = []
    seen_paths: set[Path] = set()
    for label, folder, exts in folders:
        if not folder.is_dir():
            continue
        for child in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
            resolved = child.resolve()
            if (
                child.is_file()
                and child.suffix.lower() in exts
                and _safe_under(folder, resolved)
                and resolved not in seen_paths
            ):
                seen_paths.add(resolved)
                files.append((label, resolved))
    return files


def _list_download_ligase_dirs() -> list[Path]:
    asset_root = _asset_root().resolve()
    if not asset_root.is_dir():
        return []
    out = []
    for item in asset_root.iterdir():
        if not item.is_dir():
            continue
        if any((item / name).is_dir() for name in ("PDB", "SDF_4Download", "SDF")) and _safe_under(asset_root, item):
            out.append(item.resolve())
    return sorted(out, key=lambda path: path.name.lower())


def _zip_response(files: Iterable[tuple[str, Path]], download_name: str):
    file_list = list(files)
    if not file_list:
        abort(404, description="No matching files.")

    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zip_handle:
        for arcname, path in file_list:
            zip_handle.write(path, arcname.replace("\\", "/"))
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=download_name,
        max_age=0,
    )


def _validate_sql(sql: str) -> str:
    sql = str(sql or "").strip()
    if not sql:
        raise ValueError("Missing SQL query.")
    if not SAFE_SQL_RE.match(sql):
        raise ValueError("Only SELECT and WITH queries are allowed.")
    if UNSAFE_SQL_RE.search(sql):
        raise ValueError("Write or schema-changing SQL is not allowed.")
    if ";" in sql.rstrip(";"):
        raise ValueError("Multiple SQL statements are not allowed.")
    return sql.rstrip(";")


def _database_path(name: str) -> Path:
    database = str(name or "main").strip().lower()
    if database == "main":
        return _db_path()
    if database == "eliah":
        return _eliah_db_path()
    raise ValueError("database must be 'main' or 'eliah'")


def _ensure_shipment_store() -> None:
    db_path = _shipment_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS e3_shipment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                recruiter_code TEXT,
                client_ip TEXT,
                session_id TEXT UNIQUE,
                skip_modify INTEGER DEFAULT 0,
                source TEXT,
                status TEXT DEFAULT 'success',
                backend_mode TEXT,
                metadata_json TEXT
            )
            """
        )
        conn.commit()


def _normalize_shipment_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata_json")
    if not isinstance(metadata, dict):
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "created_at": str(payload.get("created_at") or "").strip(),
        "recruiter_code": str(payload.get("recruiter_code") or "").strip(),
        "client_ip": str(payload.get("client_ip") or "").strip(),
        "session_id": str(payload.get("session_id") or "").strip(),
        "skip_modify": 1 if bool(payload.get("skip_modify")) else 0,
        "source": str(payload.get("source") or "convert_atom_to_v").strip(),
        "status": str(payload.get("status") or "success").strip(),
        "backend_mode": str(payload.get("backend_mode") or "remote").strip(),
        "metadata_json": metadata,
    }


def _shipment_created_at(payload: dict[str, Any]) -> str:
    value = str(payload.get("created_at") or "").strip()
    if value:
        return value
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _store_shipment_event(payload: dict[str, Any]) -> bool:
    _ensure_shipment_store()
    clean = _normalize_shipment_payload(payload)
    clean["created_at"] = _shipment_created_at(payload)
    with sqlite3.connect(_shipment_db_path()) as conn:
        before = conn.total_changes
        conn.execute(
            """
            INSERT INTO e3_shipment_events (
                created_at, recruiter_code, client_ip, session_id, skip_modify,
                source, status, backend_mode, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO NOTHING
            """,
            (
                clean["created_at"],
                clean["recruiter_code"],
                clean["client_ip"],
                clean["session_id"],
                clean["skip_modify"],
                clean["source"],
                clean["status"],
                clean["backend_mode"],
                json.dumps(clean["metadata_json"], sort_keys=True),
            ),
        )
        conn.commit()
        return conn.total_changes > before


def _shipment_success_count() -> int:
    _ensure_shipment_store()
    with sqlite3.connect(_shipment_db_path()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM e3_shipment_events WHERE status = ?",
            ("success",),
        ).fetchone()
    return int(row[0] if row else 0)


def register_e3_routes(app) -> None:
    if "randy_e3" in app.blueprints:
        return

    bp = Blueprint("randy_e3", __name__, url_prefix="/backup/e3")

    @bp.before_request
    def _require_e3_auth():
        if not request.path.startswith("/backup/e3"):
            return None
        token = _token()
        if not token:
            return jsonify({"ok": False, "error": "E3 RANDY token is not configured."}), 500
        if request.headers.get("Authorization", "") != f"Bearer {token}":
            return jsonify({"ok": False, "error": "Unauthorized."}), 401
        return None

    @bp.errorhandler(HTTPException)
    def _json_http_error(error: HTTPException):
        response = error.get_response()
        response.data = jsonify({"ok": False, "error": error.description or error.name}).get_data()
        response.content_type = "application/json"
        return response

    @bp.errorhandler(Exception)
    def _json_unhandled_error(error: Exception):
        current_app.logger.exception("Unhandled RANDY E3 route error")
        return jsonify({"ok": False, "error": str(error)}), 500

    @bp.get("/healthz")
    def e3_healthz():
        return jsonify(
            {
                "ok": True,
                "service": "randy-e3-data",
                "data_dir": str(_data_dir()),
                "main_db_exists": _db_path().exists(),
                "eliah_db_exists": _eliah_db_path().exists(),
                "asset_root": str(_asset_root()),
                "asset_root_exists": _asset_root().exists(),
                "table_root": str(_table_root()),
                "table_root_exists": _table_root().exists(),
                "shipment_db_path": str(_shipment_db_path()),
                "shipment_db_exists": _shipment_db_path().exists(),
            }
        )

    @bp.post("/query")
    def e3_query():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "Expected JSON object."}), 400

        try:
            sql = _validate_sql(payload.get("sql", ""))
            db_path = _database_path(payload.get("database", "main"))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        params = payload.get("params", [])
        if not isinstance(params, list):
            return jsonify({"ok": False, "error": "params must be a list."}), 400
        if not db_path.exists():
            return jsonify({"ok": False, "error": f"Database not found: {db_path}"}), 404

        one = bool(payload.get("one"))
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(row) for row in conn.execute(sql, tuple(params)).fetchmany(MAX_QUERY_ROWS + 1)]

        truncated = len(rows) > MAX_QUERY_ROWS
        rows = rows[:MAX_QUERY_ROWS]
        if one:
            rows = rows[:1]

        return jsonify(
            {
                "ok": True,
                "database": str(payload.get("database") or "main"),
                "count": len(rows),
                "truncated": truncated,
                "rows": rows,
            }
        )

    @bp.post("/shipments")
    def store_shipment():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "Expected JSON object."}), 400

        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return jsonify({"ok": False, "error": "session_id is required."}), 400

        stored = _store_shipment_event(payload)
        return jsonify(
            {
                "ok": True,
                "stored": stored,
                "duplicate": not stored,
                "source": "randy",
            }
        )

    @bp.get("/shipments/count")
    def shipment_count():
        return jsonify(
            {
                "ok": True,
                "total": _shipment_success_count(),
                "source": "randy",
                "backup_ok": True,
            }
        )

    @bp.get("/ligase-pdbs/<ligase>")
    def ligase_pdbs(ligase: str):
        folder = _resolve_ligase_dir(ligase) / "PDB"
        if not folder.is_dir():
            return jsonify([])
        files = sorted(path.name for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".pdb")
        return jsonify(files)

    @bp.get("/file/pdb/<ligase>/<path:filename>")
    def file_pdb(ligase: str, filename: str):
        ligase_dir = _resolve_ligase_dir(ligase)
        file_path = _find_variant_file(ligase_dir / "PDB", filename, ".pdb")
        return send_file(file_path, mimetype="chemical/x-pdb", as_attachment=False, max_age=0)

    @bp.get("/file/sdf/<ligase>/<path:filename>")
    def file_sdf(ligase: str, filename: str):
        ligase_dir = _resolve_ligase_dir(ligase)
        for folder in _resolve_sdf_folders(ligase_dir):
            try:
                file_path = _find_variant_file(folder, filename, ".sdf")
                return send_file(file_path, mimetype="chemical/x-mdl-sdfile", as_attachment=False, max_age=0)
            except HTTPException as exc:
                if exc.code != 404:
                    raise
        abort(404, description=f"SDF not found: {ligase}/{filename}")

    @bp.get("/download/ligase/<ligase>/<asset_type>.zip")
    def download_ligase_zip(ligase: str, asset_type: str):
        ligase_dir = _resolve_ligase_dir(ligase)
        files = [
            (f"{ligase_dir.name}/{label}/{path.name}", path)
            for label, path in _iter_asset_files(ligase_dir, asset_type)
        ]
        return _zip_response(files, f"E3Ligandalyzer_{ligase_dir.name}_{asset_type.lower()}.zip")

    @bp.get("/download/all/<asset_type>.zip")
    def download_all_zip(asset_type: str):
        files = []
        for ligase_dir in _list_download_ligase_dirs():
            for label, path in _iter_asset_files(ligase_dir, asset_type):
                files.append((f"{ligase_dir.name}/{label}/{path.name}", path))
        return _zip_response(files, f"E3Ligandalyzer_ALL_{asset_type.lower()}.zip")

    @bp.get("/download/table/<path:table_name>")
    @bp.get("/download/table/<path:table_name>.csv")
    def download_table_csv(table_name: str):
        table_name = str(table_name or "").strip()
        if table_name.lower().endswith(".csv"):
            table_name = table_name[:-4]
        if not table_name or not SAFE_NAME_RE.fullmatch(table_name):
            abort(400, description="Invalid table name.")

        table_root = _table_root().resolve()
        if not table_root.is_dir():
            abort(404, description=f"Table root not found: {table_root}")

        csv_path = (table_root / f"{table_name}.csv").resolve()
        if not _safe_under(table_root, csv_path):
            abort(400, description="Invalid table name.")
        if not csv_path.is_file() or csv_path.suffix.lower() != ".csv":
            abort(404, description=f"CSV table not found: {table_name}.csv")

        return send_file(
            csv_path,
            mimetype="text/csv",
            as_attachment=True,
            download_name=csv_path.name,
            max_age=0,
        )

    app.register_blueprint(bp)
