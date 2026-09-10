"""Parse vibe-qc Python input files — extract structure and run parameters.

Uses Python's ``ast`` module for safe parsing (no ``exec``).  Extracts:

- ``Molecule([Atom(Z, [x,y,z]), ...])`` — molecular structure
- ``PeriodicSystem(dim, lattice, unit_cell)`` — periodic system
- ``run_job`` / ``run_periodic_job`` / any ``run_*`` entry point — parameters

Beyond literal arguments, a small restricted expression evaluator
(``_evaluate``) resolves the input library's standard builders: module
constants (``_ATOMS``, ``_LATTICE_A``, ``BOHR``, ``_BASIS``, ...) and the
straight-line function body of ``build_system`` / ``build_molecule``
(``lattice_a = _LATTICE_A * BOHR``, ``lattice = lattice_a.T.copy()``,
``atoms = [vq.Atom(int(z), (np.asarray(f) @ lattice_a).tolist())
for z, f in _ATOMS]``). Only arithmetic over literals is evaluated —
anything unrecognised resolves to ``_UNRESOLVED``, so input files are
never executed.

Used by ``vibe-view open input.py`` (v1.1 import) and the parameter panel.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Callable


class InputParseResult:
    """Parsed vibe-qc input file."""

    def __init__(self) -> None:
        self.atoms: list[dict[str, Any]] = []  # [{symbol, atomic_number, position: [x,y,z]}]
        self.charge: int = 0
        self.multiplicity: int = 1
        self.basis: str | None = None
        self.method: str | None = None
        self.functional: str | None = None
        self.is_periodic: bool = False
        self.dimensionality: int | None = None
        self.lattice_vectors: list[list[float]] | None = None
        self.output: str | None = None
        self.extra: dict[str, Any] = {}  # other kwargs


def parse_input_file(path: str | Path) -> InputParseResult:
    """Parse a vibe-qc Python input file. Returns InputParseResult."""
    source = Path(path).read_text()
    return parse_input_source(source)


# Sentinel returned by _evaluate for expressions outside the supported
# subset. Compared by identity; never a value an input could produce.
_UNRESOLVED = object()


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_seq(value: Any) -> bool:
    return isinstance(value, (list, tuple))


def _is_matrix(value: Any) -> bool:
    return (
        _is_seq(value)
        and len(value) == 3
        and all(_is_seq(row) and len(row) == 3 for row in value)
    )


def _scale_seq(seq: Any, scalar: float) -> Any:
    """Multiply every number nested in ``seq`` by ``scalar``."""
    if _is_num(seq):
        return seq * scalar
    return [_scale_seq(item, scalar) for item in seq]


def _matmul(left: Any, right: Any) -> Any:
    """NumPy semantics for ``(n,) @ (n, n)``: a row vector times a matrix."""
    return [sum(a * row[i] for a, row in zip(left, right)) for i in range(len(right[0]))]


def _transpose(matrix: Any) -> Any:
    return [list(row) for row in zip(*matrix)]


def _evaluate(node: ast.expr | None, env: dict[str, Any]) -> Any:
    """Evaluate a restricted subset of Python expressions over literals.

    Covers what vibe-qc input scripts actually compute before the
    Molecule/PeriodicSystem constructor: constants, names, lists and
    tuples, negation, scalar/sequence multiplication, matrix transpose,
    row-vector matrix products, ``Atom(...)`` construction, and the
    ``np.array`` / ``np.asarray`` / ``int`` / ``.tolist()`` / ``.copy()``
    wrapper calls the input library uses. Everything else resolves to
    ``_UNRESOLVED``, so an input file's own code never runs.
    """
    if node is None:
        return _UNRESOLVED
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id, _UNRESOLVED)
    if isinstance(node, ast.List):
        values = [_evaluate(elt, env) for elt in node.elts]
        return values if _UNRESOLVED not in values else _UNRESOLVED
    if isinstance(node, ast.Tuple):
        values = [_evaluate(elt, env) for elt in node.elts]
        return tuple(values) if _UNRESOLVED not in values else _UNRESOLVED
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _evaluate(node.operand, env)
        return -value if _is_num(value) else _UNRESOLVED
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left, env)
        right = _evaluate(node.right, env)
        if isinstance(node.op, ast.Mult):
            if _is_num(left) and _is_seq(right):
                return _scale_seq(right, left)
            if _is_num(right) and _is_seq(left):
                return _scale_seq(left, right)
            if _is_num(left) and _is_num(right):
                return left * right
        elif isinstance(node.op, ast.MatMult):
            if _is_seq(left) and _is_matrix(right):
                return _matmul(left, right)
        elif isinstance(node.op, (ast.Add, ast.Sub)):
            if _is_num(left) and _is_num(right):
                return left + right if isinstance(node.op, ast.Add) else left - right
        return _UNRESOLVED
    if isinstance(node, ast.Attribute):
        if node.attr == "T":
            value = _evaluate(node.value, env)
            return _transpose(value) if _is_matrix(value) else _UNRESOLVED
        return _UNRESOLVED
    if isinstance(node, ast.Call):
        func_name = _get_func_name(node)
        if func_name in ("array", "asarray") and node.args:
            return _evaluate(node.args[0], env)
        if func_name == "int" and node.args:
            value = _evaluate(node.args[0], env)
            return int(value) if _is_num(value) else _UNRESOLVED
        if func_name in ("tolist", "copy") and not node.args:
            return _evaluate(node.func.value, env)
        if func_name == "Atom":
            z_value = _evaluate(node.args[0] if node.args else None, env)
            if _is_num(z_value):
                z = int(z_value)
            elif isinstance(z_value, str):
                z = _symbol_to_z(z_value)
            else:
                return _UNRESOLVED
            position = _evaluate(node.args[1] if len(node.args) > 1 else None, env)
            if not _is_seq(position):
                return _UNRESOLVED
            return {
                "symbol": _z_to_symbol(z),
                "atomic_number": z,
                "position": [float(coord) for coord in position[:3]],
            }
        return _UNRESOLVED
    if isinstance(node, ast.ListComp):
        generator = node.generators[0]
        iterable = _evaluate(generator.iter, env)
        if not _is_seq(iterable):
            return _UNRESOLVED
        target = generator.target
        if isinstance(target, ast.Name):
            names = [target.id]
        elif isinstance(target, ast.Tuple):
            names = [elt.id for elt in target.elts]
        else:
            return _UNRESOLVED
        out = []
        for item in iterable:
            if len(names) == 1:
                env[names[0]] = item
            elif _is_seq(item) and len(item) == len(names):
                for name, value in zip(names, item):
                    env[name] = value
            else:
                return _UNRESOLVED
            value = _evaluate(node.elt, env)
            if value is _UNRESOLVED:
                return _UNRESOLVED
            out.append(value)
        return out
    return _UNRESOLVED


def parse_input_source(source: str) -> InputParseResult:
    """Parse vibe-qc Python source code. Returns InputParseResult."""
    tree = ast.parse(source)
    result = InputParseResult()
    env: dict[str, Any] = {}

    def run_stmts(stmts: list[ast.stmt]) -> None:
        # Straight-line interpretation of assignments only: module
        # constants plus helper-function bodies (the input library's
        # build_system / build_molecule pattern). No user code runs —
        # only the restricted expression subset is evaluated.
        for stmt in stmts:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
                if isinstance(target, ast.Name):
                    value = _evaluate(stmt.value, env)
                    if value is not _UNRESOLVED:
                        env[target.id] = value
            elif isinstance(stmt, ast.FunctionDef):
                run_stmts(stmt.body)
            elif (
                isinstance(stmt, ast.If)
                and isinstance(stmt.test, ast.Compare)
                and isinstance(stmt.test.left, ast.Name)
                and stmt.test.left.id == "__name__"
                and any(
                    isinstance(comp, ast.Constant) and comp.value == "__main__"
                    for comp in stmt.test.comparators
                )
            ):
                run_stmts(stmt.body)

    def resolve(node: ast.expr | None) -> Any:
        return _evaluate(node, env)

    run_stmts(tree.body)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = _get_func_name(node)
        if func_name == "Molecule":
            _parse_molecule_call(node, result, resolve)
        elif func_name == "PeriodicSystem":
            result.is_periodic = True
            _parse_periodic_system(node, result, resolve)
        elif func_name and func_name.startswith("run_"):
            _parse_runner_call(node, result, resolve)

    _apply_module_defaults(result, env)
    return result


def _get_func_name(node: ast.Call) -> str | None:
    """Extract the function name from a Call node."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _kwarg(node: ast.Call, name: str) -> ast.expr | None:
    """Return the value node of keyword ``name``, or None."""
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _arg_or_kwarg(node: ast.Call, index: int, name: str) -> ast.expr | None:
    """Positional arg at ``index`` if present, else keyword ``name``."""
    if len(node.args) > index:
        return node.args[index]
    return _kwarg(node, name)


