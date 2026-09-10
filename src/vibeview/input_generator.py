"""Generate vibe-qc Python input scripts from a structure.

Used by ``vibe-view export input.py`` and the parameter panel (v1.1).
"""

from __future__ import annotations

from typing import Any

import numpy as np

# QVF structures store positions in Angstrom; vibe-qc Atom()/PeriodicSystem
# take bohr (CODATA 2018, same constant vibe-qc uses internally). Emitting
# Å values verbatim shrinks every generated geometry by 0.529x.
_ANGSTROM_TO_BOHR = 1.0 / 0.529177210903

_TEMPLATES = {
    "single_point": """# {title}
from vibeqc import Atom, Molecule, run_job

mol = Molecule([
{atoms}
])

run_job(
    mol,
    basis="{basis}",
    method="{method}",
    functional="{functional}",
    charge={charge},
    multiplicity={multiplicity},
    output="{output}",
    output_qvf=True,
    write_molden_file=True,
)
""",
    "optimization": """# {title} — geometry optimization
from vibeqc import Atom, Molecule, run_job

mol = Molecule([
{atoms}
])

run_job(
    mol,
    basis="{basis}",
    method="{method}",
    functional="{functional}",
    charge={charge},
    multiplicity={multiplicity},
    output="{output}",
    output_qvf=True,
    write_molden_file=True,
    optimize=True,
)
""",
    "frequencies": """# {title} — vibrational frequencies
from vibeqc import Atom, Molecule, run_job

mol = Molecule([
{atoms}
])

run_job(
    mol,
    basis="{basis}",
    method="{method}",
    functional="{functional}",
    charge={charge},
    multiplicity={multiplicity},
    output="{output}",
    output_qvf=True,
    write_molden_file=True,
    hessian=True,
)
""",
    "full": """# {title} — full analysis
from vibeqc import Atom, Molecule, run_job

mol = Molecule([
{atoms}
])

run_job(
    mol,
    basis="{basis}",
    method="{method}",
    functional="{functional}",
    charge={charge},
    multiplicity={multiplicity},
    output="{output}",
    output_qvf=True,
    write_cube=["density", "homo", "lumo"],
    write_molden_file=True,
    optimize=True,
    hessian=True,
)
""",
    "periodic": """# {title} — periodic calculation
import numpy as np
from vibeqc import Atom, BasisSet, PeriodicSystem, run_periodic_job

# Lattice matrix in bohr — COLUMNS are the lattice vectors
# (vibe-qc PeriodicSystem convention).
cell = np.array([
{lattice}
])

system = PeriodicSystem(
    {dimensionality},
    cell,
    [
{atoms}
    ],
    charge={charge},
    multiplicity={multiplicity},
)

basis = BasisSet(system.unit_cell_molecule(), "{basis}")

run_periodic_job(
    system,
    basis=basis,
    method="{method}",
    functional="{functional}",
    output="{output}",
    output_qvf=True,
    write_density=True,
)
""",
}


