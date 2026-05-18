# Job Artifact Policy

## Purpose

Warhead Hunter job folders mix execution assets, intermediate files, final scientific outputs, and browser/API-facing deliverables. This policy defines conservative artifact categories so cleanup and packaging can reduce clutter without breaking reproducibility, browser workflows, or API access.

## Canonical Categories

### KEEP

Must remain in the job folder.

Examples:

- `job.log`
- `job_metadata.json`
- `input.csv`
- `Protein_Data.csv`
- `job_result_manifest.json`
- `cleanup_report.md`
- Final summary CSVs and SASA tables
- Final PDB/SDF/SVG/HTML outputs relied on by browser or API routes

### BUNDLE

Should remain in the job folder and should usually be included in the curated public downloadable ZIP.

Examples:

- Cleaned ligand-bound PDB files
- Protein-only PDB files when generated
- Ligand SDF files
- SVG atom maps
- Final results CSV and TSV files
- Useful HTML viewer outputs
- `job_result_manifest.json`
- `cleanup_report.md`

### ARCHIVE_ONLY

Useful for provenance or reruns but not normally needed in the default public bundle.

Examples:

- Copied pipeline scripts
- Helper modules copied from `pipeline_assets/`
- Static source assets copied only to execute the pipeline
- Mapping/provenance CSVs not required by live routes

### REBUILDABLE_INTERMEDIATE

Generated during the run and likely reproducible from upstream artifacts.

Examples:

- Early-step filtering tables
- Intermediate chain/similarity tables
- Temporary mapping tables not needed by final pages

### DEBUG_LOG

Helpful for troubleshooting but not usually desirable in public-facing bundles.

Examples:

- Failure CSVs
- Per-step error dumps
- Non-core logs

### CACHE_TEMP

System/cache debris that can be ignored or deleted conservatively.

Examples:

- `__pycache__/`
- `.DS_Store`
- Hidden cache files

### DELETE_CANDIDATE

Only removable with explicit operator intent.

Examples:

- Backup/temp suffix files such as `*.tmp`, `*.bak`, `*.old`, `*.cache`
- Empty directories
- Older duplicate ZIP bundles

### UNKNOWN_KEEP

Default category for anything not clearly classifiable. Preserve first, refine later.

## What Belongs In The Public Downloadable ZIP

The curated public bundle should favor downstream-useful outputs and avoid repetitive execution clutter.

Include:

- `job_result_manifest.json`
- `cleanup_report.md`
- `Protein_Data.csv`
- `job_metadata.json`
- Final result CSVs and TSVs
- Cleaned/prepared PDB outputs
- Ligand and mapped SDF outputs
- SVG atom maps
- Useful HTML result/viewer pages when self-contained enough to be meaningful
- A short `README.txt`

## What Belongs In The Archive ZIP

The archive ZIP may include broader provenance and rebuildable content while still excluding unsafe/system artifacts.

Include when requested:

- Copied pipeline scripts
- Helper modules
- Intermediate mapping tables
- Rebuildable early-step outputs
- Debug-oriented CSVs and logs

Exclude even from archive ZIP:

- `.git/`
- `__pycache__/`
- hidden system files
- secrets or environment files
- stale bundle duplicates

## What Should Stay In The Job Folder

Even when bundles are generated, the live job directory should keep the files needed by:

- Target gallery pages
- Past Jobs Browser behavior
- `/api/jobs/<job_id>/files`
- `/api/jobs/<job_id>/bundle`
- `/api/examples/<job_id>/files`
- `/api/examples/<job_id>/bundle`
- downstream route families such as `/api/pdb`, `/api/sdf`, `/api/svg`, and SASA helpers

## What Can Be Safely Deleted

Only with explicit `--apply --delete-rebuildable` style intent:

- `__pycache__/`
- `.DS_Store`
- temp/cache/backup files
- empty folders
- older duplicate ZIP bundles
- other files already classified as `DELETE_CANDIDATE`

Do not delete automatically:

- `KEEP`
- `BUNDLE`
- `UNKNOWN_KEEP`
- `job_metadata.json`
- `job.log`
- `Protein_Data.csv`
- `input.csv`

## How This Supports API Endpoints

This policy helps the API by:

- keeping stable final files discoverable through `/api/jobs/<job_id>/files`
- enabling a curated bundle for `/api/jobs/<job_id>/bundle`
- allowing example jobs to expose prepared structures without shipping copied scripts
- preserving manifest/report artifacts that help downstream users understand what a job produced

## How This Supports Manuscript Reproducibility

The public bundle and manifest provide a cleaner, more interpretable output package for demonstration and downstream inspection while preserving the full job folder as the local provenance record.

## Updating The Policy When New Pipeline Outputs Appear

When future pipeline scripts add new artifacts:

1. inspect whether the new file is consumed by routes or templates
2. decide whether it is final, archive-only, rebuildable, or unknown
3. add a filename or directory pattern to the cleanup classifier
4. keep new patterns as `UNKNOWN_KEEP` until their role is clear

Conservative promotion is preferred over aggressive cleanup.