def _atom_dicts(value: Any) -> list[dict[str, Any]] | None:
    """Return ``value`` when it is a list of evaluated atom dicts."""
    if isinstance(value, list) and all(
        isinstance(atom, dict) and "position" in atom for atom in value
    ):
        return value
    return None


def _parse_molecule_call(
    node: ast.Call,
    result: InputParseResult,
    resolve: Callable[[ast.expr | None], Any],
) -> None:
    """Extract atoms from ``Molecule([Atom(...), ...])`` — literal lists
    (the docs style) or computed values (library build_molecule bodies)."""
    first_arg = _arg_or_kwarg(node, 0, "atoms")
    atoms = _atom_dicts(resolve(first_arg))
    if atoms is not None:
        result.atoms = atoms
    elif isinstance(first_arg, ast.List):
        result.atoms = _parse_molecule_atoms(node)
    for idx, name in ((1, "charge"), (2, "multiplicity")):
        val_node = _arg_or_kwarg(node, idx, name)
        value = resolve(val_node)
        if _is_num(value):
            setattr(result, name, int(value))


def _parse_molecule_atoms(node: ast.Call) -> list[dict[str, Any]]:
    """Extract atoms from a literal ``Molecule([Atom(...), ...])`` list —
    positional or the ``atoms=[...]`` keyword form used in the docs."""
    atoms = []
    first_arg = _arg_or_kwarg(node, 0, "atoms")
    if first_arg is None or not isinstance(first_arg, ast.List):
        return atoms
    for elt in first_arg.elts:
        if isinstance(elt, ast.Call):
            fn = _get_func_name(elt)
            if fn == "Atom" and len(elt.args) >= 2:
                try:
                    z = int(_eval_literal(elt.args[0]))
                except (ValueError, TypeError):
                    z = _symbol_to_z(str(_eval_literal(elt.args[0])))
                pos = _eval_list(elt.args[1])
                symbol = _z_to_symbol(z)
                atoms.append({"symbol": symbol, "atomic_number": z, "position": pos})
    return atoms


