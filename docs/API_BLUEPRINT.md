# Warhead Hunter API Blueprint

## Purpose

This document began as a future-facing API blueprint for Warhead Hunter. The current repository now implements the core single-job API, read-only curated examples, indexed jobs, WAR_PDB retrieval, artifact listing, and a lightweight batch API. The goal remains to preserve the current Flask/job-folder architecture while making single-job, batch, and result-retrieval workflows easier to automate.

## Current API Audit

### Current JSON-style capabilities already present

The repository already contains several API-like routes:

- Job lifecycle and export
  - `GET /api/job_log/<job_id>`
  - `GET /api/job_summary/<job_id>`
  - `GET /api/jobs/<job_id>/download`
  - `GET /api/jobs/export`
- Structure and visualization data
  - `GET /api/svg/...`
  - `GET /api/svg-plain/...`
  - `GET /api/pdb/...`
  - `GET /api/protein/...`
  - `GET /api/sdf/...`
  - `GET /api/ligand_props/...`
  - `GET /api/ligand_chain/...`
  - `GET /api/sasa_overlay/...`
  - `GET /api/sasa_atommap/...`
- SASA blueprint endpoints in `api/sasa_api.py`
  - `GET /api/jobs/<job_id>/sasa/available`
  - `GET /api/jobs/<job_id>/sasa/atoms`
  - `POST /api/jobs/<job_id>/sasa/bulk`
  - `GET /api/jobs/<job_id>/sasa/residue_for_ligand`
- Miscellaneous helper routes
  - `GET /api/proxy_fasta/<pdb_id>`
  - `POST /api/log-builder-click`
  - `GET/POST /api/protac-builder/active-session`
- Deployment-specific integration route
  - `POST /api/handoff/materialize/<job_id>/<pdb>/<chain>/<warhead>`

### What the current API does not yet provide

- Authentication or rate limiting
- A durable database-backed job registry
- A fully versioned OpenAPI contract
- Large-scale queue infrastructure

## Recommended API Goals

1. Preserve the current job-folder execution model.
2. Add structured JSON submission and retrieval around that model.
3. Keep Flask as the first implementation target.
4. Use SQLite first for metadata and status persistence.
5. Avoid requiring Celery or RQ in the first public API version.
6. Provide clear JSON result manifests so downstream tools can consume outputs without relying on ad hoc file discovery.
7. Support both synchronous metadata operations and asynchronous job execution.

## Recommended API Design Principles

- Resource-oriented paths
- Stable JSON envelopes
- Explicit job states
- File-manifest-driven result retrieval
- Backward compatibility with current route surface when practical
- Minimal required server-side dependencies in Phase 1

## Single-Job API Design

### Proposed endpoint

`POST /api/jobs`

### Purpose

Submit one Warhead Hunter job using the same conceptual inputs already used by `/launch_job`.

### Proposed request body

```json
{
  "target_name": "KRAS",
  "search_query": "KRAS covalent inhibitor bound",
  "fasta_seq": ">KRAS\nMTEYKLVVVGAGGVGKSALTIQLIQNHFVDEYDPTIE",
  "options": {
    "run_sasa": true,
    "generate_svg": true,
    "generate_viewer": true
  },
  "source": "api"
}
```

### Proposed response

```json
{
  "ok": true,
  "job_id": "abc12345",
  "status": "queued",
  "submitted_at": "2026-05-18T19:00:00Z",
  "links": {
    "self": "/api/jobs/abc12345",
    "results": "/api/jobs/abc12345/results",
    "files": "/api/jobs/abc12345/files"
  }
}
```

## Batch-Job API Design

### Proposed endpoint

`POST /api/batches`

### Purpose

Submit multiple jobs in one request while reusing the same background execution model.

### Example request

```json
{
  "jobs": [
    {
      "target_name": "OGA",
      "search_query": "O-GlcNAcase 9BA9 6PM9 5UN9 5M7T",
      "fasta_seq": ">EXAMPLE_FASTA\nMKT..."
    }
  ],
  "delay_seconds": 2
}
```

### Notes

- The current repository does not yet support this request format directly.
- A first implementation could internally translate each entry into a normal single-job submission.
- If `pdb_id`-centric submission is added, the backend should clearly distinguish it from the existing `target_name`/`search_query`/`fasta_seq` launch flow.

