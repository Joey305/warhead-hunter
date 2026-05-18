# Title

Warhead Hunter: Atom-Level Solvent Exposure Mapping of Protein-Bound Ligands for Structure-Guided Modification-Site Prioritization

## Authors And Affiliations

- Author 1 [AFFILIATION NEEDED]
- Author 2 [AFFILIATION NEEDED]
- Corresponding Author [AFFILIATION NEEDED]

## Abstract

Warhead Hunter is a web-based software workflow for converting ligand-bound protein structures into atom-level solvent exposure maps. The platform combines structure retrieval, sequence-guided filtering, ligand-focused preprocessing, solvent-accessible surface area analysis, RDKit-based atom mapping, and browser-based visualization to support inspection of potentially modifiable ligand atoms. Warhead Hunter is designed to assist structure-guided evaluation of candidate sites for warhead installation, linker attachment, chemical expansion, and PROTAC-oriented ligand design.

## Introduction

Ligand-bound protein structures often answer one central medicinal chemistry question while leaving another unresolved: where can the ligand be modified without obviously disrupting the observed binding mode? For covalent inhibitor design, linker installation, PROTAC-oriented warhead selection, and related ligand-expansion workflows, this question is frequently considered at the level of individual ligand atoms rather than only at the level of whole-molecule affinity or pose geometry. [REF NEEDED]

Solvent accessibility is one practical starting point for this analysis. Ligand atoms that remain solvent exposed in a bound complex may be more suitable for further inspection as candidate derivatization sites, whereas deeply buried atoms may be less attractive for attachment strategies. Solvent exposure alone does not establish synthetic tractability, retained potency, or productive induced-proximity behavior, but it can provide an interpretable structural filter for follow-up design work. [REF NEEDED]

Warhead Hunter was developed as a web-oriented software workflow for this use case. Based on the current repository, the platform organizes structure retrieval, sequence-guided filtering, ligand-focused preprocessing, atom-level solvent-accessible surface area (SASA) analysis, and 2D/3D atom mapping into a job-centered interface. The resulting outputs are intended to help users inspect ligand atoms in bound structural context and prioritize candidate modification vectors for downstream medicinal chemistry.

## Software Design and Implementation

Warhead Hunter is implemented as a Flask application with HTML templates, JavaScript-driven front-end viewers, and a threaded job runner that writes outputs to per-job directories. The current application registers three blueprints or route groups: a results route layer, a SASA-focused JSON API, and a deployment-specific handoff endpoint for transferring selected outputs to a remote downstream environment. [INFERRED]

The default workflow is initiated from a web form that accepts a target identifier, a structure-search query, and an optional FASTA sequence. A new job directory is created under `jobs/<job_id>/`, pipeline assets are copied into that directory, and the analysis proceeds through a defined sequence of scripts. Based on the current code, the default step order includes structure retrieval, sequence and ligand filtering, PDB generation and cleanup, SASA analysis, ligand metadata generation, scaffold summarization, 2D/3D mapping, and final result assembly.

The app stores job logs and status in memory during execution while also writing a persistent `job.log` inside each job folder. Completed results are then surfaced through a browser-based gallery that retrieves CSV, SVG, SDF, and PDB outputs from the job directory.

## Workflow

The current repository supports a target-to-results workflow that can be summarized as follows:

1. The user supplies a target label, an RCSB-oriented search phrase, and optionally a FASTA sequence.
2. Candidate structural entries are retrieved from RCSB, with a PDBe search fallback in the current code.
3. Protein-chain sequence content and ligand annotations are queried from PDBe and compared against the user-provided sequence.
4. Single-chain ligand-containing PDB files are produced and normalized into a working directory.
5. Non-target HETATM records and ion-only structures are removed according to rule-based filters.
6. Ligand-atom SASA values are computed in the protein-bound context using `Bio.PDB.ShrakeRupley`.
7. Ligand identity, resolved SMILES, and descriptor metadata are compiled into summary tables.
8. RDKit-based atom mapping connects ligand SMILES atoms to 3D ligand atoms in the processed structures.
9. SVG, SDF, and result tables are assembled for web presentation.

This workflow is directly consistent with the current pipeline scripts and job runner. The exact behavior of every edge case, including partial failures and all alternative output conventions, should still be treated as [TO VERIFY].

## Atom-Level Solvent Exposure and Modification-Site Prioritization

The central computational layer of Warhead Hunter is ligand-atom SASA analysis in protein context. In the inspected code, `6_SASA.py` parses processed PDB files, removes waters, computes per-atom SASA with `ShrakeRupley`, and writes both atom-level and summary-level CSV outputs. Exposed atoms are recorded in `Warhead_SASA_atoms.csv`, and aggregate statistics are recorded in `Warhead_SASA_summary.csv`.

Downstream scripts then connect atom-level exposure values to ligand identity and atom indices. `10_2DmappingExtraction.py` extracts ligand atom coordinates from processed PDB files, and `11_mcsMatcher.py` performs RDKit-based maximum common substructure mapping between 2D ligand representations and 3D ligand atoms. The current app also exposes a SASA JSON interface that serves atom-level exposure data for specific `(pdb, chain, residue_id)` combinations.

In the browser interface, exposure values are grouped into display categories. The current code uses thresholds that separate low, medium, and high exposure bands, with values below 15 A^2 treated as low exposure and larger values assigned to higher display categories. These thresholds are confirmed in the code as visualization logic; whether they should be described as scientifically validated classification bins requires external justification and should remain [TO VERIFY].

