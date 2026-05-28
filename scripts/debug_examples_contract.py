#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    CURATED_EXAMPLE_CONFIG,
    app,
    build_curated_example_entry,
    get_curated_example_by_id,
    safe_job_dir,
)


PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


def mark(ok: bool, warn: bool = False) -> str:
    if ok:
        return PASS
    return WARN if warn else FAIL


def check_example(job_id: str) -> tuple[str, list[str]]:
    item = get_curated_example_by_id(job_id)
    if not item:
        return FAIL, [f"{FAIL} configured example missing from resolver: {job_id}"]

    entry = build_curated_example_entry(item, include_preview=True)
    client = app.test_client()

    lines: list[str] = []
    base = safe_job_dir(job_id)
    job_exists = bool(base and base.exists())
    lines.append(f"{mark(job_exists)} job directory exists: {job_exists}")
    lines.append(f"{mark(entry['available'] == job_exists)} availability flag matches disk: {entry['available']}")
    lines.append(f"{mark(entry['has_results'], warn=job_exists)} has results: {entry['has_results']}")

    route_checks = [
        (f"/examples/{job_id}", "example page", {200}),
        (f"/api/examples/{job_id}/metadata", "metadata endpoint", {200}),
        (f"/api/examples/{job_id}/files", "files endpoint", {200}),
        (f"/api/examples/{job_id}/war-pdbs", "WAR_PDB list endpoint", {200}),
        (f"/api/examples/{job_id}/war-pdbs.zip", "WAR_PDB ZIP endpoint", {200, 404}),
        (f"/api/examples/{job_id}/bundle", "bundle endpoint", {200, 404}),
    ]
    route_ok = True
    for path, label, allowed in route_checks:
        resp = client.get(path)
        ok = resp.status_code in allowed
        route_ok = route_ok and ok
        lines.append(f"{mark(ok)} {label}: {resp.status_code} {path}")

    counts = entry["counts"]
    lines.append(f"{PASS} result rows: {counts['result_rows']}")
    lines.append(f"{PASS} WAR_PDB count: {counts['war_pdb_count']}")
    lines.append(f"{PASS} SDF count: {counts['sdf_count']}")
    lines.append(f"{PASS} SVG count: {counts['svg_count']}")
    lines.append(f"{PASS} CSV count: {counts['csv_count']}")
    lines.append(f"{PASS} table count: {counts['table_count']}")
    lines.append(f"{PASS} bundle available: {counts['bundle_available']}")
    lines.append(f"{PASS} status: {entry['status']}")
    lines.append(f"{PASS} status reason: {entry['status_reason']}")
    lines.append(f"{PASS} preview rows: {len(entry.get('preview_rows') or [])}")

    artifact_present = (
        counts["result_rows"] > 0
        or counts["war_pdb_count"] > 0
        or counts["sdf_count"] > 0
        or counts["svg_count"] > 0
    )
    lines.append(f"{mark(artifact_present, warn=job_exists)} result display artifact presence: {artifact_present}")

    overall = PASS if job_exists and route_ok else WARN if route_ok else FAIL
    return overall, lines


def main() -> int:
    selected = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    configured = [item["job_id"] for item in CURATED_EXAMPLE_CONFIG]
    if selected:
        configured = [jid for jid in configured if jid == selected]
        if not configured:
            print(f"{FAIL}: job_id is not configured as a curated example: {selected}")
            return 2

    client = app.test_client()
    top_checks = [
        ("/examples", "examples index", {200}),
        ("/api/examples", "examples API index", {200}),
    ]

    print("# Curated Example Contract Diagnostic")
    print(f"configured examples: {', '.join(configured)}")
    print()

    for path, label, allowed in top_checks:
        resp = client.get(path)
        ok = resp.status_code in allowed
        print(f"{mark(ok)} {label}: {resp.status_code} {path}")

    unknown = client.get("/examples/not-a-curated-example")
    print(f"{mark(unknown.status_code == 404)} unknown example page route: {unknown.status_code} /examples/not-a-curated-example")
    print()

    summary = {PASS: 0, WARN: 0, FAIL: 0}
    report: dict[str, dict[str, object]] = {}

    for job_id in configured:
        overall, lines = check_example(job_id)
        summary[overall] += 1
        report[job_id] = {"overall": overall, "lines": lines}
        print(f"[{overall}] {job_id}")
        for line in lines:
            print(f"  {line}")
        print()

    print("summary:")
    print(json.dumps(summary, indent=2))
    return 0 if summary[FAIL] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
