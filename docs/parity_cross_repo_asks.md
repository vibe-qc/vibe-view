# Cross-repo asks for Avogadro-parity (vibe-view side)

These are the changes outside the vibe-view tree that the parity roadmap
([ROADMAP_AVOGADRO_PARITY.md](ROADMAP_AVOGADRO_PARITY.md)) depends on. Each is
written as a **self-contained prompt** to hand to the owning chat. All three
are designed so vibe-view is **not blocked**: it builds against the documented
interim contract and upgrades when the ask lands.

Routing (per CLAUDE.md §3 drop-box / §11 escalate / §15 vq):
- **Ask 1 & 2** → molecular-methods chat + periodic-SCF chat (they co-own the
  QVF writer `python/vibeqc/output/formats/qvf.py`, the schema in
  `python/vibeqc/output/formats/`, and `run_job` / `run_periodic_job`).
- **Ask 3** → vq queue chat (owns `vibe-queue/` and `agent_interaction.md`).

---

## Ask 1 — QVF v3 schema extension (streaming + biomolecule metadata)

> **Context.** vibe-view is building Avogadro-2.0 parity with a heavy focus on
> visualizing *running* vibe-qc/vq jobs and biomolecules. Two capabilities need
> the QVF manifest to carry fields it doesn't have today. The schema lives in
> `python/vibeqc/output/formats/qvf_manifest.schema.json` (+ `_v2`); this asks
> for a `qvf_manifest_v3.schema.json` (bump `qvf_version` to 3).
>
> **Ask A — streaming / checkpoint fields** (for live job visualization):
> - `provenance.run_status`: enum `"running" | "converged" | "failed"`.
> - `provenance.checkpoint`: object `{seq: int (monotonic), wall_time_s: number,
>   written_at: string (ISO-8601)}` — lets a reader tell a fresh snapshot from a
>   stale one without diffing bytes.
> - per-section optional `partial: boolean` (default false) — e.g. an
>   optimization trajectory still growing, or an SCF-history section not yet at
>   its final point.
>
> **Ask B — biomolecule metadata** (for cartoon/ribbon rendering) on the
> `structure` section:
> - `residues`: array of `{name: string, seq: int, chain: string,
>   atom_indices: [int]}`.
> - `chains`: array of chain-id strings.
> - `secondary_structure` (optional): array of `{type: "helix"|"sheet"|"coil",
>   chain: string, start_seq: int, end_seq: int}`.
> - `b_factors` (optional): array of `number`, one per atom, parallel to the
>   section's atom order. **Added 2026-07-26.** The ribbon can already be
>   coloured by chain and by secondary structure; colour-by-b-factor is the
>   remaining mode in roadmap D4 and is the one thing vibe-view cannot infer,
>   because it is experimental data rather than geometry. Nothing in vibe-view
>   blocks on it — the mode simply will not be offered until some producer path
>   supplies the field. Note that for a *computed* structure there is no
>   b-factor at all; this is mainly about preserving the column through
>   `pdb_to_qvf`, so treat vibe-qc-side emission as optional.
>
> All new fields are **optional and additive** — a v2 reader ignores them, and a
> v3 file with none of them is a valid plain structure. Please keep them optional
> in the schema so producers adopt incrementally.
>
> **Interim contract vibe-view uses until this lands:** the viewer infers
> run-status from file mtime + a `.running` sentinel if present, and populates
> residue/chain fields itself at import time from PDB records (secondary
> structure via a geometry-based DSSP-lite). So this ask is *non-blocking* — it
> makes vibe-qc-produced QVFs first-class for these features rather than
> PDB-import-only.
>
> **Status 2026-07-26: that interim contract is now implemented, not planned.**
> `pdb_to_qvf` preserves atom name / residue name / residue seq / chain id, and
> `StructureData` exposes `has_residues`, `chains()`, `backbone_trace()` and
> `secondary_structure()` (CA-only H/E/C). The cartoon representation consumes
> all of it. So Ask B would now let a vibe-qc-produced QVF *skip* per-open
> re-derivation and, more usefully, carry a **producer-supplied** assignment
> that the viewer would prefer over its own geometric guess — worth knowing if
> you are weighing the schema bump. Two things the viewer's own inference cannot
> recover and that Ask B should therefore keep: a distinction between
> alpha/pi/3-10 helices, and beta-bridge versus sheet.
>
> **Acceptance:** `qvf_manifest_v3.schema.json` committed with the fields above,
> `qvf.py` writer accepting/emitting them when provided, and
> `tests/` coverage that a v3 file with the new fields validates and a v3 file
> without them still validates. Please reply with the final field names/types if
> they differ from the proposal so vibe-view matches exactly.

