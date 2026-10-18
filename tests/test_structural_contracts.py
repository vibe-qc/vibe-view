"""Structural contracts: guards that read the viewer's source on purpose.

Most tests should check behaviour: the state a controller leaves, the page
the client is served (``tests/_served.py``), or the served-DOM lane in
``test_design_refresh.py``. A few contracts are about the *shape* of the
code instead, and have no cheaper behavioural form: every dialog, slider and
playback button in the layout tree names itself, and every palette entry has
a dispatch branch. (Every panel kind having a click branch is now a check on
the section registry itself, in ``test_issue48_browser.py``.) Those live here, and
only here, so a refactor of ``vibeview/app.py`` knows which file to update
(#41-29).

Rules for adding one: parse the source with :mod:`ast` rather than matching
substrings, and assert on names and literals, not on formatting. A source
substring check that a behavioural test could replace belongs in that
behavioural test instead.
"""

from __future__ import annotations

import ast
import inspect

from tests._sources import app_source
from tests.test_design_refresh import (
    _DIALOG_MODELS_AND_LABELS,
    _PLAYBACK_BUTTON_CONTRACT,
    _SLIDER_MODELS_AND_NAMES,
)


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    matches = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(matches) == 1, f"expected one function named {name}, found {len(matches)}"
    return matches[0]


def _app_tree() -> ast.AST:
    return ast.parse(app_source())


def test_create_app_remains_a_composition_root():
    """#37-R6: keep controller bodies out of the application factory."""
    import vibeview.app as app

    source = inspect.getsource(app.create_app)
    factory = ast.parse(source).body[0]
    nested = [
        node.name for statement in factory.body for node in ast.walk(statement)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert not nested, f"controller implementations returned to create_app: {nested}"
    assert len(source.splitlines()) <= 300, "move area setup out of the composition root"


def test_every_palette_action_has_a_dispatch_entry():
    """Every command-palette id is a key of run_palette_action's dispatch
    table (``open:<section>`` ids are routed by prefix), and the table holds
    no entry the palette cannot reach."""
    import vibeview.app as app

    handler = _function(_app_tree(), "run_palette_action")
    tables = [
        node.value
        for node in ast.walk(handler)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(isinstance(t, ast.Name) and t.id == "dispatch"
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))
        and isinstance(node.value, ast.Dict)
    ]
    assert len(tables) == 1, "run_palette_action lost its dispatch table"
    keys = {k.value for k in tables[0].keys if isinstance(k, ast.Constant)}
    ids = {a["id"] for a in app._PALETTE_ACTIONS if not a["id"].startswith("open:")}
    assert ids - keys == set(), f"palette ids with no dispatch entry: {sorted(ids - keys)}"
    assert keys - ids == set(), f"dispatch entries no palette id reaches: {sorted(keys - ids)}"


def test_every_modal_dialog_forwards_a_unique_accessible_name():
    """Every Vuetify dialog must name its served ``role=dialog`` root."""
    import ast


    source = app_source()
    tree = ast.parse(source)
    dialogs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "VDialog"
    ]
    assert source.count("VDialog") == len(dialogs), (
        "call dialogs directly as v.VDialog so the complete inventory stays testable"
    )

    models_and_labels = {}
    for dialog in dialogs:
        keywords = {kw.arg: kw.value for kw in dialog.keywords}
        assert "v_model" in keywords, f"VDialog at line {dialog.lineno} has no model"
        assert "aria_label" in keywords, f"unnamed VDialog at line {dialog.lineno}"
        assert "__properties" in keywords, (
            f"VDialog at line {dialog.lineno} does not forward aria-label"
        )
        model = ast.literal_eval(keywords["v_model"])[0]
        models_and_labels[model] = ast.literal_eval(keywords["aria_label"])
        properties = ast.literal_eval(keywords["__properties"])
        assert ("aria_label", "aria-label") in properties

    assert models_and_labels == _DIALOG_MODELS_AND_LABELS
    assert len(dialogs) == len(_DIALOG_MODELS_AND_LABELS)
    assert len(set(models_and_labels.values())) == len(models_and_labels), (
        "each dialog must have a distinct accessible name"
    )

