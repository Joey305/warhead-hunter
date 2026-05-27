(() => {
  // ============================================================================
  // 3Drender.js — NGL viewer + protein/ligand render + SASA "GLOW ORB" halos
  // ----------------------------------------------------------------------------
  // Fixes included (copy/paste ready):
  //   ✅ TRON ligand coloring via a REAL element colormaker (not selection rules).
  //   ✅ SASA halos are "glow orbs" = OUTER soft sphere + INNER bright core.
  //   ✅ Only atoms >= MIN_SHOW get halos, but hover works for ALL atoms (even 0.0).
  //   ✅ Hover HUD updates in real time, showing Atom (atom_name) + Exposed SA.
  //   ✅ Hover clears reliably when leaving atoms.
  //   ✅ Protein won’t occlude ligand/halos (depthWrite off + renderOrder control).
  //   ✅ Halos/markers render “on top” (depthTest=false + high renderOrder).
  //
  // Public API:
  //   window.Render3D.init()
  //   window.Render3D.load({ pdb, chain, warhead, resid })
  //   window.Render3D.clear()
  //   window.Render3D.debugLigandElements()  // optional
  // ============================================================================

  const CFG = window.PROTACABLE_CONFIG || {};
  const JOB_ID = CFG.job_id;
  
  

  // ============================================================================
  // 1) TRON element colors (HEX is safest)
  // ============================================================================
  const TRON_HEX = {
    C:   "#14F0F7", // cyan
    N:   "#390FF6", // electric blue
    O:   "#FF5252", // red
    S:   "#FFD600", // yellow
    P:   "#FF80AB", // pink
    HAL: "#69F0AE", // halogens green
    X:   "#FFFFFF"  // fallback
  };

  
  // ============================================================================
  // 2) SASA band rules (inclusive)
  // ============================================================================
  const SASA_RULES = {
    MIN_SHOW: 15,
    GREEN_MIN: 15, GREEN_MAX: 23.99999,
    YELLOW_MIN: 24, YELLOW_MAX: 33.999,
    RED_MIN: 34
  };

  // ============================================================================
  // 3) Visual tuning
  // ============================================================================
  // Protein
  const PROTEIN_COLOR = "#624E6B";
  const LYS_SURFACE_COLOR = "#0A1E8A";   // deep dark blue
  const LYS_CARTOON_COLOR = "#102A9E";   // optional, slightly brighter for ribbon
  const PROTEIN_SURFACE_OPACITY = 0.22;
  const PROTEIN_CARTOON_OPACITY = 0.35;

  // Ligand
  const LIGAND_BS_SCALE = 3.9;     // larger ball+stick
  const LIGAND_LINEWIDTH = 2;
  const LIGAND_LINE_OPACITY = 0.22;

  // SASA glow orbs (two-layer)  ✅ UPDATED: larger + more translucent
  // Bigger + actually visible head-on
  const SASA_OUTER_RADIUS  = 1.1;   // big soft glow
  const SASA_OUTER_OPACITY = 0.28;  // IMPORTANT: too low = only visible at angles

  const SASA_INNER_RADIUS  = 0.9;   // bright core
  const SASA_INNER_OPACITY = 0.85;  // keep strong so it reads at all angles

  // Hover / Pick markers
  const HOVER_RADIUS  = 0.70;
  const HOVER_OPACITY = 0.85;

  const PICK_RADIUS  = 0.95; 
  const PICK_OPACITY = 0.92;

  // Picking tolerance (Å)
  const HOVER_PICK_DIST = 1.6;
  const CLICK_PICK_DIST = 2.0;

  const CLEAR_HOVER_ON_EMPTY = true;

  // Render order (higher draws later)
  const ORDER_PROTEIN  = 0;
  const ORDER_LIGAND   = 500;
  const ORDER_HALOS    = 900;
  const ORDER_MARKERS  = 999;

  // ============================================================================
  // 4) State
  // ============================================================================
  const State = {
    stage: null,
    tronScheme: null,
    proteinScheme: null,

    proteinComp: null,
    ligandComp: null,

    sasaOuterComp: null,
    sasaInnerComp: null,

    hoverComp: null,
    pickComp: null,

    // all atoms (for hover), includes 0.0 exposure:
    // {x,y,z, exposure_a2, atom_id, atom_name, atom_symbol}
    allAtoms: [],

    // atoms that pass threshold:
    // {x,y,z, exposure_a2, atom_id, atom_name, atom_symbol, band, color}
    sasaAtoms: [],

    current: { pdb: null, chain: null, warhead: null, resid: null },

    // used to debounce hover updates
    lastHoverSig: null
  };

  // ============================================================================
  // 5) DOM helpers
  // ============================================================================
  const $ = (id) => document.getElementById(id);

  function setViewportLoading(on, msg = "Loading…") {
    const el = $("viewport-loading");
    if (!el) return;
    el.style.display = on ? "flex" : "none";
    const label = el.querySelector("span");
    if (label) label.textContent = msg;
  }

  function setHudDebug(msg) {
    const node = $("hud-debug");
    if (node) node.innerText = msg || "";
  }

  // ============================================================================
  // 6) Fetch helper
  // ============================================================================
  async function fetchJSONStrict(url) {
    const r = await fetch(url, { headers: { "Accept": "application/json" } });
    const text = await r.text();
    let j = null;
    try { j = text ? JSON.parse(text) : null; } catch {}
    if (!r.ok) {
      const msg = (j && j.error) ? j.error : `Request failed (${r.status})`;
      throw new Error(msg);
    }
    return j;
  }

  // ============================================================================
  // 7) SASA band color helper
  // ============================================================================
  function bandColor(expA2) {
    const exp = Number(expA2);
    if (!Number.isFinite(exp)) return null;

    if (exp >= SASA_RULES.RED_MIN) {
      return { band: "red", color: new window.NGL.Color("#D50000") };
    }
    if (exp >= SASA_RULES.YELLOW_MIN && exp <= SASA_RULES.YELLOW_MAX) {
      return { band: "yellow", color: new window.NGL.Color("#FFD600") };
    }
    if (exp >= SASA_RULES.GREEN_MIN && exp <= SASA_RULES.GREEN_MAX) {
      return { band: "green", color: new window.NGL.Color("#00C853") };
    }
    return null;
  }

  // ============================================================================
  // 8) Render order helper
  // ============================================================================
  function bumpRenderOrder(comp, order) {
    try {
      if (!comp || !comp.reprList) return;
      comp.reprList.forEach(r => {
        if (r && r.repr && r.repr.renderOrder != null) r.repr.renderOrder = order;
      });
    } catch {}
  }



  
  function noHydrogenSele(sele) {
    return `(${sele}) and not hydrogen`;   // or: `and not _H`
  }


  // ============================================================================
  // 9) NGL init + TRON colormaker
  // ============================================================================
  function initNGL() {
    if (State.stage) return true;
    if (!window.NGL) return false;

    // ✅ Disable NGL tooltip at the source (works in modern NGL)
    State.stage = new window.NGL.Stage("viewport", {
      backgroundColor: "black",
      tooltip: false
    });

    // ✅ Robust TRON colormaker reading atom.element / atom.atomicNumber
    State.tronScheme = window.NGL.ColormakerRegistry.addScheme(function TronColormaker() {
      const toHex = (hex) => new window.NGL.Color(hex).getHex();

      const C  = toHex(TRON_HEX.C);
      const N  = toHex(TRON_HEX.N);
      const O  = toHex(TRON_HEX.O);
      const S  = toHex(TRON_HEX.S);
      const P  = toHex(TRON_HEX.P);
      const H  = toHex(TRON_HEX.HAL);
      const X  = toHex(TRON_HEX.X);

      this.atomColor = function (atom) {
        const e = (atom.element || "").toUpperCase();

        if (e === "C") return C;
        if (e === "N") return N;
        if (e === "O") return O;
        if (e === "S") return S;
        if (e === "P") return P;
        if (e === "F" || e === "CL" || e === "BR" || e === "I") return H;

        const z = atom.atomicNumber || atom.number || 0;
        if (z === 6)  return C;
        if (z === 7)  return N;
        if (z === 8)  return O;
        if (z === 16) return S;
        if (z === 15) return P;
        if (z === 9 || z === 17 || z === 35 || z === 53) return H;

        return X;
      };
    });

    State.proteinScheme = window.NGL.ColormakerRegistry.addScheme(function ProteinLysColormaker() {
      const baseHex = new window.NGL.Color(PROTEIN_COLOR).getHex();
      const lysHex  = new window.NGL.Color(LYS_SURFACE_COLOR).getHex();
    
      this.atomColor = function (atom) {
        const res = String(atom.resname || "").toUpperCase();
        return (res === "LYS") ? lysHex : baseHex;
      };
    });

    // ----------------------------
    // Hover: updates HUD in real time
    // ----------------------------
    State.stage.signals.hovered.add((pickingProxy) => {
      try {
        if (!State.allAtoms.length) {
          if (CLEAR_HOVER_ON_EMPTY) clearHover();
          return;
        }
        if (!pickingProxy || !pickingProxy.position) {
          if (CLEAR_HOVER_ON_EMPTY) clearHover();
          return;
        }

        const pos = pickingProxy.position;
        const pt = findNearest(State.allAtoms, pos.x, pos.y, pos.z, HOVER_PICK_DIST);

        if (!pt) {
          if (CLEAR_HOVER_ON_EMPTY) clearHover();
          return;
        }

        showHover(pt);
      } catch {
        // ignore hover errors
      }
    });

    State.stage.signals.clicked.add((pickingProxy) => {
      try {
        if (!State.allAtoms.length) return;
        if (!pickingProxy || !pickingProxy.position) return;

        const pos = pickingProxy.position;
        const pt = findNearest(State.allAtoms, pos.x, pos.y, pos.z, CLICK_PICK_DIST);
        if (!pt) return;

        showPick(pt);
      } catch {}
    });

    return true;
  }

  // ============================================================================
  // 10) Clear helpers
  // ============================================================================
  function clearHover() {
    if (State.stage && State.hoverComp) {
      try { State.stage.removeComponent(State.hoverComp); } catch {}
    }
    State.hoverComp = null;
    State.lastHoverSig = null;
    setHudDebug("");
  }

  function clearPick() {
    if (State.stage && State.pickComp) {
      try { State.stage.removeComponent(State.pickComp); } catch {}
    }
    State.pickComp = null;
  }

  function clearSasaOverlay() {
    if (State.stage && State.sasaOuterComp) {
      try { State.stage.removeComponent(State.sasaOuterComp); } catch {}
    }
    if (State.stage && State.sasaInnerComp) {
      try { State.stage.removeComponent(State.sasaInnerComp); } catch {}
    }
    State.sasaOuterComp = null;
    State.sasaInnerComp = null;

    clearHover();
    clearPick();

    State.allAtoms = [];
    State.sasaAtoms = [];
  }

  function clearAllComponents() {
    if (!State.stage) return;
    State.stage.removeAllComponents();

    State.proteinComp = null;
    State.ligandComp = null;
    State.sasaOuterComp = null;
    State.sasaInnerComp = null;

    clearHover();
    clearPick();

    State.allAtoms = [];
    State.sasaAtoms = [];
  }

  /// ============================================================================
  // 11A) Protein loading
  // ============================================================================
  function proteinSele(chain) {
    return `polymer and chain ${chain} and not hetero`;
  }

  async function loadProtein(pdb, chain) {
    const url = `/api/protein/${JOB_ID}/${pdb}/${chain}`;
    const comp = await State.stage.loadFile(url, { ext: "pdb", defaultRepresentation: false });

    // One continuous protein surface
    comp.addRepresentation("surface", {
      sele: proteinSele(chain),
      color: State.proteinScheme,
      opacity: PROTEIN_SURFACE_OPACITY,
      depthWrite: false,
      depthTest: true,
      side: "double",
      surfaceType: "av",
      probeRadius: 1.4,
      scaleFactor: 0.9
    });

    // Optional cartoon using the same lys-aware coloring
    comp.addRepresentation("cartoon", {
      sele: proteinSele(chain),
      color: State.proteinScheme,
      opacity: PROTEIN_CARTOON_OPACITY,
      depthWrite: false,
      depthTest: true
    });

    // IMPORTANT:
    // Do NOT add a protein-level spacefill representation here.
    // That is what was making the whole protein look like spheres.

    bumpRenderOrder(comp, ORDER_PROTEIN);
    return comp;
  }


  // ============================================================================
  // 11B) Ligand loading
  // ============================================================================
  async function loadLigand(pdb, chain, warhead) {
    const url = `/api/sdf/${JOB_ID}/${pdb}/${chain}/${warhead}`;
    const comp = await State.stage.loadFile(url, { ext: "sdf", defaultRepresentation: false });

    const LIG_NO_H = "not hydrogen";

    comp.addRepresentation("ball+stick", {
      sele: LIG_NO_H,
      scale: LIGAND_BS_SCALE,
      multipleBond: "symmetric",
      colorScheme: State.tronScheme,
      depthWrite: true,
      depthTest: true
    });

    comp.addRepresentation("line", {
      sele: LIG_NO_H,
      linewidth: LIGAND_LINEWIDTH,
      color: "white",
      opacity: LIGAND_LINE_OPACITY,
      depthWrite: false,
      depthTest: true
    });

    bumpRenderOrder(comp, ORDER_LIGAND);
    comp.autoView(800);
    return comp;
  }

  function debugProteinResnames() {
    if (!State.proteinComp || !State.proteinComp.structure) {
      console.log("No protein loaded");
      return;
    }
  
    const counts = {};
    State.proteinComp.structure.eachResidue(r => {
      const name = (r.resname || "").toUpperCase();
      counts[name] = (counts[name] || 0) + 1;
    });
  
    console.log("Protein residue counts:", counts);
  }

  // ============================================================================
  // 12) SASA atoms fetch + build glow-orb halos
  // ============================================================================
  async function fetchSasaAtoms(pdb, chain, resid) {
    const url =
      `/api/jobs/${encodeURIComponent(JOB_ID)}/sasa/atoms?` +
      `pdb_id=${encodeURIComponent(String(pdb).toLowerCase())}` +
      `&chain=${encodeURIComponent(String(chain).toUpperCase())}` +
      `&residue_id=${encodeURIComponent(String(resid))}`;

    const j = await fetchJSONStrict(url);
    if (!j || j.ok !== true || !Array.isArray(j.atoms)) {
      throw new Error(j?.error || "SASA response missing atoms[]");
    }
    return j;
  }


  function normalizeAtomRow(a) {
    const x = Number(a.x);
    const y = Number(a.y);
    const z = Number(a.z);

    const expRaw = (a.Exposure_A2 ?? a.exposure_a2 ?? a.exposure ?? 0);
    const exposure = Number(expRaw);

    const atom_id = (a.atom_id != null) ? Number(a.atom_id) : null;

    const atom_name = (a.atom_name ?? a.AtomName ?? a.atomName ?? "").toString().trim();
    const atom_symbol_raw = (a.AtomSymbol ?? a.atom_symbol ?? a.element ?? a.Atom ?? "").toString().trim();

    // --- HYDROGEN FILTER (NEW) -----------------------------------
    // Prefer atom_symbol/element; fallback to atom_name heuristics.
    const sym = (atom_symbol_raw || "").toUpperCase();

    // If API gives proper element:
    if (sym === "H") return null;

    // If API sometimes encodes as atom_name like "H1", "H12", "1H", "HA", etc:
    // (this is intentionally conservative)
    const nm = (atom_name || "").toUpperCase();
    if (!sym && nm && /^H[A-Z0-9]*$/.test(nm)) return null;
    // -------------------------------------------------------------

    if (![x, y, z, exposure].every(Number.isFinite)) return null;

    return {
      x, y, z,
      exposure_a2: exposure,
      atom_id,
      atom_name,
      atom_symbol: atom_symbol_raw
    };
  }





  // function normalizeAtomRow(a) {
  //   const x = Number(a.x);
  //   const y = Number(a.y);
  //   const z = Number(a.z);

  //   const expRaw = (a.Exposure_A2 ?? a.exposure_a2 ?? a.exposure ?? 0);
  //   const exposure = Number(expRaw);

  //   const atom_id = (a.atom_id != null) ? Number(a.atom_id) : null;

  //   // prefer atom_name (what you asked for)
  //   const atom_name = (a.atom_name ?? a.AtomName ?? a.atomName ?? "").toString().trim();

  //   // fallback to symbol/element if atom_name missing
  //   const atom_symbol = (a.AtomSymbol ?? a.atom_symbol ?? a.element ?? a.Atom ?? "").toString().trim();

  //   if (![x, y, z, exposure].every(Number.isFinite)) return null;

  //   return { x, y, z, exposure_a2: exposure, atom_id, atom_name, atom_symbol };
  // }

  async function applySasaOverlay(pdb, chain, resid) {
    if (!resid) return;

    clearSasaOverlay();

    const data = await fetchSasaAtoms(pdb, chain, resid);

    // ALL atoms for hover (including 0.0 exposure)
    for (const a of (data.atoms || [])) {
      const n = normalizeAtomRow(a);
      if (!n) continue;
      State.allAtoms.push(n);
    }

    // Highlight atoms (>= threshold) get halos
    const outer = new window.NGL.Shape("SASA-outer");
    const inner = new window.NGL.Shape("SASA-inner");

    let nGreen = 0, nYellow = 0, nRed = 0;

    for (const p of State.allAtoms) {
      if (p.exposure_a2 < SASA_RULES.MIN_SHOW) continue;

      const bc = bandColor(p.exposure_a2);
      if (!bc) continue;

      State.sasaAtoms.push({ ...p, band: bc.band, color: bc.color });

      if (bc.band === "green") nGreen++;
      else if (bc.band === "yellow") nYellow++;
      else if (bc.band === "red") nRed++;

      outer.addSphere([p.x, p.y, p.z], bc.color, SASA_OUTER_RADIUS);
      inner.addSphere([p.x, p.y, p.z], bc.color, SASA_INNER_RADIUS);
    }

    const highlighted = State.sasaAtoms.length;

    if (highlighted > 0) {
      
        
        State.sasaOuterComp = State.stage.addComponentFromObject(outer);

        // OUTER = surface glow (more “cloudy” and visible head-on)
        State.sasaOuterComp.addRepresentation("surface", {
        opacity: SASA_OUTER_OPACITY,
        side: "double",
        useWorker: true
        });

        State.sasaOuterComp.addRepresentation("surface", {
            opacity: 0.12,
            side: "double",
            useWorker: true
        });


        // Make sure it draws last-ish
        bumpRenderOrder(State.sasaOuterComp, ORDER_HALOS);
        State.sasaOuterComp.name = "sasaGlowOuter";


        State.sasaInnerComp = State.stage.addComponentFromObject(inner);

        // INNER = bright core spheres
        State.sasaInnerComp.addRepresentation("buffer", {
        opacity: SASA_INNER_OPACITY,
        depthWrite: false,
        depthTest: false,
        radiusScale: 1.0,
        sphereDetail: 1
        });

        bumpRenderOrder(State.sasaInnerComp, ORDER_HALOS + 1);
        State.sasaInnerComp.name = "sasaGlowInner";

        bumpRenderOrder(State.sasaInnerComp, ORDER_HALOS + 1);

    } else {
      // Don’t spam the HUD if you prefer—comment this out if it’s annoying.
      // setHudDebug(`SASA halos: 0 atoms ≥ ${SASA_RULES.MIN_SHOW} Å²`);
    }

    State.stage.viewer.requestRender();

    console.log("[SASA] total:", State.allAtoms.length,
                "highlighted:", highlighted,
                "green:", nGreen, "yellow:", nYellow, "red:", nRed);
  }

  // ============================================================================
  // 13) Hover + Pick markers + LIVE HUD
  // ============================================================================
  function formatHoverLabel(pt) {
    if (pt.atom_name && pt.atom_name.length) return pt.atom_name;
    if (pt.atom_symbol && pt.atom_symbol.length) return pt.atom_symbol;
    if (pt.atom_id != null && Number.isFinite(pt.atom_id)) return String(pt.atom_id);
    return "—";
  }

  function hoverSignature(pt) {
    // Use atom_id if available; else coordinate signature
    if (pt.atom_id != null && Number.isFinite(pt.atom_id)) return `id:${pt.atom_id}`;
    return `xyz:${pt.x.toFixed(2)},${pt.y.toFixed(2)},${pt.z.toFixed(2)}`;
  }

  function showHover(pt) {
    if (!State.stage || !pt) return;

    const sig = hoverSignature(pt);

    // Debounce: avoid rebuilding marker/HUD if hovering the same atom repeatedly
    if (sig === State.lastHoverSig) return;
    State.lastHoverSig = sig;

    // Remove prior hover marker (but don't reset lastHoverSig here)
    if (State.stage && State.hoverComp) {
      try { State.stage.removeComponent(State.hoverComp); } catch {}
      State.hoverComp = null;
    }

    const shape = new window.NGL.Shape("hover-marker");
    shape.addSphere([pt.x, pt.y, pt.z], new window.NGL.Color("#FFFFFF"), HOVER_RADIUS);

    State.hoverComp = State.stage.addComponentFromObject(shape);
    State.hoverComp.addRepresentation("buffer", {
      opacity: HOVER_OPACITY,
      depthWrite: false,
      depthTest: false,
      disablePicking: true
    });
    bumpRenderOrder(State.hoverComp, ORDER_MARKERS);

    // ✅ LIVE HUD update
    const exp = Number(pt.exposure_a2);
    const expTxt = Number.isFinite(exp) ? exp.toFixed(2) : "—";
    const label = formatHoverLabel(pt);

    setHudDebug(`Atom: ${label} | Exposed SA: ${expTxt} Å²`);

    State.stage.viewer.requestRender();
  }

  function showPick(pt) {
    if (!State.stage || !pt) return;

    clearPick();

    const shape = new window.NGL.Shape("pick-marker");
    shape.addSphere([pt.x, pt.y, pt.z], new window.NGL.Color("#FFFFFF"), PICK_RADIUS);

    State.pickComp = State.stage.addComponentFromObject(shape);
    State.pickComp.addRepresentation("buffer", {
      opacity: PICK_OPACITY,
      depthWrite: false,
      depthTest: false,
      disablePicking: true
    });
    bumpRenderOrder(State.pickComp, ORDER_MARKERS);

    // HUD update for pick too
    const exp = Number(pt.exposure_a2);
    const expTxt = Number.isFinite(exp) ? exp.toFixed(2) : "—";
    const label = formatHoverLabel(pt);

    setHudDebug(`Atom: ${label} | Exposed SA: ${expTxt} Å²`);

    State.stage.viewer.requestRender();
  }

  // ============================================================================
  // 14) Nearest finder
  // ============================================================================
  function findNearest(list, x, y, z, maxDist) {
    if (!list || !list.length) return null;
    const max2 = maxDist * maxDist;

    let best = null;
    let bestD2 = Infinity;

    for (const p of list) {
      const dx = p.x - x, dy = p.y - y, dz = p.z - z;
      const d2 = dx*dx + dy*dy + dz*dz;
      if (d2 <= max2 && d2 < bestD2) {
        bestD2 = d2;
        best = p;
      }
    }
    return best;
  }

  // ============================================================================
  // 15) Public load()
  // ============================================================================
  async function load({ pdb, chain, warhead, resid }) {
    if (!JOB_ID) {
      console.warn("Render3D: Missing job_id in window.PROTACABLE_CONFIG");
      return;
    }
    if (!initNGL()) {
      setHudDebug("NGL not loaded.");
      return;
    }

    const PDB = String(pdb || "").toLowerCase().trim();
    const CH  = String(chain || "").toUpperCase().trim() || "A";
    const WAR = String(warhead || "").toUpperCase().trim();
    const RES = (resid == null) ? "" : String(resid).trim();

    State.current = { pdb: PDB, chain: CH, warhead: WAR, resid: RES };

    setViewportLoading(true, "Loading 3D…");
    setHudDebug("Loading 3D…");

    clearAllComponents();

    try {
      State.proteinComp = await loadProtein(PDB, CH);
    } catch (e) {
      console.warn("Protein load failed", e);
      setHudDebug(`Protein load failed: ${e.message || e}`);
    }

    try {
      State.ligandComp = await loadLigand(PDB, CH, WAR);
    } catch (e) {
      console.warn("Ligand load failed", e);
      setHudDebug(`Ligand load failed: ${e.message || e}`);
    }

    if (RES) {
      try {
        await applySasaOverlay(PDB, CH, RES);
      } catch (e) {
        console.warn("SASA overlay failed", e);
        setHudDebug(`SASA overlay failed: ${e.message || e}`);
      }
    } else {
      setHudDebug("SASA skipped: missing resid.");
    }

    setViewportLoading(false);
  }

  // ============================================================================
  // 16) Optional debugging: print element histogram from ligand
  // ============================================================================
  function debugLigandElements() {
    try {
      if (!State.ligandComp || !State.ligandComp.structure) {
        console.log("No ligand component loaded.");
        return;
      }
      const s = State.ligandComp.structure;
      const counts = {};
      s.eachAtom(a => {
        const e = (a.element || "??").toUpperCase();
        counts[e] = (counts[e] || 0) + 1;
      });
      console.log("Ligand element counts:", counts);
    } catch (e) {
      console.warn("debugLigandElements failed:", e);
    }
  }

  // ============================================================================
  // 17) Expose API
  // ============================================================================
  window.Render3D = {
    init: initNGL,
    load,
    clear: clearAllComponents,
    debugLigandElements,
    debugProteinResnames
  };
})();