### CLOSED 2026-07-26 — both halves landed, and there is no v3

**Ask A (streaming / checkpoint): landed 2026-07-02, `1eb02de1`.**
`provenance.run_status`, `provenance.checkpoint`, and per-section `partial` are
emitted by `write_qvf` and by the opt-in live checkpointer, and the recognized
`run_status` value set gained `pending` on 2026-07-25 for an archive describing a
job not yet run. Viewer side: `QVFReader.run_status`, `checkpoint_info`,
`section_is_partial`.

**Ask B (biomolecule metadata): landed 2026-07-26, `da55109fd` (producer) and
the commit that follows it (viewer).** Field names are **exactly as proposed** —
`residues` `{name, seq, chain, atom_indices}`, `chains`, `secondary_structure`
`{type, chain, start_seq, end_seq}`, `b_factors` — plus one addition: a payload
atom may carry a per-atom `b_factor` (PDB cols 61-66), which `pdb_to_qvf` now
preserves. Every field is optional; a structure carrying none of them is a valid
plain structure. They are now **named in the schema** rather than merely riding
an open Section object: `$defs/SectionStructure` gained the four properties, and
`$defs/BiomoleculeResidue` / `$defs/BiomoleculeSecondaryStructure` give the item
shapes. `$defs/StructureAtom` gained `b_factor`.

**There is no `qvf_manifest_v3.schema.json`, and `qvf_version` stays 1.** This is
the one point on which the answer differs from the ask, so it is worth stating
plainly. Every field here is optional, which the versioning policy makes the
*additive* case, and the specification requires a consumer that only understands
`qvf_version = 1` to **refuse** a file with a higher major version. A bump would
therefore have broken every conforming third-party consumer over fields they
were explicitly free to ignore. That is not a new judgement: `qvf_version: 2` was
withdrawn on 2026-07-10 for exactly this reason, and this decision is recorded
alongside it in `qvf-writer/GOVERNANCE.md`. **Detect these fields by presence,
never by version** — which is what the viewer does.

**Precedence, as requested.** Spec § 5.1 now says normatively that a consumer
which can also derive residues / chains / secondary structure **MUST** prefer
producer-supplied values over its own inference, and that a per-atom `b_factor`
wins over the section-level array. The viewer implements this: `chains()` and
`secondary_structure()` prefer supplied values, the latter **per chain** so a
partially annotated file keeps geometry on its unannotated chains; `chain_ids()`
returns the supplied chain order; a `b_factors` array whose length disagrees
with the atom count is ignored whole rather than applied partially. Tests:
`tests/test_qvf_biomolecule.py` (producer, including that the schema now rejects
malformed values) and `vibe-view/tests/test_biomolecule_metadata.py` (viewer
precedence + b-factor path).

**Still open:** nothing in Ask B. Producer *emission* of these fields from a real
calculation remains optional and unwired, as the ask itself allowed: a computed
structure has no b-factor and vibe-qc has no PDB input path today. The writer
accepts them through `biomolecule_data` for any producer that does.

---

## Ask 2 — Producer checkpoint emission from `run_job` / `run_periodic_job`

