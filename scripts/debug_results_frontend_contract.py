#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check(label: str, condition: bool) -> bool:
    print(f"{'PASS' if condition else 'FAIL'}: {label}")
    return condition


def main() -> int:
    template = (ROOT / "templates" / "results_gallery.html").read_text(encoding="utf-8", errors="ignore")
    protacable = (ROOT / "static" / "js" / "protacable.js").read_text(encoding="utf-8", errors="ignore")
    render3d = (ROOT / "static" / "js" / "3Drender.js").read_text(encoding="utf-8", errors="ignore")

    checks = [
        check("viewport loader controller exists", "window.WHViewportLoader" in template),
        check("background validation requests are tagged", "X-WH-Background-Validation" in protacable),
        check("same-pose reuse guard exists", "lastLoadedPoseKey" in protacable and "reused: true" in protacable),
        check("viewport loader is not rebound by result-card click capture handlers", "document.addEventListener('click'" not in template),
        check("protein base remains gray", 'colorValue: PROTEIN_COLOR' in render3d and 'colorValue: PROTEIN_SURFACE_COLOR' in render3d),
        check("LYS overlay uses targeted selection", "resname LYS" in render3d and 'addRepresentation("licorice"' in render3d),
        check("LYS surface overlay removed", "LYS_SURFACE_COLOR" not in render3d),
    ]
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
