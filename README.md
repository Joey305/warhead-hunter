# 🧪 Warhead Hunter

<p align="center">
  <strong>Warhead Hunter: Structure-Aware Solvent Exposure Analysis for Warhead, Linker, and PROTAC Attachment Vector Discovery</strong>
</p>

<p align="center">
  <em>A web-based molecular design tool for identifying solvent-exposed ligand atoms in protein-bound structures and prioritizing chemically useful modification sites.</em>
</p>

<p align="center">
  <a href="https://warheadhunter.com">
    <img src="https://img.shields.io/badge/Launch-WarheadHunter.com-00e5ff?style=for-the-badge&logo=googlechrome&logoColor=white" alt="Launch Warhead Hunter">
  </a>
  <a href="https://github.com/Joey305/warhead-hunter">
    <img src="https://img.shields.io/badge/GitHub-warhead--hunter-181717?style=for-the-badge&logo=github" alt="Warhead Hunter GitHub repository">
  </a>
  <a href="#quick-start">
    <img src="https://img.shields.io/badge/Get%20Started-Quick%20Start-orange?style=for-the-badge&logo=gnubash" alt="Quick start">
  </a>
  <a href="#citation">
    <img src="https://img.shields.io/badge/Manuscript-In%20Preparation-blueviolet?style=for-the-badge&logo=readthedocs" alt="Manuscript in preparation">
  </a>
</p>

<p align="center">
  <a href="https://e3ligandalyzer.com">
    <img src="https://img.shields.io/badge/Companion%20Tool-E3%20Ligandalyzer-7c3aed?style=for-the-badge" alt="E3 Ligandalyzer">
  </a>
  <a href="https://protacbuilder.com">
    <img src="https://img.shields.io/badge/Companion%20Tool-PROTAC%20Builder-06b6d4?style=for-the-badge" alt="PROTAC Builder">
  </a>
  <a href="https://vlisemod.com">
    <img src="https://img.shields.io/badge/Companion%20Tool-V--LiSEMOD-22c55e?style=for-the-badge" alt="V-LiSEMOD">
  </a>
</p>

<p align="center">
  <a href="mailto:jxs794@miami.edu?subject=Warhead%20Hunter%20Question%20%2F%20Collaboration">
    <img src="https://img.shields.io/badge/Contact-Joseph--Michael%20Schulz-blue?style=for-the-badge&logo=gmail" alt="Contact Joseph-Michael Schulz">
  </a>
</p>

---

<p align="center">
  <strong>Find the atoms that can be modified.</strong>
</p>

<p align="center">
  <em>From protein-ligand structures to interpretable solvent-exposure maps for covalent design, linker attachment, and induced-proximity workflows.</em>
</p>

---

<a id="overview"></a>

## 🚀 Overview

**Warhead Hunter** is a structure-aware web platform for analyzing ligand-bound protein structures and identifying **solvent-exposed ligand atoms** that may be suitable for:

- covalent warhead installation,
- linker attachment,
- PROTAC warhead optimization,
- chemical expansion,
- exit-vector discovery, and
- structure-guided medicinal chemistry.

Many molecular design workflows begin with a high-confidence ligand-bound structure. But after identifying a binder, the next question is often more difficult:

> **Where can this ligand be modified without disrupting its bound pose?**

Warhead Hunter addresses that question by combining protein-ligand structural context, ligand atom mapping, solvent-accessible surface area analysis, and interactive visual interpretation.

### PROTAC Builder handoff URL

`PROTAC_BUILDER_BASE` controls the external PROTAC Builder handoff target.

Default:

```text
https://protacbuilder.com/copy/COPYindex
```

For private development only, override it with:

```text
PROTAC_BUILDER_BASE=https://your-private-builder/copy/COPYindex
```

Do not hardcode private builder URLs in source.

---

<a id="why-warhead-hunter"></a>

## 🎯 Why Warhead Hunter?

A ligand may bind well, but that does not mean every atom is synthetically or structurally useful for modification.

For covalent inhibitor design, linker installation, and PROTAC development, researchers need to identify atoms that are:

- sufficiently solvent exposed,
- not deeply buried in the binding pocket,
- not essential to the observed protein-ligand interaction network,
- chemically plausible for derivatization, and
- geometrically useful as potential exit vectors.

