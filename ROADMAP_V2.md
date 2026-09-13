# vibe-view 2.0 — superseded historical release proposal

> This file records the original v1.1-v2.0 proposal. Its "shipped" and
> "feature complete" labels are not current feature status, and its version
> numbers and codenames never corresponded to a vibe-view release. Use the
> [roadmap](docs/roadmap.md) for what has shipped and what is planned.

```
v1.0  "Fukui's Fox"         ✅ shipped
v1.1  "Mulliken's Mole"     ✅ shipped
v1.2  "Hoffmann's Hawk"     ✅ shipped
v1.3  "Karplus's Kestrel"   ✅ shipped
v1.4  "Coulson's Condor"    ✅ shipped
v1.5  "Bragg's Bear"        ✅ shipped
v1.6  "Drebin's Dragon"     ✅ shipped
v1.7  "Berners-Lee's Bee"   ✅ shipped
v1.8  "Jobs' Jaguar"        ✅ shipped
v1.9  "Turing's Turtle"     ✅ shipped
v2.0  "Pauling's Panther"   ✅ feature complete — June 2026
```

---

## v1.1 — "Mulliken's Mole" ✅ shipped (July 2026)

*Robert Mulliken, molecular orbital theory + population analysis.
The mole digs into input files and surfaces what's inside.*

| # | Feature | Phase | Effort |
|---|---------|-------|--------|
| 1 | **Parse vibe-qc Python input** — extract Molecule/PeriodicSystem, basis, method, functional | B1.1 | 3h |
| 2 | **Open .py as editable** — `vibe-view open input.py` loads structure into editor | B1.2 | 1h |
| 3 | **Import from ASE** — read any ASE-supported format | B1.3 | 2h |
| 4 | **Export vibe-qc script** — save current structure as runnable `input.py` | B2.1 | 3h |
| 5 | **Calculation template picker** — single-point, opt, freq, NEB, TD-DFT, periodic | B2.2 | 2h |
| 6 | **Parameter panel** — method, functional, basis, charge, multiplicity, solvent | B2.3 | 3h |
| 7 | **Distance measurement** — click two atoms, show distance in Å (complete existing partial impl) | A4.1 | 1h |
| 8 | **Angle measurement** — click three atoms, show angle in ° | A4.2 | 1h |
| 9 | **Dihedral measurement** — click four atoms, show dihedral | A4.3 | 1h |
| 10 | **Measurement panel** — persistent list with export | A4.4 | 2h |

**Total: ~19h.** Ships the first workflow: "open input → inspect → edit parameters → export script."

---

## v1.2 — "Hoffmann's Hawk" ✅ shipped (August 2026)

*Roald Hoffmann, Nobel Prize for orbital interaction theory.
The hawk swoops with precision — atom-by-atom editing.*

| # | Feature | Phase | Effort |
|---|---------|-------|--------|
| 1 | **Click-to-select** atoms with glow/wireframe highlight | A1.1 | 3h |
| 2 | **Drag-to-move** selected atoms in 3D (screen-plane + constrained axes) | A1.2 | 4h |
| 3 | **Add atom** — click empty space, place atom (default H, configurable element) | A1.3 | 2h |
| 4 | **Delete atom** — right-click → Delete or Del key | A1.4 | 1h |
| 5 | **Change element** — dropdown on selected atom | A1.5 | 1h |
| 6 | **Element palette** — periodic-table widget for quick element selection | A1.6 | 3h |
| 7 | **Add bond** — click two atoms to bond them | A2.1 | 1h |
| 8 | **Delete bond** — right-click bond → Delete | A2.2 | 1h |
| 9 | **Bond order** — click bond to cycle single → double → triple → aromatic | A2.3 | 1h |
| 10 | **Auto-bonding** — recompute bonds after every edit | A2.4 | 1h |
| 11 | **Undo/redo** stack for all edit operations | A1.7 | 3h |

**Total: ~21h.** Ships the core editor — users can build molecules from scratch.

---

## v1.3 — "Karplus's Kestrel" ✅ shipped (September 2026)

*Martin Karplus, Nobel Prize for multiscale models. The kestrel hovers
effortlessly — fast structure building tools.*

| # | Feature | Phase | Effort |
|---|---------|-------|--------|
| 1 | **Fragment library** — CH3, OH, NH2, Ph, COOH, … insertable at selected atom | A3.1 | 4h |
| 2 | **Add hydrogens** — saturate all open valences | A3.2 | 2h |
| 3 | **Clean geometry** — MMFF94/UFF force-field relaxation | A3.3 | 4h |
| 4 | **Conformer search** — RDKit ETKDG conformer generation | A3.4 | 3h |
| 5 | **Supercell builder** — replicate Nx×Ny×Nz for periodic systems | A3.5 | 2h |
| 6 | **360° turntable export** — render rotating view as MP4 | D1.4 | 2h |
| 7 | **Keyframe camera animation** — define camera positions, interpolate | D2.1 | 4h |
| 8 | **Presentation mode** — fullscreen, bookmarks as slides, arrow-key nav | D2.3 | 3h |

**Total: ~24h.** Ships build tools — users can rapidly construct realistic structures.

---

## v1.4 — "Coulson's Condor" ✅ shipped (October 2026)

*Charles Coulson, pioneer of computational quantum chemistry.
The condor soars above the cluster — job submission and monitoring.*