def _parse_periodic_system(
    node: ast.Call,
    result: InputParseResult,
    resolve: Callable[[ast.expr | None], Any],
) -> None:
    """Extract lattice + atoms from ``PeriodicSystem(dim, lattice, unit_cell)``
    (positional or keyword form; kwarg names match the pybind signature)."""
    dimensionality = resolve(_arg_or_kwarg(node, 0, "dim"))
    if _is_num(dimensionality):
        parsed_dimensionality = int(dimensionality)
        if parsed_dimensionality == dimensionality and 1 <= parsed_dimensionality <= 3:
            result.dimensionality = parsed_dimensionality

    lattice_value = resolve(_arg_or_kwarg(node, 1, "lattice"))
    if _is_matrix(lattice_value):
        result.lattice_vectors = [
            [float(coord) for coord in row] for row in lattice_value
        ]
    else:
        # Unresolvable lattice (unknown input style): keep the legacy
        # identity fallback so a periodic QVF still renders a cell.
        result.lattice_vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

    atoms_arg = _arg_or_kwarg(node, 2, "unit_cell")
    atoms = _atom_dicts(resolve(atoms_arg))
    if atoms is not None:
        result.atoms = atoms
    elif isinstance(atoms_arg, ast.List):
        result.atoms = _parse_molecule_atoms(
            ast.Call(func=ast.Name(id="Molecule"), args=[atoms_arg], keywords=[])
        )
    for idx, name, attr in ((3, "charge", "charge"), (4, "multiplicity", "multiplicity")):
        value = resolve(_arg_or_kwarg(node, idx, name))
        if _is_num(value):
            setattr(result, attr, int(value))