Warhead Hunter was designed to make that evaluation faster, clearer, and more reproducible.

---

<a id="repository-navigation"></a>

## 🧭 Repository Navigation

<p align="center">
  <a href="#quick-start">
    <img src="https://img.shields.io/badge/Quick%20Start-Run%20Locally-orange?style=for-the-badge&logo=python" alt="Run locally">
  </a>
  <a href="#pipeline">
    <img src="https://img.shields.io/badge/Pipeline-Structure%20to%20Exposure%20Map-00bcd4?style=for-the-badge" alt="Pipeline">
  </a>
  <a href="#scientific-interpretation">
    <img src="https://img.shields.io/badge/Interpretation-Design%20Guidance-8b5cf6?style=for-the-badge" alt="Scientific interpretation">
  </a>
  <a href="#data-policy">
    <img src="https://img.shields.io/badge/Data%20Policy-Keep%20Repos%20Light-lightgrey?style=for-the-badge&logo=github" alt="Data policy">
  </a>
</p>

- [Overview](#overview)
- [Why Warhead Hunter?](#why-warhead-hunter)
- [Core capabilities](#core-capabilities)
- [Companion tool ecosystem](#companion-tool-ecosystem)
- [Pipeline](#pipeline)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Local development](#local-development)
- [Data and output policy](#data-policy)
- [Scientific interpretation](#scientific-interpretation)
- [Manuscript status and citation](#citation)
- [Roadmap](#roadmap)
- [Contact](#contact)

---

<a id="core-capabilities"></a>

## ✨ Core Capabilities

| Capability | Purpose |
|---|---|
| **Structure-aware ligand analysis** | Analyze ligand atoms in the context of their bound protein structure. |
| **SASA-guided exposure classification** | Use solvent-accessible surface area values to identify atoms likely to tolerate modification. |
| **Atom-level exposure mapping** | Highlight ligand atoms by exposure tier for rapid interpretation. |
| **Warhead design support** | Identify possible covalent attachment vectors and chemically accessible positions. |
| **PROTAC linker guidance** | Prioritize ligand exit vectors for linker installation and degrader design. |
| **Interactive web interface** | Provide accessible upload, processing, visualization, and result-browsing workflows. |
| **Pipeline-style execution** | Organize structure processing, mapping, analysis, and result generation into reproducible steps. |

---

<a id="companion-tool-ecosystem"></a>

## 🧬 Companion Tool Ecosystem

Warhead Hunter is part of a broader structure-guided molecular design ecosystem.

<p align="center">
  <a href="https://warheadhunter.com">
    <img src="https://img.shields.io/badge/Warhead%20Hunter-Solvent%20Exposure%20%2B%20Exit%20Vectors-00e5ff?style=for-the-badge" alt="Warhead Hunter">
  </a>
</p>

<p align="center">
  <a href="https://e3ligandalyzer.com">
    <img src="https://img.shields.io/badge/E3%20Ligandalyzer-E3%20Recruiter%20Ligand%20Analytics-7c3aed?style=for-the-badge" alt="E3 Ligandalyzer">
  </a>
  <a href="https://protacbuilder.com">
    <img src="https://img.shields.io/badge/PROTAC%20Builder-Ternary%20Complex%20Modeling-06b6d4?style=for-the-badge" alt="PROTAC Builder">
  </a>
  <a href="https://vlisemod.com">
    <img src="https://img.shields.io/badge/V--LiSEMOD-Viral%20Ligand%20Interaction%20Explorer-22c55e?style=for-the-badge" alt="V-LiSEMOD">
  </a>
</p>

Together, these tools support a connected workflow:

```text
Protein-ligand structure
        ↓
Ligand exposure and exit-vector analysis
        ↓
Warhead or linker attachment hypothesis
        ↓
E3 recruiter selection / PROTAC design
        ↓
Ternary complex modeling and downstream evaluation
```

---

<a id="pipeline"></a>

## 🧩 Conceptual Pipeline

Warhead Hunter is designed around a practical structure-to-design workflow:

```text
1. Upload or select a protein-ligand structure
2. Extract and validate ligand context
3. Map ligand atoms across 2D and 3D representations
4. Compute or load solvent-accessible surface area values
5. Classify atoms by exposure tier
6. Generate interpretable visual outputs
7. Prioritize possible modification vectors
```

The central design question is:

```text
Which ligand atoms are exposed enough to modify,
while preserving the bound pose and meaningful protein-ligand contacts?
```

---

<a id="exposure-logic"></a>

## 🎨 Exposure Logic

Warhead Hunter focuses on atom-level ligand solvent exposure.

A typical interpretation scheme is:

| Exposure concept | Design meaning |
|---|---|
| **Low exposure / buried** | More likely to participate in binding-pocket fit or protein contacts. Modify cautiously. |
| **Moderate exposure** | May tolerate careful derivatization depending on geometry and chemistry. |
| **High exposure** | Stronger candidate for linker growth, warhead installation, or chemical expansion. |

Exposure should not be interpreted alone. It is most useful when considered alongside binding pose quality, local interactions, synthetic feasibility, and known structure-activity relationships.

---

<a id="example-use-cases"></a>

## 🔬 Example Use Cases

Warhead Hunter can support:

1. **Covalent inhibitor design**  
   Identify ligand atoms that may be suitable for warhead installation.

2. **PROTAC warhead optimization**  
   Evaluate whether a ligand has solvent-facing atoms that could support linker attachment.

3. **Exit-vector discovery**  
   Prioritize positions for chemical expansion without disrupting the binding pose.

4. **Protein-ligand structure triage**  
   Compare multiple structures to identify ligands with more favorable modification opportunities.

5. **Medicinal chemistry communication**  
   Generate visual, interpretable exposure maps for reports, presentations, and manuscripts.

6. **Integrated degrader design workflows**  
   Connect target-ligand evaluation with E3 recruiter analysis and ternary complex modeling.

---

<a id="repository-layout"></a>

## 📦 Repository Layout

```text
warhead-hunter/
├── api/                  # API routes and backend helpers
├── app.py                # Main Flask application entry point
├── job_runner.py         # Job execution and background processing logic
├── pipeline_assets/      # Structure-processing and analysis scripts
├── routes.py             # Web routes and application views
├── static/               # CSS, JavaScript, images, and frontend assets
├── templates/            # HTML templates
├── uploads/              # Runtime uploads; ignored by Git except .gitkeep
├── requirements.txt      # Python dependencies
├── .gitignore            # Git ignore rules
└── README.md             # Project documentation
```

The application is organized as a Flask-based web tool with dedicated folders for backend routes, API helpers, static assets, templates, uploads, and pipeline execution.

---

<a id="quick-start"></a>

## ⚡ Quick Start

Clone the repository:

```bash
git clone https://github.com/Joey305/warhead-hunter.git
cd warhead-hunter
```

Create and activate a Python environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run locally:

```bash
python app.py
```

Open the local development URL shown in the terminal, usually:

```text
http://127.0.0.1:5000
```

---

<a id="local-development"></a>

## 🛠️ Local Development

Recommended development loop:

```bash
cd warhead-hunter
source .venv/bin/activate
python app.py
```

Check Git status before committing:

```bash
git status --short
```

Commit changes:

```bash
git add .
git commit -m "Describe update"
git push
```

If the remote contains newer commits:

```bash
git pull --rebase origin main
git push
```

---

<a id="data-policy"></a>

## 🧹 Data and Output Policy

This repository should remain lightweight and source-code focused.

Recommended to commit:

- application source code,
- API logic,
- templates,
- static frontend assets,
- lightweight pipeline assets,
- documentation,
- small curated examples, if needed.

Recommended to exclude:

- full job output folders,
- user uploads,
- large generated molecular files,
- docking or simulation output,
- temporary analysis artifacts,
- private datasets,
- environment files,
- credentials, tokens, and secrets.

Before committing, check repository size:

```bash
du -sh .
du -h --max-depth=1 . | sort -hr
```

Check for large files outside ignored folders:

```bash
find . -type f -size +50M \
  -not -path "./.git/*" \
  -not -path "./jobs/*" \
  -not -path "./uploads/*" \
  -exec ls -lh {} \;
```

Confirm ignored folders are ignored:

```bash
git check-ignore -v jobs
git check-ignore -v uploads/example_file
```

---

<a id="scientific-interpretation"></a>

## 🧠 Scientific Interpretation

Warhead Hunter provides computational guidance, not automatic chemical truth.

Solvent exposure can help identify possible modification sites, but final decisions should also consider:

- protein-ligand binding pose reliability,
- electron density or model confidence when available,
- known structure-activity relationships,
- direct hydrogen bonding or ionic interactions,
- hydrophobic packing and shape complementarity,
- exit-vector geometry,
- synthetic feasibility,
- warhead reactivity,
- linker length and flexibility,
- target biology and selectivity.

Warhead Hunter is intended to support expert-guided medicinal chemistry and structure-based design. It should be used as a decision-support tool, not as a replacement for experimental validation.

---

<a id="manuscript-positioning"></a>

## 📄 Manuscript Positioning

Warhead Hunter is being developed as a manuscript-ready scientific software platform for structure-guided ligand modification analysis.

Potential manuscript framing:

> **Warhead Hunter enables atom-level solvent exposure analysis of protein-bound ligands to support covalent warhead placement, linker attachment, and PROTAC-oriented exit-vector prioritization.**

Planned manuscript themes may include:

- motivation for exposure-guided ligand modification,
- structure-aware atom mapping,
- 2D/3D visual interpretation,
- use cases in covalent inhibitor and PROTAC design,
- comparison across ligand-bound structures,
- integration with companion degrader design tools.

---

<a id="roadmap"></a>

## 🧭 Roadmap

Potential development directions:

- public API documentation,
- example datasets,
- batch structure upload support,
- improved ligand atom mapping validation,
- downloadable analysis reports,
- integrated 2D ligand SVG export,
- interactive 3D protein-ligand viewers,
- per-atom exposure tooltips,
- multi-structure comparison mode,
- Docker-based deployment,
- authentication for private/internal deployments,
- manuscript figure generation workflows.

---

<a id="citation"></a>

## 🧬 Citation

A manuscript for Warhead Hunter is currently in preparation.

For now, cite the GitHub repository and website:

```text
Schulz, J.-M. Warhead Hunter: Structure-Aware Solvent Exposure Analysis for Warhead, Linker, and PROTAC Attachment Vector Discovery. GitHub repository: https://github.com/Joey305/warhead-hunter. Web platform: https://warheadhunter.com.
```

Once a preprint or publication is available, this section should be updated with the formal citation, DOI, and manuscript link.

<p align="center">
  <a href="https://warheadhunter.com">
    <img src="https://img.shields.io/badge/Web%20Platform-WarheadHunter.com-00e5ff?style=for-the-badge&logo=googlechrome" alt="Warhead Hunter website">
  </a>
  <a href="https://github.com/Joey305/warhead-hunter">
    <img src="https://img.shields.io/badge/Source%20Code-GitHub-181717?style=for-the-badge&logo=github" alt="Warhead Hunter GitHub">
  </a>
</p>

---

<a id="contact"></a>

## 📬 Contact

For questions, bug reports, workflow support, or collaboration inquiries:

<p align="center">
  <a href="mailto:jxs794@miami.edu?subject=Warhead%20Hunter%20Question%20%2F%20Collaboration">
    <img src="https://img.shields.io/badge/Joseph--Michael%20Schulz-jxs794%40miami.edu-blue?style=for-the-badge&logo=gmail" alt="Email Joseph-Michael Schulz">
  </a>
  <a href="https://warheadhunter.com">
    <img src="https://img.shields.io/badge/Visit-WarheadHunter.com-00e5ff?style=for-the-badge&logo=googlechrome" alt="Visit Warhead Hunter">
  </a>
</p>

---

<a id="repository-description"></a>

## 🧾 Repository Description

> Warhead Hunter is a structure-aware web platform for identifying solvent-exposed ligand atoms and prioritizing warhead, linker, and PROTAC attachment vectors.

---

<a id="practical-takeaway"></a>

## 🙌 Practical Takeaway

Use Warhead Hunter when you have a protein-ligand structure and need to answer:

```text
Where can this ligand be modified?
```

The platform helps convert structural data into interpretable atom-level exposure maps for rational warhead placement, linker design, and PROTAC-oriented medicinal chemistry.
