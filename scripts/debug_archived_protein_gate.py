#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlencode

import requests


DEFAULT_BASE_URL = "https://warheadhunter.com"


class CardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cards: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        classes = set((attr.get("class") or "").split())
        if tag == "div" and "result-card" in classes:
            self.cards.append({
                "pdb": attr.get("data-pdb", "").strip().lower(),
                "chain": attr.get("data-chain", "").strip().upper(),
                "ligand": attr.get("data-warhead", "").strip().upper(),
                "resid": attr.get("data-resid", "").strip(),
            })


def head_status(url: str) -> int:
    try:
        resp = requests.head(url, timeout=20, allow_redirects=False)
        if resp.status_code == 405:
            resp = requests.get(url, timeout=20, stream=True)
        return resp.status_code
    except Exception:
        return 0


def fetch_cards(base_url: str, job_id: str) -> list[dict[str, str]]:
    url = f"{base_url}/results/{quote(job_id)}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    parser = CardParser()
    parser.feed(resp.text)
    return parser.cards


def endpoint(base_url: str, path: str, query: dict[str, str] | None = None) -> str:
    url = f"{base_url}{path}"
    if query:
        clean = {k: v for k, v in query.items() if v}
        if clean:
            url += f"?{urlencode(clean)}"
    return url


def row_report(base_url: str, job_id: str, row: dict[str, str], index: int) -> dict[str, Any]:
    pdb = row["pdb"]
    chain = row["chain"]
    ligand = row["ligand"]
    resid = row["resid"]
    residue_status = 0
    if not resid and pdb and chain and ligand:
        residue_url = endpoint(
            base_url,
            f"/api/jobs/{quote(job_id)}/sasa/residue_for_ligand",
            {"pdb_id": pdb, "chain": chain, "ligand": ligand},
        )
        try:
            residue_resp = requests.get(residue_url, timeout=20)
            residue_status = residue_resp.status_code
            if residue_resp.ok:
                payload = residue_resp.json()
                resid = str(payload.get("resid") or payload.get("residue_id") or "").strip()
        except Exception:
            residue_status = 0
    stem = f"{pdb}_{chain}_{ligand}"
    expected = {
        "pdb": f"{stem}.pdb",
        "sdf": f"{stem}_{resid}.sdf" if resid else f"{stem}_<resid>.sdf",
        "svg_plain": f"{stem}_{resid}_plain.svg" if resid else f"{stem}_<resid>_plain.svg",
        "svg_exposed": f"{stem}_{resid}_exposed.svg" if resid else f"{stem}_<resid>_exposed.svg",
    }
    qs = {"resid": resid}
    protein_qs = {"ligand": ligand, "resid": resid}
    urls = {
        "protein": endpoint(base_url, f"/api/protein/{quote(job_id)}/{quote(pdb)}/{quote(chain)}", protein_qs),
        "sdf": endpoint(base_url, f"/api/sdf/{quote(job_id)}/{quote(pdb)}/{quote(chain)}/{quote(ligand)}", qs),
        "pdb": endpoint(base_url, f"/api/pdb/{quote(job_id)}/{quote(stem)}.pdb"),
        "svg": endpoint(base_url, f"/api/svg/{quote(job_id)}/{quote(pdb)}/{quote(chain)}/{quote(ligand)}", qs),
        "svg_plain": endpoint(base_url, f"/api/svg-plain/{quote(job_id)}/{quote(pdb)}/{quote(chain)}/{quote(ligand)}", qs),
    }
    status = {name: head_status(url) for name, url in urls.items()}
    hard_missing = []
    if status["sdf"] != 200:
        hard_missing.append("sdf")
    if status["protein"] != 200:
        hard_missing.append("protein")
    return {
        "index": index,
        "pdb": pdb,
        "chain": chain,
        "ligand": ligand,
        "resid": resid,
        "expected": expected,
        "status": status,
        "residue_status": residue_status,
        "renderable": not hard_missing,
        "missing_hard": hard_missing,
    }


def main() -> int:
    job_id = sys.argv[1] if len(sys.argv) > 1 else "b82be2c2"
    base_url = (os.environ.get("WARHEAD_HUNTER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    limit = int(os.environ.get("DEBUG_ARCHIVED_GATE_LIMIT", "8"))

    print(f"base_url: {base_url}")
    print(f"job_id: {job_id}")
    cards = fetch_cards(base_url, job_id)
    print(f"cards found: {len(cards)}")
    if not cards:
        print("FAIL: no result cards found")
        return 1

    reports = [row_report(base_url, job_id, row, idx) for idx, row in enumerate(cards[:limit], start=1)]
    for report in reports:
        exp = report["expected"]
        status = report["status"]
        print(
            f"{report['index']:02d}. {report['pdb']} {report['chain']} {report['ligand']} resid={report['resid']} "
            f"renderable={report['renderable']}"
        )
        print(f"    expected PDB={exp['pdb']} SDF={exp['sdf']} SVG={exp['svg_exposed']} / {exp['svg_plain']}")
        print(
            f"    status protein={status['protein']} sdf={status['sdf']} pdb={status['pdb']} "
            f"svg={status['svg']} svg_plain={status['svg_plain']} residue_lookup={report['residue_status'] or 'skipped'}"
        )
        if report["missing_hard"]:
            print(f"    missing hard artifact: {', '.join(report['missing_hard'])}")

    renderable = [r for r in reports if r["renderable"]]
    protein_failures = [r for r in reports if r["status"]["sdf"] == 200 and r["status"]["protein"] != 200]
    print(f"renderable in first {len(reports)}: {len(renderable)}")
    if protein_failures:
        print("FAIL: SDF is available but protein is missing for at least one checked row.")
        return 1
    if not renderable:
        print("FAIL: no checked row has both SDF and protein.")
        return 1
    print("PASS: at least one checked row has the hard SDF/protein pair.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
