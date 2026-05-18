# Methods From Code

## Scope

This methods note is intentionally constrained to behaviors confirmed or strongly suggested by the repository code. When implementation details are uncertain, they are marked [TO VERIFY] or [INFERRED].

## Flask Application Structure

The web application is implemented in `app.py` as a Flask server with three registered blueprints:

- `routes_bp` from `routes.py`
- `sasa_bp` from `api/sasa_api.py`
- `hand_bp` from `api/handoff_server.py`

The application sets:

- `UPLOAD_FOLDER` to `uploads/`
- `JOBS_DIR` to `jobs/`

During startup it creates upload subdirectories for:

- `sasa`
- `mcs`
- `metadata`
- `scaffold`
- `structures`

The app defines helpers for job-directory resolution, result-file lookup, column normalization, exposure bucketing, and chain/residue inference from result tables.

## Route / Page Structure

### Launch and navigation pages

- `/` renders `index.html`
- `/hunter` renders `warhead_hunter.html`
- `/scout` renders `rcsb_scout.html`
- `/upload_manual` renders `upload.html`
- `/browse` renders `browse.html`
- `/about` renders `about.html`
- `/explore` renders `explore.html`

### Job lifecycle

- `POST /launch_job` starts a background job and redirects to `/monitor/<job_id>`
- `/monitor/<job_id>` renders `monitor.html`
- `/api/job_log/<job_id>` returns current in-memory status/log data
- `/api/job_summary/<job_id>` returns a simple summary based on `Resolved_SASA_Summary.csv`
- `/api/jobs/<job_id>/download` returns a ZIP archive of the full job directory

### Results

- `/results/<job_id>` is defined in `routes.py`
- It loads `Resolved_SASA_Summary.csv` or `.tsv`
- It normalizes columns such as `pdb_id`, `%Exposed`, `%Buried`, `Chain`, `Residue_ID`, and ligand identifier fields
- It deduplicates rows by pose key and selects a best row by maximum `%Exposed`
- It renders `results_gallery.html`

### Visualization and data-serving routes

- `/api/svg/...` serves exposure-highlighted SVG ligand depictions
- `/api/svg-plain/...` serves plain ligand SVG depictions
- `/api/pdb/...` serves complex PDB files
- `/api/protein/...` serves protein-only PDB content extracted from complex files
- `/api/sdf/...` serves ligand SDF files
- `/api/ligand_props/...` returns ligand descriptors from metadata CSV or RDKit fallback calculations
- `/api/ligand_chain/...` infers the best chain for a ligand
- `/api/sasa_overlay/...` serves legacy-style per-atom exposure points from `Warhead_SASA_atoms.csv`
- `/api/sasa_atommap/...` serves atom-index/exposure pairs from merged CSV outputs

## Job Execution Structure

Background execution is implemented in `job_runner.py`.

### Job model

Each launched job receives:

- a short UUID-like `job_id`
- a dedicated folder under `jobs/<job_id>/`
- in-memory status metadata in `JOB_STORE`

Tracked job fields include:

- `status`
- `target`
- `created_at`
- `started_at`
- `finished_at`
- `current_step`
- `step_started_at`
- `log`

### Execution model

- A daemon thread runs `run_pipeline_task(...)`.
- Pipeline assets are copied from `pipeline_assets/` into the job directory.
- Input metadata are written to `input.csv` and `Protein_Data.csv`.
- Each pipeline script is executed as a subprocess using `python3 -u`.
- `stderr` is merged into `stdout`.
- Per-step timeouts and no-output watchdogs are applied for selected steps.
- A persistent `job.log` is written in the job folder.

### Confirmed default pipeline sequence

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
14. `16_ResultsDisplay.py`

Present but not part of the default sequence:

- `13_mcsSASA_svg.py`
- `14_SVGmkr.py`
- `17_obabelSDF.py`

## Pipeline Script Sequence And Apparent Roles

### 1. Structure retrieval

`1_GRABBER.py`:

- reads input rows containing `protein` and `search_query`
- performs RCSB full-text search with PDBe backup search
- downloads CIF files from RCSB or PDBe
- writes `CIFdata.csv` and `queries.csv`

### 2. Sequence and ligand filtering

`2_SQchk.py`:

- reads `queries.csv`, `Protein_Data.csv`, and `summary.json` [TO VERIFY exact current `summary.json` provenance]
- queries the PDBe molecules endpoint for each candidate PDB ID
- extracts polymer sequences and nonpolymer ligand annotations
- computes sequence identity using `Bio.Align.PairwiseAligner`
- writes:
  - `filtered_data.csv`
  - `chain_similarity.csv`
- excludes entries listed in `NON_LIGAND_CODES.py`

### 3. PDB generation

`3_PDBmkr.py`:

- parses downloaded CIF files with `Bio.PDB.MMCIFParser`
- writes single-chain PDB files
- generates:
  - `5CharMAP.csv`
  - `ChainRenameMAP.csv`
- introduces placeholder ligand IDs such as `A00`, `A01`, etc., when needed

### 4. Ligand renaming / PDB record normalization

`4_PDBfxr.py`:

- reads `5CharMAP.csv`
- rewrites HETATM records
- replaces long ligand identifiers with shorter placeholders where necessary

