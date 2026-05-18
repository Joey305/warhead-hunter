# Manuscript To API Bridge

## Purpose

This document explains how a future Warhead Hunter API can strengthen the scientific software manuscript while keeping present-tense claims aligned with the current repository.

## What Can Be Mentioned Now

The manuscript can safely state that the current repository:

- is organized as a Flask-based web application
- runs analyses as per-job workflows stored in job-specific directories
- produces structured output artifacts including CSV, SVG, SDF, and PDB files
- already includes JSON-style endpoints for atom-level SASA retrieval and result-serving support
- can expose curated completed jobs through read-only API endpoints for example-based exploration
- is architecturally compatible with future programmatic access

These claims are grounded in the current code.

## What Should Be Held Until Implemented

The manuscript should not yet claim that Warhead Hunter:

- provides a full public API
- supports batch submission through an API
- supports authenticated or rate-limited API access
- exposes a stable OpenAPI-described interface
- is ready for large-scale automated deployment

Those should remain future-direction statements unless implemented.

## How To Phrase The Future API In The Manuscript

Recommended manuscript phrasing:

- "The current job-oriented architecture is compatible with future programmatic interfaces."
- "The repository already includes JSON-serving components for SASA-focused result retrieval."
- "Curated completed jobs can be exposed through read-only API endpoints to demonstrate output formats and support reproducible inspection of example results."
- "A future API layer could expose job submission, result manifests, and downloadable outputs."

Avoid stronger wording such as:

- "Warhead Hunter provides a public API"
- "Warhead Hunter supports batch automation"

unless those features are actually present.

## How Batch Processing Could Support Future Validation

Batch processing would strengthen future manuscript extensions by enabling:

- repeated execution across curated target panels
- larger internal validation studies
- comparison of exposure patterns across ligand series
- systematic generation of supplementary result sets

In other words, a batch API would not only improve engineering ergonomics; it would also make future validation studies easier to execute reproducibly.

## How An API Would Make Warhead Hunter Reusable By Other Tools

A structured API would make Warhead Hunter easier to reuse as an upstream analytical service rather than only as a browser application.

Potential downstream uses include:

- requesting atom-level exposure maps from external workflows
- retrieving standardized result manifests for notebook-based analysis
- feeding selected ligands or attachment vectors into downstream design tools
- integrating exposure-aware filtering into broader medicinal chemistry pipelines

This would align well with the current repository’s job-folder outputs and existing JSON-serving SASA endpoints.

## How API Support Connects To PROTAC Builder, E3 Ligandalyzer, And V-LiSEMOD

The current repository already suggests a companion-tool ecosystem.

### Warhead Hunter role

- identify exposed ligand atoms in bound structural context
- provide interpretable candidate derivatization positions

### Possible downstream API-enabled roles

- PROTAC Builder:
  - consume selected ligand identities, structures, or candidate attachment vectors
- E3 Ligandalyzer:
  - integrate warhead-side prioritization with recruiter-side ligand analysis
- V-LiSEMOD:
  - reuse prepared structure and ligand outputs in related structural-analysis workflows

The present manuscript can mention this as an ecosystem direction, but the depth of integration should remain conservative unless each connection is explicitly documented and verified.

## Why The API Matters For The Paper

A future API strengthens the paper in three ways:

1. It reinforces that Warhead Hunter is software infrastructure, not only a webpage.
2. It improves reproducibility by making outputs more discoverable and scriptable.
3. It creates a path from interactive case-study use to larger validation campaigns.

For the current manuscript, the best framing is:

- the software already has structured result components and API-adjacent routes
- the next engineering step is to formalize those into a stable submission and results API

That is a credible, code-grounded bridge between the present manuscript and future platform growth.