def test_supercell_dialog_only_offers_real_periodic_axes():
    """Periodic builder fields must follow the structure's per-axis PBC."""
    import ast


    tree = ast.parse(app_source())  # the dialog lives in the layout module
    expected = {
        "build_supercell_nx": ("!is_periodic || pbc_a",),
        "build_supercell_ny": ("!is_periodic || pbc_b",),
        "build_supercell_nz": ("!is_periodic || pbc_c",),
    }
    actual = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "VTextField"
        ):
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        if "v_model" not in keywords:
            continue
        model = ast.literal_eval(keywords["v_model"])[0]
        if model not in expected:
            continue
        assert model not in actual, f"duplicate supercell field for {model}"
        assert "v_if" in keywords, f"{model} is not gated by periodicity"
        actual[model] = ast.literal_eval(keywords["v_if"])

    assert actual == expected

def test_every_state_driven_dialog_shares_the_focus_return_contract():
    """One global focus lifecycle must cover the complete dialog inventory."""
    import ast

    import vibeview.app as app

    source = app_source()
    tree = ast.parse(source)
    scripts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Script"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "_DIALOG_FOCUS_JS"
    ]

    assert len(scripts) == 1
    assert source.count("v.VDialog(") == len(_DIALOG_MODELS_AND_LABELS)
    dialogs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "VDialog"
    ]
    assert len(dialogs) == len(_DIALOG_MODELS_AND_LABELS)
    for dialog in dialogs:
        keywords = {kw.arg: kw.value for kw in dialog.keywords}
        v_model = keywords.get("v_model")
        assert isinstance(v_model, ast.Tuple) and v_model.elts
        assert isinstance(v_model.elts[0], ast.Constant)
        model = v_model.elts[0].value
        assert isinstance(model, str)
        label = keywords.get("aria_label")
        assert isinstance(label, ast.Constant) and isinstance(label.value, str)
        hook = keywords.get("afterLeave")
        assert isinstance(hook, ast.Call), (
            f"VDialog for {model!r} at line {dialog.lineno} has no afterLeave hook"
        )
        assert isinstance(hook.func, ast.Name) and hook.func.id == "_dialog_focus_restore"
        assert len(hook.args) == 1 and isinstance(hook.args[0], ast.Constant)
        assert hook.args[0].value == label.value

    focus_script = app._DIALOG_FOCUS_JS
    for marker in (
        "document.addEventListener('focusin'",
        "document.addEventListener('pointerdown'",
        "Object.create(null)",
        "event.relatedTarget",
        "_vvFocusCaptured",
        "current[current.length-1]!==entry",
        "window._vvRestoreDialogFocus",
        "requestAnimationFrame",
        "[role=dialog]",
        "el.isConnected",
        ".vv-presentation-overlay-content",
        "delete origins[key]",
        "top.contains(target)",
        "target.focus({preventScroll:true})",
    ):
        assert marker in focus_script
    assert "history" not in focus_script

def test_every_slider_forwards_a_unique_accessible_name():
    """Every slider must name the focusable Vuetify thumb it generates."""
    import ast


    source = app_source()
    tree = ast.parse(source)
    sliders = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "VSlider"
    ]
    assert source.count("v.VSlider(") == len(sliders), (
        "call sliders directly as v.VSlider so the complete inventory stays testable"
    )

    models_and_names = {}
    for slider in sliders:
        keywords = {kw.arg: kw.value for kw in slider.keywords}
        assert "v_model" in keywords, f"VSlider at line {slider.lineno} has no model"
        assert "aria_label" in keywords, f"unnamed VSlider at line {slider.lineno}"
        assert "__properties" in keywords, (
            f"VSlider at line {slider.lineno} does not forward aria-label"
        )
        model = ast.literal_eval(keywords["v_model"])[0]
        name = ast.literal_eval(keywords["aria_label"])
        assert model not in models_and_names, f"duplicate VSlider model {model!r}"
        models_and_names[model] = name
        properties = ast.literal_eval(keywords["__properties"])
        assert ("aria_label", "aria-label") in properties

    assert models_and_names == _SLIDER_MODELS_AND_NAMES
    assert len(sliders) == len(_SLIDER_MODELS_AND_NAMES)
    assert len(set(models_and_names.values())) == len(models_and_names), (
        "each slider must have a distinct accessible name"
    )

