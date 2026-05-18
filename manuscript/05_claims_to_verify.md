# Claims To Verify

## Every Claim That Needs Verification

- Warhead Hunter is available at a public production URL and that URL is intended for manuscript citation. [TO VERIFY]
- The primary automated workflow supports all formats implied in README/about-page language. [TO VERIFY]
- The manual-upload path can reproduce the same analysis outputs as the target/query/FASTA pipeline. [TO VERIFY]
- Exposure thresholds used in the UI are scientifically justified as classification bins rather than only display heuristics. [TO VERIFY]
- The pipeline can be run reproducibly from a clean environment using the current repository state. [TO VERIFY]
- `Ligand_MCS_SASA_ALL_ATOMS.csv` is reliably produced in the current default step-11 path. [TO VERIFY]
- The job-launch, monitor, and results flow works end to end on a fresh install. [TO VERIFY]
- The current result ranking or pose selection logic reflects the intended scientific reporting rule. [TO VERIFY]
- Any claim about support for PROTAC-oriented design beyond candidate site identification. [TO VERIFY]
- Any claim about covalent warhead design beyond candidate site identification. [TO VERIFY]
- Any claim about structure coverage, target coverage, or ligand coverage. [TO VERIFY]
- Any claim about runtime, throughput, or scaling behavior. [TO VERIFY]
- Any claim about accuracy, predictive power, or medicinal chemistry success rate. [TO VERIFY]
- Any claim about integration with companion tools beyond currently coded links/handoff behavior. [TO VERIFY]

## Missing References

- Solvent-accessible surface area methodology reference. [REF NEEDED]
- Biopython reference. [REF NEEDED]
- RDKit reference. [REF NEEDED]
- Flask reference. [REF NEEDED]
- NGL reference. [REF NEEDED]
- RCSB PDB reference. [REF NEEDED]
- PDBe reference. [REF NEEDED]
- If Murcko scaffolds are discussed in the manuscript, scaffold reference. [REF NEEDED]
- If drug-likeness filters are discussed, references for Lipinski, Veber, Ghose, Muegge, and Egan rules. [REF NEEDED]
- If covalent-design or PROTAC-design framing is included, supporting medicinal chemistry references. [REF NEEDED]

## Missing Screenshots

- Home page or launch page. [FIGURE NEEDED]
- RCSB Scout workflow. [FIGURE NEEDED]
- Job monitor page. [FIGURE NEEDED]
- Results gallery overview. [FIGURE NEEDED]
- 2D plain SVG example. [FIGURE NEEDED]
- 2D SASA-highlighted SVG example. [FIGURE NEEDED]
- 3D viewer screenshot with exposed atom highlights. [FIGURE NEEDED]
- One case-study figure with ligand close-up. [FIGURE NEEDED]

## Missing Validation Data

- One or more representative target/ligand case studies with final outputs. [TO VERIFY]
- A reproducible demonstration dataset or example job. [TO VERIFY]
- Software environment specification or install recipe. [TO VERIFY]
- Evidence that the pipeline runs from a clean checkout. [TO VERIFY]
- Optional but useful:
  - a small panel of case studies spanning different ligand chemotypes
  - comparison of exposed versus buried atoms across examples
  - annotation of known derivatization sites if available

## Possible Reviewer Concerns

- Solvent exposure is useful, but how should users interpret it relative to potency, binding mode retention, and synthetic tractability?
- Are the exposure thresholds empirically chosen, visually chosen, or literature-based?
- How robust is the workflow to ligand naming irregularities, alternate locations, and repeated ligands?
- Is the software intended for single-structure analysis, target-wide retrieval, or both?
- Which parts of the pipeline are current and which are legacy fallbacks?
- Can users install and run the software easily given the empty `requirements.txt`?
- Is the remote handoff logic a manuscript-relevant feature or only a local deployment detail?

## Possible JCIM Reviewer Questions

- What distinguishes Warhead Hunter from a general SASA script plus a molecular viewer?
- Why is atom-level ligand mapping necessary, and how is it performed?
- How are candidate structures filtered before analysis?
- Does the platform analyze all ligand atoms or only exposed atoms?
- How are multiple ligand occurrences across PDB entries handled?
- What is the intended domain of use: covalent design, linker design, PROTAC design, or general medicinal chemistry?
- What are the required inputs for routine use?
- What outputs are generated and how should they be interpreted?
- What evidence supports the chosen example case study?

## Things That Should Not Be Claimed Yet

- That the method is validated, benchmarked, or prospectively proven
- That highlighted atoms are optimal attachment sites
- That the platform predicts successful covalent warheads
- That the platform predicts successful PROTAC linkers
- That the software is production-ready as a public API
- That the app supports large-scale batch processing today
- That all structure and ligand input formats are fully supported
- That the workflow is fully automated end to end without user interpretation
- That the companion-tool ecosystem is fully integrated unless the exact implemented behavior is documented
