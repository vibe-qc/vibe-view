# vibe-view design refresh 2026 — "THE QC visualization program"

Goal (maintainer, 2026-07-16): make vibe-view *the* quantum-chemistry
visualization and editing program — Avogadro-class editing, but designed
around QVF files as the native format, with first-class result
visualization (orbitals, spectra, convergence, periodic data) that
Avogadro doesn't have.

This doc is the workstream anchor: the agreed item list, added ideas,
and per-item status. Update it as milestones land.

## Maintainer's list (2026-07-16)

| # | Item | Status |
|---|------|--------|
| 1 | Readable, understandable toolbar icons | **done 2026-07-17** — Camera ▾, Info ▾ and Export ▾ labelled menus; duplicate memory button retired |
| 2 | Better layout; resizable panes/sections | **M1 done** — left/right panels user-resizable (native CSS resize, like the bottom panel) |
| 3 | More visualization options | ideas below; representation presets pending |
| 4 | SCF convergence: option to hide the initial guess | **M1 done** — checkbox above the plot |
| 5 | Fresher look overall | incremental; theme/accent pass pending |
| 6 | Nice rendering options | SSAO/toon exist; presets + depth cue pending |
| 7 | **NEW: insert structures from the qc input library** | **M2 done** — Structure Library card (curated DB (83 molecules today), autocomplete; insert into scene or open as new file) |
| 8 | **NEW: spectrum comparison across multiple QVF files** | **M2 done** — "Compare open files" toggle overlays same-kind spectra with per-file legend |

## Added ideas (agent, 2026-07-16) — proposed roadmap

**Toolbar / UX**
- Grouped into labelled menus — Camera ▾, Info ▾, Export ▾
  (**done 2026-07-17**). Pending: a Tools ▾ pass over the remaining
  edit/measure/file icons if the bar still feels dense.
- Command palette (Ctrl/Cmd+K): fuzzy-search quick actions (camera,
  view presets, HOMO/LUMO, energy diagram, labels, screenshot, symmetry)
  (**done 2026-07-16**); open-section entries (one per section of the
  loaded file, rebuilt on reload) + all export formats
  (**done 2026-07-24**).
- Section sidebar: filter box, kind icons + warning badges
  (**all done 2026-07-17**).

**Look & feel**
- One accent color (#4361EE) + working light/dark theme toggle
  (**done 2026-07-16**); denser card headers. The old toggle was a no-op
  (its class was never bound); now switches every component + accent.
- Publication mode: white background, thick outlines, no gizmos, one
  click.

**3D rendering**
- Representation presets: Publication / Presentation / Analysis
  (**done 2026-07-16** — one-click, combines representation, material,
  background, SSAO, labels).
- Orthographic toggle (**done 2026-07-16**). **Depth-cue fog and
  silhouette outlines are not viable** (audited 2026-07-17): fog is a
  renderer setting, so like SSAO it never reaches the client; and a
  vtkPolyDataSilhouette is view-dependent (it selects different line
  cells per camera — verified 32 vs 86 on a sphere), so a
  server-computed outline goes stale the moment the client rotates,
  and the client owns its camera.
- Per-element colour overrides (**done 2026-07-17**) — element picker +
  colour swatch in the Display panel, applied via cpk_color() so every
  render path honours them. Pending: periodic-table popover styling.

**Structure editing (Avogadro parity+)**
- Structure Library (done, M2) + recents + pinned favourites
  (**done 2026-07-17**). Pending: thumbnails.
- Periodic-table element picker for the editor (**done 2026-07-24** —
  one CPK-tinted 18-column table dialog serves both "element for new
  atoms" and "change selected to"; replaces the two dropdowns, which
  covered only a dozen elements where the table offers all 96).
- Geometry overlay/diff: **already shipped** (compare mode, Phase D1/D4:
  per-file colours, Kabsch-align toggle, RMSD legend). New in 2026-07-16:
  richer `vibeview.align` API (`align_by_species` handles permuted atom
  order) available to upgrade the overlay's same-order-only pairing.
- Point-group detect exists → symmetrize-to-group action
  (**done 2026-07-24** — "Symmetrize to <group>" button in the Symmetry
  card + palette entry; finds the supported operations, closes them into
  a group, iteratively orbit-averages, and verifies exact invariance
  before applying (refuses otherwise); undoable. Closure can exceed the
  detected label: jittered benzene detected as D2h symmetrizes to the
  full D6h with 24 operations).

**Plots & data**
- SCF: skip-guess (done, M1), log|E − E_final| view toggle and |ΔE|
  per-cycle bars (**all done 2026-07-17**).
- Spectra: multi-file overlay (done, M2); broadening slider + normalize,
  X-unit toggle (cm⁻¹ / eV / nm), CSV export (**all done 2026-07-16**);
  stick-vs-envelope display selector (**done 2026-07-24** — Envelope +
  sticks / Envelope only / Sticks only; sticks-only promotes the sticks
  to the hoverable primary trace; the multi-file comparison overlay
  honours the same selector with per-file stick colours, defaulting to
  envelopes-only as before).
- MO panel: HOMO/LUMO one-click buttons + click a level in the energy
  diagram to render that orbital (**both done 2026-07-16**).
- Unit toggles everywhere (Ha/eV, Å/bohr) via one FormatPolicy-like
  setting.

**Robustness**
- Client-render freeze on MO renders: **root-caused and fixed**
  (2026-07-19). Cause is vtk.js depth-peeling of translucent isosurfaces —
  confirmed from the desktop app, where an opaque orbital renders fine and a
  translucent one froze. Orbitals now default to opaque. Mesh size (~12k
  triangles) and SSAO (never ran; a renderer pass cannot reach the client)
  were both ruled out along the way.

- Client-render freeze/blank (MO render, large supercell) — root-cause
  narrowing (2026-07-17): the 3D view renders client-side in vtk.js/WebGL.
  **Mesh size is ruled out** — the methane-dimer HOMO isosurface is only
  ~12k triangles at defaults (23k at |iso|=0.02, 42k at n=80), trivial for
  WebGL — so decimation is *not* the fix. Suspects are the screen-space
  render config. **SSAO is now ruled out too** (2026-07-17): it is a
  server-side VTK render pass, and render passes are not serialised to the
  client — a scene sent to vtk.js is byte-identical with and without it
  (pinned by test_ambient_occlusion_is_server_side_only). It also never even
  applied, due to a SetSamples() API bug. The remaining suspect is the
  translucent (opacity 0.6) isosurface's depth-peeling in vtk.js. The
  Electron shell already catches the hang/crash and offers Reload.

- Stale-server detection (**done 2026-07-16**): the desktop shell moves
  to a free port when the default is held by a leftover server.
- Next: single-instance lock + "adopt or replace" for healthy stale servers.

**QVF-first**
- Multi-file workspaces: session save/restore already exists — surface
  it; per-file color identity used across all comparison overlays.
- Trajectory/vibration scrubber with thumbnails.

## Non-goals (for now)

- Web deployment / multi-user server (trame launcher) — desktop first.
- Replacing the VTK pipeline.

## Verification discipline

Every UI change is live-verified in a browser against a real QVF
(`vibe-view open <file> --no-browser`, drive with real clicks) before
push; py_compile + ruff-delta-zero as usual.