def test_clip_sliders_share_a_payload_safe_update_contract():
    """Every clip slider sends its model value to a callback that accepts it."""
    import ast


    source = app_source()
    tree = ast.parse(source)
    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "update_clip"
    ]
    assert len(handlers) == 1
    handler = handlers[0]
    assert [arg.arg for arg in handler.args.args] == ["self", "axis", "value"]
    assert len(handler.args.defaults) == 2
    assert all(ast.literal_eval(default) is None for default in handler.args.defaults)

    clip_models = {"clip_x", "clip_y", "clip_z"}
    bindings = {}
    for slider in (
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "VSlider"
    ):
        keywords = {kw.arg: kw.value for kw in slider.keywords}
        if "v_model" not in keywords:
            continue
        model = ast.literal_eval(keywords["v_model"])[0]
        if model in clip_models:
            bindings[model] = ast.unparse(keywords["update_modelValue"])

    assert bindings == {
        "clip_x": "(ctrl.update_clip, \"['x', $event]\")",
        "clip_y": "(ctrl.update_clip, \"['y', $event]\")",
        "clip_z": "(ctrl.update_clip, \"['z', $event]\")",
    }

def test_every_trajectory_and_orbital_playback_button_has_an_accessible_name():
    """The six icon-only playback buttons must remain completely inventoried."""
    import ast


    source = app_source()
    tree = ast.parse(source)
    target_actions = {action for action, _argument in _PLAYBACK_BUTTON_CONTRACT}

    def action_key(click: ast.expr) -> tuple[str, str | None] | None:
        if (
            isinstance(click, ast.Attribute)
            and isinstance(click.value, ast.Name)
            and click.value.id == "ctrl"
        ):
            return click.attr, None
        if (
            isinstance(click, ast.Tuple)
            and len(click.elts) == 2
            and isinstance(click.elts[0], ast.Attribute)
            and isinstance(click.elts[0].value, ast.Name)
            and click.elts[0].value.id == "ctrl"
            and click.elts[0].attr in target_actions
        ):
            return click.elts[0].attr, ast.literal_eval(click.elts[1])
        return None

    found = {}
    for button in (
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "VBtn"
    ):
        keywords = {kw.arg: kw.value for kw in button.keywords}
        if "click" not in keywords:
            continue
        key = action_key(keywords["click"])
        if key is None or key[0] not in target_actions:
            continue

        assert key not in found, f"duplicate playback control for {key!r}"
        assert not button.args, f"playback VBtn {key!r} must remain icon-only"
        assert "icon" in keywords, f"playback VBtn {key!r} lost its icon"
        assert "title" in keywords, f"playback VBtn {key!r} has no hover name"
        assert "aria_label" in keywords, f"playback VBtn {key!r} is unnamed"
        assert "__properties" in keywords, (
            f"playback VBtn {key!r} does not forward aria-label"
        )
        icon = ast.literal_eval(keywords["icon"])
        title = ast.literal_eval(keywords["title"])
        aria_label = ast.literal_eval(keywords["aria_label"])
        properties = ast.literal_eval(keywords["__properties"])
        assert title == aria_label
        assert ("aria_label", "aria-label") in properties
        found[key] = (icon, aria_label)

    assert found == _PLAYBACK_BUTTON_CONTRACT
    assert len(found) == 6

def test_visible_native_colour_input_forwards_an_accessible_name():
    """The raw colour picker is named; the other raw input remains hidden."""
    import ast


    source = app_source()
    tree = ast.parse(source)
    inputs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "html"
        and node.func.attr == "Input"
    ]
    assert source.count("html.Input(") == len(inputs) == 2

    by_type = {}
    for input_node in inputs:
        keywords = {kw.arg: kw.value for kw in input_node.keywords}
        input_type = ast.literal_eval(keywords["type"])
        assert input_type not in by_type, f"duplicate raw input type {input_type!r}"
        by_type[input_type] = keywords

    assert set(by_type) == {"file", "color"}
    assert "display: none" in ast.literal_eval(by_type["file"]["style"])

    colour = by_type["color"]
    assert ast.literal_eval(colour["v_model"]) == ("element_color_value",)
    assert ast.literal_eval(colour["title"]) == "Element colour override"
    assert ast.literal_eval(colour["aria_label"]) == "Element colour override"
    assert ("aria_label", "aria-label") in ast.literal_eval(colour["__properties"])

