# Warhead Hunter Repository Capability Audit

## Scope

This audit is grounded in direct inspection of:

- `README.md`
- `app.py`
- `routes.py`
- `job_runner.py`
- `api/`
- `pipeline_assets/`
- `templates/`
- `static/js/`
- `static/css/`
- `requirements.txt`

Ignored as requested:

- `jobs/`
- `uploads/`
- `__pycache__/`
- `.git/`
- generated runtime files

## Executive Summary

Warhead Hunter appears to be a Flask-based web application that launches per-job structure-analysis pipelines, stores outputs in job-specific directories, and presents ligand-centered results through a browser interface. The central confirmed computation is atom-level solvent exposure analysis of ligands inside protein-ligand structures, followed by 2D/3D mapping and a results gallery that prioritizes exposed ligand atoms for potential modification.

The repository supports both:

- a primary job-launch workflow based on target name, text search query, and optional FASTA sequence; and
- a secondary manual-upload workflow for precomputed mapping/structure files.

The code strongly suggests a medicinal-chemistry use case centered on identifying solvent-exposed ligand atoms that may support warhead installation, linker attachment, chemical expansion, or PROTAC-oriented exit-vector analysis. The exact scientific validation of those design outcomes is not present in the repository and should be treated as [TO VERIFY].

## What The Application Appears To Do

### Confirmed facts

- The Flask app defines user-facing pages for home, job launch, job monitoring, browsing previous jobs, results viewing, manual upload, an RCSB scouting page, an about page, and simple structure viewers.
- `job_runner.py` launches a threaded per-job pipeline and writes outputs under `jobs/<job_id>/`.
- The default pipeline downloads or retrieves structure files, prepares PDB files, computes ligand SASA, generates ligand metadata, produces 2D/3D mapping artifacts, and assembles a `TARGET_RESULTS` directory.
- The results interface uses:
  - `Resolved_SASA_Summary.csv` or `.tsv`
  - `Results_Display.csv`
  - protein PDB files
  - ligand SDF files
  - SVG ligand maps
  - per-atom SASA data
- The SASA API in `api/sasa_api.py` serves per-job atom-level exposure records from `Ligand_MCS_SASA_ALL_ATOMS.csv`.
- The results page uses NGL in the browser for 3D visualization and overlay-style highlighting of SASA-exposed ligand atoms.

### Inferred facts

- The intended scientific output is an interpretable atom-level solvent exposure map that can guide medicinal chemistry decisions about where a bound ligand may be derivatized. [INFERRED]
- The workflow is designed to move from broad structure retrieval to ligand-specific prioritization rather than from a user-supplied single complex alone. [INFERRED]
- The repository has legacy and current output conventions coexisting in parallel, with fallbacks in the Flask app to support both. [INFERRED]

## Confirmed User Workflows

### 1. Job-launch workflow

Confirmed from `templates/warhead_hunter.html`, `templates/rcsb_scout.html`, `app.py`, and `job_runner.py`.

1. User opens `/hunter` or `/scout`.
2. User provides:
   - `target_name`
   - `search_query`
   - optional `fasta_seq`
3. `POST /launch_job` calls `start_job(...)`.
4. A new `job_id` is created.
5. Pipeline assets are copied into `jobs/<job_id>/`.
6. Input metadata are written to:
   - `input.csv`
   - `Protein_Data.csv`
7. The pipeline runs stepwise inside the job directory.
8. User monitors progress at `/monitor/<job_id>`.
9. Completed outputs are browsed at `/results/<job_id>`.

### 2. Browse/open existing jobs

Confirmed from `app.py` and `templates/browse.html`.

1. User opens `/browse`.
2. App scans `jobs/`.
3. App reads `Protein_Data.csv` when present.
4. App marks whether likely results exist.
5. User can:
   - open the live monitor for active in-memory jobs
   - open results for finished jobs
   - download a zip of the job directory
   - export the job index as CSV