> **Context.** For live job visualization, vibe-view wants to hot-reload a QVF as
> a calculation progresses (SCF convergence climbing, optimization trajectory
> growing, geometry morphing to the relaxed structure). Today `run_job`
> (`python/vibeqc/runner.py`) and `run_periodic_job`
> (`python/vibeqc/periodic_runner.py`) only write the QVF once, at the end, via
> `write_qvf` in `python/vibeqc/output/formats/qvf.py`. You already have the
> incremental machinery for this — `ManifestUpdater` in
> `python/vibeqc/output/manifest.py` (`mark_written` / `update_wall_seconds` /
> `finish` / `crash`) and the `OutputWriter` dispatch.
>
> **Ask.** Add opt-in checkpointing to both runners:
> - New kwargs `checkpoint_qvf: str | Path | None = None` and
>   `checkpoint_every: int = 0` (0 = off; N = write/refresh the checkpoint QVF
>   every N SCF or optimization iterations).
> - When enabled, periodically write a QVF snapshot to `checkpoint_qvf` carrying
>   whatever has converged so far, with `provenance.run_status="running"`, a
>   monotonic `provenance.checkpoint.seq`, and any still-growing section marked
>   `partial: true` (the Ask-1 fields).
> - On completion write the final QVF with `run_status="converged"`; on failure,
>   `run_status="failed"` (mirrors the existing `ManifestUpdater.crash`).
> - Writes should be atomic (temp file + rename) so a reader never sees a
>   half-written zip.
>
> **Interim contract vibe-view uses until this lands:** vibe-view watches the
> job's normal output QVF and hot-reloads on mtime change with a settle-delay +
> content-hash guard — so *any* producer that rewrites the QVF already streams;
> this ask makes it first-class, atomic, and status-labeled. Non-blocking.
>
> **Acceptance:** both runners accept the kwargs; a short SCF with
> `checkpoint_every=1` produces a sequence of readable QVFs whose `checkpoint.seq`
> increases and whose final file is `run_status="converged"`; atomic-write
> verified (no torn reads under a concurrent reader). Coordinate the exact
> cadence/size policy with the vq queue chat (Ask 3) so checkpoints don't
> hammer the shared filesystem.

---

## Ask 3 — vq conventions for live job monitoring

> **Context.** vibe-view is becoming a vq cockpit: submit a job from the viewer,
> watch it run, and live-stream its results (see Asks 1–2). It already uses
> `vq.listing.list_jobs`, `vq.fetch.fetch_local`, and submits via payloads per
> the `agent_interaction.md` protocol (§15 — never writing to the shared
> checkouts). To monitor a *running* job it needs a few things the queue can
> expose.
>
> **Ask.**
> 1. **Progress-visible status.** Does `vq status <job>` already surface
>    running-state detail (current iteration / elapsed / a progress hint), and is
>    there a stable programmatic form (JSON) vibe-view can poll? If not, a
>    machine-readable `vq status --json` with `state`, `submitted_at`,
>    `started_at`, and (when available) a progress field.
> 2. **Log tail access.** A supported way to read a running job's stdout/stderr
>    tail programmatically (e.g. `vq logs <job> --tail N`) for the monitor panel.
> 3. **Checkpoint QVF discovery.** When a job opts into checkpointing (Ask 2),
>    vibe-view needs the path of the live checkpoint QVF inside `$VQ_WORKDIR`
>    from the job status/handle, so it can watch it without guessing. Propose:
>    surface the workdir + a conventional checkpoint filename in the status
>    output.
> 4. **(nice-to-have) Job handle back-reference.** Agree a convention for
>    recording `{job_id, host, submitted_at}` so an opened result links back to
>    its queue entry (vibe-view can also cache this client-side if you'd rather
>    not touch the QVF).
>
> **Interim contract vibe-view uses until this lands:** poll `list_jobs` + parse
> `vq status` text, tail logs via `vq logs` if it exists, and fall back to
> watching the fetched output dir. Non-blocking; this ask makes monitoring
> robust and removes text-parsing fragility.
>
> **Acceptance:** documented in `vibe-queue/docs/agent_interaction.md` (the
> queue chat owns it): the JSON status shape, the log-tail command, and the
> checkpoint-path convention. Please also weigh in on checkpoint cadence/size
> policy from Ask 2 (filesystem-load concerns on remote compute hosts).
