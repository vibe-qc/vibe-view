# vibe-view → Avogadro 2.0 parity roadmap

**Goal:** make vibe-view the definitive builder/viewer for vibe-qc + vq work,
at feature parity with Avogadro 2.0, with the QVF file at the center and a
heavy focus on *visualizing and interacting with running vq jobs and vibe-qc
calculations* — the axis where we can beat Avogadro, not just match it.

**Status:** in progress. M1-M5 are complete; M6 is active. Decisions locked
with the maintainer 2026-07-02 are recorded in [§0](#0-locked-decisions).

**Landed so far:**
- **M1 — vq cockpit COMPLETE (A1–A3, A5)** — the Job Manager is now a full
  cockpit: always-available drawer listing the live queue (state chips,
  elapsed, fetch/open); a queue-overview strip (daemon health, host + `vq`
  version, capacity, live load); a 5s live-monitor auto-refresh with per-job
  status + log tail; and submit-from-viewer (generate input → `vq` submit with
  name/tag → track). Fixed two bugs that meant listing never worked (host
  `"local"` rejected by `vq.host.is_local_host`; sort on a nonexistent
  `created_at`). See [`docs/user_guide/vibe_view_vq_jobs.md`](../../docs/user_guide/vibe_view_vq_jobs.md).
- **M2 — file-watch hardening + live hot-reload COMPLETE (C1,
  A4-interim)** — `file_watcher.py` rewritten around a content fingerprint
  (per-section manifest digests + whole-file fallback), a settle delay, and
  a mid-write hold, killing the audit's mtime-only race (finding #7). The
  fired event carries a per-section diff; the viewer's new
  `apply_watcher_event` hot-reload swaps in a *fresh* reader (the old one
  served stale data from its open zip handle), refreshes only the active
  panel when the change is confined to 2D-panel sections, and otherwise
  runs a full reload with the user's camera preserved and the active
  section restored. Also fixed in passing: `_reload_active_file` had been
  truncated by a mis-indented SSAO block (aec246e5) so every file
  switch/open blanked the 3D scene, and
  `toggle_file_watcher`/`toggle_dark_ui` were trapped inside
  `camera_preset`'s body and never registered until a preset was clicked.
  See [`docs/user_guide/vibe_view_live_reload.md`](../../docs/user_guide/vibe_view_live_reload.md).
- **M4 — live result streaming COMPLETE (A4, C2–C3)** — the viewer now
  consumes Ask A's streaming checkpoint fields (below). `QVFReader` exposes
  `run_status` / `checkpoint_info` / `section_is_partial`; an app-bar chip
  pulses `running` (with `#seq · iter N · E … Eh`) and settles
  green `converged` / red `failed`; still-growing sections carry a
  "streaming" badge in the sidebar. The Job Manager grows a **Watch live**
  action on running jobs that opens the daemon-advertised
  `checkpoint_qvf_path` and turns auto-reload on. The hot-reload follows a
  growing trajectory's head, announces the terminal frame ("Job
  converged — final results loaded"), and swaps in a fresh reader on every
  frame (M2). Submit-from-viewer defaults to streaming a live checkpoint
  (`checkpoint_qvf=$VQ_WORKDIR/checkpoint.qvf`) so a just-submitted job is
  immediately watchable. Verified end-to-end with trusted-event Playwright
  against a progressive checkpoint producer (`docs/qa_live_stream.py`, 5/5).
  See [`docs/user_guide/vibe_view_live_reload.md`](../../docs/user_guide/vibe_view_live_reload.md) §
  Streaming a running job.
- **M3 — live geometry optimization COMPLETE (B1–B2, 2026-07-08)** — the
  Avogadro-signature builder feature, powered by vibe-qc per decision 2:
  an **Auto-optimize** switch in the Atom Editor card debounce-schedules a
  background-subprocess **MSINDO** relax after every edit pause
  (`vibeview/live_opt.py`: JSON-lines streaming protocol, analytic
  gradients H–Xe, scipy L-BFGS-B mirroring `msindo_optimize` — no ASE
  needed), streams each optimizer step into the viewport, cancels on
  edit, pushes one undo entry per run, and degrades cleanly when vibeqc
  is absent. Engine survey in the module docstring (SCC-DFTB default
  params rejected: no repulsive wall + broken analytic gradient — flagged
  to the semiempirical owners). **B3 (freeze-atom constraints) is DONE** — verified 2026-07-26. Select
  atoms, **Freeze Selected** / **Unfreeze All** in the Atom Editor; frozen
  atoms are highlighted and held fixed while the rest relax. Index-based,
  so any structural edit that reindexes atoms (delete, undo, file switch)
  clears the set rather than silently constraining a different atom, and
  an all-frozen structure is refused before it reaches the worker. The
  worker end was already pinned by `tests/test_live_opt.py`; the controller
  layer had no coverage at all and now does
  (`tests/test_frozen_atoms.py`).
  **2026-07-09 follow-up:** the worker protocol grew an engine field —
  **MACE** (vibeqc.mlip, MIT MACE-MPA-0 only, `note` events for weight
  download) joins MSINDO behind an Engine picker when the `[mace]` stack
  is importable; the no-vibe-qc gating is pinned by blocked-import
  subprocess tests. **M5 cartoon parity and B3 constraints are now complete.**
- **Cross-repo Ask A (streaming checkpoints)** — landed on the producer side
  (commit `1eb02de1`): `run_job` / `run_periodic_job` opt-in `checkpoint_qvf`
  writing `provenance.run_status` + `provenance.checkpoint` + `partial`
  section marking, additively (no `qvf_version` bump). Unblocks A4/M4.
- **Editor edit-compounding fix (2026-07-08)** — sequential edits used to
  each re-read the pristine file and drop all earlier ones (and export/submit
  used the un-edited structure). Fixed via a viewer-side edit overlay on
  `QVFReader` (`set_edit_overlay`, returned by `read_structure()`); pinned by
  `tests/test_edit_overlay.py`. Prerequisite for M3 live-opt, which must read
  the current edited geometry and write relaxed positions back.
- **Cross-repo Ask B (biomolecule metadata)** — **CLOSED 2026-07-26**, both
  halves (see [parity_cross_repo_asks.md](parity_cross_repo_asks.md) § Ask 1).
  Optional `chains` / `residues` / `secondary_structure` / `b_factors` on the
  `structure` section, now *named in the manifest schema* rather than merely
  tolerated by it, plus a per-atom `b_factor` that `pdb_to_qvf` preserves from
  cols 61-66. Additive: **no `qvf_version` bump and no v3 schema** — detect the
  fields by presence, never by version. The reader prefers producer-supplied
  residues / chains / secondary structure over its own geometric inference (per
  chain, for the last). Unblocks M5 cartoon rendering, including the
  colour-by-b-factor mode of D4.

> Note on the old plan: `ROADMAP_V2.md` declares "feature parity with Avogadro
> 2.0 achieved." That was aspirational — the 2026-07-02 audit
> ([audit_2026_07_02.md](audit_2026_07_02.md)) showed many of those features
> had never actually worked (all keyboard shortcuts, context menu, tooltips,
> live-opt, animation export, several exporters). Those are now fixed. This
> roadmap supersedes ROADMAP_V2's parity claim with a real gap analysis and a
> buildable sequence.

---

## 0. Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Compute path | **Both, vq-first** — vq queue is primary; local vibe-qc subprocess for small/instant jobs when installed |
| 2 | Live geometry optimization | **vibe-qc semi-empirical** in a background subprocess (no new deps; dogfoods vibe-qc) |
| 3 | Running-job integration | **Live streaming** — checkpoint QVFs written during a run, viewer hot-reloads (needs QVF extension) |
| 4 | File-format breadth | **ASE-backed, optional** — hand-rolled core set, ASE (`[ase]` extra) for the long tail |
| 5 | Biomolecules | **Full cartoon parity** — ribbon/cartoon, secondary structure, residue/chain selection |
| 6 | Render stack | **Stay on Trame/PyVista** — push VtkLocalView as far as it goes, optimize hotspots |
| 7 | Delivery | **Plan doc first** (this doc), then small release-ready increments on `main` |
| 8 | Cross-repo work | **Formal asks** — QVF/vibe-qc/vq changes proposed to the owning chats; vibe-view built against a documented interim contract |

---

## 1. Where we are vs Avogadro 2.0 — gap analysis

Legend: ✅ done & verified · 🟡 partial/needs work · ❌ missing · ➕ we exceed Avogadro

### Building & editing
| Capability | Avogadro | vibe-view | Gap |
|---|---|---|---|
| Click-select / move / add / delete atoms | ✅ | ✅ | — |
| Bond add/delete/order, auto-bond | ✅ | ✅ | — |
| Element palette, fragment library | ✅ | ✅ | — |
| Undo/redo | ✅ | ✅ | — |
| **Live geometry optimization while drawing** | ✅ (force field) | ✅ (M3: MSINDO subprocess, streamed) | — |
| Build from SMILES | ✅ | ✅ | `[smiles]` extra; RDKit embeds, vibe-qc relaxes |
| Crystal builder / supercell / slab | ✅ | ✅ (slab math has a known limit, audit §2) | §7 polish |
| Measurements (dist/angle/dihedral) | ✅ | ✅ | — |

### Visualization
| Capability | Avogadro | vibe-view | Gap |
|---|---|---|---|
| Ball&stick / spacefill / wireframe / sticks | ✅ | ✅ | — |
| Molecular orbitals, densities, ESP isosurfaces | ✅ | ✅ | — |
| Vibrations (animated modes) | ✅ | ✅ | — |
| Trajectories / optimization playback | ✅ | ✅ | — |
| Spectra (IR/Raman/UV-Vis/NMR) | ✅ (plugin) | ✅ ➕ (more spectra kinds) | — |
| Band structure / DOS / phonons | limited | ✅ ➕ | — |
| **Cartoon / ribbon for biomolecules** | ✅ | ✅ | — |
| Labels (atom/charge/index) camera-facing | ✅ | ✅ | — |
| Measurement/selection overlays | ✅ | ✅ | — |

### Compute & data
| Capability | Avogadro | vibe-view | Gap |
|---|---|---|---|
| Generate input for an external code | ✅ (many codes) | ✅ (vibe-qc `.py`; units, PBC, lattice, dimension, charge, and spin preserved) | — |
| Run a calculation | ✅ (local exec) | ✅ (vq submit + streaming checkpoint, M1/M4) | — |
| **Monitor a running job** | ❌ | ✅ ➕ (M1 live monitor + log tail) | — |
| **Live-stream results as the job runs** | ❌ | ✅ ➕ (M4 watch-live + hot-reload) | ➕ our killer feature |
| Fetch/open completed results | ✅ | ✅ (`from-vq`, open_vq_job) | — |
| Format breadth (import) | ✅ ~100 via OpenBabel | 🟡 core + ASE-if-installed | **§8** |

### Platform
| Capability | Avogadro | vibe-view | Gap |
|---|---|---|---|
| Native desktop app | ✅ | ✅ (Electron shell, shipped) | — |
| Plugin/script system | ✅ (Python) | 🟡 (Python SDK exists) | §9 |
| Cross-platform packaging | ✅ | 🟡 (dev-mode branded; `.app` documented, not bundled) | §9 |

---

## 2. QVF as the hub — landed additive extensions

Everything centers on QVF. The producer-owned manifest schema lives in
`python/vibeqc/output/formats/`; vibe-view consumes its optional fields and
degrades gracefully when they are absent. Both former cross-repo asks below
landed additively under QVF v1, so no v3 version bump was needed.

### 2a. Streaming / checkpoint QVFs (for §3 live jobs) — ✅ LANDED

- `provenance.run_status` records `"running"`, `"converged"`, or `"failed"`.
- `provenance.checkpoint` carries a monotonic sequence plus timing metadata;
  mid-SCF snapshots may also carry the iteration and energy.
- A still-growing section may carry `partial: true`.
- `run_job` and `run_periodic_job` accept `checkpoint_qvf=<path>` and
  `checkpoint_every=<n>`. Writes are atomic, and vibe-view content-diffs each
  replacement before hot-reloading the affected view.

### 2b. Residue / chain / secondary-structure metadata (for §6 cartoon) — ✅ LANDED

The `structure` section optionally carries chains, residues, secondary
structure, and per-atom B factors. The manifest schema names these fields;
vibe-view preserves them during PDB import and falls back to geometry-based
inference when producer metadata is absent.

### 2c. vq job handle in QVF (nice-to-have)
- `provenance.vq: {job_id, host, submitted_at}` so an opened result links back
  to its queue entry (re-open in monitor, resubmit). Interim: vibe-view records
  this in its own recent/job cache keyed by path.

---

## 3. Workstream A — vq & vibe-qc as first-class citizens (highest priority)

This is the parity axis where we *beat* Avogadro. Sequenced as landable
milestones.

- **A1 — Job manager panel. ✅ DONE (2026-07-02).** Always-available right-side
  drawer listing the live queue with colored state chips, elapsed time, and
  per-job fetch/open; auto-refresh switch. Uses `vq` per §15.
- **A2 — Submit from the viewer. ✅ DONE (2026-07-02).** Generates the
  unit-correct `.py` whose `Molecule`/`PeriodicSystem` choice, lattice, and
  dimension follow the active QVF and whose charge and multiplicity follow the
  requested calculation state, then submits via `vq.submit.submit_local` with
  a derived job name + `vibe-view` tag and opens the Job Manager (live monitor
  on) to track it. Pending QVF containers record the same state in `job.spec`.
  Unsupported non-prefix PBC fails closed. Local vibe-qc fallback when `vq` is
  absent.
- **A3 — Live monitor. ✅ DONE (2026-07-02).** Live-monitor switch drives a 5s
  auto-refresh loop; per-job status + stdout/stderr **log tail** via
  `show_status_json`, pinned above the list.
- **A5 — vq feature/host overview. ✅ DONE (2026-07-02).** Queue-overview strip:
  daemon health, host + `vq` version, capacity (max CPU/jobs), live load.
- **A4 — Live result streaming. ✅ DONE (2026-07-02, M4).** Watch a running
  job's checkpoint QVF (§2a) from the Job Manager's **Watch live** action;
  hot-reload the scene as it updates, following a growing trajectory's head and
  announcing the terminal frame. App-bar chip reflects `run_status`; still-
  growing sections badged. Built on M2's content-diff hot-reload and Ask A's
  manifest fields. *This is the demo that sells the tool.*

## 4. Workstream B — live geometry optimization while building

- **B1 — Background relax service. ✅ DONE (M3, 2026-07-08).** Debounced
  subprocess worker (`vibeview/live_opt.py`) relaxing the sketch on the
  MSINDO surface with analytic gradients, streaming per-step geometries;
  cancel-on-edit; never blocks the UI thread.
- **B2 — "Auto-optimize" toggle + convergence feedback. ✅ DONE (M3).**
  Atom Editor card switch + live status (step / E / gₘₐₓ → "relaxed in N
  steps"); probe-on-first-enable snaps the switch off with the reason when
  vibe-qc is absent.
  **Lifecycle hardening DONE 2026-08-25 (issues #359/#373).** Each schedule
  binds step/done/error/note callbacks to an immutable reader+run generation,
  and edit, history, reader, and mode boundaries invalidate the preceding
  generation before stale output can touch geometry, history, or status.
  Capability probes are last-writer-wins: stale toggle/reader generations are
  ignored before applying state, while a current unavailable result cancels
  the current run before disabling the switch. Undo, Redo, and Symmetrize also
  retire pending probes because those boundaries deliberately do not
  auto-reschedule; a Supercell build schedules its new eligible geometry.
- **B3 — Freeze-atom constraints. ✅ DONE (2026-07-26).** The worker protocol
  carries frozen atom indices; the Atom Editor exposes **Freeze Selected** and
  **Unfreeze All**, highlights the frozen set, clears it after any reindexing
  edit, and refuses an all-frozen optimization. Controller coverage lives in
  `tests/test_frozen_atoms.py`.
- No new dependency; if vibe-qc isn't importable, the toggle is disabled with a
  clear message (decision 1/2).

## 5. Workstream C — QVF streaming plumbing (viewer side)

- **C1 — Robust file-watch + section-diff hot-reload. ✅ DONE (M2).**
  `file_watcher.py` rewritten: content fingerprint + settle-delay + mid-write
  hold, per-section diff, reload only what changed. Foundation for A4.
- **C2 — `run_status`/`checkpoint` manifest awareness. ✅ DONE (M4).**
  `QVFReader.run_status` / `.checkpoint_info`; app-bar chip; graceful (empty)
  when absent.
- **C3 — Partial-section rendering. ✅ DONE (M4).** `QVFReader.section_is_partial`;
  a still-growing section (trajectory, SCF history mid-run) shows a "streaming"
  badge in the sidebar and reloads in place as it grows.

## 6. Workstream D — biomolecule / cartoon parity (largest rendering effort)

- **D1 — Residue/chain model. ✅ DONE (2026-07-25).** `Atom` carries optional
  `atom_name` / `residue_name` / `residue_seq` / `chain_id`; `pdb_to_qvf`
  populates them and `read_structure` reads them back. `StructureData` exposes
  `has_residues`, `chains()` (residues per chain, in file order — grouped by
  contiguous run, since PDB residue numbers wrap at 9999 and a solvated system
  reuses them) and `backbone_trace()` (the CA spine D3 splines through,
  optionally per chain so a ribbon does not jump between them). Verified on a
  150k-atom membrane protein: 12,010 residues matching the file exactly, an
  885-point CA trace with median CA-CA spacing 3.84 A. Optional throughout, so
  non-biomolecular structures are untouched.
- **D2 — Secondary-structure detection. ✅ DONE (2026-07-26).**
  `StructureData.secondary_structure(chain_id=None)` returns one character per
  CA aligned with `backbone_trace()`: `H` / `E` / `C`. CA-only, using the
  descriptors P-SEA established (CA(i)-CA(i+k) distances plus virtual angle and
  dihedral) rather than real DSSP's hydrogen-bond energies, since a QVF is not
  guaranteed to carry backbone O and the viewer needs this only to pick ribbon
  geometry. Assignment is per chain even when no chain is requested. Thresholds
  are this implementation's, bracketed around ideal geometry the tests build.
  **The dihedral sign is load-bearing** — an ideal right-handed helix measures
  +50 deg and its left-handed mirror -50 with identical distances, so both are
  pinned. Validated on DHFR (21% H in 4 helices, 29% E in 11 strands, against
  its published 4 helices and 8-stranded sheet) and on the ClC channel (77% H
  in 36 helices of 9-40 residues). Tests:
  `tests/test_secondary_structure.py`.
  *Not yet:* no distinction between alpha/pi/3-10 helices, no beta-bridge vs
  sheet distinction, and a chain-terminal strand's last residue reads coil
  because CA(i)-CA(i+3) is undefined there.
- **D3 — Cartoon/ribbon renderer. ✅ DONE, flat ribbons + sheet arrows
  (2026-07-26).**
  `cartoon` is a representation in the structure renderer: one swept ribbon
  per chain through the CA trace, colour-cycled per chain, offered in the
  Representation picker as "Cartoon (biomolecule)". Chains spline separately so
  a ribbon never leaps between them; a chain with fewer than four CAs is
  skipped rather than drawn as a stub; a structure with no backbone falls back
  to ball-and-stick and the controller says so instead of leaving the picker
  reading "Cartoon" over an unchanged scene. Rendered live on the 885-residue
  ClC benchmark (screenshot in the 2026-07-26 QA run).
  **It is also the default** for any file with a splineable backbone — see the
  performance note below; molecules are untouched. Tests:
  `tests/test_cartoon.py`.
  **2026-07-26 follow-up:** driven by D2 — the ribbon widens over helices and
  strands and thins over loops, smoothed over ~1.5 residues so a helix
  terminus does not render as a visible step. Falls back to one uniform
  round cord when the assignment is missing, is the wrong length, or the
  chain is all coil.
  **2026-07-26, third pass — flat ribbons and true sheet arrows.** The
  geometry is no longer a tube. Each spline sample carries an orientation
  frame plus its own half-width and half-thickness, and an elliptical
  cross-section is swept along it, so a helix renders as a flat band
  (2.2 A across, 0.4 A edge-on) and each strand run ends in an arrowhead
  that steps out to 3.2 A across and tapers to a point at its C-terminus.
  Loops keep equal width and thickness, so they stay round cords.
  The frame is the standard alpha-carbon construction (Carson & Bugg,
  *Algorithm for ribbon models of proteins*, J. Mol. Graphics **4**,
  121-122 (1986)): `side = normalise(A x B)` with `A = CA(i+1) - CA(i-1)`
  and `B = CA(i-1) - 2 CA(i) + CA(i+1)`, which puts the thin direction
  along the curvature, so a helix presents its face outward rather than
  edge-on.
  **The frame's sign has to be propagated forward.** A strand's
  alpha-carbons zig-zag, so `B` reverses at every residue and the raw
  `A x B` flips 180 degrees each step; swept as-is a strand twists a half
  turn per residue instead of lying flat. Each frame is flipped to agree
  with its predecessor.
  Arrowheads are written **over** the smoothed width profile, not through
  it: a barb is a deliberate discontinuity, and smoothing rounds it into a
  bulge. Runs under three residues get no arrow, since D2 reads a
  two-residue run as a bridge rather than a sheet.
  **Payload is unchanged at 0.60 MB** on the 885-residue ClC benchmark —
  identical to the tube it replaces, 56,636 triangles either way — but only
  because three details were measured rather than assumed; each roughly
  doubled the wire cost for no visible difference. Triangle strips, not
  quads (1.28 MB). Point normals computed in the renderer, so
  `add_mesh(smooth_shading=True)` does not derive them and triangulate the
  strips away (1.51 MB). float32 arrays, as `tube()` emits (1.06 MB).
  Server build is 0.07 s against the tube's 0.04 s. Tests:
  `tests/test_cartoon.py` (`TestRibbonFrames`, `TestSheetArrows`,
  `TestRibbonPayload`). Reproduce the numbers with
  `docs/bench_cartoon_payload.py <protein.pdb>`, which now measures the
  shipped ribbon and the old tube side by side.
  *Not yet:* the cross-section is an ellipse, so a strand's edges are
  rounded rather than crisp; no separate profile for pi / 3-10 helices; the
  two end caps per chain reuse the wall's radial normals, which is
  invisible at two cells but is not strictly right.
- **D4 — Residue/chain selection and colour-by ✅ DONE (2026-08-01).**
  A "Select residues" field appears whenever the structure has CA-bearing
  residues. Matching residues render **white** in every representation:
  ribbon samples in the cartoon, every atom in ball-and-stick and
  space-filling, and bonds whose two endpoints are both selected in
  sticks-only and wireframe. A boundary bond stays unhighlighted, so the
  selection does not bleed into an adjacent residue. White preserves
  the cartoon selector's established accent across the other representations.
  The grammar is deliberately three
  forms and no operators: `A` for a whole chain, `A/24-38` for a range,
  `A/24` for one residue, `*` in the chain position for every chain;
  terms separated by commas or whitespace.
  **A term with no slash is always a chain id, never a residue number.**
  mmCIF chain ids can be numeric, so `24` is chain 24 and `*/24` is
  residue 24 — inferring from the shape of the token would make one
  selection mean different things in different files. Chain ids match
  case-sensitively, because in a PDB chain `a` and chain `A` are two
  different chains.
  The field commits on Enter or blur, not per keystroke: each commit
  rebuilds the scene, and `A/2` is a selection the user never asked to
  see on the way to typing `A/24`.
  **The summary line is load-bearing.** A selection that matches nothing
  is indistinguishable from a render bug unless it says so, so the
  control reports the residue count and calls out an absent chain id and
  an unreadable term separately. It also handles the case that broke the
  first live QA run: a PDB with blank cols 22 groups every residue under
  the *empty* chain id, which no selection can name — DHFR from the RCSB
  is one — so naming a chain that does not exist on such a file adds
  "this file has no chain ids — select with \*". Rejected terms narrow
  nothing rather than raising.
  A structure with no selection is byte-for-byte the previous render: the
  ribbon's per-sample colour array only appears once something matches, atom
  glyphs keep their old groups, and the bond mesh is not partitioned. Selected
  periodic atoms remain glyph-batched, and selected/unselected large bond sets
  stay as two batched actors rather than falling back to one actor per bond.
  Tests: `tests/test_cartoon.py` (`TestSelectionGrammar`,
  `TestSelectionHighlight`, `TestSelectionInAtomRepresentations`,
  `TestSelectionSummary`,
  `TestSelectionController`).
  *Not yet:* no isolate/hide-the-rest and no click-to-select in the viewport.
  The "Ribbon colour" picker remains cartoon-only. Its first two modes are
  **Chain** (one hue per chain, so subunits are
  tellable apart) and **Secondary structure** (helix red / sheet yellow / loop
  grey, the convention the field reads without a legend). One colour per spline
  sample, repeated across that sample's whole cross-section ring, so a colour
  boundary is a clean cut across the ribbon rather than a diagonal smear.
  Colour is deliberately **not** smoothed, unlike the width profile: a blended
  colour would imply a structure that was never assigned.
  *(Until the extruded ribbon landed this rode the spline's own point data and
  had to be attached before `tube()`, which silently drops anything added
  afterwards. The extrusion writes point data on the finished mesh, so that
  trap is gone — but it is the reason the ordering looks arbitrary in the
  history.)*
  **Residue type ✅ DONE (2026-07-26)** — a third "Ribbon colour" option
  colours each residue by its own type, in the **RasMol "amino" scheme**
  (Sayle & Milner-White, *RASMOL: biomolecular graphics for all*, Trends
  Biochem. Sci. **20**, 374 (1995)). Transcribed from that scheme rather
  than invented: the whole value of colouring by residue type is that a
  reader already knows the mapping — acidic red, basic blue, aromatic
  indigo, sulphur yellow, hydroxyl orange, amide cyan, aliphatic green.
  Non-standard and unknown residues take the scheme's own fallback; a
  chain that carries residue *numbers* but no *names* falls back to chain
  colour rather than rendering one flat fallback hue, which would say
  nothing and read as a bug. Not smoothed, for the same reason the
  secondary-structure colours are not. A selection still overrides it.
  Verified live on DHFR: 9 distinct saturated hue buckets in the viewport
  against 3 for secondary structure and 1 for chain, with the lit-pixel
  fraction unchanged (7.03% → 6.24%).
  **B-factor ✅ DONE (2026-07-26).** `Atom.b_factor` is populated for any PDB
  carrying cols 61-66 and for an archive supplying the section-level array.
  A per-structure blue-to-red ramp shows measured values and the status line
  reports its range. A missing value stays `None` and renders in a distinct
  neutral grey rather than at the cold end, because a b-factor of 0.00 is a
  real measurement.
- **D5 — Camera-facing labels. ✅ DONE (2026-07-26).** `build_label_mesh` takes
  an optional `camera=(position, focal_point, view_up)`, rotates each `Text3D`
  glyph onto a camera-facing basis and lifts it along the camera's up rather
  than world +z, so the label sits above its atom *on screen* instead of
  drifting sideways. Closes the audit's deferred label-orientation finding
  (`docs/audit_2026_07_02.md` item 5).
  **The scoping above was right that this is not a tweak to
  `build_label_mesh`.** It needed option (a) — client-to-server camera
  reporting on interaction end — which did not exist and now does
  (`ctrl.sync_client_camera`, bound to `EndAnimation`). Chasing that
  prerequisite turned up a separate live bug: with no readback, Save View and
  Save Session had been storing the server's stale camera, so a saved view
  always restored the default orientation. Both are fixed.
  Because the labels are geometry, facing the viewer means rebuilding them:
  actors register through `register_label_actor()` and `reorient_labels()`
  re-meshes only those actors, once per interaction end. Degenerate bases (an
  anchor at the camera, a view-up parallel to the view direction) fall back to
  the flat placement rather than emitting NaNs.
  **Verify by looking at renders from several angles**, per the note this
  entry replaced. `docs/bench_label_orientation.py` does exactly that:
  1,138 lit px flat against 2,866 billboarded from one off-axis camera, plus
  two PNGs. Treat the count as a tripwire only — `Text3D` is *extruded*, so
  edge-on text is still a visible slab. A browser-side attempt to measure this
  by counting label ink across a rotation reported the un-billboarded labels
  retaining **more** ink, which is true and says nothing about legibility.
  Tests: `tests/test_billboard_labels.py`.
  *Caveat:* orientation refreshes at interaction **end**, not per frame, so a
  label lags during a drag and snaps when it stops. Continuous billboarding
  still needs option (b), client-side vtk.js follower emulation.
- ~~Risk: this is a big, latency-sensitive rendering piece on Trame; benchmark on
  a few-thousand-atom protein early.~~ **Benchmarked 2026-07-25 — payload risk
  retired.** On cp2k's ClC benchmark (885 residues), a ribbon backbone is
  **56,636 triangles / 0.60 MB** on the wire and builds in under 0.1 s. That is
  *half* of **1,000 atoms** of ball-and-stick (1.23 MB) and ~25x less than
  8,000 atoms (15.27 MB). Cartoon is therefore **cheaper than the
  representation it replaces** — the expensive case is showing a protein as
  ball-and-stick, which is exactly what D3 avoids. Reproduce with
  `docs/bench_cartoon_payload.py <protein.pdb>`.
  *(This bullet carried 1.06 MB until 2026-07-26. That figure was measured on
  a triangulated copy; the mesh that actually crosses the websocket keeps its
  triangle strips and costs 0.60 MB, for the tube and for the ribbon alike.
  The script no longer triangulates before serialising.)*
- **The actual biomolecule bottleneck was not rendering at all (2026-07-26).**
  While building D3 the ribbon path was found to hang on a real protein. It
  was not the ribbon: `StructureRenderer.load()` infers bonds, and covalent-
  radius inference was an all-pairs Python loop. 13,772 atoms is 94.8 million
  iterations, measured at **158.7 s**; 150,925 atoms did not finish. Trame
  binds its port only after the first render, so a real PDB was unopenable
  rather than slow. Two fixes landed with D3: inference now uses a uniform
  cell list (identical output, verified against a verbatim transcription of
  the old loop; **0.23 s** and **2.91 s** on those two systems), and startup
  no longer infers bonds merely to test whether a structure is periodic. This
  is why cartoon is the *default* for backboned structures rather than only an
  option: opening a protein atom-by-atom is the expensive case even now that
  it is possible. Measured in one process on the 13,772-atom ClC protein after
  the inference fix, `add_to_plotter` takes **0.08 s** for the ribbon (1 actor)
  against **120.06 s** for ball-and-stick (8 actors, 220,208 cells) — the
  remaining cost there is glyph and cylinder construction, not bonding.
  Reproduce with `docs/bench_representation_build.py <protein.qvf>`.
- **Interaction feel (decision 6) — judged, with a caveat.** The ribbon
  rotates in a real browser and the page is usable; the ClC screenshot in the
  2026-07-26 QA run is a live render. A *quantitative* client-side frame rate
  was not obtained: the trusted-event Playwright harness has a per-drag floor
  of ~8 s even on a 3-atom molecule, so it cannot resolve rendering cost, and
  a WebGL `readPixels` probe reports 0% ink because the vtk.js canvas does not
  preserve its drawing buffer. Server-side build and wire payload — the stated
  bottleneck in decision 6 — are both measured and both favour cartoon by
  large margins. If a real frame-rate number is ever needed, instrument
  inside the page rather than around it.

## 7. Workstream E — builder polish to close editor parity

- **SMILES→3D. Dependency decided 2026-07-28; converter landed.** RDKit
  behind a `[smiles]` extra parses and embeds (ETKDG); the geometry is
  refined by the existing MSINDO live-opt, so decision 2 still holds and no
  force field inside the dependency decides what the user sees. RDKit is
  BSD-3-Clause and was already a soft dependency for IUPAC naming; Open
  Babel was ruled out as GPL-2.0. `converters.smiles_to_qvf` +
  `tests/test_smiles.py`. **Builder UI landed 2026-07-28**: a SMILES field in
  the Build molecule dialog, which loads the structure and arms auto-optimize
  so vibe-qc produces the geometry the user keeps.
- ~~Crystal `miller_slab` d-spacing fix (audit deferred item).~~ **Done
  2026-07-28.** Reciprocal-lattice spacing; thickness and layer selection
  corrected with it; non-c-normal orientations now raise instead of
  returning a mislabelled box. A general surface cell (two lattice vectors
  spanning the plane, as ASE's `surface()` does) remains unimplemented.
- Fragment/template expansion. **Auto-bonding: the periodic edge case is
  fixed (2026-07-28)** — bonds through cell walls were missed entirely, so
  crystals and slabs rendered with unbonded surfaces. Remaining bonding
  **edge cases audited 2026-07-28.** Hydrogen bonds and disulfides were already
  right (a 1.95 A O-H contact is correctly not covalent; S-S at 2.05 A is
  bonded). Metals were not: neutral-atom radii over-bond cations, so MgO showed
  24 spurious Mg-Mg bonds at 18-coordinate and CsCl 3 Cs-Cs at 14. Metal-metal
  pairs now bond only in an all-metal structure. Remaining: metal-metal bonds in
  clusters and organometallics are real chemistry the heuristic cannot see, and
  hydrogen bonds are still not drawn as a distinct dashed style.
  **Resolved 2026-07-28 (maintainer approved the format change).** A bond is
  now `(i, j, order, image)`, carrying the integer lattice translation applied
  to `j`. Rocksalt NaCl reports 6 neighbours per ion and fcc copper 12, both
  measured; diamond was already correct at 4. The 3D renderer's private
  all-pairs bond loop was removed in favour of the shared inference, so there
  is one bonding implementation rather than three. Explicit producer-supplied
  bonds carry a zero image and still fall back to the minimum image, so their
  rendering is unchanged.
- **Periodic supercell lattice consistency DONE 2026-08-25.** Build supercell
  previously duplicated atoms without scaling the unit cell, producing
  out-of-cell fractional coordinates, an absent or stale unit-cell wireframe,
  a stale CIF cell, and a stale generated-input lattice. Atoms and lattice now
  form one edit overlay and one undo/redo transaction. Real periodic rows scale
  with replication, while synthesized non-periodic rows in lower-dimensional
  structures remain unchanged. A committed physical build also resets the
  separate display-only replication, preventing later scene rebuilds from
  tiling the expanded structure twice. Reader switches and full hot reloads
  clear file-scoped edit transactions and atom-indexed interaction state;
  panel-only reloads migrate the unchanged atoms+lattice overlay with its
  matching history. The related live-optimization reader/run and probe
  generation hardening is recorded under Workstream B (issues #359 and #373).

## 8. Workstream F — format breadth

- **F1 — `[ase]` extra. ✅ DONE** (verified 2026-07-28). The extra is declared,
  and a format needing it raises with `pip install 'vibe-view[ase]'`.
- **F2 — `_SUPPORTED_EXTENSIONS`. ✅ DONE** (verified 2026-07-28). Native plus
  ASE extensions (`.vasp`, `.poscar`, `.extxyz`, `.traj`), 16 in total, offered
  by the browse page and recognised by `detect_format`.
  **Both were already implemented and simply never marked here; verifying that
  turned up a real defect.** `ase_to_qvf` wrote `pbc` but not the lattice, so a
  POSCAR arrived flagged periodic with no cell and rendered as a molecule --
  no unit cell, no periodic bonding, no replication, and no error. Fixed
  2026-07-28: a diamond-silicon POSCAR now imports with its 5.43 A cell and
  comes out 4-coordinate at 2.351 A. Tests: `tests/test_ase_import.py`.
- **F3 — Export breadth. ✅ DONE (2026-07-28).** All twelve CLI formats swept
  on a molecule *and* a crystal, each output validated as its own format
  (atom counts in XYZ, parseable JSON, AST-parseable Python, well-formed XML
  for SVG/CML, a `%PDF-` header, vertices in OBJ, an asset block in glTF)
  rather than checking that a file appeared. Eleven were clean both times.
  **The find: vibe-view could not read its own CIF export** — a cell-less
  structure has no fractional coordinates, so the export writes
  `_atom_site_Cartn_*`, and the reader only understood `_atom_site_fract_*`.
  Fixed in the reader; fractional still wins when a file carries both.
  Tests: `tests/test_export_roundtrip.py`.

## 9. Workstream G — platform & extensibility

- ~~Cross-platform `.app`/AppImage bundling (currently dev-mode only).~~
  **Mostly built; remaining work is not agent-actionable.** The onboarding
  app, the `mac`/`linux`/`win` electron-builder targets, `build-desktop.sh`
  and the [publishing runbook](../electron/PUBLISHING.md) are all on `main`,
  and the auto-update feed is live. What is left is gated on things an agent
  must not or cannot do: the feed publish is a **maintainer-run deploy**
  needing an SSH deploy key (deliberately excluded from the version bump —
  `docs/release_process.md` § 5), and Linux/Windows artifacts need build
  hosts, since electron-builder cannot cross-build them from macOS. The feed
  is currently **stale at 2.10.0** against a 2.15.2 source.
  Signing stays deferred (no Apple Developer ID).
- ~~Plugin/script surface documentation for the Python SDK.~~ **DONE
  2026-08-05** —
  [`docs/user_guide/vibe_view_python_api.md`](../../docs/user_guide/vibe_view_python_api.md)
  documents the supported `vibeview.__all__` surface with verified examples,
  and scopes what a real plugin API would require. There is no plugin API
  today; the page says so rather than implying one.
- ~~Accessibility + docs pass.~~ **DONE for the onboarding screen
  2026-08-05** — measured contrast fixes (white-on-accent was 3.52:1),
  a live region for the recheck loop, keyboard reachability for the
  scrollable command, focus-visible styling and `prefers-reduced-motion`.
  **Served-viewer baseline DONE 2026-08-20; full audit remains open.** The
  first generated-DOM pass now preserves toolbar accessible names across
  custom tooltip hover, names both vq icon-only close controls and the VTK
  viewport, gives all three navigation landmarks unique labels, and exposes
  checkpoint state transitions plus the general status surface as polite
  atomic live regions. Changing checkpoint detail and five-second job-row
  refreshes remain non-live. Source-contract tests and
  trusted browser events pin the initial and post-hover names, landmarks,
  canvas, and live status (issues #179-#182). **Dialog naming DONE
  2026-08-25.** All thirteen served modal roots now expose a stable descriptive
  name, including the titleless command palette and the separately built
  element picker; module-wide source and trusted-Chromium final-DOM regressions
  prevent a new unnamed `VDialog` from landing (issue #314). **Presentation
  keyboard workflow DONE 2026-08-25.** The shipped overlay now mounts visibly,
  exposes named controls, preserves 3D and panel-rendered bookmark slides,
  restores volume settings before rendering, supports button and Left/Right
  navigation, exits cleanly on Escape, and performs real timed advancement
  (issue #321). Shortcut help now matches the implemented modifier bindings.
  **Slider accessible names DONE 2026-08-25.** All eleven slider controls now
  forward distinct workflow-specific names to their focusable thumbs, with
  module-wide source/template coverage and trusted-Chromium final-DOM checks
  for the volume and conditional clip-plane controls (issue #323).
  **Playback-control accessible names DONE 2026-08-25.** The six icon-only
  trajectory and molecular-orbital previous/play/next controls now expose
  context-specific names, with each play button reacting between Start and
  Pause. Complete source/template inventories and separate trusted-Chromium
  final-DOM scenarios pin both conditional panels and transitions (issue #326).
  **Element-colour input name DONE 2026-08-25.** The visible native colour
  override now exposes an explicit stable name and title. Raw-input source and
  generated-template inventories distinguish it from the hidden file picker,
  and trusted Chromium pins the visible focusable final DOM control (issue
  #328).
  **Clip-slider render synchronization DONE 2026-08-25.** Each clip slider now
  sends an explicit axis/value payload before rebuilding, and retired VTK actor
  graphs stay alive through replacement allocation so Trame cannot confuse
  reused native-address IDs across object types. Trusted Chromium requires the
  requested slice position to reach server-rendered feedback with no renderer
  exception. High-frequency position text remains visual rather than globally
  live, while mode transitions are announced once (issue #331).
  **Dialog focus restoration DONE 2026-08-25.** All thirteen state-driven
  dialogs capture keyed focus origins and restore the exact usable control
  after their leave transition. Direct toolbar, transient-menu,
  keyboard-shortcut, nested-dialog, and presentation-overlay launches preserve
  their own context; removed, hidden, disabled, inert, accessibility-hidden,
  or closing-overlay targets fail safely. Source/template inventories and
  physical trusted-Chromium paths pin the final focus behavior and browser
  console (issue #332).
  **Session replication replay DONE 2026-08-25.** Version-1 sessions now
  validate and restore saved periodic replication through the shared clamped
  scene-rebuild path, keep UI/render/geometry state synchronized, and apply the
  saved camera last; legacy and malformed fields remain recoverable (issue
  #324).
  **Local server output isolation DONE 2026-08-25.** Directory, recursive,
  detail, and cold-start pages now encode filesystem and QVF strings for their
  exact HTML, URL, or inline-script context. Download and error-response
  headers likewise exclude raw filesystem or operating-system text, with
  adversarial rendering, URL round-trip, and CR/LF regressions (issue #334).
  **Panel-separator keyboard access DONE 2026-08-25.** The two side-panel
  resize edges and the result-details splitter now expose three named,
  focusable ARIA separators. Spatial Arrow keys, larger Shift steps, bounds,
  and collapse/restore are available without a pointer; rendered range values
  stay synchronized after drag, native resizing, layout clamps, and
  conditional remounts. Trusted Chromium covers both input modes, Trame width
  persistence, remount deduplication, and the final DOM (issue #341).
  Remaining audit
  work includes systematic contrast, focus order and visibility, complete
  keyboard reachability, reduced-motion behavior, and announcement-rate
  behavior for high-frequency general status messages across the served viewer.

---

## 10. Proposed milestone sequence (each ships green on `main`)

1. ✅ **M1 — Job manager + submit + monitor (A1–A3, A5).** Immediate value, no
   format change. Makes vibe-view a real vq cockpit.
2. ✅ **M2 — File-watch hardening + live single-QVF hot-reload (C1, A4-interim).**
   Content-diff hot-reload; demo-able against a job that rewrites its QVF.
3. ✅ **M3 — Live geometry optimization (B1–B2).** The Avogadro-signature builder
   feature, powered by vibe-qc (MSINDO subprocess; landed 2026-07-08).
4. ✅ **M4 — streaming manifest fields (C2–C3, A4).** Ask A's
   `run_status` / `checkpoint` / `partial` consumed (no v3 bump was needed —
   the fields validate against the v1 schema); Watch-live + app-bar chip +
   terminal announcement. Landed ahead of M3.
5. ✅ **M5 — Cartoon/biomolecule parity (D1–D5).** Complete 2026-08-01.
6. 🟡 **M6 — Format breadth + builder polish + platform (F, E, G): ACTIVE.**
   Format breadth is complete; builder polish remains incremental; desktop
   publishing and cross-platform build-host work are maintainer-gated; and the
   served-viewer accessibility audit remains active.

The checkpoint and biomolecule cross-repo asks in §2a/§2b are closed.

## 11. Open questions to resolve per milestone (not blocking this plan)

- ~~SMILES→3D dependency choice (§7).~~ **CLOSED (2026-07-28).** RDKit is the
  optional `[smiles]` dependency; it embeds with ETKDG and the existing MSINDO
  live-opt refines the geometry.
- ~~Cartoon on Trame latency ceiling (§6/decision 6) — benchmark at M5 start;
  the decision-6 hedge ("evaluate later") applies if the feel isn't there.~~
  **CLOSED (2026-07-26). Payload was never the constraint.** A whole
  885-residue cartoon is **0.60 MB** on the wire — the flat extruded ribbon and
  the spline tube it replaced are byte-identical at 56,636 triangles — against
  1.23 MB for merely 1,000 atoms of ball-and-stick. Interaction feel was judged
  in a real browser during D3/D4 and the ribbon is usable; see §6 for what was
  and was not measured there.
  *(This bullet asserted 1.06 MB from 2026-07-25 until 2026-07-26. That figure
  came from a bench that triangulated the mesh before sizing it, so it measured
  something the viewer never sends. The qualitative conclusion held and in fact
  understated the margin — 2x cheaper than 1,000 atoms of ball-and-stick, not
  1.15x. Corrected here rather than silently overwritten, since the wrong number
  was quoted onward.)*
- ~~Exact checkpoint cadence / size policy for streaming (§2a).~~ **CLOSED.**
  Cadence is route-dependent and documented: periodic progress-hook routes
  honor `checkpoint_every=N`; molecular single-point SCF emits start and
  terminal frames because its loop runs in compiled C++.
