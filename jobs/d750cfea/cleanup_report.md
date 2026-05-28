# Cleanup Report — d750cfea

## Summary
- Cleaned at: `2026-05-18T21:26:49+00:00`
- Total size before: `50913305` bytes
- Public ZIP: `bundles/d750cfea_warhead_hunter_public_results.zip` (2825365 bytes)
- Archive ZIP: not created
- Files included in public ZIP: `386`
- Archive-only files detected: `96`
- Archived copies created: `0`
- Delete candidates: `0`
- Unknown files preserved: `4`
- Downstream-used files: `401`
- Unused files: `104`

## Warnings
- None

## Downstream Usage Audit
- `10_2DmappingExtraction.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `11_mcsMatcher.py`
  used by gallery/API: yes
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence:
  - pattern: pipeline_assets/11_mcsMatcher.py — Referenced by source text via '11_mcsMatcher.py'.
- `12_Results.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `13_mcsSASA_svg.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `14_SVGmkr.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `15_ResultsMerged.py`
  used by gallery/API: yes
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence:
  - pattern: pipeline_assets/15_ResultsMerged.py — Referenced by source text via '15_ResultsMerged.py'.
- `16_ResultsDisplay.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `17_obabelSDF.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `1_GRABBER.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `2_SQchk.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `3_PDBmkr.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `4_PDBfxr.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `5_PDBcln.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `5CharMAP.csv`
  used by gallery/API: yes
  included in public ZIP: no
  can archive: no
  can delete with explicit flags: no
  evidence:
  - pattern: pipeline_assets/11_mcsMatcher.py — Referenced by source text via '5CharMAP.csv'.
- `6_SASA.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `7_metadata.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `8_scaffold.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `9_2Dmapping.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `chain_similarity.csv`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `ChainRenameMAP.csv`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `CIFdata.csv`
  used by gallery/API: no
  included in public ZIP: no
  can archive: no
  can delete with explicit flags: no
  evidence: none found
- `Components-smiles-stereo-oe.smi`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `filtered_data.csv`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `input.csv`
  used by gallery/API: no
  included in public ZIP: no
  can archive: no
  can delete with explicit flags: no
  evidence: none found
- `job.log`
  used by gallery/API: yes
  included in public ZIP: no
  can archive: no
  can delete with explicit flags: no
  evidence:
  - template: templates/monitor.html — Referenced by source text via 'job.log'.
- `Ligand_3D_Atoms.csv`
  used by gallery/API: yes
  included in public ZIP: yes
  can archive: no
  can delete with explicit flags: no
  evidence:
  - pattern: pipeline_assets/15_ResultsMerged.py — Referenced by source text via 'Ligand_3D_Atoms.csv'.
  - pattern: pipeline_assets/11_mcsMatcher.py — Referenced by source text via 'Ligand_3D_Atoms.csv'.
- `Ligand_Metadata.csv`
  used by gallery/API: yes
  included in public ZIP: yes
  can archive: no
  can delete with explicit flags: no
  evidence:
  - pattern: Ligand_Metadata.csv — Used by ligand property helpers and Results_Display generation.
  - route: app.py — Referenced by source text via 'Ligand_Metadata.csv'.
  - pattern: pipeline_assets/16_ResultsDisplay.py — Referenced by source text via 'Ligand_Metadata.csv'.
- `Ligand_Metadata_Failures.csv`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `Ligand_PDB_Index.csv`
  used by gallery/API: no
  included in public ZIP: yes
  can archive: no
  can delete with explicit flags: no
  evidence: none found
- `MCS_Output`
  used by gallery/API: yes
  included in public ZIP: yes
  can archive: no
  can delete with explicit flags: no
  evidence:
  - folder_policy: MCS_Output — Mapped ligand outputs are served by SVG/SDF API routes and result views.
  - pattern: pipeline_assets/11_mcsMatcher.py — Referenced by source text via 'Ligand_AllAtoms_Map.csv'.
  - pattern: pipeline_assets/11_mcsMatcher.py — Referenced by source text via 'Ligand_MCS_Failures.csv'.
  - pattern: Ligand_MCS_Map.csv — Used by SVG selection and downstream atom mapping helpers.
  - route: app.py — Referenced by source text via 'Ligand_MCS_Map.csv'.
- `NON_LIGAND_CODES.py`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `Protein_Data.csv`
  used by gallery/API: yes
  included in public ZIP: yes
  can archive: no
  can delete with explicit flags: no
  evidence:
  - route: app.py — Referenced by source text via 'Protein_Data.csv'.
  - route: routes.py — Referenced by source text via 'Protein_Data.csv'.
  - template: templates/browse.html — Referenced by source text via 'Protein_Data.csv'.
- `queries.csv`
  used by gallery/API: no
  included in public ZIP: no
  can archive: no
  can delete with explicit flags: no
  evidence: none found
- `Resolved_SASA_Summary.csv`
  used by gallery/API: yes
  included in public ZIP: yes
  can archive: no
  can delete with explicit flags: no
  evidence:
  - pattern: Resolved_SASA_Summary.csv — Used by route helpers and downstream result display generation.
  - route: app.py — Referenced by source text via 'Resolved_SASA_Summary.csv'.
  - route: routes.py — Referenced by source text via 'Resolved_SASA_Summary.csv'.
- `Results_Display.csv`
  used by gallery/API: yes
  included in public ZIP: yes
  can archive: no
  can delete with explicit flags: no
  evidence:
  - pattern: Results_Display.csv — Used by gallery/result views as the primary display manifest.
  - route: app.py — Referenced by source text via 'Results_Display.csv'.
  - pattern: pipeline_assets/16_ResultsDisplay.py — Referenced by source text via 'Results_Display.csv'.
- `Skip4.txt`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence: none found
- `summary.json`
  used by gallery/API: no
  included in public ZIP: no
  can archive: no
  can delete with explicit flags: no
  evidence: none found
- `TARGET_RESULTS`
  used by gallery/API: yes
  included in public ZIP: yes
  can archive: yes
  can delete with explicit flags: no
  evidence:
  - folder_policy: TARGET_RESULTS — Final result collection directory assembled by 12_Results.py and consumed by result-serving helpers.
  - route: app.py — Referenced by source text via '3DSASAmapped.csv'.
  - pattern: pipeline_assets/12_Results.py — Referenced by source text via '3DSASAmapped.csv'.
  - pattern: pipeline_assets/11_mcsMatcher.py — Referenced by source text via '5CharMAP.csv'.
- `Target_Table`
  used by gallery/API: yes
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: no
  evidence:
  - folder_policy: Target_Table — Target_Table is consumed by downstream result-generation scripts such as 11_mcsMatcher.py.
  - pattern: pipeline_assets/11_mcsMatcher.py — Referenced by source text via 'Target_Table/Ligand_SMILES_Map.csv'.
  - pattern: pipeline_assets/11_mcsMatcher.py — Referenced by source text via 'Target_Table/SMILES_Ligand_Map.csv'.
- `WAR`
  used by gallery/API: no
  included in public ZIP: no
  can archive: yes
  can delete with explicit flags: yes
  evidence: none found
- `WAR_PDB`
  used by gallery/API: yes
  included in public ZIP: yes
  can archive: no
  can delete with explicit flags: no
  evidence:
  - folder_policy: WAR_PDB — Bound PDB directory is served by the gallery/API and Results_Display generation.
  - csv_reference: Results_Display.csv — Referenced by source text via 'WAR_PDB/Protease/1d4k_A_PI8.pdb'.
  - csv_reference: Results_Display.csv — Referenced by source text via 'WAR_PDB/Protease/1d4l_A_PI9.pdb'.
  - csv_reference: Results_Display.csv — Referenced by source text via 'WAR_PDB/Protease/1z1h_A_HBB.pdb'.
- `Warhead_SASA_atoms.csv`
  used by gallery/API: yes
  included in public ZIP: yes
  can archive: no
  can delete with explicit flags: no
  evidence:
  - pattern: Warhead_SASA_atoms.csv — Used by SASA overlay/atom mapping helpers and downstream merges.
  - route: app.py — Referenced by source text via 'Warhead_SASA_atoms.csv'.
  - pattern: pipeline_assets/12_Results.py — Referenced by source text via 'Warhead_SASA_atoms.csv'.
- `Warhead_SASA_summary.csv`
  used by gallery/API: no
  included in public ZIP: yes
  can archive: no
  can delete with explicit flags: no
  evidence: none found

## Delete Candidates
- None

## Unknown Files Preserved
- `5CharMAP.csv` — Alias/provenance table is still referenced downstream.
- `CIFdata.csv` — CIFdata.csv preserved conservatively until rebuildability and downstream use are confirmed.
- `queries.csv` — CSV artifact preserved conservatively.
- `summary.json` — JSON artifact preserved conservatively.

## Next Recommended Cleanup Rules
- Review UNKNOWN_KEEP artifacts after a real completed job audit and promote stable patterns only when route/template/API dependencies are confirmed.
- Keep actual `.cif` coordinate files out of public bundles by default unless a downstream dependency is proven.
- Prefer `--safe-package` in automation; reserve `--apply` cleanup for deliberate review.
