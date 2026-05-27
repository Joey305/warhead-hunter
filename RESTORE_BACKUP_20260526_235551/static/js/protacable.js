(() => {
  // ============================================================================
  // 0) CONFIG
  // ============================================================================
  const CFG = window.PROTACABLE_CONFIG || {};
  const JOB_ID = CFG.job_id;

  // ============================================================================
  // 1) STATE (UI-only)
  // ============================================================================
  const State = {
    current: { pdb: null, chain: null, warhead: null, resid: null },
    last2D:  { pdb: null, chain: null, warhead: null, resid: null },
    mapMode: "sasa",
    hudInitialized: false,
    handoff: null
  };

  const QED_TOOLTIP =
  `QED — Quantitative Estimate of Drug-likeness
  Score range: 0 → 1

  ● < 0.40   — Poor drug-likeness
  ● 0.40–0.60 — Moderate
  ● > 0.60   — Good

  Combines molecular weight, lipophilicity (LogP),
  polarity (TPSA), HBD/HBA counts, ring systems,
  and structural alerts into a single metric.`;



  // ============================================================================
  // 2) DOM + HELPERS
  // ============================================================================
  const $ = (id) => document.getElementById(id);

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g,"&amp;")
      .replace(/</g,"&lt;")
      .replace(/>/g,"&gt;")
      .replace(/"/g,"&quot;")
      .replace(/'/g,"&#039;");
  }

  async function fetchJSON(url) {
    try {
      const r = await fetch(url, { headers: { "Accept": "application/json" } });
      if (!r.ok) return { ok: false, status: r.status, data: null };
      return { ok: true, status: r.status, data: await r.json() };
    } catch {
      return { ok: false, status: 0, data: null };
    }
  }

  async function fetchText(url) {
    try {
      const r = await fetch(url);
      if (!r.ok) return { ok: false, status: r.status, data: "" };
      return { ok: true, status: r.status, data: await r.text() };
    } catch {
      return { ok: false, status: 0, data: "" };
    }
  }

  async function logBuilderClick(smile, job) {
    try {
      const r = await fetch("/api/log-builder-click", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          smile: String(smile || "").trim(),
          job: String(job || "").trim()
        })
      });
  
      return r.ok;
    } catch (err) {
      console.warn("Failed to log builder click:", err);
      return false;
    }
  }
  // ============================================================================
  // 3) HUD + UI helpers
  // ============================================================================
  function normalizePercent(x) {
    const v = Number(x);
    if (!isFinite(v)) return null;

    // If it's a fraction (0-1), treat as fraction -> percent
    if (v <= 1.00001) return v * 100;

    // If it's already percent (0-100), keep it
    if (v <= 100.0) return v;

    // If it looks double-multiplied (e.g. 5280), scale down
    if (v <= 10000.0) return v / 100.0;

    // Otherwise clamp (shouldn't happen)
    return 100.0;
  }

  function setHUD(pdb, chain, warhead, exposedValue, debug = "") {
    const hudPdb   = $("hud-pdb");
    const hudLig   = $("hud-lig");
    const hudExp   = $("hud-exp");
    const hudDebug = $("hud-debug");
    const hud      = $("hud");
    const toggle   = $("hud-toggle");
    if (!hud) return;

    if (hudPdb) hudPdb.innerText = `TARGET: ${pdb || "—"}`;
    if (hudLig) hudLig.innerText = warhead ? `LIGAND: ${warhead} (CHAIN ${chain || "?"})` : "";

    if (hudExp) {
      const pct = normalizePercent(exposedValue);
      hudExp.innerText = `OVERALL % EXPOSED: ${pct == null ? "—" : pct.toFixed(1)}%`;
    }

    if (hudDebug) hudDebug.innerText = debug || "";

    if (!State.hudInitialized) {
      hud.classList.remove("collapsed");
      if (toggle) toggle.textContent = "▾";
      State.hudInitialized = true;
    }
  }

  (function initHudToggle() {
    const hud = $("hud");
    const toggle = $("hud-toggle");
    if (!hud || !toggle) return;
    toggle.addEventListener("click", () => {
      const collapsed = hud.classList.toggle("collapsed");
      toggle.textContent = collapsed ? "▸" : "▾";
    });
  })();

  function renderSmiles(smiles) {
    const box = $("smiles-box");
    if (!box) return;
    const s = String(smiles || "").trim();
    box.innerHTML = s
      ? `<span style="color:#ffd600;">${escapeHtml(s)}</span>`
      : `<span style="color:#666;">(no SMILES)</span>`;
  }

  // ============================================================================
  // 4) Ligand properties panel
  // ============================================================================
  function qedColor(q) {
    const v = Number(q);
    if (!isFinite(v)) return "#444";
    if (v < 0.40) return "#d50000";
    if (v <= 0.60) return "#ffd600";
    return "#00c853";
  }


  function setTooltip(el, html) {
    if (!el) return;
    el.setAttribute("data-tooltip", html);
  }




  function setQEDChip(qed) {
    const node = $("qed-chip");
    if (!node) return;

    node.innerHTML = ""; // reset chip

    const v = Number(qed);
    if (!isFinite(v)) {
      node.textContent = "QED: —";
      node.style.color = "#bbb";
      node.style.borderColor = "rgba(255,255,255,0.12)";
      return;
    }

    // Chip text
    node.textContent = `QED: ${v.toFixed(2)}`;

    // Bar / chip color (already correct)
    const c = qedColor(v);
    node.style.color = c;
    node.style.borderColor = c;
    node.style.boxShadow = `0 0 10px ${c}22`;

    // Build tooltip HTML
    const tooltip = document.createElement("div");
    tooltip.className = "qed-tooltip";
    tooltip.innerHTML = `
      <div class="qed-title">
        QED — Quantitative Estimate of Drug-likeness
      </div>

      <div>Score range: 0 → 1</div><br>

      <div class="qed-line-poor">
        ● &lt; 0.40 — Poor drug-likeness
      </div>

      <div class="qed-line-moderate">
        ● 0.40–0.60 — Moderate
      </div>

      <div class="qed-line-good">
        ● &gt; 0.60 — Good
      </div>

      <div class="qed-muted">
        Combines molecular weight, lipophilicity (LogP),
        polarity (TPSA), HBD/HBA counts, ring systems,
        and structural alerts into a single metric.
      </div>
    `;

    node.appendChild(tooltip);
  }


  
  


  
  
  function setQEDBar(qed) {
    const fill = $("qed-bar-fill");
    if (!fill) return;
    const v = Number(qed);
    if (!isFinite(v)) {
      fill.style.width = "0%";
      fill.style.background = "#333";
      return;
    }
    const pct = Math.max(0, Math.min(1, v)) * 100;
    fill.style.width = `${pct}%`;
    fill.style.background = qedColor(v);
  }

  function fmt(v, fn) {
    if (v == null || v === "" || v === "None") return "—";
    try { return fn ? fn(v) : v; } catch { return v; }
  }

  function renderProperties(d = {}, ligandCode) {
    const ul = $("chem-props");
    if (!ul) return;

    const rows = [
      ["Ligand Code", ligandCode, "Internal ligand identifier (resname)."],
      ["MW", fmt(d.MW, v => `${Number(v).toFixed(1)} g/mol`), "Molecular weight."],
      ["LogP", fmt(d.LogP, v => Number(v).toFixed(2)), "Lipophilicity estimate."],
      ["TPSA", fmt(d.TPSA, v => `${Number(v).toFixed(1)} Å²`), "Polar surface area."],
      ["HBA / HBD", (d.HBA != null && d.HBD != null) ? `${d.HBA}/${d.HBD}` : "—", "Hydrogen bond acceptors/donors."],
      ["Rotatable Bonds", fmt(d.Rotatable_Bonds), "Flexibility metric."],
      ["Ring Count", fmt(d.Ring_Count), "Total rings."],
      ["Aromatic Rings", fmt(d.Aromatic_Rings), "Aromatic ring count."],
    ];

    ul.innerHTML = rows.map(([k,v,tip]) => `
      <li class="prop-row" title="${escapeHtml(tip)}">
        <span class="prop-k">${escapeHtml(k)}</span>
        <span class="prop-v">${escapeHtml(v ?? "—")}</span>
      </li>
    `).join("");
  }

  function renderRules(d = {}) {
    const node = $("drug-rules");
    if (!node) return;

    const rules = [
      ["Lipinski", d.Lipinski_Pass, "Lipinski: MW ≤ 500, LogP ≤ 5, HBD ≤ 5, HBA ≤ 10"],
      ["Veber", d.Veber_Pass, "Veber: rotatable bonds ≤ 10 and TPSA ≤ 140 Å²"],
      ["Ghose", d.Ghose_Pass, "Ghose drug-like range"],
      ["Muegge", d.Muegge_Pass, "Muegge drug-like window"],
      ["Egan", d.Egan_Pass, "Egan PSA/LogP window"]
    ];

    node.innerHTML = rules.map(([name, pass, tip]) => {
      if (pass == null || pass === "" || pass === "None") return "";
      const ok = (pass === true || pass === "True" || pass === "true" || pass === 1 || pass === "1");
      return `<span class="rule-chip ${ok ? "ok" : "bad"}" title="${escapeHtml(tip)}">${escapeHtml(name)}</span>`;
    }).join(" ");
  }

  async function loadLigandProps(ligandCode, smiles="") {
    const qs = new URLSearchParams();
    if (smiles && String(smiles).trim()) qs.set("smiles", String(smiles).trim());

    const url = `/api/ligand_props/${JOB_ID}/${encodeURIComponent(ligandCode)}?${qs.toString()}`;
    const r = await fetchJSON(url);

    if (!r.ok || !r.data || Object.keys(r.data).length === 0) {
      renderProperties({}, ligandCode);
      renderRules({});
      setQEDChip(null);
      setQEDBar(null);
      return;
    }

    renderProperties(r.data, ligandCode);
    renderRules(r.data);
    setQEDChip(r.data.QED);
    setQEDBar(r.data.QED);
  }

  // ============================================================================
  // 5) 2D MAP (includes resid)
  // ============================================================================
  async function load2DMap(pdb, chain, warhead, resid) {
    const node = $("2d-map");
    if (!node) return;

    const base = (State.mapMode === "normal") ? "svg-plain" : "svg";

    const qs = new URLSearchParams();
    const r = String(resid || "").trim();
    if (r) qs.set("resid", r);
    qs.set("_", String(Date.now()));

    const url = `/api/${base}/${JOB_ID}/${pdb}/${chain}/${warhead}?${qs.toString()}`;

    const legend = $("sasa-legend");
    const normalUI = $("normal-ui");
    if (State.mapMode === "sasa") {
      if (legend) legend.style.display = "block";
      if (normalUI) normalUI.style.display = "none";
    } else {
      if (legend) legend.style.display = "none";
      if (normalUI) normalUI.style.display = "block";
    }

    if (node.tagName === "IMG") {
      node.src = url;
      return;
    }

    const t = await fetchText(url);
    if (!t.ok || !t.data) {
      node.innerHTML = `<div style="color:#888;padding:8px;">Failed to load 2D map.</div>`;
      return;
    }
    node.innerHTML = t.data;
  }

  function bindMapToggle() {
    const btnSASA = $("toggle-sasa");
    const btnNorm = $("toggle-normal");
    if (!btnSASA || !btnNorm) return;

    function setMode(mode) {
      State.mapMode = mode;
      btnSASA.classList.toggle("active", mode === "sasa");
      btnNorm.classList.toggle("active", mode === "normal");
      if (State.last2D.pdb && State.last2D.chain && State.last2D.warhead) {
        load2DMap(State.last2D.pdb, State.last2D.chain, State.last2D.warhead, State.last2D.resid);
      }
    }

    btnSASA.addEventListener("click", () => setMode("sasa"));
    btnNorm.addEventListener("click", () => setMode("normal"));
    setMode("sasa");
  }

  // ============================================================================
  // 6) Cards + actions
  // ============================================================================
  async function bestChain(pdb, chainFromResults, warhead) {
    let c = String(chainFromResults || "").toUpperCase().trim();
    if (c) return c;

    const r = await fetchJSON(`/api/ligand_chain/${JOB_ID}/${pdb}/${warhead}`);
    if (r.ok && r.data && r.data.chain) {
      const srv = String(r.data.chain).toUpperCase().trim();
      if (srv) return srv;
    }
    return "A";
  }

  function bindCards() {
    document.querySelectorAll(".result-card").forEach(card => {
      card.addEventListener("click", () => {
        const pdb     = card.dataset.pdb;
        const chain   = card.dataset.chain;
        const warhead = card.dataset.warhead;
        const resid   = (card.dataset.resid || "").trim();
        const smiles  = card.dataset.smiles || "";
        const exposedValue = Number(card.dataset.exposed || 0);

        window.syncView(pdb, chain, warhead, resid, smiles, exposedValue);
      });
    });
  }

  function bindSmilesActions() {
    const copyBtn = $("copy-smiles");
    const sdfBtn  = $("download-sdf");

    if (copyBtn) {
      copyBtn.addEventListener("click", () => {
        const s = $("smiles-box")?.innerText?.trim();
        if (!s) return;
        navigator.clipboard.writeText(s);
        copyBtn.textContent = "COPIED";
        copyBtn.classList.add("flash");
        setTimeout(() => {
          copyBtn.textContent = "COPY";
          copyBtn.classList.remove("flash");
        }, 900);
      });
    }

    if (sdfBtn) {
      sdfBtn.addEventListener("click", () => {
        const { pdb, chain, warhead, resid } = State.current;
        if (!pdb || !chain || !warhead) return;

        const qs = new URLSearchParams();
        if (resid) qs.set("resid", resid);

        window.open(`/api/sdf/${JOB_ID}/${pdb}/${chain}/${warhead}?${qs.toString()}`, "_blank");
      });
    }
  }

  // ✅ FIXED: top-level function (not nested)
  function bindProtacBuilder() {
    const btn = $("protac-builder");
    if (!btn) return;
  
    btn.addEventListener("click", async () => {
      const { pdb, chain, warhead } = State.current;
      if (!pdb || !chain || !warhead) return;
  
      const smiles = ($("smiles-box")?.innerText || "").trim();
      if (!smiles) {
        alert("No SMILES found for PROTAC Builder.");
        return;
      }
  
      // ------------------------------------------------------------
      // 1) BACKEND HANDOFF (keep your existing behavior if desired)
      // ------------------------------------------------------------
      fetch(
        `/api/handoff/materialize/${CFG.job_id}/${pdb}/${chain}/${warhead}`,
        { method: "POST" }
      ).catch(err => {
        console.warn("Hunter handoff failed:", err);
      });
  
      // ------------------------------------------------------------
      // 2) LOG THE BUILDER CLICK TO builderjobs.csv
      // ------------------------------------------------------------
      await logBuilderClick(smiles, CFG.job_id);
  
      // ------------------------------------------------------------
      // 3) OPEN PROTAC BUILDER
      // ------------------------------------------------------------
      const builderUrl = `https://protacbuilder.com/?smiles=${encodeURIComponent(smiles)}`;
      window.open(builderUrl, "_blank", "noopener,noreferrer");
    });
  }


  function bindDownloadPDB() {
    const pdbBtn = $("download-pdb");
    if (!pdbBtn) return;

    pdbBtn.addEventListener("click", () => {
      const { pdb, chain, warhead } = State.current;
      if (!pdb || !chain || !warhead) return;
      window.location.href = `/api/pdb/${JOB_ID}/${pdb}_${chain}_${warhead}.pdb`;
    });
  }

  async function resolveResid(pdb, chain, warhead, residFromCard) {
    const raw = String(residFromCard || "").trim();
    if (raw) return raw;

    const url =
      `/api/jobs/${encodeURIComponent(JOB_ID)}/sasa/residue_for_ligand?` +
      `pdb_id=${encodeURIComponent(String(pdb).toLowerCase())}` +
      `&chain=${encodeURIComponent(String(chain).toUpperCase())}` +
      `&ligand=${encodeURIComponent(String(warhead).toUpperCase())}`;

    const r = await fetchJSON(url);
    if (r.ok && r.data && r.data.residue_id) return String(r.data.residue_id).trim();
    return "";
  }

  // ============================================================================
  // 7) syncView (UI + calls Render3D)
  // ============================================================================
  window.syncView = async function syncView(pdb, chainFromResults, warhead, resid, smiles, exposedValue) {
    const PDB = String(pdb || "").toLowerCase().trim();
    const WAR = String(warhead || "").toUpperCase().trim();

    const chain = await bestChain(PDB, chainFromResults, WAR);

    let RESID = (resid == null) ? "" : String(resid).trim();
    RESID = await resolveResid(PDB, chain, WAR, RESID);

    State.current = { pdb: PDB, chain, warhead: WAR, resid: RESID };
    State.last2D  = { pdb: PDB, chain, warhead: WAR, resid: RESID };

    renderSmiles(smiles || "");
    setHUD(PDB, chain, WAR, exposedValue, "Loading…");

    loadLigandProps(WAR, smiles || "");
    await load2DMap(PDB, chain, WAR, RESID);

    if (window.Render3D && typeof window.Render3D.load === "function") {
      window.Render3D.load({ pdb: PDB, chain, warhead: WAR, resid: RESID });
      setHUD(PDB, chain, WAR, exposedValue, "Loaded");
    } else {
      setHUD(PDB, chain, WAR, exposedValue, "3D renderer not loaded.");
      console.warn("Render3D not available. Check script order in HTML.");
    }
  };

  // ============================================================================
  // 8) Handoff links (moved INSIDE IIFE so CFG/State exist)
  // ============================================================================
  async function loadHandoffLinks(pdb, chain, warhead) {
    const url = `/api/handoff/prefill/${CFG.job_id}/${pdb}/${warhead}?chain=${encodeURIComponent(chain || "")}`;
    const headers = {};
    if (window.HANDOFF_TOKEN) headers["X-HANDOFF-TOKEN"] = window.HANDOFF_TOKEN;

    const data = await fetch(url, { headers }).then(r => r.json());
    State.handoff = data; // store: {smiles, urls:{pdb,sdf,molblock}, chain,resid}
    return data;
  }
  // If you need it elsewhere:
  window.loadHandoffLinks = loadHandoffLinks;

  // ============================================================================
  // 9) BOOT
  // ============================================================================
  document.addEventListener("DOMContentLoaded", () => {
    bindCards();
    bindMapToggle();
    bindSmilesActions();
    bindDownloadPDB();
    bindProtacBuilder();

    const first = document.querySelector(".result-card");
    if (first) {
      const pdb     = first.dataset.pdb;
      const chain   = first.dataset.chain;
      const warhead = first.dataset.warhead;
      const smiles  = first.dataset.smiles || "";
      const resid   = (first.dataset.resid || "").trim();
      const exposedValue = Number(first.dataset.exposed || 0);

      window.syncView(pdb, chain, warhead, resid, smiles, exposedValue);
    }
  });
})();