def test_section_restore_actually_activates():
    """Restoring a bookmark or session must re-open the section, not just set
    state.

    Both paths used to assign ``state.active_section`` — a key that is never
    declared in the state defaults and that nothing renders from (the open
    section is ``selected_section``, and rendering happens in
    ``ctrl.activate_section``). So a bookmark recorded ``section_id: ""`` and
    restoring one left the viewport wherever it already was. Bookmark, session,
    and presentation restore now share the same saved-view preparation step and
    all activate the section it resolves.
    """
    import ast
    src = app_source()

    # nothing writes the phantom key any more
    assert "state.active_section =" not in src, "phantom active_section still assigned"
    assert "state.active_section or" not in src, "bookmark still saves the phantom key"

    # bookmark save records the real section
    assert '"section_id": state.selected_section or ""' in src

    # Every restore path prepares the saved view and activates the section it
    # resolves. Pin the shared helper rather than the old direct dict indexing:
    # issue #322 deliberately centralized validation and render-hint ordering.
    functions = {
        node.name: ast.get_source_segment(src, node)
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.FunctionDef)
    }
    for handler, saved_view in (
        ("apply_user_bookmark", "bm"),
        ("load_session", "session"),
        ("_apply_slide", "slide"),
    ):
        restore_source = functions[handler]
        anchor = f"_prepare_saved_view_restore({saved_view})"
        assert anchor in restore_source, f"missing saved-view restore wiring: {anchor}"
        assert "activate_section(section_id)" in restore_source, handler


def test_browser_uses_the_shared_signed_kind_list():
    """The browser's volume rebuild decides signedness from the one shared
    list, not a local literal set (moved from test_terminal_correctness)."""
    import vibeview.app as appmod

    rebuild = ast.parse(inspect.getsource(appmod._rebuild_volume))
    tests = [
        node
        for node in ast.walk(rebuild)
        if isinstance(node, ast.Compare)
        and any(isinstance(op, ast.In) for op in node.ops)
        and any(isinstance(c, ast.Name) and c.id == "SIGNED_VOLUME_KINDS" for c in node.comparators)
    ]
    assert tests, "_rebuild_volume no longer tests membership in SIGNED_VOLUME_KINDS"



def test_actor_removals_name_their_group():
    """#37-41: every _remove_actors_by_prefix call passes an _Actors name (or
    a variable bound from them), never a retyped string."""

    tree = ast.parse(app_source())
    literal = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == "_remove_actors_by_prefix"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
    ]
    assert not literal, f"_remove_actors_by_prefix called with a string literal at {literal}"
    loops = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Tuple)
        and any(isinstance(e, ast.Constant) for e in node.iter.elts)
        and "_remove_actors_by_prefix" in ast.unparse(node)
    ]
    assert not loops, f"prefix loops over string literals at {loops}"


def test_app_sets_no_attributes_on_the_plotter():
    """#37-R3: create_app's hooks for module-level scene code live in one
    _SceneHooks record per plotter, not in attributes stuck on the plotter."""

    tree = ast.parse(app_source())
    stuck = [
        (node.lineno, ast.unparse(target))
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AugAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "plotter"
        and target.attr.startswith("_vibe")
    ]
    assert not stuck, stuck
    getattrs = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and str(node.args[1].value).startswith(("_vibe_view_", "_vibeview_"))
        and ast.unparse(node.args[0]) == "plotter"
    ]
    assert not getattrs, getattrs


def test_the_state_protocol_names_every_key_the_helpers_use():
    """#37-44: the module-level helpers take a _ViewerUIState, whose members
    are exactly the keys they touch, and each is declared in create_app's
    state defaults, so a misspelt key cannot become a phantom."""

    tree = ast.parse(app_source())
    protocol = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_ViewerUIState"
    )
    members = {n.target.id for n in protocol.body if isinstance(n, ast.AnnAssign)}
    helpers = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and any(a.arg == "state" for a in n.args.args + n.args.kwonlyargs)
    ]
    used = {
        node.attr
        for f in helpers
        for node in ast.walk(f)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "state"
    }
    assert used == members, (sorted(used - members), sorted(members - used))
    untyped = [
        f.name
        for f in helpers
        for a in f.args.args + f.args.kwonlyargs
        if a.arg == "state" and ast.unparse(a.annotation) != "_ViewerUIState"
    ]
    assert not untyped, untyped
    declared = {
        k.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "update"
        and ast.unparse(node.func.value).endswith("state")
        for arg in node.args
        if isinstance(arg, ast.Dict)
        for k in arg.keys
        if isinstance(k, ast.Constant)
    }
    assert members <= declared, sorted(members - declared)


def test_the_transfer_function_editor_trigger_is_bound():
    """The node editor's trigger is registered on the viewer controller."""
    from vibeview.app import bindings, tf_editor

    source = inspect.getsource(bindings)
    assert f'viewer.ctrl.trigger("{tf_editor.TF_EDITOR_TRIGGER}")' in source