### Proposed response

```json
{
  "ok": true,
  "batch_id": "batch_001",
  "status": "queued",
  "job_count": 1,
  "jobs": [
    {
      "job_id": "abc12345",
      "status": "queued"
    }
  ],
  "links": {
    "self": "/api/batches/batch_001",
    "results": "/api/batches/batch_001/results"
  }
}
```

## Result Retrieval API Design

### Proposed endpoints

- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/results`
- `GET /api/jobs/{job_id}/files`
- `GET /api/jobs/{job_id}/files/{filename}`

### `GET /api/jobs/{job_id}`

Purpose:

- return top-level job metadata
- return state, timestamps, input summary, and current step

Proposed response:

```json
{
  "ok": true,
  "job_id": "abc12345",
  "status": "running",
  "target_name": "KRAS",
  "search_query": "KRAS covalent inhibitor bound",
  "created_at": "2026-05-18T19:00:00Z",
  "started_at": "2026-05-18T19:00:02Z",
  "finished_at": null,
  "current_step": "6_SASA.py",
  "job_dir": "jobs/abc12345",
  "links": {
    "results": "/api/jobs/abc12345/results",
    "files": "/api/jobs/abc12345/files"
  }
}
```

### `GET /api/jobs/{job_id}/results`

Purpose:

- provide a structured JSON manifest of the analytical outputs
- decouple downstream consumers from filesystem assumptions

Proposed response structure:

```json
{
  "ok": true,
  "job_id": "abc12345",
  "status": "completed",
  "manifest_version": "1.0",
  "summary": {
    "target_name": "KRAS",
    "total_entries": 24,
    "unique_ligands": 9
  },
  "artifacts": {
    "resolved_sasa_summary": "TARGET_RESULTS/Resolved_SASA_Summary.csv",
    "results_display": "TARGET_RESULTS/Results_Display.csv",
    "ligand_metadata": "TARGET_RESULTS/Ligand_Metadata.csv",
    "mcs_output_dir": "TARGET_RESULTS/MCS_Output",
    "war_pdb_dir": "TARGET_RESULTS/WAR_PDB"
  },
  "poses": [
    {
      "pdb_id": "4ein",
      "chain": "A",
      "ligand": "NOH",
      "residue_id": "1101",
      "percent_exposed": 52.8,
      "sasa_in_complex_a2": 148.2,
      "files": {
        "svg_exposed": "/api/jobs/abc12345/files/4ein_A_NOH_1101_exposed.svg",
        "svg_plain": "/api/jobs/abc12345/files/4ein_A_NOH_1101_plain.svg",
        "sdf": "/api/jobs/abc12345/files/4ein_A_NOH_1101.sdf",
        "pdb": "/api/jobs/abc12345/files/4ein_A_NOH.pdb"
      }
    }
  ]
}
```

## Downloadable Output API Design

### Proposed endpoints

- `GET /api/jobs/{job_id}/files`
- `GET /api/jobs/{job_id}/files/{filename}`
- `GET /api/jobs/{job_id}/bundle`

### Purpose

- expose file-level result retrieval in a documented way
- keep existing ZIP download behavior while adding structured discovery

### Suggested behavior

- `GET /api/jobs/{job_id}/files` returns a JSON list of file records
- `GET /api/jobs/{job_id}/files/{filename}` streams one specific file
- `GET /api/jobs/{job_id}/bundle` returns a ZIP archive

## Curated Example Jobs and Prepared Structure Retrieval

Read-only curated examples provide immediate API usability without requiring full public job submission. They are useful for demonstrating output formats, supporting reproducible inspection of completed jobs, and allowing users to download prepared structure outputs from known example runs.

### Why read-only examples are useful

- they let users test the API safely
- they expose real output formats from completed jobs
- they avoid requiring new job submission for first-contact API exploration
- they support downstream structure collection and notebook workflows

### Endpoint table

| Method | Path | Role |
|---|---|---|
| GET | `/api/examples` | List curated examples and availability metadata |
| GET | `/api/examples/{job_id}` | Return metadata for one curated example |
| GET | `/api/examples/{job_id}/files` | List safe downloadable files for one example |
| GET | `/api/examples/{job_id}/files/{filename}` | Download one safe result file |
| GET | `/api/examples/{job_id}/bundle` | Download ZIP bundle of safe example outputs |
| GET | `/api/indexed-jobs` | Optional read-only index aligned with the Past Jobs Browser |

### How curated examples differ from new job submission

- curated examples are read-only
- curated examples refer to known completed jobs on a given deployment
- curated examples do not change pipeline execution behavior
- curated examples are not a substitute for future `POST /api/jobs`

### How users can download cleaned structures

Curated example jobs can expose:

- cleaned ligand-bound PDB files
- ligand SDF files
- SVG atom maps
- CSV and related result tables
- downloadable ZIP bundles of safe job-derived outputs

These endpoints are intended for prepared, job-derived outputs rather than for unrestricted filesystem access.

### Future extension

- protein-class search over indexed jobs
- richer target/query filtering
- batch export of prepared structures from selected completed jobs

## Health / Status Endpoints

### Proposed endpoints

- `GET /api/health`
- `GET /api/manifest`

### `GET /api/health`

Purpose:

- quick server liveness check
- safe for load balancers and monitoring

Proposed response:

```json
{
  "ok": true,
  "service": "warhead-hunter",
  "status": "healthy",
  "time": "2026-05-18T19:00:00Z"
}
```

### `GET /api/manifest`

Purpose:

- API discovery
- software version and route manifest
- feature flags for clients

Proposed response:

```json
{
  "ok": true,
  "service": "warhead-hunter",
  "api_version": "0.1",
  "features": {
    "single_job_submission": true,
    "batch_submission": false,
    "sasa_lookup": true,
    "download_bundle": true
  },
  "endpoints": [
    "/api/health",
    "/api/jobs",
    "/api/jobs/{job_id}",
    "/api/jobs/{job_id}/results"
  ]
}
```

## Possible JSON Schemas

### Job submission schema

```json
{
  "type": "object",
  "required": ["target_name", "search_query"],
  "properties": {
    "target_name": { "type": "string" },
    "search_query": { "type": "string" },
    "fasta_seq": { "type": "string" },
    "options": {
      "type": "object",
      "properties": {
        "run_sasa": { "type": "boolean" },
        "generate_svg": { "type": "boolean" },
        "generate_viewer": { "type": "boolean" }
      }
    },
    "source": { "type": "string" }
  }
}
```

### Job status schema

```json
{
  "type": "object",
  "required": ["job_id", "status"],
  "properties": {
    "job_id": { "type": "string" },
    "status": {
      "type": "string",
      "enum": ["queued", "running", "completed", "failed", "canceled"]
    },
    "created_at": { "type": "string" },
    "started_at": { "type": ["string", "null"] },
    "finished_at": { "type": ["string", "null"] },
    "current_step": { "type": ["string", "null"] }
  }
}
```

## Job Status Lifecycle

### Recommended lifecycle

- `queued`
- `running`
- `completed`
- `failed`
- `canceled` [future]

### Mapping from current code

Current `JOB_STORE` states:

- `pending`
- `running`
- `completed`
- `failed`

Recommendation:

- map `pending` to `queued` in the future API
- preserve the internal runner logic initially

## Error Response Format

### Suggested standard envelope

```json
{
  "ok": false,
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Missing target_name",
    "details": {
      "field": "target_name"
    }
  }
}
```

### Suggested error codes

- `INVALID_REQUEST`
- `NOT_FOUND`
- `JOB_NOT_READY`
- `PIPELINE_FAILED`
- `RATE_LIMITED`
- `UNAUTHORIZED`
- `INTERNAL_ERROR`

## Suggested Authentication Options

### Private or lab-only deployment

- no auth at first
- IP allowlist if needed
- reverse-proxy protection

### Public deployment

- API key in `Authorization: Bearer <token>`
- simple key table in SQLite
- per-key quota tracking

### Institutional deployment

- reverse proxy auth
- optional session-backed user model

## Suggested Rate Limiting Options

- Per-IP limit for anonymous use
- Per-API-key limit for authenticated use
- Separate submission and retrieval limits
- Conservative default for `POST /api/jobs` and `POST /api/batches`

Suggested starting values:

- `GET` endpoints: 60 requests/minute per IP
- `POST /api/jobs`: 5 requests/minute per IP or key
- `POST /api/batches`: 1 request/minute per key

## Local SQLite-First Approach

### Why SQLite first

- matches the current lightweight Flask architecture
- simple deployment
- enough for job metadata, manifests, and API keys
- avoids requiring a queue stack before the data model is stable

### Suggested tables

- `jobs`
- `job_inputs`
- `job_outputs`
- `batches`
- `batch_jobs`
- `api_keys` [future]
- `request_logs` [future]

## Future Queue Options

Queue frameworks should remain optional in early API phases.

### Phase-appropriate approach

- Phase 1 to 3:
  - background threads or subprocess launches
  - SQLite-backed metadata
- Phase 4 onward:
  - optional RQ or Celery when concurrency or public usage requires it

### Recommended future queue candidates

- RQ
  - simpler operational model
  - good fit for Redis-backed job execution
- Celery
  - richer ecosystem
  - heavier operational footprint

Neither should be required for the first documented API release.

## OpenAPI-Style Endpoint Table

| Method | Path | Current status | Proposed role |
|---|---|---|---|
| GET | `/api/health` | Implemented | Health check |
| GET | `/api/manifest` | Implemented | API discovery |
| POST | `/api/jobs` | Implemented | Submit one job |
| GET | `/api/jobs/{job_id}` | Implemented | Job metadata/status |
| GET | `/api/jobs/{job_id}/results` | Implemented | Structured results manifest |
| GET | `/api/jobs/{job_id}/files` | Implemented | File listing |
| GET | `/api/jobs/{job_id}/files/{filename}` | Implemented | File download/stream |
| GET | `/api/jobs/{job_id}/bundle` | Implemented | ZIP bundle |
| GET | `/api/jobs/{job_id}/war-pdbs` | Implemented | WAR_PDB-only listing |
| GET | `/api/jobs/{job_id}/war-pdbs.zip` | Implemented | WAR_PDB-only ZIP download |
| GET | `/api/jobs/{job_id}/artifacts` | Implemented | Artifact listing by kind/folder |
| POST | `/api/batches` | Implemented | Submit batch |
| GET | `/api/batches/{batch_id}` | Implemented | Batch metadata/status |
| GET | `/api/batches/{batch_id}/results` | Implemented | Batch result manifest |
| POST | `/api/rcsb/submit` | Not implemented | Optional direct PDB-centric submission |
| POST | `/api/sasa/analyze` | Not implemented | Optional direct SASA service endpoint |
| GET | `/api/examples` | Implemented | Curated example listing |
| GET | `/api/examples/{job_id}` | Implemented | Curated example metadata |
| GET | `/api/examples/{job_id}/files` | Implemented | Curated example file listing |
| GET | `/api/examples/{job_id}/bundle` | Implemented | Curated example ZIP bundle |
| GET | `/api/examples/{job_id}/war-pdbs` | Implemented | Curated example WAR_PDB listing |
| GET | `/api/examples/{job_id}/war-pdbs.zip` | Implemented | Curated example WAR_PDB ZIP |
| GET | `/api/indexed-jobs` | Implemented | Read-only indexed job inventory |
| GET | `/api/jobs/{job_id}/sasa/available` | Implemented | Retain as detailed subresource |
| GET | `/api/jobs/{job_id}/sasa/atoms` | Implemented | Retain as detailed subresource |
| POST | `/api/jobs/{job_id}/sasa/bulk` | Implemented | Retain as detailed subresource |
| GET | `/api/jobs/{job_id}/sasa/residue_for_ligand` | Implemented | Retain as detailed subresource |

## Recommended Near-Term Path

The cleanest immediate next step is not basic route creation anymore; it is to stabilize schemas, improve test coverage, and tighten output contracts for the active API:

1. standardize result-manifest fields,
2. keep WAR_PDB and artifact retrieval stable,
3. harden batch metadata persistence,
4. document example-driven automation patterns.

That path keeps Warhead Hunter scriptable while staying faithful to the existing job-folder design.