### 3. Manual-upload workflow

Confirmed from `templates/upload.html` and `POST /upload`.

User can upload:

- a SASA mapping file
- a 2D-3D MCS mapping file
- a ligand metadata CSV
- a scaffold CSV
- a structures ZIP or directory

Uploaded files are stored under `uploads/` subfolders. The repository confirms storage and ZIP extraction, but it does not clearly confirm a fully integrated downstream analysis path from these uploads into the main results gallery. [TO VERIFY]

### 4. Results exploration workflow

Confirmed from `routes.py`, `templates/results_gallery.html`, `static/js/protacable.js`, and `static/js/3Drender.js`.

1. `/results/<job_id>` loads normalized rows from `Resolved_SASA_Summary.csv` or `.tsv`.
2. The gallery renders cards keyed by:
   - PDB ID
   - chain
   - ligand/warhead code
   - residue/variant
3. Front-end JavaScript filters out cards lacking:
   - a retrievable protein PDB
   - a retrievable ligand SDF
   - SASA atom data when residue assignment is available
4. Selecting a card loads:
   - protein-only PDB
   - ligand SDF
   - exposed or plain SVG
   - ligand properties
   - atom-level SASA data
5. The page supports:
   - toggling between SASA-colored and plain 2D maps
   - copying SMILES
   - downloading SDF/PDB
   - sending a selected ligand into a PROTAC Builder handoff workflow

## Routes And Endpoints Discovered

### Pages

| Route | Method | Purpose | Status |
|---|---|---|---|
| `/` | GET | Home page | Confirmed |
| `/upload_manual` | GET | Manual upload page | Confirmed |
| `/hunter` | GET | Main Warhead Hunter launch form | Confirmed |
| `/browse` | GET | Browse jobs on disk | Confirmed |
| `/open_job/<job_id>` | GET | Redirect to monitor or results | Confirmed |
| `/explore` | GET | Simple structure-file explorer for uploaded PDB/CIF files | Confirmed |
| `/viewer/<path:filename>` | GET | Browser viewer page for an uploaded structure | Confirmed |
| `/structures/<path:filename>` | GET | Serves uploaded structure files | Confirmed |
| `/about` | GET | About page with simple counts | Confirmed |
| `/scout` | GET | RCSB scouting / pre-launch workflow page | Confirmed |
| `/monitor/<job_id>` | GET | Live job monitor | Confirmed |
| `/results/<job_id>` | GET | Results gallery | Confirmed |

### Job and file endpoints

| Route | Method | Purpose | Notes |
|---|---|---|---|
| `/upload` | POST | Saves uploaded files into `uploads/` subfolders | Confirmed |
| `/launch_job` | POST | Starts a pipeline job and redirects to monitor | Confirmed |
| `/api/job_log/<job_id>` | GET | Returns in-memory job status/log | Confirmed |
| `/api/job_summary/<job_id>` | GET | Returns summary counts from `Resolved_SASA_Summary.csv` | Confirmed |
| `/api/jobs/<job_id>/download` | GET | Zips and downloads the full job folder | Confirmed |
| `/api/jobs/export` | GET | Exports job index CSV | Confirmed |
| `/api/target-stats` | GET | Counts files in upload subfolders | Confirmed |

### Structure and visualization endpoints