def _structure_calculation_params(
    sdata: Any, *, atoms: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Translate one QVF structure into shared input-generation parameters.

    QVF records periodicity per lattice axis, while ``PeriodicSystem(dim)``
    treats the first ``dim`` lattice columns as periodic. Non-prefix PBC flags
    therefore cannot be represented without reordering the structure; reject
    them instead of silently generating a calculation with different physics.
    """
    if atoms is None:
        atoms = []
        for atom in sdata.atoms:
            position = atom.position
            coordinates = position.tolist() if hasattr(position, "tolist") else list(position)
            atoms.append(
                {
                    "symbol": atom.symbol,
                    "atomic_number": int(atom.atomic_number or 0),
                    "position": coordinates,
                }
            )

    params: dict[str, Any] = {"atoms": list(atoms)}
    raw_pbc = getattr(sdata, "pbc", None)
    if raw_pbc is None:
        pbc = (False, False, False)
    else:
        pbc = tuple(bool(value) for value in raw_pbc)
        if len(pbc) != 3:
            raise ValueError("Periodic structure PBC must contain exactly three axes")
    if not any(pbc):
        return params

    dimensionality = sum(pbc)
    expected_pbc = tuple(axis < dimensionality for axis in range(3))
    if pbc != expected_pbc:
        raise ValueError(
            "vibe-qc PeriodicSystem requires periodic axes in a/b/c prefix; "
            f"cannot represent pbc={list(pbc)}"
        )

    raw_lattice = getattr(sdata, "lattice_vectors", None)
    if raw_lattice is None:
        raise ValueError("Periodic structure has no lattice vectors")
    lattice = np.asarray(raw_lattice, dtype=float)
    if lattice.shape != (3, 3) or not np.all(np.isfinite(lattice)):
        raise ValueError("Periodic structure lattice must be a finite 3x3 matrix")
    params["lattice_vectors"] = lattice.tolist()
    params["dimensionality"] = dimensionality
    return params


def generate_input(
    atoms: list[dict[str, Any]],
    *,
    basis: str = "sto-3g",
    method: str = "rks",
    functional: str = "PBE",
    charge: int = 0,
    multiplicity: int = 1,
    output: str = "output",
    template: str = "single_point",
    lattice_vectors: list[list[float]] | None = None,
    dimensionality: int | None = None,
    title: str | None = None,
    live_checkpoint: bool = False,
) -> str:
    """Generate a vibe-qc Python input script as a string.

    Parameters
    ----------
    atoms : list of dict
        Each dict has ``symbol``, ``position`` [x,y,z], ``atomic_number``.
    template : str
        One of ``"single_point"``, ``"optimization"``, ``"frequencies"``,
        ``"full"``, ``"periodic"``.
    lattice_vectors : list of list of float, optional
        Three QVF lattice-vector rows in Angstrom. Supplying them selects the
        periodic template and emits the transposed, bohr-valued cell matrix.
    dimensionality : int, optional
        Number of periodic axes for ``PeriodicSystem``. Defaults to 3 for
        backward compatibility when callers provide a lattice without this
        metadata.
    live_checkpoint : bool
        Add ``checkpoint_qvf`` / ``checkpoint_every`` to the runner call so
        the job rewrites a live snapshot at ``$VQ_WORKDIR/checkpoint.qvf``
        as it runs — the path/filename the vq daemon advertises in its
        status JSON and vibe-view's "Watch live" opens (M4 streaming).
    """
    atom_lines = []
    for a in atoms:
        # QVF positions are Å; vibe-qc Atom() expects bohr.
        pos = [c * _ANGSTROM_TO_BOHR for c in a["position"]]
        atom_lines.append(
            f"    Atom({a['atomic_number']}, [{pos[0]:.6f}, {pos[1]:.6f}, {pos[2]:.6f}]),  # bohr"
        )

    if lattice_vectors and template != "periodic":
        template = "periodic"
    if template == "periodic" and not lattice_vectors:
        raise ValueError("Periodic input generation requires lattice vectors")

    if dimensionality is None or template != "periodic":
        periodic_dimensionality = 3
    else:
        if isinstance(dimensionality, bool):
            raise ValueError("Periodic dimensionality must be an integer from 1 to 3")
        try:
            periodic_dimensionality = int(dimensionality)
        except (TypeError, ValueError) as exc:
            raise ValueError("Periodic dimensionality must be an integer from 1 to 3") from exc
        if periodic_dimensionality != dimensionality or not 1 <= periodic_dimensionality <= 3:
            raise ValueError("Periodic dimensionality must be an integer from 1 to 3")

    tpl = _TEMPLATES.get(template, _TEMPLATES["single_point"])

    script = tpl.format(
        title=title or "vibe-qc calculation",
        atoms="\n".join(atom_lines),
        basis=basis,
        method=method,
        functional=functional,
        charge=charge,
        multiplicity=multiplicity,
        output=output,
        lattice=_format_lattice(lattice_vectors) if lattice_vectors else "",
        dimensionality=periodic_dimensionality,
    )
    if live_checkpoint:
        # Snapshot into the vq scratch workdir under the fixed filename the
        # daemon reports as checkpoint_qvf_path (vq.status
        # CHECKPOINT_QVF_FILENAME); falls back to cwd outside vq.
        script = script.replace(
            "    output_qvf=True,",
            "    output_qvf=True,\n"
            '    checkpoint_qvf=os.path.join(os.environ.get("VQ_WORKDIR", "."), '
            '"checkpoint.qvf"),\n'
            "    checkpoint_every=1,",
            1,
        )
        script = script.replace("from vibeqc import", "import os\n\nfrom vibeqc import", 1)
    return script


def generate_input_script(template: str, params: dict[str, Any]) -> str:
    """Generate a vibe-qc input script from a template name and params dict.

    Convenience wrapper used by the ``export_py`` and ``vq_submit_job``
    controllers in ``app.py``.  The params dict carries the same keys as
    :func:`generate_input`.
    """
    atoms = params.get("atoms", [])
    lattice = params.get("lattice_vectors")
    return generate_input(
        atoms,
        basis=params.get("basis", "sto-3g"),
        method=params.get("method", "rhf"),
        functional=params.get("functional", ""),
        charge=params.get("charge", 0),
        multiplicity=params.get("multiplicity", 1),
        output=params.get("output", "output"),
        template=template,
        lattice_vectors=lattice,
        dimensionality=params.get("dimensionality"),
        title=params.get("title"),
        live_checkpoint=bool(params.get("live_checkpoint", False)),
    )


def _format_lattice(vectors: list[list[float]]) -> str:
    """Format lattice vectors as a numpy matrix string for PeriodicSystem.

    QVF carries lattice vectors as ROWS in Å; vibe-qc's PeriodicSystem
    expects a matrix whose COLUMNS are the vectors, in bohr (see the
    pybind docstring / ewald.cpp `lattice.col(k)`). Transpose + convert.
    """
    vecs = [[c * _ANGSTROM_TO_BOHR for c in v] for v in vectors[:3]]
    rows = []
    for i in range(3):
        rows.append(f"    [{vecs[0][i]:.6f}, {vecs[1][i]:.6f}, {vecs[2][i]:.6f}],")
    return "\n".join(rows)
