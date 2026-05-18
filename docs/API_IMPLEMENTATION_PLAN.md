# API Implementation Plan

## Goal

Add a documented, automatable API to Warhead Hunter without discarding the current Flask application, job-folder execution model, or existing SASA endpoints.

## Phase 1: Document Current Endpoints

### Objectives

- Inventory all current routes under `/api/`
- Separate browser-helper endpoints from reusable programmatic endpoints
- Document which outputs each route depends on

### Deliverables

- Endpoint inventory document
- Route-to-output dependency map
- Identification of legacy versus current file conventions

### Notes

- This phase can be done with no behavioral changes.
- It should explicitly cover `api/sasa_api.py`, `api/handoff_server.py`, and JSON-producing routes in `app.py`.

## Phase 2: Standardize Job Metadata

### Objectives

- Create a single normalized job metadata structure
- Persist job state outside in-memory `JOB_STORE`
- Preserve compatibility with current monitor pages

### Suggested work

- Add SQLite table for job metadata
- Record:
  - `job_id`
  - `status`
  - `created_at`
  - `started_at`
  - `finished_at`
  - `current_step`
  - `target_name`
  - `search_query`
  - optional input FASTA hash or truncated preview

### Benefits

- Survives process restart better than in-memory-only status
- Enables `GET /api/jobs/{job_id}`
- Creates a base for future batch orchestration

## Phase 3: Add JSON Result Manifest

### Objectives

- Standardize discovery of job outputs
- Avoid front-end and downstream tools relying on fragile file searches

### Suggested work

- Write `result_manifest.json` into each job folder or `TARGET_RESULTS/`
- Include:
  - artifact paths
  - target metadata
  - per-pose summary rows
  - file existence flags

### Benefits

- Simplifies scripting
- Simplifies future API result endpoints
- Documents the de facto output contract

## Phase 3A: Read-only Curated Examples

### Objectives

- expose a small set of completed example jobs through safe read-only endpoints
- make the API immediately usable before full job submission exists
- support prepared structure retrieval from completed jobs

### Suggested work

- define a centralized curated example list
- add:
  - `GET /api/examples`
  - `GET /api/examples/{job_id}`
  - `GET /api/examples/{job_id}/files`
  - `GET /api/examples/{job_id}/files/{filename}`
  - `GET /api/examples/{job_id}/bundle`
- optionally add `GET /api/indexed-jobs`

### Benefits

- immediate real-output API exploration
- reproducible example workflows
- safe access to cleaned, job-derived structure outputs
- useful bridge between browser-only use and future full API support

## Phase 4: Add Single-Job API Submission

### Objectives

- Introduce `POST /api/jobs`
- Accept JSON requests that map onto the current launch form

### Suggested work

- Add Flask route for JSON submission
- Reuse existing `start_job(...)`
- Normalize response envelope
- Add `GET /api/jobs/{job_id}`

### Benefits

- Immediate automation support
- Minimal architecture change
- Reuses current background pipeline

## Phase 5: Add Batch Submission

### Objectives

- Introduce `POST /api/batches`
- Support submission of multiple jobs in one request

### Suggested work

- Define batch table in SQLite
- Expand JSON schema
- Submit each entry as a normal child job
- Add:
  - `GET /api/batches/{batch_id}`
  - `GET /api/batches/{batch_id}/results`

### Benefits

- Supports larger validation campaigns
- Makes the platform more useful for internal screening workflows

## Phase 6: Add Downloadable Result Bundles

### Objectives

- Provide structured file discovery and downloadable bundles

### Suggested work

- Add `GET /api/jobs/{job_id}/files`
- Add `GET /api/jobs/{job_id}/files/{filename}`
- Add `GET /api/jobs/{job_id}/bundle`
- Reuse current ZIP download logic where possible

### Benefits

- Better machine access to outputs
- Easier downstream reuse by companion tools

## Phase 7: Add Authentication And Rate Limits If Public

### Objectives

- Protect public deployments
- Prevent abuse of expensive pipeline submission routes

### Suggested work

- Add optional API key model
- Add per-IP and per-key rate limits
- Add audit logging for job submissions

### Benefits

- Safer public exposure
- Better operational control

## Risks

- Current mixed file conventions may make manifest generation more complex than expected.
- Some front-end code already reflects drift, for example the missing `/api/handoff/prefill/...` route.
- If pipeline outputs are not fully stable, the API may accidentally freeze a schema too early.
- In-memory execution and thread-based jobs may be sufficient for lab use but fragile for public scale.
- The empty `requirements.txt` increases setup risk until the environment is formalized.

## Migration Notes

- Keep existing browser routes intact while adding new JSON routes.
- Treat existing `/api/svg`, `/api/sdf`, and `/api/pdb` routes as file-serving primitives rather than the final API shape.
- Avoid forcing Celery or RQ in the first API release.
- Make SQLite the first persistence layer so the app remains easy to deploy.
- Version new API responses from the beginning, even if only as `api_version: "0.1"`.

## Recommended Immediate Next Step

Implement Phase 1 to 3 first:

1. document endpoints,
2. standardize persisted job metadata,
3. generate a JSON result manifest.

That sequence creates a stable foundation for both the manuscript and future external automation.