| Route | Method | Purpose | Notes |
|---|---|---|---|
| `/api/svg/<job_id>/<pdb>/<chain>/<warhead>` | GET | Returns SASA-highlighted SVG | Confirmed |
| `/api/svg/<job_id>/<pdb>/<warhead>` | GET | Same, with chain inferred | Confirmed |
| `/api/svg-plain/<job_id>/<pdb>/<chain>/<warhead>` | GET | Returns plain SVG | Confirmed |
| `/api/svg-plain/<job_id>/<pdb>/<warhead>` | GET | Same, with chain inferred | Confirmed |
| `/api/pdb/<job_id>/<pdb_chain_warhead>.pdb` | GET | Returns full complex PDB file | Confirmed |
| `/api/protein/<job_id>/<pdb>/<chain>` | GET | Returns protein-only PDB extracted from a complex | Confirmed |
| `/api/sdf/<job_id>/<pdb>/<chain>/<ligand>` | GET | Returns ligand SDF | Confirmed |
| `/api/sdf/<job_id>/<pdb>/<ligand>` | GET | Same, with chain inferred | Confirmed |
| `/api/ligand_chain/<job_id>/<pdb>/<warhead>` | GET | Infers best chain | Confirmed |
| `/api/sasa_overlay/<job_id>/<pdb>/<chain>/<warhead>` | GET | Legacy-style SASA point list from `Warhead_SASA_atoms.csv` | Confirmed |
| `/api/sasa_atommap/<job_id>/<pdb>/<chain>/<warhead>` | GET | Reads atom index to exposure pairs from `3DSASAmapped.csv`-style files | Confirmed |

### Metadata / helper endpoints

| Route | Method | Purpose | Notes |
|---|---|---|---|
| `/api/ligand_props/<job_id>/<ligand_code>` | GET | Returns ligand properties from metadata CSV or computes them from SMILES | Confirmed |
| `/api/proxy_fasta/<pdb_id>` | GET | Fetches FASTA or PDBe text content for a PDB ID | Confirmed |
| `/api/log-builder-click` | POST | Appends a record to `static/data/builderjobs.csv` | Confirmed |
| `/api/protac-builder/active-session` | GET/POST | Stores or retrieves active Builder session metadata | Confirmed |

### `api/sasa_api.py`

| Route | Method | Purpose |
|---|---|---|
| `/api/jobs/<job_id>/sasa/available` | GET | Lists `(chain, residue_id)` combinations available for a PDB |
| `/api/jobs/<job_id>/sasa/atoms` | GET | Returns atom-level SASA records and basic summary stats |
| `/api/jobs/<job_id>/sasa/bulk` | POST | Bulk SASA lookup for multiple `(pdb, chain, residue_id)` requests |
| `/api/jobs/<job_id>/sasa/residue_for_ligand` | GET | Maps `(pdb, chain, ligand)` to `residue_id` |

### `api/handoff_server.py`

| Route | Method | Purpose |
|---|---|---|
| `/api/handoff/materialize/<job_id>/<pdb>/<chain>/<warhead>` | POST | Copies selected PDB, SDF, and SVG files to a remote filesystem via `ssh`/`scp` |

### Potential route mismatch

- `static/js/protacable.js` references `/api/handoff/prefill/...`, but no such endpoint exists in `api/handoff_server.py`. This appears to be a front-end/backend mismatch and should be treated as a confirmed limitation.

## Templates And Pages Discovered

| Template | Apparent purpose | Status |
|---|---|---|
| `templates/index.html` | Landing page with entry points | Confirmed |
| `templates/warhead_hunter.html` | Launch form for target/query/FASTA job submission | Confirmed |
| `templates/upload.html` | Manual upload form for precomputed files | Confirmed |
| `templates/browse.html` | Existing jobs table/cards | Confirmed |
| `templates/monitor.html` | Live job progress and completion screen | Confirmed |
| `templates/results_gallery.html` | Main results gallery with 2D/3D viewer panels | Confirmed |
| `templates/rcsb_scout.html` | RCSB scouting/search page that can launch jobs | Confirmed |
| `templates/about.html` | About/stats/companion-tool page | Confirmed |
| `templates/explore.html` | Uploaded structure file explorer | Confirmed |
| `templates/viewer.html` | Simple 3Dmol.js file viewer | Confirmed |
| `templates/viewer_pdb.html` | Additional PDB viewer template | Confirmed |
| `templates/base.html` | Shared layout | Confirmed |
| `templates/nav.html` | Navigation | Confirmed |
| `templates/footer.html` | Footer and companion links | Confirmed |
| `templates/error.html` | Error page | Confirmed |
| `templates/browser.html` | Present but no clear active use in inspected routes | [TO VERIFY] |