| # | Feature | Phase | Effort |
|---|---------|-------|--------|
| 1 | **Submit button** — "Run on cluster" → `vq submit input.py` | B3.1 | 2h |
| 2 | **Job monitor** — poll `vq status`, show progress badge in UI | B3.2 | 3h |
| 3 | **Auto-open results** — on completion, `vibe-view from-vq JOB_ID` | B3.3 | 2h |
| 4 | **Queue manager panel** — list recent jobs, re-submit, kill | B3.4 | 3h |
| 5 | **QVF-ready export** — `output_qvf=True`, `write_cube=["density","homo","lumo"]` pre-configured | B2.4 | 1h |
| 6 | **Reaction path movie** — animate NEB images with morphing interpolation | D2.2 | 3h |
| 7 | **Notebook export** — export current view as Jupyter notebook cell | E1.3 | 2h |

**Total: ~16h.** Ships the full compute loop: edit → submit → monitor → view results.

---

## v1.5 — "Bragg's Bear" (November 2026)

*William Henry Bragg, Nobel Prize for X-ray crystallography.
The bear is solid and unshakeable — crystal structure editing.*

| # | Feature | Phase | Effort |
|---|---------|-------|--------|
| 1 | **Cell parameters** — edit a, b, c, α, β, γ with live wireframe update | C1.1 | 3h |
| 2 | **Space group browser** — searchable list of 230 space groups from spglib | C1.2 | 2h |
| 3 | **Fractional coordinate editor** — table of atoms with fractional positions | C1.3 | 2h |
| 4 | **Asymmetric unit → full cell** — expand by space group symmetry | C1.4 | 3h |
| 5 | **Miller plane cutter** — specify (hkl), generate slab with N layers | C2.1 | 3h |
| 6 | **Vacuum padding** — adjustable vacuum gap above/below slab | C2.2 | 1h |
| 7 | **Adsorbate placement** — place molecule on surface at specified site | C2.3 | 2h |

**Total: ~16h.** Ships the crystal builder — vibe-qc's unique strength becomes interactive.

---

## v1.6 — "Drebin's Dragon" (December 2026)

*Robert Drebin, published the seminal volume rendering paper (SIGGRAPH 1988).
The dragon breathes fire — stunning visuals.*

| # | Feature | Phase | Effort |
|---|---------|-------|--------|
| 1 | **Ambient occlusion** — SSAO in the viewport (VTK 9.2+) | D1.1 | 2h |
| 2 | **Shadow mapping** — ground plane shadows for presentation renders | D1.2 | 2h |
| 3 | **Non-photorealistic** — toon/outline shader for textbook-style figures | D1.3 | 3h |
| 4 | **GPU volume rendering** — replace marching cubes with ray-cast volumes | — | 6h |
| 5 | **Material presets** — glossy, matte, glass, metallic rendering styles | — | 2h |
| 6 | **Background environments** — HDR environment maps for realistic lighting | — | 2h |

**Total: ~17h.** Ships Hollywood-quality rendering.

---

## v1.7 — "Berners-Lee's Bee" (January 2027)

*Tim Berners-Lee, inventor of the World Wide Web.
The bee connects the hive — collaboration and sharing.*

| # | Feature | Phase | Effort |
|---|---------|-------|--------|
| 1 | **Shareable URL** — `vibe-view share` generates temporary public link via tunnel | E1.1 | 4h |
| 2 | **Embed widget** — `<iframe>`-embeddable viewer for web pages | E1.2 | 4h |
| 3 | **WebSocket sync** — two viewers see same camera + section in real time | E2.1 | 6h |
| 4 | **Annotations** — draw arrows/text on 3D viewport, visible to collaborators | E2.2 | 4h |

**Total: ~18h.** Ships collaboration features.

---

## v1.8 — "Jobs' Jaguar" (February 2027)

*Steve Jobs, redefined consumer software. The jaguar is sleek and
native — a proper desktop application.*

| # | Feature | Phase | Effort |
|---|---------|-------|--------|
| 1 | **Electron wrapper** — package as native macOS/Windows/Linux app | F1 | 8h |
| 2 | **File associations** — double-click .qvf to open, .py to import | F2 | 2h |
| 3 | **System tray** — minimise to tray, quick-open recent files | F3 | 2h |
| 4 | **Auto-update** — check for new versions on vibe-qc.com | F4 | 2h |
| 5 | **Offline mode** — bundle Three.js locally, no CDN dependency | — | 2h |
| 6 | **Installer** — signed .dmg / .msi / .AppImage | — | 4h |

**Total: ~20h.** Ships as a proper desktop application.

---

## v1.9 — "Turing's Turtle" (March 2027)

*Alan Turing, laid the foundations of computer science. The turtle is
methodical and steady — polish, performance, and documentation.*

| # | Feature | Effort |
|---|---------|--------|
| 1 | **Performance audit** — profile and optimise large-system rendering | 4h |
| 2 | **Test coverage** — push from 395 to 500+ tests | 4h |
| 3 | **Accessibility** — keyboard navigation, screen-reader labels | 3h |
| 4 | **i18n** — extract strings, add German + Japanese translations | 4h |
| 5 | **Video tutorials** — 5× 3-minute screencasts for key workflows | 4h |
| 6 | **API docs** — full Sphinx API reference for the Python SDK | 2h |
| 7 | **Benchmark suite** — reproducible performance benchmarks | 2h |

**Total: ~23h.** Ships a polished, documented, accessible product.

---

## v2.0 — "Pauling's Panther" (April 2027)

*Linus Pauling, the father of molecular structure. The panther embodies
precision, power, and grace — the complete molecular editor.*

Final release: all v1.1–v1.9 features integrated, tested, and documented.
Feature parity with Avogadro 2.0 achieved. The definitive tool for building
molecules and launching vibe-qc calculations.

---

## Timeline

```
Jun 2026  v1.0-v2.0 "Pauling's Panther"   ████████ feature complete
```
