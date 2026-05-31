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
    css = (ROOT / "static" / "css" / "protacable.css").read_text(encoding="utf-8", errors="ignore")
    protacable = (ROOT / "static" / "js" / "protacable.js").read_text(encoding="utf-8", errors="ignore")
    render3d = (ROOT / "static" / "js" / "3Drender.js").read_text(encoding="utf-8", errors="ignore")

    checks = [
        check("template contains a stable protein sticks toggle button", 'id="protein-sticks-toggle"' in template),
        check("template places the button in the top-right viewer control wrapper", 'class="viewer-control-topright"' in template),
        check("viewer control CSS positions the button overlay", ".viewer-control-topright" in css and ".viewer-control-btn" in css),
        check("Render3D exposes protein stick visibility state", "proteinStickVisible" in render3d and "proteinStickReprs" in render3d),
        check("Render3D exposes public protein stick toggle methods", "setProteinSticksVisible" in render3d and "toggleProteinSticks" in render3d and "getProteinSticksVisible" in render3d),
        check("protein stick representations are explicitly remembered", "rememberProteinStickRepresentation(comp.addRepresentation(\"licorice\"" in render3d),
        check("ligand component is not added to the protein stick registry", "rememberProteinStickRepresentation(comp.addRepresentation(\"ball+stick\"" not in render3d and "rememberProteinStickRepresentation(comp.addRepresentation(\"line\"" not in render3d),
        check("existing ligand representations remain present", 'comp.addRepresentation("ball+stick"' in render3d and 'comp.addRepresentation("line"' in render3d),
        check("toggle binding is protected against duplicates", 'btn.dataset.bound = "1"' in protacable and "proteinSticksToggleBound" in protacable),
        check("toggle button syncs from Render3D state after loads", "updateProteinSticksToggleButton();" in protacable and "window.Render3D.toggleProteinSticks" in protacable),
        check("no broad global licorice hide logic was introduced", 'reprList.forEach' not in protacable and 'querySelectorAll("[data-repr-type' not in protacable),
    ]
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