## API Files And Apparent Purpose

### `api/sasa_api.py`

Purpose:

- Provides structured JSON access to per-job, atom-level SASA results.
- Builds an in-memory cache keyed by `job_id`.
- Reads `Ligand_MCS_SASA_ALL_ATOMS.csv`.
- Exposes ligand-specific residue resolution and atom payloads suitable for front-end visualization.

Notable implementation details:

- Uses ETag caching.
- Normalizes residue IDs such as `"9001.0"` to `"9001"`.
- Stores per-key min/max/p95 exposure values.

### `api/handoff_server.py`

Purpose:

- Materializes selected Warhead Hunter outputs for a remote downstream environment.
- Searches local job outputs for:
  - source PDB
  - source SDF
  - source SVG files
- Copies those files to a hard-coded remote host (`kyle`) and base path (`/home/jxs794/VLISEMOD/static/hunter_jobs`).
- Writes a small `manifest.json` remotely.

Implication:

- This is an integration endpoint, not a general public API. It is tightly coupled to one deployment environment. Confirmed from code.

## Pipeline Scripts And Apparent Order

### Default order in `job_runner.py`

Confirmed execution order:

1. `1_GRABBER.py`
2. `2_SQchk.py`
3. `3_PDBmkr.py`
4. `4_PDBfxr.py`
5. `5_PDBcln.py`
6. `6_SASA.py`
7. `7_metadata.py --auto Warhead_SASA_summary.csv`
8. `8_scaffold.py`
9. `9_2Dmapping.py --input Resolved_SASA_Summary.csv --auto`
10. `10_2DmappingExtraction.py`
11. `11_mcsMatcher.py`
12. `12_Results.py`
13. `15_ResultsMerged.py`
14. `16_ResultsDisplay.py <job_id>`

Present but not invoked by default:

- `13_mcsSASA_svg.py`
- `14_SVGmkr.py`
- `17_obabelSDF.py`

### Script-by-script audit

| Script | Apparent role | Status |
|---|---|---|
| `1_GRABBER.py` | Searches RCSB text endpoint with PDBe fallback and downloads CIF files | Confirmed |
| `2_SQchk.py` | Uses PDBe molecules API plus FASTA matching to log chain similarity and ligand rows, excluding non-ligands | Confirmed |
| `3_PDBmkr.py` | Builds single-chain PDB files from CIFs and writes `5CharMAP.csv` and `ChainRenameMAP.csv` | Confirmed |
| `4_PDBfxr.py` | Renames 5-character ligand identifiers to shorter placeholders and rewrites HETATM records | Confirmed |
| `5_PDBcln.py` | Removes non-target HETATMs and drops ion-only or ion-ligand files | Confirmed |
| `6_SASA.py` | Computes ligand-atom SASA using `Bio.PDB.ShrakeRupley` | Confirmed |
| `7_metadata.py` | Builds `Resolved_SASA_Summary.csv`, ligand metadata, descriptor tables, and chain assignments | Confirmed |
| `8_scaffold.py` | Computes Murcko-scaffold-style summaries from resolved ligand records | Confirmed |
| `9_2Dmapping.py` | Generates target/ligand/SMILES mapping tables and ligand atom index map | Confirmed |
| `10_2DmappingExtraction.py` | Extracts 3D ligand atom coordinates from `WAR_PDB` files | Confirmed |
| `11_mcsMatcher.py` | Performs RDKit MCS-based 2D-to-3D atom mapping and generates SVG/SDF outputs | Confirmed |
| `12_Results.py` | Collects files into `TARGET_RESULTS` and builds `3DSASAmapped.csv` | Confirmed |
| `13_mcsSASA_svg.py` | Alternative or older SVG-generation path based on MCS + SASA joins | Confirmed file presence, not default pipeline |
| `14_SVGmkr.py` | Alternative or older SVG/SDF/debug writer with multiple atom-index strategies | Confirmed file presence, not default pipeline |
| `15_ResultsMerged.py` | Left-joins ligand atoms with SASA values into `Ligand_3D_Atoms_with_SASA.csv` | Confirmed |
| `16_ResultsDisplay.py` | Builds `Results_Display.csv` for gallery consumption | Confirmed |
| `17_obabelSDF.py` | Standalone PDB-to-SDF conversion helper using RDKit/Open Babel fallback | Confirmed file presence, not default pipeline |

