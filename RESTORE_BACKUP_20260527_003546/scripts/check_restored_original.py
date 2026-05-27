#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ORDER = [
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
    "15_ResultsMerged.py",
    "16_ResultsDisplay.py",
]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def active_script_order(job_runner_source: str) -> list[str]:
    tree = ast.parse(job_runner_source)
    for node in ast.walk(tree):
        value = None
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "scripts" for t in node.targets):
            value = node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "scripts":
            value = node.value
        if value is None:
            continue
        scripts: list[str] = []
        for item in value.elts:
            if isinstance(item, ast.Tuple) and item.elts and isinstance(item.elts[0], ast.Constant):
                scripts.append(str(item.elts[0].value))
        return scripts
    return []


def passfail(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"{status}: {name}{suffix}")
    return ok


def main() -> int:
    checks: list[bool] = []
    app_source = read("app.py")
    runner_source = read("job_runner.py")

    checks.append(passfail("app.py uses port 5070", "port=5070" in app_source))
    checks.append(passfail("job_runner.py exists", (ROOT / "job_runner.py").exists()))
    checks.append(passfail("job_runner.py defines PYTHON_BIN", "PYTHON_BIN" in runner_source and "sys.executable" in runner_source))
    checks.append(passfail("active subprocesses use PYTHON_BIN", '["python3"' not in runner_source and "[PYTHON_BIN" in runner_source))
    checks.append(passfail("pipeline_assets exists", (ROOT / "pipeline_assets").is_dir()))
    checks.append(passfail("api exists", (ROOT / "api").is_dir()))
    checks.append(passfail("templates/results_gallery.html exists", (ROOT / "templates/results_gallery.html").exists()))
    checks.append(passfail("static/js/protacable.js exists", (ROOT / "static/js/protacable.js").exists()))
    checks.append(passfail("lowercase components SMILES exists", (ROOT / "pipeline_assets/components-smiles-stereo-oe.smi").exists()))

    order = active_script_order(runner_source)
    checks.append(passfail("active script order matches corrected ORIGINAL", order == EXPECTED_ORDER, ", ".join(order)))

    user_facing_sources = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for folder in ("static", "templates")
        for p in (ROOT / folder).rglob("*")
        if p.is_file()
    )
    checks.append(passfail("Kyle URL is not default in static/templates", "kyle.rove-vernier.ts.net" not in user_facing_sources))
    checks.append(passfail("PROTAC Builder default is public", "https://protacbuilder.com/copy/COPYindex" in user_facing_sources or "https://protacbuilder.com/copy/COPYindex" in app_source))

    if all(checks):
        print("PASS")
        return 0
    print("FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