Warhead Hunter therefore supports a practical prioritization task: mapping solvent exposure back to explicit ligand atoms so that users can inspect candidate modification sites rather than only whole-ligand summary statistics. The software does not, based on the inspected code alone, prove that an exposed atom is optimal for synthesis, potency retention, or induced-proximity design. That interpretation remains a user-level design step.

## Web Interface and Visualization

The application provides multiple front-end entry points. The main launch page supports direct job initiation from a target identifier, search query, and optional FASTA sequence. A separate RCSB Scout page provides a structure-search-oriented interface that can be used to assemble the same launch inputs. Completed jobs are browsed through a results gallery.

The results gallery combines three synchronized views:

- a card-based summary of ligand entries with exposure statistics,
- a 2D ligand depiction that can be toggled between plain and exposure-highlighted renderings, and
- a 3D protein-ligand viewer that loads protein-only PDB context, ligand SDF coordinates, and atom-level SASA highlights.

The 3D rendering layer uses NGL according to the current front-end code. The app also serves per-ligand property panels derived either from `Ligand_Metadata.csv` or from SMILES-based RDKit calculations when necessary. Together, these components support rapid inspection of ligand exposure patterns in both atom-indexed 2D space and protein-bound 3D space.

## Example Use Case / Demonstration

A representative demonstration for the manuscript should begin with a target for which bound ligand structures are available in the PDB. Using the current application flow, the user would submit a target label, an RCSB search phrase, and a FASTA sequence, then allow the pipeline to retrieve candidate structures, filter entries, compute ligand SASA, and assemble results. The resulting gallery would then be used to inspect one bound ligand, identify ligand atoms with higher exposure, and compare those candidate positions in both 2D and 3D views. [FIGURE NEEDED]

The present repository does not by itself establish which target-ligand example should be used as the definitive manuscript case study. Any specific biological case should therefore be selected, rerun, and documented before submission. [TO VERIFY]

## Discussion

The codebase supports a manuscript contribution centered on interpretability and workflow integration rather than predictive benchmarking. Warhead Hunter combines structure retrieval, protein-context filtering, ligand-specific preprocessing, atom-level SASA analysis, and interactive visualization into one web workflow. This combination is potentially useful for medicinal chemists who need a practical, structure-guided way to examine which ligand atoms remain exposed in a bound complex.

The strongest manuscript position is that Warhead Hunter provides a software environment for generating and inspecting atom-level solvent exposure maps of bound ligands. That claim is supported directly by the pipeline scripts, result artifacts, and front-end viewer architecture. Stronger claims about design success, prospective validation, or superiority over other methods are not supported by the inspected repository alone and should not be made without additional evidence.

The repository also suggests a broader software ecosystem that includes PROTAC Builder, E3 Ligandalyzer, and V-LiSEMOD. In its current form, Warhead Hunter already contains a deployment-specific handoff path for selected files, indicating that reuse of exposure-mapped ligand outputs by downstream tools is an intended direction. [INFERRED]

## Limitations

Several limitations are evident from code inspection.

- The repository does not currently provide benchmark datasets, accuracy metrics, runtime characterization, or prospective validation results. [TO VERIFY]
- The live job monitor depends on an in-memory job store, which may limit robustness across process restarts.
- The current application uses threaded background execution rather than a durable queue.
- Multiple legacy and current output conventions coexist, increasing the need for path and schema normalization.
- The deployment-specific handoff route is not equivalent to a general public API.
- `requirements.txt` is empty in the inspected checkout, so the install environment should be reconstructed from observed imports before publication.
- A front-end reference to `/api/handoff/prefill/...` is present, but the matching backend endpoint was not found in the inspected `api/` code.

More broadly, solvent exposure should be interpreted as one layer of structural evidence rather than a complete ranking function for medicinal chemistry decision-making. Exposure does not independently resolve synthetic accessibility, exit-vector geometry quality, pharmacology, or tolerated substitution.

## Conclusions

Warhead Hunter is a web-based scientific software workflow for transforming ligand-bound protein structures into atom-level solvent exposure maps. Based on the current repository, the platform supports structure retrieval, sequence-guided filtering, ligand-focused preparation, atom-level SASA analysis, RDKit-based atom mapping, and browser-based interpretation of candidate ligand modification sites. Its clearest contribution is to provide an interpretable, structure-aware environment for examining which ligand atoms remain exposed in a bound complex and may warrant further medicinal chemistry evaluation.

## Data and Software Availability

The software repository is available at [TO VERIFY repository URL]. The current codebase is organized as a Flask application with job-specific output folders and browser-accessible result pages. Public deployment status, long-term hosting, software license, and archival release information should be confirmed before submission. [TO VERIFY]

## Author Contributions

- Conceptualization: [AUTHOR NEEDED]
- Software: [AUTHOR NEEDED]
- Methodology: [AUTHOR NEEDED]
- Validation: [AUTHOR NEEDED]
- Writing - original draft: [AUTHOR NEEDED]
- Writing - review and editing: [AUTHOR NEEDED]

## Acknowledgments

[ACKNOWLEDGMENTS NEEDED]

## References

1. SASA methodology reference. [REF NEEDED]
2. RDKit reference. [REF NEEDED]
3. Biopython reference. [REF NEEDED]
4. Flask reference. [REF NEEDED]
5. NGL reference. [REF NEEDED]
6. RCSB PDB reference. [REF NEEDED]
7. PDBe reference. [REF NEEDED]
8. Any covalent design / PROTAC design framing references used in the Introduction. [REF NEEDED]