## Input Files And Output Files Used By The Workflow

### User/job inputs

Confirmed:

- `target_name` form field
- `search_query` form field
- `fasta_seq` form field

Job initialization writes:

- `input.csv`
- `Protein_Data.csv`

### External data sources used by pipeline

Confirmed from code:

- RCSB search API
- RCSB structure download URLs for CIF and FASTA
- PDBe search endpoint fallback
- PDBe molecules API
- RCSB chemical component API in `7_metadata.py`

### Major intermediate files

Observed in code:

- `CIFdata.csv`
- `queries.csv`
- `summary.json` [TO VERIFY exact producer/consumer details across current code]
- `filtered_data.csv`
- `chain_similarity.csv`
- `5CharMAP.csv`
- `ChainRenameMAP.csv`
- `Warhead_SASA_atoms.csv`
- `Warhead_SASA_summary.csv`
- `Resolved_SASA_Summary.csv`
- `Ligand_Metadata.csv`
- `Ligand_Metadata_Failures.csv`
- `Dropped_Ion_Rows.csv`
- `Target_Table/*.csv`
- `Ligand_3D_Atoms.csv`
- `MCS_Output/Ligand_MCS_Map.csv`
- `MCS_Output/Ligand_MCS_SASA_ALL_ATOMS.csv` [INFERRED as a key step-11 output consumed by the SASA API]
- `MCS_Output/MCS_SVG/*.svg`
- `MCS_Output/MCS_SDF/*.sdf`

### Final or near-final outputs

Observed in code:

- `TARGET_RESULTS/`
- `TARGET_RESULTS/Resolved_SASA_Summary.csv`
- `TARGET_RESULTS/Results_Display.csv`
- `TARGET_RESULTS/WAR_PDB/...`
- `TARGET_RESULTS/MCS_Output/...`
- `TARGET_RESULTS/Ligand_Metadata.csv`
- `TARGET_RESULTS/Ligand_3D_Atoms.csv`
- `TARGET_RESULTS/Ligand_3D_Atoms_with_SASA.csv`
- `TARGET_RESULTS/3DSASAmapped.csv`

### Uploaded-file workflow inputs

Confirmed from `upload.html`:

- SASA mapping file
- MCS mapping file
- ligand metadata CSV
- scaffold CSV
- structures ZIP/folder

Whether those uploaded files can fully reproduce the gallery workflow without a pipeline run is not confirmed. [TO VERIFY]

## Major Dependencies

### Directly confirmed from imports

- Flask
- pandas
- requests
- urllib3
- RDKit
- Bio.PDB / Biopython
- numpy
- tqdm
- networkx

### Optional or situational dependencies inferred from code paths

- Open Babel / `obabel` for some SDF conversion paths [INFERRED]
- `sascorer` for synthetic accessibility scoring in `7_metadata.py` when available [Confirmed optional import]

### Dependency declaration status

- `requirements.txt` exists but is empty in this checkout.
- Therefore, the most reliable dependency list comes from runtime imports, not from pinned environment metadata.

## Current Limitations

### Confirmed limitations