### 5. PDB cleanup

`5_PDBcln.py`:

- removes non-target HETATM entries
- keeps ATOM, TER, END, and only the intended ligand residue
- drops ion-only structures
- can also drop structures whose intended ligand is itself an ion-like residue name

### 6. SASA analysis

`6_SASA.py`:

- scans processed PDB files under `WAR_PDB`
- uses `Bio.PDB.PDBParser`
- removes waters
- computes per-atom SASA with `ShrakeRupley`
- records only atoms above a threshold (`THRESHOLD = 0.1`) in `Warhead_SASA_atoms.csv`
- writes ligand-level summaries to `Warhead_SASA_summary.csv`

## Ligand / Structure Processing Steps Inferred From Filenames And Code

The repository suggests the following structure-processing logic:

1. candidate structures are retrieved as CIF files
2. CIF files are reduced to single-chain PDB outputs
3. ligand identifiers may be normalized into shorter codes
4. processed PDBs are cleaned so that only the protein plus intended ligand remain
5. cleaned ligand-containing PDBs become the working source for SASA and mapping

This structure preparation is confirmed in broad outline. The exact treatment of all ligand edge cases, altlocs, and chain-collision cases is [TO VERIFY].

## SASA-Related Processing

The code-grounded SASA workflow is:

1. identify the intended ligand from the processed PDB filename
2. parse the processed complex with `Bio.PDB`
3. remove waters
4. compute atom-level SASA in the full complex context
5. collect:
   - per-atom exposure values
   - ligand-level counts of total atoms and exposed atoms
   - total ligand SASA in the complex

The current app uses display buckets defined in `app.py`:

- low: `< 15.0`
- medium: `15.0` to `< 35.0`
- high: `>= 35.0`

These buckets are confirmed as code-level UI logic. Their manuscript interpretation as exposure classes requires [TO VERIFY].

## 2D Mapping / SVG / Results Generation

### 2D mapping

`9_2Dmapping.py`:

- reads `Resolved_SASA_Summary.csv`
- derives mapping tables among targets, ligands, and SMILES
- creates `Ligand_Atom_Map.csv` by iterating over RDKit atom indices from ligand SMILES

### 3D ligand atom extraction

`10_2DmappingExtraction.py`:

- scans `WAR_PDB/`
- extracts HETATM coordinates for ligand atoms
- writes `Ligand_3D_Atoms.csv`

### RDKit MCS mapping and SVG generation

`11_mcsMatcher.py`:

- performs per-instance 2D to 3D atom mapping using RDKit MCS logic
- handles multiple occurrences of a ligand across PDB entries
- writes MCS-linked outputs under `MCS_Output/`
- according to the script header, also generates:
  - plain SVG depictions
  - SASA-exposed SVG depictions

The SASA API further implies that `11_mcsMatcher.py` generates or supports `Ligand_MCS_SASA_ALL_ATOMS.csv`, which is later consumed for JSON atom retrieval. This is strongly supported by the code path but should remain [INFERRED] until directly verified from an output-producing code block.

### Result collation

`12_Results.py`:

- creates `TARGET_RESULTS/`
- copies:
  - `MCS_Output`
  - `Target_Table`
  - `WAR_PDB`
  - most non-input CSVs
- merges atom-level SASA and mapping data into `3DSASAmapped.csv`

### Final merged atom table

`15_ResultsMerged.py`:

- left-joins `Ligand_3D_Atoms.csv` with exposure data
- writes `Ligand_3D_Atoms_with_SASA.csv`

### Gallery display table

`16_ResultsDisplay.py`:

- traverses `WAR_PDB`
- links per-pose files to exposure summaries and SMILES metadata
- writes `Results_Display.csv`

## Where Outputs Are Stored

### Primary job outputs

- `jobs/<job_id>/`

### Collected final outputs

- `jobs/<job_id>/TARGET_RESULTS/`

### Key subfolders referenced by the app

- `TARGET_RESULTS/WAR_PDB/`
- `TARGET_RESULTS/MCS_Output/MCS_SVG/`
- `TARGET_RESULTS/MCS_Output/MCS_SDF/`
- `TARGET_RESULTS/LIGAND_SDF/` [legacy/fallback path support]
- `TARGET_RESULTS/Target_Table/`

### Additional upload storage

- `uploads/sasa/`
- `uploads/mcs/`
- `uploads/metadata/`
- `uploads/scaffold/`
- `uploads/structures/`

## What Is Uncertain And Must Be Verified

- Whether `summary.json` is always produced in the current default run or is a legacy expectation carried by `2_SQchk.py`. [TO VERIFY]
- The exact on-disk producer of `Ligand_MCS_SASA_ALL_ATOMS.csv` in the current step-11 code path. [TO VERIFY]
- Whether manual uploads are sufficient to reproduce a full interactive results workflow without a pipeline launch. [TO VERIFY]
- The extent to which multi-format ligand/structure support is active versus described only in narrative text. [TO VERIFY]
- Whether all legacy fallback routes are still exercised in current deployments. [TO VERIFY]
- Whether the duplicated PROTAC Builder code in `static/js/protacable.js` represents intentional transitional logic or unresolved drift. [TO VERIFY]
