# Release codenames

Every vibe-view minor release carries a **"Person's Animal"** codename. It is
printed by `vibe-view --version`, shown in the browser and desktop About
boxes, and stamped on the CHANGELOG section for the release.

## Why the form is shared and the pool is not

vibe-qc, vibe-view and vibe-queue came out of one monorepo and are meant to
read as one family of products. Keeping the *form* — a scientist's surname,
possessive, then an animal, usually alliterating — is what makes a reader
recognise a vibe-* release from the name alone.

Keeping the *pools* separate is what stops two products claiming the same
person. Each draws from the field it belongs to:

| Product | Pool |
|---|---|
| **vibe-qc** | Quantum chemistry and electronic-structure theory |
| **vibe-view** | Visualization, computer graphics and crystallographic imaging |
| **vibe-queue** | *(its own; not this project's to assign)* |

An animal is used once across the whole family, not once per product.

## How a name resolves

The catalogue lives in {mod}`vibeview.codenames` and is the single source of
truth — the CLI, both About boxes and this documentation site all read it.
`RELEASE_CODENAMES` maps `X.Y.Z` to a name, and
`codename_for_version()` resolves in this order:

1. Strip any PEP-440 `.devN` / `aN` / `bN` / `rcN` suffix and local version
   label, so a dev build inherits the codename of the release it leads up to.
2. Look up the resulting `X.Y.Z`.
3. Fall back to the parent minor, `X.Y.0` — so patch releases inherit without
   needing an entry of their own.
4. Return `None` if the minor has no name; the surfaces then print the bare
   version rather than a dangling em dash.

A patch release only needs its own entry when it has a distinct theme worth
surfacing separately.

## The catalogue

| Version | Codename | For |
|---|---|---|
| 2.15.0 | *Roothaan's Roadrunner* | The name v2.15.2 shipped with |
| 2.16.0 | *Sayle's Starling* | vibe-view becomes a product you can adopt on its own |
| 2.17.0 | *Lorensen's Loon* | TREXIO molecular orbitals in the isosurface workflow |
| 2.18.0 | *Richardson's Robin* | Biomolecules, secondary structure, cartoons — **approved, not yet released** |
| 2.19.0 | *Phong's Pheasant* | Materials and lighting — **approved, not yet released** |
| 2.20.0 | *Levoy's Lemur* | Transfer functions and direct volume rendering — **approved, not yet released** |
| 3.0.0 | *Levinthal's Lynx* | Held for a major version about the interactive viewer itself — **approved, not yet released** |

:::{note}
*Roothaan's Roadrunner* predates the split and the per-product pools, and it
borrows a quantum-chemistry figure that vibe-qc had already used (*Roothaan's
Raven*, v0.1.0). It is in the wild — in the CLI, in the desktop About box and
in a tagged wheel — so it stands as history, by maintainer decision on
2026-09-09. Names from v2.16.0 on come from the pool below.
:::

**Six names are registered ahead of their cuts**, all approved by the
maintainer on 2026-09-09 — v2.16.0 so its artwork brief could be written
against it, and v2.17.0 through v3.0.0 as a settled slate.

Registered is not scheduled. A version resolves to its name whenever it is
cut, and an entry here commits to nothing about when, or whether, that release
happens. What each name *is for* is recorded in the catalogue above and in the
comment on its entry in `RELEASE_CODENAMES`.
Roger Sayle's RasMol (1992) was the first molecular viewer a scientist could
simply install and run — free, and without the program that produced the data.
That is the release: vibe-view becoming a product you can adopt on its own.
Starlings are ordinary and gregarious, and their murmurations are the standard
image of structure emerging from many small parts.

### v2.15.0 — Roothaan's Roadrunner

```{figure} _static/images-codenames/01-vibe-view-v2.15.0-roothaans-roadrunner.png
:alt: AI-generated Roothaan's Roadrunner artwork: a real greater roadrunner strides right beside four separated molecular clusters on a pale reflective surface.
:width: 100%
:figclass: codename-art

v2.15.0 "Roothaan's Roadrunner" — the monorepo splits into four repositories.
```

### v2.16.0 — Sayle's Starling

```{figure} _static/images-codenames/02-vibe-view-v2.16.0-sayles-starling.png
:alt: AI-generated Sayle's Starling artwork: a green and purple iridescent starling with a yellow bill stands independently beside silver spheres condensing into a molecular fragment.
:width: 100%
:figclass: codename-art

v2.16.0 "Sayle's Starling" — a product you can adopt on its own.
```

### v2.17.0 — Lorensen's Loon

```{figure} _static/images-codenames/03-vibe-view-v2.17.0-lorensens-loon.png
:alt: AI-generated Lorensen's Loon codename artwork for vibe-view v2.17.0: a red-eyed breeding loon surfaces through teal water, with triangular mesh edges and a glowing sampling grid below.
:width: 100%
:figclass: codename-art

v2.17.0 "Lorensen's Loon".
```

### v2.18.0 — Richardson's Robin

```{figure} _static/images-codenames/04-vibe-view-v2.18.0-richardsons-robin.png
:alt: AI-generated Richardson's Robin codename artwork for vibe-view v2.18.0: a European robin perches on teal beta-strand arrows and violet alpha helices in a pale studio.
:width: 100%
:figclass: codename-art

v2.18.0 "Richardson's Robin".
```

### v2.19.0 — Phong's Pheasant

```{figure} _static/images-codenames/05-vibe-view-v2.19.0-phongs-pheasant.png
:alt: AI-generated Phong's Pheasant codename artwork for vibe-view v2.19.0: a male ring-necked pheasant stands under amber and blue light beside matte, mirror-like and glossy shading spheres.
:width: 100%
:figclass: codename-art

v2.19.0 "Phong's Pheasant".
```

### v2.20.0 — Levoy's Lemur

```{figure} _static/images-codenames/06-vibe-view-v2.20.0-levoys-lemur.png
:alt: AI-generated Levoy's Lemur codename artwork for vibe-view v2.20.0: an amber-eyed ring-tailed lemur contemplates a soft translucent teal and violet volume cloud.
:width: 100%
:figclass: codename-art

v2.20.0 "Levoy's Lemur".
```

### v3.0.0 — Levinthal's Lynx

```{figure} _static/images-codenames/07-vibe-view-v3.0.0-levinthals-lynx.png
:alt: AI-generated Levinthal's Lynx codename artwork for vibe-view v3.0.0: a Eurasian lynx with long ear tufts and broad paws faces forward, with molecules reflected in its amber eyes and particle networks around its ruff.
:width: 100%
:figclass: codename-art

v3.0.0 "Levinthal's Lynx".
```

## Proposed pool

:::{warning}
**A pool, not a schedule.** Codenames are a maintainer decision. Six names
have been picked out of this pool already; their rows below are marked
**taken** with the version that holds them, and the catalogue at the top of
this page is the authority. Every unmarked row is unclaimed, and no release
should be tagged against one until the maintainer picks it.

Mark the row in the same commit that registers the name. A prose note saying
an animal is spent does not help the next picker, because what they read is
the table: they choose it, write the rationale, and only then does
`test_the_catalogue_never_reuses_an_animal` tell them it collides.
:::

The field vibe-view belongs to is the one that turned computed numbers into
something you can look at. Three strands, all of them ancestors of this
program:

**Molecular graphics** — the people who first made a molecule something you
could rotate on a screen.

| Person | Contribution | Proposed animal |
|---|---|---|
| Cyrus Levinthal | The first interactive molecular graphics system (MIT, 1965) | Lynx — **taken, v3.0.0** |
| Robert Langridge | Interactive molecular graphics for biology, 1960s–70s | Lark |
| Jane Richardson | The ribbon diagram (1981) — the cartoon this viewer draws | Robin — **taken, v2.18.0** |
| Michael Connolly | The solvent-excluded molecular surface (1983) | Condor |
| Roger Sayle | RasMol (1992) — the first molecular viewer anyone could just install | Starling — **taken, v2.16.0** |
| Nelson Max | Early molecular surface rendering | Marten |

**Computer graphics** — the algorithms the renderer is literally built out of.

| Person | Contribution | Proposed animal |
|---|---|---|
| William Lorensen | Marching cubes (1987); a founder of VTK, which this viewer renders with | Loon — **taken, v2.17.0** |
| Will Schroeder | VTK co-creator, *The Visualization Toolkit* | Stork |
| Bui Tuong Phong | The Phong reflection model — the shading a molecular scene uses | Pheasant — **taken, v2.19.0** |
| Jim Blinn | Blinn–Phong; blobby models, i.e. isosurfaces of summed densities | Bittern |
| Marc Levoy | Direct volume rendering (1988) | Lemur — **taken, v2.20.0** |
| Turner Whitted | Recursive ray tracing (1980) | Wren |
| James Kajiya | The rendering equation (1986) | Kite |
| Edwin Catmull | Z-buffer, texture mapping, subdivision surfaces | Curlew |
| Ivan Sutherland | Sketchpad (1963) | Swift |
| Ken Perlin | Perlin noise | Petrel |
| Arthur Appel | The first ray casting (1968) | Auk |

**Crystallographic imaging** — seeing structure that no microscope could
resolve.

| Person | Contribution | Proposed animal |
|---|---|---|
| Kathleen Lonsdale | X-ray crystallography; proved benzene planar (1929) | Linnet |
| Dorothy Hodgkin | Structures of penicillin, B12, insulin; Nobel 1964 | Heron |
| Rosalind Franklin | Photo 51 | Falcon |
| Max von Laue | X-ray diffraction by crystals; Nobel 1914 | Lapwing (Lark is Langridge's) |
| A. Lindo Patterson | The Patterson function | Pelican |
| Herbert Hauptman | Direct methods; Nobel 1985 | Hare |

**Data visualization**, if a release is ever about the charts rather than the
3-D scene: Jacques Bertin (Bittern), Edward Tufte (Toucan), John Tukey
(Tapir).

Names already spent by vibe-qc, and therefore unavailable: Raven, Eagle, Wolf,
Magpie, Hedgehog, Otter, Owl, Kingfisher, Goose, Cheetah, Puffin, Tern,
Badger, Raccoon, Sloth, Axolotl, Llama.

## Names taken out of the pool

Every name below is registered in `RELEASE_CODENAMES` and appears in the
catalogue at the top of this page. They are kept here with their reasoning
because the *why* is the point: each is tied to what that release would
actually be about, the way the vibe-qc series does it, and that is what keeps
the names from being decoration.

**v2.16.0 — *Sayle's Starling*** — see above.

**v2.17.0 — *Lorensen's Loon***
: For a release centred on isosurfaces and volume rendering. William Lorensen
  gave us marching cubes (1987) — the algorithm behind every density and
  orbital surface this viewer draws — and co-founded VTK, which it renders
  with. The most directly earned name in the pool. A loon dives and surfaces.

**v2.18.0 — *Richardson's Robin***
: For biomolecular work — chains, residues, secondary structure, cartoons.
  Jane Richardson invented the ribbon diagram in 1981, and it is still what
  the viewer draws when a `structure` section carries secondary structure.

**v2.19.0 — *Phong's Pheasant***
: For materials and lighting, anchoring a release in the renderer rather than
  in the product. Bui Tuong Phong's reflection model is how a molecular scene
  is lit.

**v2.20.0 — *Levoy's Lemur***
: For transfer functions. Marc Levoy's direct volume rendering (1988) takes
  colour and opacity straight from the data without extracting a surface —
  the other half of the volume story from Lorensen's.

**v3.0.0 — *Levinthal's Lynx***
: For a major version, if one is about the interactive viewer itself. Cyrus
  Levinthal built the first interactive molecular graphics system in 1965, on
  hardware that had no business doing it. A lynx is what you name the thing
  that sees in the dark.

Two consequences for the pool above:

* **Lynx is spent**, so Kathleen Lonsdale's entry above now reads *Linnet*
  alone. It offered *Lynx / Linnet* until v3.0.0 took Lynx; leaving the
  alternative in the table would have handed the next picker a collision the
  test only catches after they have written the rationale. Every other
  registered name is now marked **taken** in the pool tables for the same
  reason.
* **Lark was offered twice** — to Robert Langridge and to Max von Laue —
  which the one-animal-per-family rule forbids and no test can see, since
  neither is registered. Von Laue's row now proposes *Lapwing*.
* *Blinn's Bittern* (blobby models — isosurfaces of summed atomic densities,
  which is exactly what a density surface is) remains the strongest unclaimed
  candidate for a renderer-anchored release.

## Adding a name at release time

In the same commit that bumps the version:

1. Add the `X.Y.0` entry to `RELEASE_CODENAMES` in
   `src/vibeview/codenames.py`, with a comment saying who the person is and
   why the release earned the name.
2. Update the trailing comment on `__version__` in `src/vibeview/__init__.py`.
3. Promote `## [Unreleased]` in `CHANGELOG.md` to
   `## [X.Y.Z] - YYYY-MM-DD - *Person's Animal*`.
4. Add the row to the catalogue table on this page.

`tests/test_release_codenames.py` checks that every surface agrees with the
catalogue — the CLI, the browser About box, the Electron fallback string and
the version comment. Before the catalogue existed the name was pasted into
each by hand, and nothing could see them drift.

## Artwork

Each release gets a 1672 × 941 image in one of the two house treatments the
vibe-qc series established. The brief for the next one is written before the
release and lives with private release coordination outside this checkout. The images above are AI-generated
artwork, separate from the real software captures used in the manual.
