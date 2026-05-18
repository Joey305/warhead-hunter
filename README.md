# Warhead Hunter

**Warhead Hunter** is a structure-aware web platform for analyzing ligand-bound protein structures and identifying solvent-exposed ligand atoms that may be suitable for covalent warhead design, linker attachment, or PROTAC-style molecular engineering.

The goal is simple: turn complex protein-ligand structural data into a practical design question:

> Which ligand atoms are exposed enough to modify?

Warhead Hunter helps researchers inspect ligand exposure, evaluate possible exit vectors, and prioritize chemically useful attachment sites while preserving the context of the bound protein structure.

---

## Why Warhead Hunter?

In structure-guided drug discovery, a ligand may bind well, but that does not automatically mean it is easy to modify. For covalent inhibitor design, linker installation, or PROTAC development, the key question is often whether a ligand has solvent-facing atoms that can tolerate chemical expansion.

Warhead Hunter was built to help answer that question using structural context, ligand atom mapping, solvent-accessible surface area analysis, and interactive visualization.

---

## Core Features

- **Structure-aware ligand analysis**: analyze ligand atoms in the context of their bound protein structure.
- **SASA-guided exposure classification**: use solvent-accessible surface area values to identify exposed ligand atoms.
- **Atom-level visualization**: highlight exposed atoms to support fast interpretation of possible modification sites.
- **Warhead and linker design support**: prioritize potential exit vectors for covalent warheads, linker attachment, and PROTAC-style design.
- **Web-based research interface**: Flask-based structure with organized routes, templates, static assets, API logic, uploads, and background job execution.

---

## Example Use Cases

Warhead Hunter can support workflows such as:

1. Identifying solvent-exposed ligand atoms.
2. Prioritizing PROTAC linker attachment vectors.
3. Evaluating possible covalent warhead installation sites.
4. Comparing ligand exposure across related protein-ligand structures.
5. Generating interpretable visual summaries for reports, presentations, and manuscripts.
6. Supporting structure-guided medicinal chemistry decisions.

---

## Repository Structure

```text
warhead-hunter/
├── api/                  # API routes and backend helpers
├── app.py                # Main Flask application entry point
├── job_runner.py         # Job execution and background processing logic
├── pipeline_assets/      # Required pipeline resources and lightweight assets
├── routes.py             # Web routes and application views
├── static/               # CSS, JavaScript, images, and frontend assets
├── templates/            # HTML templates
├── uploads/              # Runtime uploads; ignored by Git except .gitkeep
├── requirements.txt      # Python dependencies
├── .gitignore            # Git ignore rules
└── README.md             # Project documentation
```

Runtime folders such as `jobs/` and most of `uploads/` should not be committed to GitHub because they may contain large generated outputs, user uploads, temporary files, or scientific data artifacts.

---

## Installation

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

---

## Running Locally

Start the Flask app:

```bash
python app.py
```

Then open the local development URL shown in the terminal, usually:

```text
http://127.0.0.1:5000
```

---

## Data and Output Policy

This repository is intended to store source code and lightweight project assets, not large runtime outputs.

Recommended to commit:

- Application source code
- API logic
- Templates
- Static frontend assets
- Lightweight pipeline assets
- Documentation
- Small curated examples, if needed

Recommended to exclude:

- Full job output folders
- User uploads
- Large molecular simulation or docking files
- Temporary analysis artifacts
- Private datasets
- Environment files
- Credentials, tokens, or secrets

---

## Safe Git Workflow

Before committing, check the folder size:

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

Initialize and push:

```bash
git init
touch uploads/.gitkeep
git add .
git status --short
git commit -m "Initial Warhead Hunter commit"
git branch -M main
git remote add origin https://github.com/Joey305/warhead-hunter.git
git push -u origin main
```

If the remote already exists locally, use:

```bash
git remote set-url origin https://github.com/Joey305/warhead-hunter.git
git push -u origin main
```

---

## Scientific Interpretation

Warhead Hunter provides computational guidance, not automatic chemical truth.

Solvent exposure can help identify possible ligand modification sites, but final decisions should also consider:

- Binding pose reliability
- Known structure-activity relationships
- Protein-ligand contact networks
- Exit-vector geometry
- Synthetic feasibility
- Linker length and flexibility
- Warhead reactivity
- Target biology and selectivity

The platform is intended to support expert-guided structure-based design rather than replace medicinal chemistry judgment.

---

## Future Directions

Potential development directions include:

- Public API documentation
- Example datasets
- Batch structure upload support
- Improved ligand atom mapping validation
- Downloadable analysis reports
- Integrated 2D ligand SVG export
- Interactive 3D protein-ligand viewers
- Per-atom exposure tooltips
- Multi-structure comparison mode
- Docker-based deployment
- Authentication for private/internal deployments

---

## Maintainer

Developed and maintained by **Joseph-Michael Schulz**.

Part of a broader structure-guided molecular design toolkit for ligand analysis, PROTAC development, and computational drug discovery.
