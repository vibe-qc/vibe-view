"""Basic molecular ORCA inputs, independent of ORCA and queue installations.

Syntax and resource units follow the ORCA 6.1 manual:
https://www.faccts.de/docs/orca/6.1/manual/contents/essentialelements/input.html
https://www.faccts.de/docs/orca/6.1/manual/contents/essentialelements/parallel.html
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from vibeview.elements import SYMBOL_TO_Z

if TYPE_CHECKING:
    from vibeview.qvf import StructureData

ORCA_METHODS = ("HF", "PBE", "PBE0", "B3LYP", "BP86", "TPSS", "r2SCAN")
ORCA_BASES = ("STO-3G", "def2-SVP", "def2-TZVP", "def2-TZVPP", "def2-QZVP", "cc-pVDZ")
ORCA_TASKS = {
    "single_point": "SP",
    "optimization": "Opt",
    "frequencies": "Freq",
    "full": "Opt Freq",
}


def _integer(value: Any, label: str, *, minimum: int | None = None) -> int:
    # Text fields arrive as strings. Do not silently truncate fractional
    # charge/spin/resource values or replace an explicitly entered zero.
    if isinstance(value, bool) or not re.fullmatch(r"[+-]?\d+", str(value).strip()):
        raise ValueError(f"{label} must be an integer")
    result = int(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    return result


@dataclass(frozen=True)
class OrcaInputSettings:
    method: str = "PBE"
    basis: str = "def2-SVP"
    charge: int = 0
    multiplicity: int = 1
    template: str = "single_point"
    nprocs: int = 1
    maxcore: int = 1000
    raman: bool = False
    vcd: bool = False

    @classmethod
    def from_state(cls, state: Any) -> OrcaInputSettings:
        return cls(
            method=state.calc_orca_method,
            basis=state.calc_orca_basis,
            charge=_integer(state.calc_charge, "Charge"),
            multiplicity=_integer(state.calc_multiplicity, "Multiplicity", minimum=1),
            template=state.calc_template,
            nprocs=_integer(state.calc_orca_nprocs, "Processes", minimum=1),
            maxcore=_integer(state.calc_orca_maxcore, "MaxCore (MB per process)", minimum=1),
            raman=getattr(state, "calc_orca_raman", False),
            vcd=getattr(state, "calc_orca_vcd", False),
        )

    @property
    def queue_memory_mb(self) -> int:
        """Reserve headroom: MaxCore is per process, not total peak memory."""
        return math.ceil(self.nprocs * self.maxcore / 0.75)


def generate_orca_input(structure: StructureData, settings: OrcaInputSettings) -> str:
    """Generate a self-contained .inp with Cartesian coordinates in Å.

    HF/DFT keywords let ORCA choose the spin reference from multiplicity.
    Reject unsupported models instead of silently dropping periodicity or
    emitting arbitrary text as ORCA directives.
    """
    if any(structure.pbc):
        raise ValueError("Basic ORCA input supports isolated molecules only; periodic cells "
                         "cannot be exported as molecular calculations")
    if settings.method not in ORCA_METHODS or settings.basis not in ORCA_BASES:
        raise ValueError("Select a supported ORCA method and basis")
    if settings.template not in ORCA_TASKS:
        raise ValueError("Select an ORCA single point, optimization or frequency calculation")
    if type(settings.raman) is not bool or type(settings.vcd) is not bool:
        raise ValueError("Raman and VCD options must be booleans")
    if (settings.raman or settings.vcd) and settings.template not in ("frequencies", "full"):
        raise ValueError("Raman and VCD require a frequency calculation")
    charge = _integer(settings.charge, "Charge")
    multiplicity = _integer(settings.multiplicity, "Multiplicity", minimum=1)
    nprocs = _integer(settings.nprocs, "Processes", minimum=1)
    maxcore = _integer(settings.maxcore, "MaxCore (MB per process)", minimum=1)
    if not structure.atoms:
        raise ValueError("No atoms to export")
    coordinates = []
    nuclear_charge = 0
    for atom in structure.atoms:
        z = SYMBOL_TO_Z.get(atom.symbol)
        if z is None or atom.atomic_number not in (None, z):
            raise ValueError(f"Unsupported or inconsistent element: {atom.symbol!r}")
        position = [float(x) for x in atom.position]
        if len(position) != 3 or not all(math.isfinite(x) for x in position):
            raise ValueError("Each atom needs three finite coordinates in angstrom")
        nuclear_charge += z
        coordinates.append(f"{atom.symbol:2s} " + " ".join(f"{x:.10f}" for x in position))
    electrons = nuclear_charge - charge
    unpaired = multiplicity - 1
    if electrons < 1 or unpaired > electrons or (electrons - unpaired) % 2:
        raise ValueError("Charge and multiplicity are inconsistent with the electron count")
    task = ORCA_TASKS[settings.template]
    # ORCA 6.1 §5.18.3–4: VCD requires analytical frequencies; static
    # polarizability derivatives provide Raman activities during Freq.
    # https://www.faccts.de/docs/orca/6.1/manual/contents/spectroscopyproperties/vibrations.html
    if settings.vcd:
        task = task.replace("Freq", "AnFreq")
    spectroscopy = []
    if settings.raman:
        spectroscopy.append("%elprop Polar 1 end")
    if settings.vcd:
        spectroscopy.append("%freq doVCD true end")
    return "\n".join([
        "# Generated by vibe-view; Cartesian coordinates in angstrom",
        f"! {settings.method} {settings.basis} TightSCF {task}",
        f"%pal nprocs {nprocs} end",
        f"%maxcore {maxcore}",
        *spectroscopy,
        f"* xyz {charge} {multiplicity}",
        *coordinates,
        "*",
        "",
    ])


def queue_wall_time_seconds(value: Any) -> int:
    """Validate the queue-only time budget independently of saved inputs."""
    return 60 * _integer(value, "Wall time (minutes)", minimum=1)
