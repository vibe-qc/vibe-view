"""Native input bundles. Syntax references and capability limits: docs/input_writers.md.

Coordinates/cells are QVF rows in angstrom; frozen indices are zero based.
Raw text is deliberately executable program input, never a shell command.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from vibeview.elements import SYMBOL_TO_Z
from vibeview.orca_input import _integer

if TYPE_CHECKING:
    from vibeview.qvf import StructureData

PROGRAMS = ("Gaussian", "Psi4", "NWChem", "Q-Chem", "GAMESS", "CP2K", "xtb", "VASP")
TASKS = ("single_point", "optimization", "frequencies", "tddft")
DEFAULTS = {
    "Gaussian": ("PBE1PBE", "def2SVP"),
    "Psi4": ("pbe0", "def2-svp"),
    "NWChem": ("pbe0", "def2-svp"),
    "Q-Chem": ("PBE0", "def2-svp"),
    "GAMESS": ("PBE0", "N31"),
    "CP2K": ("PBE", "DZVP-MOLOPT-SR-GTH"),
    "xtb": ("GFN2-xTB", ""),
    "VASP": ("", ""),
}


@dataclass(frozen=True)
class InputSettings:
    program: str
    method: str = ""
    basis: str = ""
    charge: int = 0
    multiplicity: int = 1
    task: str = "single_point"
    frozen: tuple[int, ...] = ()
    raw_keywords: str = ""
    states: int = 5
    # CP2K needs explicit pseudopotential identifiers for each kind: no
    # electron-count-dependent GTH-q value is guessed from nuclear charge.
    potentials: dict[str, str] = field(default_factory=dict)
    cutoff: float = 400.0


@dataclass(frozen=True)
class InputBundle:
    program: str
    primary: str
    files: dict[str, str]
    warnings: tuple[str, ...] = ()
    command: tuple[str, ...] = ()

    def archive(self) -> bytes:
        """A portable bundle, including auxiliary inputs; no files are executed."""
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, text in self.files.items():
                archive.writestr(name, text)
        return stream.getvalue()


def _token(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_+*(),.=/\-]+", value):
        raise ValueError(f"{label} must be a single program token; use raw keywords for blocks")
    return value


def write_input(structure: StructureData, settings: InputSettings) -> InputBundle:
    """Prepare input; reject unrepresentable physics instead of dropping it.

    The installed program must still validate method/basis availability and raw
    directives. This is an input writer, not a prediction of SCF convergence.
    """
    s = settings
    if s.program not in PROGRAMS or s.task not in TASKS:
        raise ValueError("Select a supported program and calculation type")
    charge = _integer(s.charge, "Charge")
    mult = _integer(s.multiplicity, "Multiplicity", minimum=1)
    states = _integer(s.states, "Excited states", minimum=1)
    if not structure.atoms:
        raise ValueError("No atoms to export")
    symbols = [a.symbol for a in structure.atoms]
    if any(
        a.symbol not in SYMBOL_TO_Z or a.atomic_number not in (None, SYMBOL_TO_Z.get(a.symbol))
        for a in structure.atoms
    ):
        raise ValueError("Unsupported or inconsistent element")
    xyz = np.asarray([a.position for a in structure.atoms], dtype=float)
    if xyz.shape != (len(symbols), 3) or not np.isfinite(xyz).all():
        raise ValueError("Coordinates must be finite Cartesian triples in angstrom")
    electrons = sum(SYMBOL_TO_Z[x] for x in symbols) - charge
    if electrons < 1 or mult - 1 > electrons or (electrons - mult + 1) % 2:
        raise ValueError("Charge/multiplicity inconsistent with electron count")
    frozen = sorted(set(s.frozen))
    if any(type(i) is not int or i < 0 or i >= len(symbols) for i in frozen):
        raise ValueError("Frozen atom indices must be zero-based integers within the structure")
    pbc = tuple(bool(x) for x in structure.pbc)
    if len(pbc) != 3:
        raise ValueError("PBC must contain three axes")
    periodic = any(pbc)
    cell = None
    if periodic or s.program in ("CP2K", "VASP"):
        if structure.lattice_vectors is None:
            raise ValueError(
                f"{s.program} requires an explicit simulation cell (also for molecules)"
            )
        cell = np.asarray(structure.lattice_vectors, dtype=float)
        if cell.shape != (3, 3) or not np.isfinite(cell).all() or abs(np.linalg.det(cell)) < 1e-10:
            raise ValueError("Cell must be a finite nonsingular 3x3 row matrix in angstrom")
    if periodic and s.program in ("Psi4", "Q-Chem", "GAMESS"):
        raise ValueError(
            f"{s.program} writer supports molecular calculations, not this periodic cell"
        )
    if frozen and s.task != "optimization" and s.program != "VASP":
        raise ValueError(
            "Frozen atoms require optimization; constrained Hessians need explicit expert input"
        )
    method = s.method or DEFAULTS[s.program][0]
    basis = s.basis or DEFAULTS[s.program][1]
    if method:
        _token(method, "Method")
    if basis:
        _token(basis, "Basis")
    if not np.isfinite(s.cutoff) or s.cutoff <= 0:
        raise ValueError("Plane-wave cutoff must be positive and finite")
    rows = [
        f"{symbol} " + " ".join(f"{x:.12f}" for x in pos)
        for symbol, pos in zip(symbols, xyz, strict=True)
    ]
    coords = "\n".join(rows)
    raw = s.raw_keywords
    warnings = ["Review raw keywords and method/basis availability with the installed program."]
    files: dict[str, str] = {}
    command: tuple[str, ...] = ()
    if s.program == "Gaussian":
        task = {
            "single_point": "SP",
            "optimization": "Opt",
            "frequencies": "Freq",
            "tddft": f"TD(NStates={states})",
        }[s.task]
        if periodic and s.task in ("frequencies", "tddft"):
            raise ValueError("Gaussian periodic writer supports SP and fixed-cell optimization")
        lines = [f"# {method}/{basis} {task}", "", "vibe-view input", "", f"{charge} {mult}"]
        lines += [
            f"{symbol} {-1 if i in frozen else 0} " + " ".join(f"{x:.12f}" for x in pos)
            for i, (symbol, pos) in enumerate(zip(symbols, xyz, strict=True))
        ]
        if periodic and cell is not None:
            lines += ["Tv " + " ".join(f"{x:.12f}" for x in cell[i]) for i in range(3) if pbc[i]]
        # Raw Gaussian keywords belong in the route, not after geometry.
        if raw:
            lines[0] += " " + raw
        primary, text = "calculation.gjf", "\n".join(lines) + "\n\n"
    elif s.program == "Psi4":
        text = f"molecule mol {{\n{charge} {mult}\n{coords}\nunits angstrom\nsymmetry c1\n}}\n"
        text += f"set basis {basis}\nset reference {'rhf' if mult == 1 else 'uhf'}\n"
        if frozen:
            freeze = "\n".join(f"{i + 1} xyz" for i in frozen)
            text += f'freeze_list = """\n{freeze}\n"""\nset optking frozen_cartesian $freeze_list\n'
        text += raw + "\n"
        if s.task == "tddft":
            text += f"e, wfn = energy({method!r}, return_wfn=True)\n"
            text += "from psi4.driver.procrouting.response.scf_response import tdscf_excitations\n"
            text += f"tdscf_excitations(wfn, states={states}, tda=False)\n"
        else:
            call = {
                "single_point": "energy",
                "optimization": "optimize",
                "frequencies": "frequency",
            }[s.task]
            text += f"{call}({method!r})\n"
        primary = "calculation.dat"
    elif s.program == "NWChem":
        if periodic:
            if not all(pbc) or cell is None:
                raise ValueError("NWChem NWPW requires three periodic axes")
            if basis.upper() != "PLANE_WAVE":
                raise ValueError("Select basis PLANE_WAVE explicitly for NWChem NWPW")
            if s.task not in ("single_point", "optimization"):
                raise ValueError("NWChem periodic writer supports energy/optimization")
            # A triclinic lattice is rotated into NWChem's canonical lattice
            # orientation through lengths/angles; fractional positions preserve
            # every interatomic vector and the physical periodic geometry.
            lengths = np.linalg.norm(cell, axis=1)
            angles = [
                float(
                    np.degrees(
                        np.arccos(
                            np.clip(np.dot(cell[i], cell[j]) / (lengths[i] * lengths[j]), -1, 1)
                        )
                    )
                )
                for i, j in ((1, 2), (0, 2), (0, 1))
            ]
            frac = xyz @ np.linalg.inv(cell)
            lattice = "\n".join(
                f" {key} {value:.12f}"
                for key, value in zip(
                    ("lat_a", "lat_b", "lat_c", "alpha", "beta", "gamma"),
                    [*lengths, *angles],
                    strict=True,
                )
            )
            text = (
                f"start calculation\n"
                f"charge {charge}\n"
                f"geometry units angstrom nocenter noautosym noautoz\n"
                f" system crystal\n"
                f"{lattice}\n"
                f" end\n"
                f""
            )
            text += "\n".join(
                symbol + " " + " ".join(f"{x:.12f}" for x in pos)
                for symbol, pos in zip(symbols, frac, strict=True)
            )
            xc = "pbe96" if method.lower() == "pbe" else method
            text += f"\nend\nnwpw\n cutoff {s.cutoff / 2:g}\n mult {mult}\n xc {xc}\nend\n"
            if frozen:
                text += "constraints\n fix atom " + " ".join(str(i + 1) for i in frozen) + "\nend\n"
            task = "energy" if s.task == "single_point" else "optimize"
            text += raw + f"\ntask pspw {task}\n"
            warnings.append(
                "NWPW Gamma-point plane waves; cutoff converted from Ry to hartree. Check the "
                "installed pseudopotential library and convergence."
            )
            return InputBundle(
                s.program,
                "calculation.nw",
                {"calculation.nw": text, "README.txt": "\n".join(warnings) + "\n"},
                tuple(warnings),
            )
        text = (
            f"start calculation\n"
            f"charge {charge}\n"
            f"geometry units angstrom noautosym noautoz\n"
            f"{coords}\n"
            f"end\n"
            f""
        )
        text += f'basis\n * library "{basis}"\nend\n'
        if method.lower() in ("hf", "rhf", "uhf"):
            engine = "scf"
            text += f"scf\n {'rhf' if mult == 1 else 'uhf'}\n nopen {mult - 1}\nend\n"
        else:
            engine = "dft"
            text += f"dft\n xc {method}\n mult {mult}\nend\n"
        if frozen:
            text += "constraints\n fix atom " + " ".join(str(i + 1) for i in frozen) + "\nend\n"
        text += raw + "\n"
        if s.task == "tddft":
            text += f"tddft\n nroots {states}\nend\ntask tddft energy\n"
        else:
            task = {
                "single_point": "energy",
                "optimization": "optimize",
                "frequencies": "frequencies",
            }[s.task]
            text += f"task {engine} {task}\n"
        primary = "calculation.nw"
    elif s.program == "Q-Chem":
        task = {"single_point": "sp", "optimization": "opt", "frequencies": "freq", "tddft": "sp"}[
            s.task
        ]
        text = (
            f"$molecule\n"
            f"{charge} {mult}\n"
            f"{coords}\n"
            f"$end\n"
            f"$rem\n"
            f"JOBTYPE {task}\n"
            f"METHOD {method}\n"
            f"BASIS {basis}\n"
            f""
        )
        if s.task == "tddft":
            text += f"CIS_N_ROOTS {states}\nRPA TRUE\n"
        text += "$end\n"
        if frozen:
            text += (
                "$opt\nFIXED\n" + "\n".join(f"{i + 1} XYZ" for i in frozen) + "\nENDFIXED\n$end\n"
            )
        text += raw + "\n"
        primary = "calculation.in"
    elif s.program == "GAMESS":
        task = {
            "single_point": "ENERGY",
            "optimization": "OPTIMIZE",
            "frequencies": "HESSIAN",
            "tddft": "ENERGY",
        }[s.task]
        text = (
            f" $CONTRL SCFTYP={'RHF' if mult == 1 else 'UHF'} RUNTYP={task}\n"
            f" ICHARG={charge} MULT={mult} COORD=UNIQUE UNITS=ANGS"
        )
        if method.upper() not in ("HF", "RHF", "UHF"):
            text += f" DFTTYP={method}"
        if s.task == "tddft":
            text += " TDDFT=EXCITE"
        text += " $END\n"
        # GAMESS uses named basis families and NGAUSS, not common basis labels.
        families = {
            "STO-3G": "GBASIS=STO NGAUSS=3",
            "N31": "GBASIS=N31 NGAUSS=6",
            "6-31G": "GBASIS=N31 NGAUSS=6",
            "6-31G*": "GBASIS=N31 NGAUSS=6 NDFUNC=1",
            "CCD": "GBASIS=CCD",
            "CCT": "GBASIS=CCT",
        }
        if basis.upper() not in families:
            raise ValueError(
                "GAMESS basis: choose STO-3G, N31, 6-31G, 6-31G*, CCD or CCT; custom basis needs "
                "native editing"
            )
        text += f" $BASIS {families[basis.upper()]} $END\n"
        if frozen:
            indices = ",".join(str(3 * i + j) for i in frozen for j in (1, 2, 3))
            text += f" $STATPT IFREEZ(1)={indices} $END\n"
        if s.task == "tddft":
            text += f" $TDDFT NSTATE={states} $END\n"
        text += " $DATA\nvibe-view input\nC1\n"
        text += (
            "\n".join(
                f"{symbol} {SYMBOL_TO_Z[symbol]}.0 " + " ".join(f"{x:.12f}" for x in pos)
                for symbol, pos in zip(symbols, xyz, strict=True)
            )
            + "\n $END\n"
            + raw
            + "\n"
        )
        primary = "calculation.inp"
    elif s.program == "CP2K":
        assert cell is not None
        if method.upper() not in ("PBE", "BLYP", "PADE"):
            raise ValueError(
                "CP2K writer supports PBE, BLYP or PADE; hybrid setup requires explicit native "
                "editing"
            )
        missing = set(symbols) - set(s.potentials)
        if missing:
            raise ValueError(
                "Specify CP2K pseudopotential for each element: " + ", ".join(sorted(missing))
            )
        for potential in s.potentials.values():
            _token(potential, "Pseudopotential")
        periodic_axes = "".join(axis for axis, on in zip("XYZ", pbc, strict=True) if on) or "NONE"
        task = {
            "single_point": "ENERGY",
            "optimization": "GEO_OPT",
            "frequencies": "VIBRATIONAL_ANALYSIS",
            "tddft": "ENERGY",
        }[s.task]
        text = (
            f"&GLOBAL\n"
            f" PROJECT calculation\n"
            f" RUN_TYPE {task}\n"
            f"&END GLOBAL\n"
            f"&FORCE_EVAL\n"
            f" METHOD Quickstep\n"
            f" &DFT\n"
            f""
        )
        text += (
            f"  BASIS_SET_FILE_NAME BASIS_MOLOPT\n"
            f"  POTENTIAL_FILE_NAME GTH_POTENTIALS\n"
            f"  CHARGE {charge}\n"
            f"  MULTIPLICITY {mult}\n"
            f"  UKS {'T' if mult > 1 else 'F'}\n"
            f""
        )
        text += f"  &MGRID\n   CUTOFF {s.cutoff:g}\n  &END MGRID\n"
        text += f"  &POISSON\n   PERIODIC {periodic_axes}\n"
        if not all(pbc):
            if not np.allclose(cell, np.diag(np.diag(cell)), rtol=0, atol=1e-10):
                raise ValueError("CP2K non-3D writer requires an axis-aligned orthorhombic cell")
            text += "   POISSON_SOLVER ANALYTIC\n"
            warnings.append(
                "ANALYTIC electrostatics for 0D/1D/2D: converge cell extent and cutoff."
            )
        text += (
            f"  &END POISSON\n"
            f"  &XC\n"
            f"   &XC_FUNCTIONAL {method.upper()}\n"
            f"   &END XC_FUNCTIONAL\n"
            f"  &END XC\n"
            f" &END DFT\n"
            f" &SUBSYS\n"
            f"  &CELL\n"
            f""
        )
        text += "\n".join(
            "   " + axis + " " + " ".join(f"{x:.12f}" for x in row)
            for axis, row in zip("ABC", cell, strict=True)
        )
        text += f"\n   PERIODIC {periodic_axes}\n  &END CELL\n  &COORD\n{coords}\n  &END COORD\n"
        for symbol in dict.fromkeys(symbols):
            text += (
                f"  &KIND {symbol}\n"
                f"   BASIS_SET {basis}\n"
                f"   POTENTIAL {s.potentials[symbol]}\n"
                f"  &END KIND\n"
                f""
            )
        text += " &END SUBSYS\n"
        if s.task == "tddft":
            text += (
                f" &PROPERTIES\n  &TDDFPT T\n   NSTATES {states}\n  &END TDDFPT\n &END PROPERTIES\n"
            )
        text += "&END FORCE_EVAL\n"
        if frozen:
            text += "&MOTION\n &CONSTRAINT\n  &FIXED_ATOMS\n   LIST " + " ".join(
                str(i + 1) for i in frozen
            )
            text += (
                "\n   COMPONENTS_TO_FIX XYZ\n  &END FIXED_ATOMS\n &END CONSTRAINT\n&END MOTION\n"
            )
        text += raw + "\n"
        warnings.append(
            "Provide matching BASIS_MOLOPT/GTH_POTENTIALS data; cell and cutoff need "
            "convergence checks. Frozen fractional coordinates use a fixed cell."
        )
        primary = "calculation.inp"
    elif s.program == "xtb":
        if s.task == "tddft":
            raise ValueError("xtb has no TD-DFT writer; sTDA is a different method")
        methods = {
            "GFN0-xTB": ("--gfn", "0"),
            "GFN1-xTB": ("--gfn", "1"),
            "GFN2-xTB": ("--gfn", "2"),
            "GFN-FF": ("--gfnff",),
        }
        if method not in methods or s.basis:
            raise ValueError(
                "xtb uses GFN0/1/2-xTB or GFN-FF and its built-in basis (leave basis empty)"
            )
        if periodic and method != "GFN-FF":
            raise ValueError(
                "Periodic xtb writer requires GFN-FF with xtb >=6.7; no molecular fallback"
            )
        if periodic and not all(pbc):
            raise ValueError("Periodic xtb writer requires three periodic axes")
        if method == "GFN-FF" and (charge or mult != 1):
            raise ValueError(
                "GFN-FF writer requires neutral singlet; it does not define electronic spin states"
            )
        text = (
            "$coord angs\n"
            + "\n".join(
                " ".join(f"{x:.12f}" for x in pos) + " " + symbol.lower()
                for symbol, pos in zip(symbols, xyz, strict=True)
            )
            + "\n"
        )
        if periodic and cell is not None:
            text += (
                "$periodic 3\n$lattice angs\n"
                + "\n".join(" ".join(f"{x:.12f}" for x in row) for row in cell)
                + "\n"
            )
        text += "$end\n"
        control = (
            ("$fix\n atoms: " + ",".join(str(i + 1) for i in frozen) + "\n$end\n") if frozen else ""
        )
        files["xcontrol"] = control + raw + "\n"
        command = (
            "xtb",
            "coord",
            *methods[method],
            "--chrg",
            str(charge),
            "--uhf",
            str(mult - 1),
            "--input",
            "xcontrol",
        )
        if s.task != "single_point":
            command += ("--opt" if s.task == "optimization" else "--hess",)
        files["command.json"] = json.dumps(list(command), indent=2) + "\n"
        primary = "coord"
    else:
        assert cell is not None
        if not all(pbc):
            raise ValueError(
                "POSCAR requires three periodic axes; construct an explicit periodic supercell "
                "first"
            )
        if (
            s.task != "single_point"
            or s.method
            or s.basis
            or charge != 0
            or mult != 1
            or raw.strip()
        ):
            raise ValueError(
                "POSCAR is structure-only: use default charge/spin, blank method/basis/raw and "
                "single_point; configure physics separately in INCAR/POTCAR"
            )
        species = list(dict.fromkeys(symbols))
        order = [i for symbol in species for i, x in enumerate(symbols) if x == symbol]
        text = (
            "vibe-view structure\n1.0\n"
            + "\n".join(" ".join(f"{x:.12f}" for x in row) for row in cell)
            + "\n"
        )
        text += (
            " ".join(species)
            + "\n"
            + " ".join(str(symbols.count(x)) for x in species)
            + "\nSelective dynamics\nCartesian\n"
        )
        text += (
            "\n".join(
                " ".join(f"{x:.12f}" for x in xyz[i]) + (" F F F" if i in frozen else " T T T")
                for i in order
            )
            + "\n"
        )
        files["atom-order.json"] = json.dumps({"original_zero_based_indices": order}) + "\n"
        warnings.append(
            "POSCAR contains no method, basis, charge or spin. Supply matching POTCAR, INCAR "
            "and KPOINTS; no runnable VASP job is implied."
        )
        primary = "POSCAR"
    files[primary] = text
    files["README.txt"] = "\n".join(warnings) + "\n"
    return InputBundle(s.program, primary, files, tuple(warnings), command)
