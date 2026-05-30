(() => {
  // ============================================================================
  // 0) CONFIG
  // ============================================================================
  const CFG = window.PROTACABLE_CONFIG || {};
  const JOB_ID = CFG.job_id;
  const DEFAULT_PROTAC_BUILDER_BASE = "https://protacbuilder.com/copy/COPYindex";

  function getProtacBuilderBase() {
    const raw =
      String(window.PROTAC_BUILDER_BASE || "").trim() ||
      String(window.PROTACSUITE || "").trim() ||
      DEFAULT_PROTAC_BUILDER_BASE;

    return raw.replace(/\/+$/, "");
  }

  function getProtacBuilderOrigin() {
    try {
      return new URL(getProtacBuilderBase()).origin;
    } catch {
      return "https://protacbuilder.com";
    }
  }

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

  const RESULTS_LOADER_STEP_ORDER = [
    "boot",
    "artifacts",
    "selection",
    "map",
    "properties",
    "viewport"
  ];
  const RESULTS_LOADER_SAFETY_TIMEOUT_MS = 30000;

  const ResultsLoader = (() => {
    const state = {
      visible: true,
      mode: "loading",
      title: "Loading Warhead Results",
      detail: "Preparing results command center",
      current: "Initializing gallery boot sequence",
      progress: 6,
      steps: {},
      startedAt: 0,
      hidden: false,
      timeoutId: null,
      actionsVisible: false,
      continueLabel: "Continue to results"
    };

    function getParts() {
      return {
        modal: $("warhead-results-loader"),
        title: $("warhead-results-loader-title"),
        detail: $("warhead-results-loader-detail"),
        progress: $("warhead-results-loader-progress"),
        current: $("warhead-results-loader-current"),
        status: $("warhead-results-loader-state"),
        steps: $("warhead-results-loader-steps"),
        actions: $("warhead-results-loader-actions"),
        continueBtn: $("warhead-results-loader-continue"),
        refreshBtn: $("warhead-results-loader-refresh")
      };
    }

    function escapeText(value) {
      return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function renderSteps(parts) {
      if (!parts.steps) return;

      const html = RESULTS_LOADER_STEP_ORDER
        .filter((name) => state.steps[name])
        .map((name) => {
          const step = state.steps[name];
          const stepState = step.state || "pending";
          const detail = step.detail ? `: ${escapeText(step.detail)}` : "";
          return `<div class="warhead-results-loader-step is-${escapeText(stepState)}"><span>${escapeText(step.label || name)}</span><span>${detail}</span></div>`;
        })
        .join("");

      parts.steps.innerHTML = html;
      parts.steps.hidden = !html;
    }

    function apply() {
      const parts = getParts();
      if (!parts.modal) return;

      if (parts.title) parts.title.textContent = state.title;
      if (parts.detail) parts.detail.textContent = state.detail;
      if (parts.current) parts.current.textContent = state.current;
      if (parts.status) parts.status.textContent = state.mode === "error"
        ? "Results loader paused in an error state."
        : state.mode === "partial"
          ? "Results loader paused in a degraded state."
          : "Results loader is preparing the first usable ligand.";

      if (parts.progress && Number.isFinite(Number(state.progress))) {
        const pct = Math.max(3, Math.min(100, Number(state.progress)));
        parts.progress.style.width = `${pct}%`;
        parts.progress.parentElement?.style.setProperty("--results-progress", `${pct}%`);
      }

      parts.modal.classList.toggle("is-active", Boolean(state.visible));
      parts.modal.classList.toggle("is-error", state.mode === "error");
      parts.modal.classList.toggle("is-partial", state.mode === "partial");
      parts.modal.setAttribute("aria-hidden", state.visible ? "false" : "true");
      parts.modal.setAttribute("role", state.mode === "error" ? "alert" : "status");
      parts.modal.dataset.state = state.mode;

      if (parts.actions) parts.actions.hidden = !state.actionsVisible;
      if (parts.continueBtn) parts.continueBtn.textContent = state.continueLabel;

      renderSteps(parts);
    }

    function startSafetyTimeout() {
      window.clearTimeout(state.timeoutId);
      state.timeoutId = window.setTimeout(() => {
        if (state.hidden || !state.visible || state.mode !== "loading") return;
        console.warn("[results-loader] safety timeout reached");
        state.mode = "partial";
        state.title = "Results are taking longer than expected";
        state.detail = "The gallery is still preparing molecular assets.";
        state.current = "You can continue with partial results or refresh and retry.";
        state.progress = Math.max(state.progress, 95);
        state.actionsVisible = true;
        state.continueLabel = "Continue with partial results";
        apply();
      }, RESULTS_LOADER_SAFETY_TIMEOUT_MS);
    }

    function ensureStarted() {
      if (!state.startedAt) state.startedAt = Date.now();
      state.hidden = false;
      startSafetyTimeout();
    }

    function bindActions() {
      const parts = getParts();
      if (parts.continueBtn && parts.continueBtn.dataset.bound !== "1") {
        parts.continueBtn.dataset.bound = "1";
        parts.continueBtn.addEventListener("click", () => api.hide(0, { force: true }));
      }
      if (parts.refreshBtn && parts.refreshBtn.dataset.bound !== "1") {
        parts.refreshBtn.dataset.bound = "1";
        parts.refreshBtn.addEventListener("click", () => window.location.reload());
      }
    }

    const api = {
      show(title, detail, current = "Initializing gallery boot sequence") {
        ensureStarted();
        state.visible = true;
        state.mode = "loading";
        state.title = title || "Loading Warhead Results";
        state.detail = detail || "Preparing results command center";
        state.current = current || "Initializing gallery boot sequence";
        state.progress = Math.max(3, state.progress || 6);
        state.actionsVisible = false;
        state.continueLabel = "Continue to results";
        bindActions();
        apply();
      },
      update(title, detail, options = {}) {
        ensureStarted();
        state.visible = true;
        state.mode = options.mode || "loading";
        if (title) state.title = title;
        if (detail) state.detail = detail;
        if (options.current) state.current = options.current;
        if (Number.isFinite(Number(options.progress))) state.progress = Number(options.progress);
        if (typeof options.actionsVisible === "boolean") state.actionsVisible = options.actionsVisible;
        if (options.continueLabel) state.continueLabel = options.continueLabel;
        bindActions();
        apply();
      },
      hide(delayMs = 250, options = {}) {
        window.clearTimeout(state.timeoutId);
        const delay = Number.isFinite(Number(delayMs)) ? Number(delayMs) : 250;
        window.setTimeout(() => {
          if (!options.force && (state.mode === "error" || state.mode === "partial")) return;
          state.visible = false;
          state.hidden = true;
          state.actionsVisible = false;
          apply();
        }, delay);
      },
      fail(title, detail, options = {}) {
        ensureStarted();
        window.clearTimeout(state.timeoutId);
        state.visible = true;
        state.mode = options.mode || "error";
        state.title = title || "Could not prepare results";
        state.detail = detail || "The gallery did not finish loading.";
        state.current = options.current || "Refresh to retry this job, or continue if partial metadata is already visible.";
        state.progress = Number.isFinite(Number(options.progress)) ? Number(options.progress) : Math.max(state.progress, 100);
        state.actionsVisible = options.actionsVisible !== false;
        state.continueLabel = options.continueLabel || "Continue to results";
        bindActions();
        apply();
      },
      markStep(name, stepState, detail) {
        const labels = {
          boot: "Boot",
          artifacts: "Artifacts",
          selection: "Selection",
          map: "2D Map",
          properties: "Properties",
          viewport: "3D Viewport"
        };
        state.steps[name] = {
          label: labels[name] || name,
          state: stepState || "pending",
          detail: detail || ""
        };
        apply();
      }
    };

    document.addEventListener("DOMContentLoaded", () => {
      bindActions();
      apply();
    });

    return api;
  })();

  window.WarheadResultsLoader = ResultsLoader;
  window.WHResultsLoader = ResultsLoader;

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

  async function loadLigandProps(ligandCode, smiles="", pdbId="", chain="", resid="") {
    const qs = new URLSearchParams();
    if (smiles && String(smiles).trim()) qs.set("smiles", String(smiles).trim());
    if (pdbId && String(pdbId).trim()) qs.set("pdb_id", String(pdbId).trim().toLowerCase());
    if (chain && String(chain).trim()) qs.set("chain", String(chain).trim().toUpperCase());
    if (resid && String(resid).trim()) qs.set("resid", String(resid).trim());

    const url = `/api/ligand_props/${JOB_ID}/${encodeURIComponent(ligandCode)}?${qs.toString()}`;
    const r = await fetchJSON(url);

    if (!r.ok || !r.data || r.data.ok === false || Object.keys(r.data).length === 0) {
      renderProperties({}, ligandCode);
      renderRules({});
      setQEDChip(null);
      setQEDBar(null);
      return {
        ok: false,
        degraded: true,
        message: "Ligand properties were unavailable."
      };
    }

    renderProperties(r.data, ligandCode);
    renderRules(r.data);
    setQEDChip(r.data.QED);
    setQEDBar(r.data.QED);
    return {
      ok: true,
      degraded: false,
      message: "Ligand properties loaded."
    };
  }

  // ============================================================================
  // 5) 2D MAP (includes resid)
  // ============================================================================
  async function load2DMap(pdb, chain, warhead, resid) {
    const node = $("2d-map");
    if (!node) {
      return {
        ok: false,
        degraded: true,
        message: "2D map element is missing."
      };
    }

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
      const requestToken = `${Date.now()}:${Math.random().toString(16).slice(2)}`;
      node.dataset.loadToken = requestToken;

      return await new Promise((resolve) => {
        function settle(ok, message) {
          if (node.dataset.loadToken !== requestToken) return;
          resolve({ ok, degraded: !ok, message, url });
        }

        node.addEventListener("load", () => settle(true, "2D ligand map loaded."), { once: true });
        node.addEventListener("error", () => {
          node.alt = "2D ligand map failed to load";
          settle(false, "2D ligand map failed to load.");
        }, { once: true });
        node.src = url;
      });
    }

    const t = await fetchText(url);
    if (!t.ok || !t.data) {
      node.innerHTML = `<div style="color:#888;padding:8px;">Failed to load 2D map.</div>`;
      return {
        ok: false,
        degraded: true,
        message: "2D ligand map failed to load."
      };
    }
    node.innerHTML = t.data;
    return {
      ok: true,
      degraded: false,
      message: "2D ligand map loaded."
    };
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




  async function headOrGetOk(url) {
    try {
      let r = await fetch(url, { method: "HEAD", cache: "no-store" });

      if (r.status === 405 || r.status === 501) {
        r = await fetch(url, { method: "GET", cache: "no-store" });
      }

      return r.ok;
    } catch {
      return false;
    }
  }

  function markCardArtifactMissing(card, message) {
    card.classList.remove("pending");
    card.classList.add("sdf-missing");
    card.dataset.renderable = "false";
    if (!card.querySelector(".artifact-warning")) {
      const warning = document.createElement("div");
      warning.className = "artifact-warning";
      warning.textContent = message;
      card.appendChild(warning);
    }
  }

  async function filterRenderableCards() {
    const cards = Array.from(document.querySelectorAll(".result-card"));
    const renderable = [];
    const total = cards.length || 1;

    ResultsLoader.markStep("artifacts", "loading", `Checking ${cards.length} ligand result${cards.length === 1 ? "" : "s"}`);
    ResultsLoader.show(
      "Loading warhead results",
      `Checking ${cards.length} ligand result${cards.length === 1 ? "" : "s"}`,
      "Validating SDF, PDB, and SASA assets"
    );
    ResultsLoader.update("Loading warhead results", "Starting artifact validation", {
      progress: 6,
      current: "Preparing result cards"
    });

    for (let i = 0; i < cards.length; i += 1) {
      const card = cards[i];
      const pdb = String(card.dataset.pdb || "").trim().toLowerCase();
      const chain = String(card.dataset.chain || "").trim().toUpperCase();
      const warhead = String(card.dataset.warhead || "").trim().toUpperCase();
      let resid = String(card.dataset.resid || "").trim();

      const cardLabel = `${warhead || "ligand"} / ${pdb || "pdb"} chain ${chain || "?"}`;
      const baseProgress = 8 + ((i / total) * 74);
      ResultsLoader.update("Loading warhead results", `Checking result ${i + 1} of ${cards.length}`, {
        progress: baseProgress,
        current: cardLabel
      });

      if (!pdb || !chain || !warhead) {
        markCardArtifactMissing(card, "SDF missing — pipeline artifact incomplete");
        continue;
      }

      ResultsLoader.update("Loading warhead results", `Resolving ligand residue ${i + 1} of ${cards.length}`, {
        progress: baseProgress + 2,
        current: cardLabel
      });
      resid = await resolveResid(pdb, chain, warhead, resid);
      if (resid) card.dataset.resid = resid;

      const proteinUrl =
        `/api/protein/${encodeURIComponent(JOB_ID)}/${encodeURIComponent(pdb)}/${encodeURIComponent(chain)}`;

      const sdfQs = new URLSearchParams();
      if (resid) sdfQs.set("resid", resid);
      const sdfQuery = sdfQs.toString() ? `?${sdfQs.toString()}` : "";
      const sdfUrl =
        `/api/sdf/${encodeURIComponent(JOB_ID)}/${encodeURIComponent(pdb)}/${encodeURIComponent(chain)}/${encodeURIComponent(warhead)}${sdfQuery}`;

      ResultsLoader.update("Loading warhead results", `Checking protein artifact ${i + 1} of ${cards.length}`, {
        progress: baseProgress + 4,
        current: cardLabel
      });
      const okProtein = await headOrGetOk(proteinUrl);

      ResultsLoader.update("Loading warhead results", `Checking SDF artifact ${i + 1} of ${cards.length}`, {
        progress: baseProgress + 8,
        current: cardLabel
      });
      const okSdf = await headOrGetOk(sdfUrl);

      if (!okSdf) {
        console.warn("SDF missing for result card:", { job: JOB_ID, pdb, chain, warhead, resid, url: sdfUrl });
        markCardArtifactMissing(card, "SDF missing — pipeline artifact incomplete");
        continue;
      }

      if (!okProtein) {
        console.warn("Protein artifact missing for result card:", { job: JOB_ID, pdb, chain, warhead, url: proteinUrl });
        markCardArtifactMissing(card, "Protein artifact missing — pipeline artifact incomplete");
        continue;
      }

      if (resid) {
        const sasaUrl =
          `/api/jobs/${encodeURIComponent(JOB_ID)}/sasa/atoms?` +
          `pdb_id=${encodeURIComponent(pdb)}` +
          `&chain=${encodeURIComponent(chain)}` +
          `&residue_id=${encodeURIComponent(resid)}`;

        ResultsLoader.update("Loading warhead results", `Checking SASA atoms ${i + 1} of ${cards.length}`, {
          progress: baseProgress + 11,
          current: cardLabel
        });
        const okSasa = await headOrGetOk(sasaUrl);

        if (!okSasa) {
          console.warn("SASA atoms unavailable for result card; keeping card without deleting it:", {
            job: JOB_ID, pdb, chain, warhead, resid, url: sasaUrl
          });
        }
      }

      card.classList.remove("pending");
      card.dataset.renderable = "true";
      renderable.push(card);

      ResultsLoader.update(
        "Loading warhead results",
        `${renderable.length} renderable ligand${renderable.length === 1 ? "" : "s"} found`,
        {
          progress: Math.min(88, baseProgress + 13),
          current: cardLabel
        }
      );
    }

    if (!renderable.length) {
      console.warn("No result cards have required SDF/protein artifacts. SDF and protein PDB are required display contracts.");
      ResultsLoader.markStep("artifacts", "error", "No renderable ligands found");
    } else {
      ResultsLoader.markStep("artifacts", "success", `${renderable.length} renderable ligand${renderable.length === 1 ? "" : "s"} ready`);
    }

    ResultsLoader.update("Loading warhead results", "Artifact validation complete", {
      progress: 90,
      current: `${renderable.length} renderable ligand${renderable.length === 1 ? "" : "s"} ready`
    });
    return renderable;
  }

  function bindCards() {
    document.querySelectorAll(".result-card").forEach(card => {
      card.addEventListener("click", () => {
        if (card.dataset.renderable === "false") return;

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
      copyBtn.addEventListener("click", async () => {
        const s = $("smiles-box")?.innerText?.trim();
        if (!s) return;

        let copied = false;

        // Modern clipboard API: works on HTTPS or localhost
        if (navigator.clipboard && window.isSecureContext) {
          try {
            await navigator.clipboard.writeText(s);
            copied = true;
          } catch (err) {
            console.warn("navigator.clipboard failed, using fallback:", err);
          }
        }

        // Fallback for HTTP / older browsers / mobile weirdness
        if (!copied) {
          const ta = document.createElement("textarea");
          ta.value = s;
          ta.setAttribute("readonly", "");
          ta.style.position = "fixed";
          ta.style.left = "-9999px";
          ta.style.top = "-9999px";
          document.body.appendChild(ta);
          ta.focus();
          ta.select();

          try {
            copied = document.execCommand("copy");
          } catch (err) {
            console.warn("Fallback copy failed:", err);
          }

          document.body.removeChild(ta);
        }

        copyBtn.textContent = copied ? "COPIED" : "COPY FAILED";
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
    const buttons = [
      $("protac-builder"),
      $("use-as-protac-btn")
    ].filter(Boolean);
    if (!buttons.length) return;

    buttons.forEach((btn) => {
      if (btn.dataset.protacBuilderBound === "1") return;
      btn.dataset.protacBuilderBound = "1";

      btn.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
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
      try {
        await logBuilderClick(smiles, CFG.job_id);
      } catch (err) {
        console.warn("Builder click logging failed:", err);
      }
      openProtacBuilderWithSmiles(smiles);
    });
    });
  }


// ============================================================================
// 🧬 APPEND SMILES TO ALREADY-OPEN PROTAC BUILDER SESSION
// Broadcasts first. Opens a new builder only if no existing builder responds.
// ============================================================================
function openProtacBuilderWithSmiles(smiles, options = {}) {
    const cleanSmiles = String(smiles || "").trim();

    if (!cleanSmiles) {
        console.error("❌ No SMILES provided to PROTAC Builder handoff.");
        alert("No SMILES found to send to PROTAC Builder.");
        return;
    }

    const builderOrigin = options.origin || getProtacBuilderOrigin();
 
    // Optional explicit session:
    // openProtacBuilderWithSmiles(smiles, { session: "b837..." })
    const explicitSession = String(options.session || "").trim();



    // Read whichever builder session most recently registered itself.
    const activeSession = localStorage.getItem("protacBuilder.activeSession") || "";
    const activeHref = localStorage.getItem("protacBuilder.activeHref") || "";
    const activeClientId = localStorage.getItem("protacBuilder.activeClientId") || "";

    const targetSession = explicitSession || activeSession || "";

    console.log("🧬 PROTAC Builder append request:", {
        smiles: cleanSmiles,
        explicitSession,
        activeSession,
        activeClientId,
        activeHref
    });

    appendSmilesToOpenBuilderSession(cleanSmiles, {
        targetSession,
        targetClientId: activeClientId,
        fallbackOrigin: builderOrigin,
        fallbackHref: activeHref
    });
}

function appendSmilesToOpenBuilderSession(smiles, opts = {}) {
    const cleanSmiles = String(smiles || "").trim();
    const targetSession = String(opts.targetSession || "").trim();
    const targetClientId = String(opts.targetClientId || "").trim();
    const fallbackOrigin = opts.fallbackOrigin || getProtacBuilderOrigin();
    const fallbackHref = String(opts.fallbackHref || "").trim();

    const CHANNEL_NAME = "protac_builder_session_bus";
    const requestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());

    let channel;

    try {
        channel = new BroadcastChannel(CHANNEL_NAME);
    } catch (err) {
        console.warn("⚠ BroadcastChannel unavailable; opening fallback builder:", err);
        openBuilderFallback(cleanSmiles, {
            origin: fallbackOrigin,
            session: targetSession,
            href: fallbackHref
        });
        return;
    }

    let acknowledged = false;

    const timeoutMs = 900;

    const timer = setTimeout(() => {
        if (acknowledged) return;

        console.warn("⚠ No open PROTAC Builder session ACK. Opening fallback builder URL.");

        try {
            channel.close();
        } catch {}

        openBuilderFallback(cleanSmiles, {
            origin: fallbackOrigin,
            session: targetSession,
            href: fallbackHref
        });
    }, timeoutMs);

    channel.addEventListener("message", (event) => {
        const msg = event.data || {};

        if (
            msg.type === "PROTAC_APPEND_WARHEAD_ACK" &&
            msg.requestId === requestId
        ) {
            acknowledged = true;
            clearTimeout(timer);

            console.log("✅ SMILES appended directly to open PROTAC Builder session:", msg);

            try {
                channel.close();
            } catch {}
        }
    });

    const payload = {
        type: "PROTAC_APPEND_WARHEAD_SMILES",
        requestId,
        smiles: cleanSmiles,
        session: targetSession || "",
        clientId: targetClientId || "",
        ts: Date.now()
    };

    console.log("📡 Broadcasting SMILES to already-open PROTAC Builder:", payload);

    channel.postMessage(payload);
}

function openBuilderFallback(smiles, opts = {}) {
    const cleanSmiles = String(smiles || "").trim();
    const origin = opts.origin || getProtacBuilderOrigin();
    const session = String(opts.session || "").trim();
    const fallbackHref = String(opts.href || "").trim();

    const encoded = encodeURIComponent(cleanSmiles);

    let builderUrl;

    // Prefer exact active session URL if we had one.
    if (fallbackHref) {
        try {
            const u = new URL(fallbackHref);
            if (u.origin !== getProtacBuilderOrigin()) {
                throw new Error("Ignoring stale builder session from a different origin.");
            }

            u.searchParams.set("lig_smi", cleanSmiles);
            u.searchParams.set("smiles", cleanSmiles);
            u.hash = `lig_smi=${encoded}&smiles=${encoded}`;

            builderUrl = u.toString();
        } catch {
            builderUrl = "";
        }
    }

    // Otherwise construct by session.
    if (!builderUrl && session) {
        builderUrl =
            `${origin}/copy/COPYindex/build` +
            `?session=${encodeURIComponent(session)}` +
            `&lig_smi=${encoded}` +
            `&smiles=${encoded}` +
            `#lig_smi=${encoded}&smiles=${encoded}`;
    }

    // Final fallback.
    if (!builderUrl) {
        builderUrl =
            `${getProtacBuilderBase()}` +
            `?lig_smi=${encoded}` +
            `&smiles=${encoded}` +
            `#lig_smi=${encoded}&smiles=${encoded}`;
    }

    console.log("🪟 Opening fallback PROTAC Builder:", builderUrl);
    try {
        localStorage.setItem("protacBuilder.lastOpenedUrl", builderUrl);
    } catch {}

    // Named window prevents infinite tab spam on fallback.
    // If browser can reuse it, it will.
    window.open(builderUrl, "PROTAC_BUILDER_LIVE");
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

    const url =
      `/api/jobs/${encodeURIComponent(JOB_ID)}/sasa/residue_for_ligand?` +
      `pdb_id=${encodeURIComponent(String(pdb).toLowerCase())}` +
      `&chain=${encodeURIComponent(String(chain).toUpperCase())}` +
      `&ligand=${encodeURIComponent(String(warhead).toUpperCase())}`;

    const r = await fetchJSON(url);
    if (r.ok && r.data && r.data.residue_id) return String(r.data.residue_id).trim();
    return raw;
  }

  function nextFrame() {
    return new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
  }

  async function waitForInitialViewportPaint(maxMs = 1200) {
    const viewport = $("viewport");
    if (!viewport) return false;

    await nextFrame();
    await nextFrame();

    if (viewport.querySelector("canvas")) return true;

    const startedAt = Date.now();
    while ((Date.now() - startedAt) < maxMs) {
      await nextFrame();
      if (viewport.querySelector("canvas")) return true;
    }

    return Boolean(viewport.querySelector("canvas"));
  }

  // ============================================================================
  // 7) syncView (UI + calls Render3D)
  // ============================================================================
  window.syncView = async function syncView(pdb, chainFromResults, warhead, resid, smiles, exposedValue, options = {}) {
    const PDB = String(pdb || "").toLowerCase().trim();
    const WAR = String(warhead || "").toUpperCase().trim();
    const initialBoot = Boolean(options && options.initialBoot);
    const loader = window.WarheadResultsLoader;

    const chain = await bestChain(PDB, chainFromResults, WAR);
    if (initialBoot) {
      loader?.markStep("selection", "loading", `${WAR || "ligand"} / ${PDB || "pdb"} chain ${chain || "?"}`);
      loader?.update("Loading Warhead Results", "Preparing first ligand", {
        progress: 92,
        current: `${WAR || "ligand"} / ${PDB || "pdb"} chain ${chain || "?"}`
      });
    }

    let RESID = (resid == null) ? "" : String(resid).trim();
    RESID = await resolveResid(PDB, chain, WAR, RESID);

    State.current = { pdb: PDB, chain, warhead: WAR, resid: RESID };
    State.last2D  = { pdb: PDB, chain, warhead: WAR, resid: RESID };

    renderSmiles(smiles || "");
    setHUD(PDB, chain, WAR, exposedValue, "Loading…");

    if (initialBoot) {
      loader?.markStep("selection", "success", `${WAR || "ligand"} selected`);
      loader?.markStep("map", "loading", "Loading 2D ligand map");
      loader?.markStep("properties", "loading", "Loading molecular properties");
      loader?.markStep("viewport", "loading", "Rendering 3D viewport");
    }

    const propsPromise = loadLigandProps(WAR, smiles || "", PDB, chain, RESID)
      .then((result) => {
        if (initialBoot) {
          loader?.markStep("properties", result.ok ? "success" : "warn", result.message);
        }
        return result;
      })
      .catch((err) => {
        const msg = err && err.message ? err.message : String(err || "Ligand properties failed.");
        if (initialBoot) loader?.markStep("properties", "warn", msg);
        return { ok: false, degraded: true, message: msg };
      });

    const mapPromise = load2DMap(PDB, chain, WAR, RESID)
      .then((result) => {
        if (initialBoot) {
          loader?.markStep("map", result.ok ? "success" : "warn", result.message);
        }
        return result;
      })
      .catch((err) => {
        const msg = err && err.message ? err.message : String(err || "2D ligand map failed.");
        if (initialBoot) loader?.markStep("map", "warn", msg);
        return { ok: false, degraded: true, message: msg };
      });

    const renderPromise = (window.Render3D && typeof window.Render3D.load === "function")
      ? window.Render3D.load({ pdb: PDB, chain, warhead: WAR, resid: RESID })
          .then(async (result) => {
            const canvasPresent = await waitForInitialViewportPaint();
            const merged = Object.assign({}, result || {}, {
              canvasPresent: Boolean(result && result.canvasPresent) || canvasPresent
            });
            if (initialBoot) {
              const stepState = merged.usable ? "success" : (merged.canvasPresent || merged.protein?.ok ? "warn" : "error");
              const detail = merged.usable
                ? "3D viewport rendered."
                : merged.canvasPresent
                  ? "3D viewport loaded with degraded assets."
                  : "3D viewport did not finish rendering.";
              loader?.markStep("viewport", stepState, detail);
            }
            return merged;
          })
          .catch((err) => {
            const msg = err && err.message ? err.message : String(err || "Unknown 3D render error");
            if (initialBoot) loader?.markStep("viewport", "error", msg);
            return {
              ok: false,
              usable: false,
              canvasPresent: false,
              protein: { ok: false, message: "" },
              ligand: { ok: false, message: msg },
              sasa: { ok: false, skipped: false, message: "" }
            };
          })
      : Promise.resolve({
          ok: false,
          usable: false,
          canvasPresent: false,
          protein: { ok: false, message: "" },
          ligand: { ok: false, message: "3D renderer not loaded." },
          sasa: { ok: false, skipped: false, message: "" }
        });

    if (!window.Render3D || typeof window.Render3D.load !== "function") {
      setHUD(PDB, chain, WAR, exposedValue, "3D renderer not loaded.");
      console.warn("Render3D not available. Check script order in HTML.");
      if (initialBoot) loader?.markStep("viewport", "error", "3D renderer not loaded.");
    }

    const [propsResult, mapResult, renderResult] = await Promise.all([
      propsPromise,
      mapPromise,
      renderPromise
    ]);

    const issues = [];
    if (!propsResult.ok) issues.push("properties unavailable");
    if (!mapResult.ok) issues.push("2D map unavailable");
    if (!renderResult.usable) issues.push("3D viewport unavailable");

    if (!issues.length) {
      setHUD(PDB, chain, WAR, exposedValue, "Loaded");
    } else {
      setHUD(PDB, chain, WAR, exposedValue, `Loaded with issues: ${issues.join(", ")}`);
    }

    return {
      ok: !issues.length,
      usable: mapResult.ok || propsResult.ok || renderResult.usable,
      props: propsResult,
      map: mapResult,
      render3d: renderResult,
      issues
    };
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
  async function initializeResultsGallery() {
    ResultsLoader.show(
      "Loading Warhead Results",
      "Preparing controls and molecular viewer",
      "Binding interface actions"
    );
    ResultsLoader.markStep("boot", "loading", "Binding UI controls");

    bindMapToggle();
    bindSmilesActions();
    bindDownloadPDB();
    bindProtacBuilder();
    bindCards();

    ResultsLoader.markStep("boot", "success", "Interface actions bound");

    const validCards = await filterRenderableCards();
    const first = validCards[0];

    if (!first) {
      setHUD("—", "—", "—", null, "No renderable ligands found for this job.");
      const viewport = $("viewport");
      if (viewport) {
        viewport.innerHTML = `
          <div style="padding:24px;color:#ffd600;font-family:monospace;">
            No complete ligand/protein artifact pair was available for this job.
          </div>
        `;
      }
      ResultsLoader.fail(
        "No renderable ligands found",
        "Required SDF or protein artifacts were not available for this job.",
        {
          current: "Refresh if the job is still writing artifacts, or inspect the missing-card warnings.",
          progress: 100,
          actionsVisible: true,
          continueLabel: "Continue to empty results"
        }
      );
      return;
    }

    const pdb = first.dataset.pdb;
    const chain = first.dataset.chain;
    const warhead = first.dataset.warhead;
    const smiles = first.dataset.smiles || "";
    const resid = (first.dataset.resid || "").trim();
    const exposedValue = Number(first.dataset.exposed || 0);

    ResultsLoader.markStep("selection", "loading", `${warhead || "ligand"} / ${pdb || "pdb"} chain ${chain || "?"}`);
    ResultsLoader.update("Loading Warhead Results", "Preparing first ligand", {
      progress: 94,
      current: `${warhead || "ligand"} / ${pdb || "pdb"} chain ${chain || "?"}`
    });

    const readiness = await window.syncView(
      pdb,
      chain,
      warhead,
      resid,
      smiles,
      exposedValue,
      { initialBoot: true }
    );

    if (readiness.ok) {
      ResultsLoader.update("Loading Warhead Results", "Results ready", {
        progress: 100,
        current: "Warhead command center online"
      });
      ResultsLoader.hide(350);
      return;
    }

    if (readiness.usable) {
      const summary = readiness.issues.join(", ");
      ResultsLoader.fail(
        "Results loaded with degraded assets",
        "The first ligand is available, but one or more panels did not finish cleanly.",
        {
          mode: "partial",
          current: summary ? `Degraded components: ${summary}.` : "You can continue with partial results.",
          progress: 100,
          actionsVisible: true,
          continueLabel: "Continue with degraded results"
        }
      );
      return;
    }

    ResultsLoader.fail(
      "Could not prepare the first ligand",
      readiness.issues.length
        ? `Initial readiness failed: ${readiness.issues.join(", ")}.`
        : "The gallery did not finish loading.",
      {
        current: "Refresh to retry this job. If this persists, inspect browser diagnostics for missing assets.",
        progress: 100,
        actionsVisible: true
      }
    );
  }

  document.addEventListener("DOMContentLoaded", async () => {
    try {
      await initializeResultsGallery();
    } catch (err) {
      const msg = err && err.message ? err.message : String(err || "Unknown loading error");
      console.error("[results-loader] initial gallery boot failed", err);
      setHUD("—", "—", "—", null, `Result page load failed: ${msg}`);
      ResultsLoader.markStep("boot", "error", msg);
      ResultsLoader.fail("Could not prepare results", msg, {
        current: "Refresh to retry this job.",
        progress: 100,
        actionsVisible: true
      });
    }
  });
})();