- Job state is stored in the in-memory `JOB_STORE`; live monitor status does not appear to be persisted independently of the running Flask process.
- Pipeline execution uses background threads, not a durable queue.
- There is no authentication, authorization, or rate limiting layer.
- There is no documented public JSON submission API for single jobs or batch jobs.
- The handoff endpoint is deployment-specific and hard-coded to a remote host/path.
- `requirements.txt` is empty, so environment reproducibility is under-specified.
- `static/js/protacable.js` contains duplicated Builder/handoff logic and duplicate function names, which increases maintenance risk.
- Front-end code references `/api/handoff/prefill/...`, but the backend route is absent.
- Multiple legacy/current file naming conventions are supported by fallback code, suggesting unstable output schemas across revisions.
- File and path conventions appear essential. Many routes assume expected directory names such as `TARGET_RESULTS`, `MCS_Output`, `WAR_PDB`, and fixed filename patterns.

### Likely manuscript-facing limitations

- No benchmark dataset, validation set, or runtime characterization is present in the inspected code. Any such claims require external evidence. [TO VERIFY]
- No automated tests or CI evidence were inspected in the requested scope. [TO VERIFY]
- Supported input formats for the main automated workflow should not be overstated. The primary pipeline begins from target/query/FASTA, while some pages mention or accept uploaded structures/files.
- The repository confirms SASA-based exposure quantification and atom mapping, but does not itself validate that every highlighted atom is synthetically tractable or biologically tolerated.

## Assumptions Or Fragile Areas

### File naming and folder structure

- Several scripts rely on filenames of the form `<pdb>_<chain>_<ligand>.pdb`.
- Residue, ligand, and chain inference often come from filenames rather than only structured metadata.
- The app uses multiple fallback search paths for the same artifacts, indicating that file layout may vary across runs or versions.

### Mixed old/new conventions

- The code checks both `MCS_OUTPUT` and `MCS_Output`.
- The app supports both legacy `LIGAND_SVGS`-style outputs and newer `MCS_SVG` outputs.
- Results loading normalizes multiple possible column names for exposure and ligand identity.

### External service reliance

- Structure retrieval depends on RCSB and PDBe availability.
- `requests` often runs with TLS verification disabled.
- Remote handoff depends on SSH access to a named host.

### Front-end/backend drift

- Missing `prefill` route suggests integration drift.
- The RCSB Scout page posts extra fields to `/launch_job`, but the backend currently ignores them.

## Confirmed Facts Vs Inferred Facts

### Confirmed facts

- Flask app with blueprints in `api/sasa_api.py`, `routes.py`, and `api/handoff_server.py`
- Threaded per-job execution model
- SASA computation using `Bio.PDB.ShrakeRupley`
- RDKit-based ligand descriptor and mapping logic
- NGL-based 3D front-end viewer
- JSON endpoints for atom-level SASA retrieval
- Results gallery built from job-directory CSV/SVG/SDF/PDB artifacts

### Inferred facts

- The application is intended to prioritize modification sites for covalent warhead design, linker installation, and PROTAC work. [INFERRED]
- The highlighted atoms are meant to be interpreted as candidate attachment vectors rather than exhaustive design solutions. [INFERRED]
- The companion-tool ecosystem is intended for downstream reuse of Warhead Hunter outputs. [INFERRED]

## Audit Bottom Line

The repository clearly supports a manuscript centered on:

- ligand-bound structure retrieval and preparation,
- atom-level ligand SASA analysis in protein context,
- 2D/3D atom mapping,
- interactive browser-based interpretation of exposed ligand atoms.

The strongest claims should stay focused on what the code demonstrably does:

- compute or aggregate atom-level ligand exposure metrics,
- map those metrics back onto interpretable ligand depictions,
- and present them in a job-oriented web workflow.

Claims about validation, scale, robustness, supported formats, public API readiness, or medicinal chemistry success rates should remain marked [TO VERIFY] unless additional evidence is assembled outside this repository.