def _parse_runner_call(
    node: ast.Call,
    result: InputParseResult,
    resolve: Callable[[ast.expr | None], Any],
) -> None:
    """Extract keyword arguments from ``run_job`` / ``run_periodic_job`` /
    any ``run_*`` entry point (the input library uses several)."""
    for kw in node.keywords:
        key = kw.arg
        if key == "basis":
            if (
                isinstance(kw.value, ast.Call)
                and _get_func_name(kw.value) in ("BasisSet",)
                and len(kw.value.args) > 1
            ):
                name_value = resolve(kw.value.args[1])
                if isinstance(name_value, str):
                    result.basis = name_value
                    continue
            value = resolve(kw.value)
            if isinstance(value, str):
                result.basis = value
        elif key in ("method", "functional", "output"):
            value = resolve(kw.value)
            if isinstance(value, str):
                setattr(result, key, value)
        elif key == "charge":
            value = resolve(kw.value)
            if _is_num(value):
                result.charge = int(value)
        elif key == "multiplicity":
            value = resolve(kw.value)
            if _is_num(value):
                result.multiplicity = int(value)
        else:
            value = resolve(kw.value)
            if value is not _UNRESOLVED:
                result.extra[key] = value


def _apply_module_defaults(result: InputParseResult, env: dict[str, Any]) -> None:
    """Fall back to the input library's module constants (``_BASIS``,
    ``_METHOD``, ``_FUNCTIONAL``, ``_MULTIPLICITY``, ``_KMESH``) when the
    runner call left the corresponding field unset."""
    if result.basis is None and isinstance(env.get("_BASIS"), str):
        result.basis = env["_BASIS"]
    if result.method is None and isinstance(env.get("_METHOD"), str):
        result.method = env["_METHOD"]
    if result.functional is None and isinstance(env.get("_FUNCTIONAL"), str):
        result.functional = env["_FUNCTIONAL"]
    if result.multiplicity == 1 and _is_num(env.get("_MULTIPLICITY")):
        result.multiplicity = int(env["_MULTIPLICITY"])
    kpoints = env.get("_KMESH")
    if "kpoints" not in result.extra and kpoints is not None and kpoints is not _UNRESOLVED:
        result.extra["kpoints"] = kpoints


def _eval_literal(node: ast.expr) -> Any:
    """Safely evaluate a Python literal (numbers, strings, None, True/False)."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        if isinstance(node.operand, ast.Constant):
            return -node.operand.value
    if isinstance(node, ast.List):
        return [_eval_literal(e) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_literal(e) for e in node.elts)
    # Python 3.8+ uses ast.Constant for None/True/False.
    # ast.NameConstant was removed in Python 3.14; the top-level
    # ast.Constant check already handles those values.
    return None


def _eval_list(node: ast.expr) -> list[float]:
    """Evaluate a list literal as [float, float, float]."""
    vals = _eval_literal(node)
    if isinstance(vals, (list, tuple)):
        return [float(v) for v in vals[:3]]
    return [0.0, 0.0, 0.0]


# ── Element symbol ↔ atomic number ─────────────────────────────────────

_SYMBOLS = [
    "",
    "H",
    "He",
    "Li",
    "Be",
    "B",
    "C",
    "N",
    "O",
    "F",
    "Ne",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "S",
    "Cl",
    "Ar",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
    "Ga",
    "Ge",
    "As",
    "Se",
    "Br",
    "Kr",
    "Rb",
    "Sr",
    "Y",
    "Zr",
    "Nb",
    "Mo",
    "Tc",
    "Ru",
    "Rh",
    "Pd",
    "Ag",
    "Cd",
    "In",
    "Sn",
    "Sb",
    "Te",
    "I",
    "Xe",
    "Cs",
    "Ba",
]


def _z_to_symbol(z: int) -> str:
    if 0 < z < len(_SYMBOLS):
        return _SYMBOLS[z]
    return f"Z{z}"


def _symbol_to_z(symbol: str) -> int:
    try:
        return _SYMBOLS.index(symbol.capitalize())
    except ValueError:
        return 0
