# Figure Plan

## Figure 1. Overall Warhead Hunter Workflow Schematic

### Purpose

Show the full software workflow from user input through structure retrieval, ligand processing, SASA calculation, atom mapping, and final interpretation.

### Suggested panels

- Panel A: User inputs
  - target identifier
  - text query
  - optional FASTA
- Panel B: Backend workflow
  - structure retrieval
  - sequence/ligand filtering
  - PDB preparation
  - ligand SASA analysis
- Panel C: Mapping and result generation
  - RDKit atom mapping
  - SVG/SDF generation
  - result tables
- Panel D: Interpretation layer
  - 2D exposure map
  - 3D viewer
  - candidate attachment-vector inspection

### Required screenshots/data

- Screenshot of `warhead_hunter.html` or `rcsb_scout.html`
- Pipeline/file schematic made manually
- Example 2D ligand map
- Example 3D result image

### Draft caption

Figure 1. Overall Warhead Hunter workflow. The platform accepts a target label, structure-search query, and optional FASTA sequence, retrieves candidate structures, prepares ligand-containing complexes, computes ligand atom solvent-accessible surface area, maps atom identities across 2D and 3D representations, and presents browser-based outputs for inspection of candidate ligand modification sites.

### What Still Needs To Be Generated

- Clean workflow schematic
- Final screenshots from a representative completed job
- Consistent color legend for exposure tiers

## Figure 2. Web Interface Workflow, Upload To Results

### Purpose

Show how a user moves through the interface from job setup to results interpretation.

### Suggested panels

- Panel A: Home or Scout page
- Panel B: Launch form with target/query/FASTA
- Panel C: Live monitor page with job log/progress
- Panel D: Results gallery with 2D and 3D panels

### Required screenshots/data

- Landing page screenshot
- Launch form screenshot
- Monitor page screenshot
- Results gallery screenshot

### Draft caption

Figure 2. Web-interface workflow in Warhead Hunter. Users initiate analysis through the launch form or scouting interface, monitor background execution in a job-specific progress page, and inspect completed outputs in a results gallery that synchronizes ligand summary cards, 2D maps, and 3D structure views.

### What Still Needs To Be Generated

- Final screenshots from one coherent run
- Cropped/high-resolution panel exports
- Optional callouts or annotations

## Figure 3. Ligand Atom Mapping And SASA Exposure Classification

### Purpose

Explain how atom-level exposure values are transferred onto interpretable ligand views.

### Suggested panels

- Panel A: Ligand atom extraction from processed PDB
- Panel B: 2D SMILES/RDKit atom index representation
- Panel C: MCS-based 2D to 3D atom correspondence
- Panel D: Exposure-colored ligand depiction with low/medium/high categories

### Required screenshots/data

- One example `Ligand_3D_Atoms.csv` excerpt
- One example `Ligand_MCS_Map.csv` or equivalent output excerpt
- Plain SVG
- Exposure-highlighted SVG

### Draft caption

Figure 3. Atom mapping and exposure classification workflow. Ligand atoms are extracted from processed protein-ligand structures, connected to ligand atom identities in 2D representations through RDKit-based atom mapping, and displayed using exposure-linked coloring to support inspection of potentially modifiable ligand atoms.

### What Still Needs To Be Generated

- A polished atom-mapping diagram
- One real output example with readable atom numbering
- Confirmation that the selected example reflects the current step-11 output format [TO VERIFY]

## Figure 4. Representative Protein-Ligand Case Study

### Purpose

Provide one manuscript-quality demonstration of how Warhead Hunter results are interpreted for a real ligand-bound structure.

### Suggested panels

- Panel A: Protein-ligand 3D overview
- Panel B: Ligand-only 2D exposure map
- Panel C: Close-up of exposed atoms in the binding site
- Panel D: Short interpretation panel indicating candidate derivatization positions

### Required screenshots/data

- Final selected target and ligand pair [TO VERIFY]
- Full 3D viewer screenshot
- 2D exposed SVG
- Possibly a cropped table row with `%Exposed`, exposed atoms, and SASA

### Draft caption

Figure 4. Representative Warhead Hunter case study. A ligand-bound protein structure is processed to generate atom-level exposure results, which are then visualized in 2D and 3D to highlight ligand atoms that remain solvent accessible in the observed binding pose and may warrant further derivatization analysis.

### What Still Needs To Be Generated

- Selection of the actual case-study system
- Rerun of the pipeline for reproducible figures
- Final interpretation text vetted against chemistry context

## Figure 5. Companion Ecosystem With PROTAC Builder, E3 Ligandalyzer, And V-LiSEMOD

### Purpose

Place Warhead Hunter in the broader tool ecosystem without overstating current integration depth.

### Suggested panels

- Panel A: Warhead Hunter role
  - exposed-atom and exit-vector analysis
- Panel B: E3 Ligandalyzer role
  - recruiter-ligand analysis [REF NEEDED]
- Panel C: PROTAC Builder role
  - downstream linker/construct design [REF NEEDED]
- Panel D: V-LiSEMOD role
  - companion structural analysis environment [REF NEEDED]

### Required screenshots/data

- Logos or screenshots for companion tools [FIGURE NEEDED]
- One simple ecosystem flow diagram
- Confirmed wording for each tool’s role [TO VERIFY]

### Draft caption

Figure 5. Proposed companion-tool ecosystem. Warhead Hunter can be positioned as an upstream structure-interpretation layer that identifies candidate ligand modification sites, with downstream reuse of those hypotheses in companion tools for recruiter analysis, construct design, and related structure-guided workflows.

### What Still Needs To Be Generated

- Approved tool descriptions
- Final ecosystem diagram
- Confirmation of integration claims that are safe to make in the manuscript
