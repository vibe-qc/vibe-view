"""The right-hand controls drawer: the Vue tree ``create_app`` mounts, and the
dialog focus-return helper the layout shares.

Part of the ``vibeview.app`` package; see its ``__init__`` for ``create_app``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vibeview.renderers import palette as _palette

if TYPE_CHECKING:

    pass


_DIALOG_FOCUS_JS = (
    "typeof document !== 'undefined' && typeof requestAnimationFrame !== 'undefined' && "
    "(function(){"
    "if(window._vibe_dialog_focus_bound)return;"
    "window._vibe_dialog_focus_bound=1;"
    "var origins=Object.create(null);var stable=null;"
    "var selector='button,[href],input,select,textarea,[role=button],'"
    "+'[tabindex]:not([tabindex=\"-1\"])';"
    "function candidate(el){return el&&el.closest?el.closest(selector):null;}"
    "function dialog(el){return el&&el.closest?el.closest('[role=dialog]'):null;}"
    "function transientOverlay(el){return !!(el&&el.closest&&el.closest('.v-overlay')&&"
    "!dialog(el)&&!el.closest('.vv-presentation-overlay-content'));}"
    "function visible(el){if(!el||!el.isConnected||!el.getClientRects().length)return false;"
    "var style=getComputedStyle(el);return style.display!=='none'&&style.visibility!=='hidden';}"
    "function usable(el){return !!(visible(el)&&typeof el.focus==='function'&&!el.disabled&&"
    "el.getAttribute('aria-disabled')!=='true'&&!el.closest('[inert]')&&"
    "!el.closest('[aria-hidden=\"true\"]')&&!transientOverlay(el));}"
    "function remember(el){el=candidate(el);"
    "if(!el||transientOverlay(el)||!usable(el))return;stable=el;}"
    "remember(document.activeElement);"
    "document.addEventListener('focusin',function(event){"
    "var modal=event.target&&event.target.closest?event.target.closest('[role=dialog]'):null;"
    "var key=modal&&modal.getAttribute('aria-label');"
    "if(!key){remember(event.target);return;}"
    "if(modal._vvFocusCaptured){remember(event.target);return;}"
    "modal._vvFocusCaptured=true;"
    "var related=candidate(event.relatedTarget);"
    "var origin=related&&!transientOverlay(related)&&usable(related)?related:stable;"
    "modal._vvFocusOrigin=origin||null;"
    "if(!origins[key])origins[key]=[];"
    "origins[key].push({modal:modal,target:origin||null});"
    "remember(event.target);"
    "},true);"
    "document.addEventListener('pointerdown',function(event){remember(event.target);},true);"
    "window._vvRestoreDialogFocus=function(key){var stack=origins[key]||[];"
    "var entry=stack.length?stack[stack.length-1]:null;var attempts=0;"
    "if(entry&&entry.modal){entry.modal._vvFocusCaptured=false;"
    "entry.modal._vvFocusOrigin=null;}"
    "function discard(){var current=origins[key]||[];var index=current.indexOf(entry);"
    "if(index>=0)current.splice(index,1);if(!current.length)delete origins[key];}"
    "function restore(){var current=origins[key]||[];"
    "if(entry&&current.length&&current[current.length-1]!==entry){discard();return;}"
    "var same=document.querySelectorAll('[role=dialog][aria-label=\"'+key+'\"]');"
    "for(var s=0;s<same.length;s++){if(visible(same[s])){"
    "if(++attempts<8){requestAnimationFrame(restore);return;}discard();return;}}"
    "discard();"
    "var target=entry&&entry.target;var open=document.querySelectorAll('[role=dialog]');"
    "var top=null;for(var d=0;d<open.length;d++){if(visible(open[d]))top=open[d];}"
    "if(top){if(target&&top.contains(target)&&usable(target)){focus(target);return;}"
    "var active=candidate(document.activeElement);"
    "if(active&&top.contains(active)&&usable(active))return;"
    "var choices=top.querySelectorAll(selector);"
    "for(var c=0;c<choices.length;c++){if(usable(choices[c])){focus(choices[c]);return;}}"
    "if(typeof top.focus==='function')focus(top);return;}"
    "if(usable(target))focus(target);"
    "}requestAnimationFrame(restore);};"
    "function focus(target){try{target.focus({preventScroll:true});}"
    "catch(error){target.focus();}}"
    "})()"
)

def _dialog_focus_restore(label: str) -> str:
    return f"window._vvRestoreDialogFocus && window._vvRestoreDialogFocus('{label}')"

# ── Periodic-table element picker (design refresh 2026, editor) ──────
# (Z, symbol, grid row, grid column) for H..Cm — the range both the CPK
# colour table and the converters cover. Standard 18-column layout;
# lanthanides/actinides sit in their customary detached rows (9 and 10;
# row 8 is a thin spacer in the grid template).
def _periodic_table_cells() -> list[tuple[int, str, int, int]]:
    from vibeview.elements import Z_TO_SYMBOL

    cells: list[tuple[int, str, int, int]] = []
    for z in range(1, 97):
        sym = Z_TO_SYMBOL.get(z)
        if sym is None:
            continue
        if z == 1:
            row, col = 1, 1
        elif z == 2:
            row, col = 1, 18
        elif z <= 4:
            row, col = 2, z - 2
        elif z <= 10:
            row, col = 2, z + 8
        elif z <= 12:
            row, col = 3, z - 10
        elif z <= 18:
            row, col = 3, z
        elif z <= 36:
            row, col = 4, z - 18
        elif z <= 54:
            row, col = 5, z - 36
        elif z <= 56:
            row, col = 6, z - 54
        elif z <= 71:
            row, col = 9, z - 54   # La..Lu -> cols 3..17
        elif z <= 86:
            row, col = 6, z - 68   # Hf..Rn -> cols 4..18
        elif z <= 88:
            row, col = 7, z - 86
        else:
            row, col = 10, z - 86  # Ac..Cm -> cols 3..10
        cells.append((z, sym, row, col))
    return cells

def _element_button_style(z: int, row: int, col: int) -> str:
    """Inline style for one periodic-table cell, CPK-tinted."""
    from vibeview.renderers.structure import cpk_color

    color = cpk_color(z)
    # Perceived luminance decides the text colour so e.g. nitrogen blue
    # stays readable.
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    text = "#000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#fff"
    return (
        f"grid-row:{row}; grid-column:{col}; background:{color}; color:{text}; "
        "border:1px solid rgba(0,0,0,0.25); border-radius:3px; "
        "font: 600 11px/1.9 system-ui, sans-serif; cursor:pointer; "
        "padding:2px 0; text-align:center;"
    )

def _filmstrip(html: Any, *, rows: str, index: str, current: str, name: str, seek: str) -> None:
    """A strip of frame thumbnails that seeks: the scrubber.

    ``rows`` is the state list of ``{index, last, label, src}`` rows, ``current``
    the state key of the position the slider holds, and ``seek`` the trigger
    a click sends the row's ``index`` to. Each thumbnail is a button, so the
    strip is keyboard-reachable and labelled. A drag across the strip with
    the button held seeks too: the browser keeps routing pointer events to
    the thumbnail that was pressed, so the strip listens for ``pointermove``
    and ``_vvScrub`` (layout's page script) finds the thumbnail under the
    pointer, seeking once each time it changes. The helper is named with a
    leading underscore on purpose: Vue resolves every other identifier in a
    template expression through the component proxy, where ``window`` is an
    undefined property, while an underscored name reaches the real global.
    """
    active = f"{current} >= t.{index} && {current} <= t.last"
    strip = html.Div(
        v_if=(f"{rows}.length > 0",),
        classes="vv-filmstrip",
        role="group",
        aria_label=name,
        __properties=["role", ("aria_label", "aria-label")],
        __events=["pointermove", "pointerup"],
        pointermove=(
            "$event.buttons === 1 && typeof _vvScrub === 'function' && "
            f"_vvScrub($event, (i) => trigger('{seek}', [i]))"
        ),
        pointerup="$event.currentTarget._vvLast = null",
    )
    with strip:
        thumb = html.Button(
            v_for=(f"(t, i) in {rows}",),
            key=(f"t.{index}",),
            type="button",
            classes=(f"'vv-thumb' + ({active} ? ' vv-thumb-active' : '')",),
            title=("t.label",),
            aria_label=("t.label",),
            aria_pressed=(f"{active} ? 'true' : 'false'",),
            data_index=(f"t.{index}",),
            __properties=[
                "type",
                ("aria_label", "aria-label"),
                ("aria_pressed", "aria-pressed"),
                ("data_index", "data-index"),
            ],
            click=f"trigger('{seek}', [t.{index}])",
        )
    with thumb:
        html.Img(src=("t.src",), alt="", draggable="false", __properties=["draggable"])


def _build_controls(server: Any, ctrl: Any) -> None:
    """Build the contextual control panel based on the selected section."""
    try:
        from trame.widgets import client, html
        from trame.widgets import vuetify3 as v

        from vibeview.app.playback import VIBRATION_PHASE_STEPS
        from vibeview.app.tf_editor import TF_EDITOR_ID as _TF_EDITOR_ID
        from vibeview.app.tf_editor import tf_editor_script as _tf_editor_script
    except ImportError:
        return

    # Run info (total energy, SCF convergence, method/basis …) — surfaced
    # from the manifest provenance block. Always visible when present.
    with v.VCard(classes="pa-2 mb-2", v_if=("run_info",)):
        v.VCardTitle("Run Info", classes="text-subtitle-1")
        v.VCardText(
            "{{ run_info }}",
            classes="text-caption",
            style="white-space: pre; font-family: monospace;",
        )

    # Display options (always present — toggles affecting the structure itself)
    with v.VCard(classes="pa-2 mb-2"):
        v.VCardTitle("Display")
        v.VSwitch(
            v_model=("show_atom_labels",),
            label="Show atom labels",
            density="compact",
            color="primary",
            update_modelValue=(ctrl.toggle_atom_labels, "[$event]"),
        )
        v.VSwitch(
            v_model=("show_wannier_centers",),
            v_if=("has_wannier_centers",),
            label="Show Wannier centres",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_wannier_centers, "[$event]"),
        )
        # One-click view presets (design refresh 2026): set representation,
        # material/background, SSAO, and labels together.
        v.VSelect(
            v_model=("view_preset", None),
            items=(
                "view_preset_options",
                [
                    {"title": "Publication (white, clean)", "value": "publication"},
                    {"title": "Presentation (dark, glossy)", "value": "presentation"},
                    {"title": "Analysis (flat, labels)", "value": "analysis"},
                ],
            ),
            item_title="title",
            item_value="value",
            label="View preset",
            density="compact",
            hide_details=True,
            clearable=True,
            classes="mb-1",
            update_modelValue=(ctrl.apply_view_preset, "[$event]"),
        )
        v.VSelect(
            v_model=("representation_style",),
            items=(
                "representation_style_options",
                [
                    {"title": "Ball & Stick", "value": "ball_and_stick"},
                    {"title": "Space Filling (vdW)", "value": "space_filling"},
                    {"title": "Sticks Only", "value": "sticks_only"},
                    {"title": "Wireframe", "value": "wireframe"},
                    {"title": "Cartoon (biomolecule)", "value": "cartoon"},
                ],
            ),
            item_title="title",
            item_value="value",
            label="Representation",
            density="compact",
            hide_details=True,
            update_modelValue=(ctrl.set_representation_style, "[$event]"),
        )
        # One palette control across atom, bond and cartoon representations.
        v.VSelect(
            v_model=("structure_color_mode",),
            items=(
                "cartoon_color_mode_options",
                [
                    {"title": "Element (cartoon: chain)", "value": "element"},
                    {"title": "Chain", "value": "chain"},
                    {"title": "Secondary structure", "value": "structure"},
                    {"title": "Residue type", "value": "residue"},
                    {"title": "B-factor", "value": "bfactor"},
                ],
            ),
            item_title="title",
            item_value="value",
            label="Biomolecule colour",
            density="compact",
            hide_details=True,
            v_if=("residue_selection_available",),
            update_modelValue=(ctrl.set_cartoon_color_mode, "[$event]"),
        )
        # Residue/chain selection (D4), shared by every representation and
        # shown only when the structure has a CA-bearing residue. Committed
        # on Enter or blur rather than per keystroke — every commit rebuilds
        # the scene, and "A/2" is a different selection from "A/24" that the
        # user never meant to see rendered.
        v.VTextField(
            v_model=("residue_selection",),
            label="Select residues",
            placeholder="A/24-38, B",
            prepend_inner_icon="mdi-select-search",
            density="compact",
            hide_details=True,
            v_if=("residue_selection_available",),
            # Commit on Enter or on leaving the field, not per keystroke:
            # every commit rebuilds the scene, and "A/2" is a different
            # selection from "A/24" that the user never asked to see. The
            # handler reads the committed value off state, so both events
            # can share it.
            __events=["blur", ("keyup_enter", "keyup.enter")],
            keyup_enter=ctrl.set_residue_selection,
            blur=ctrl.set_residue_selection,
        )
        html.Div(
            "{{ residue_selection_summary }}",
            classes="text-caption text-medium-emphasis mb-1 px-1",
            v_if=("residue_selection_available && residue_selection_summary",),
        )
        v.VSelect(
            v_model=("residue_visibility",),
            items=("residue_visibility_options", [
                {"title": "Show all", "value": "all"},
                {"title": "Isolate selection", "value": "isolate"},
                {"title": "Hide selection", "value": "hide"},
            ]),
            item_title="title", item_value="value", label="Residue visibility",
            density="compact", hide_details=True, v_if=("residue_selection_available",),
            update_modelValue=(ctrl.set_residue_visibility, "[$event]"),
        )
        v.VSwitch(
            v_model=("residue_pick_mode",), label="Pick residues in viewport",
            density="compact", hide_details=True, v_if=("residue_selection_available",),
            update_modelValue=(ctrl.set_residue_pick_mode, "[$event]"),
        )
        v.VSwitch(
            v_model=("show_hydrogen_bonds",), label="Hydrogen-bond contacts (explicit H)",
            density="compact", hide_details=True,
            update_modelValue=(ctrl.toggle_hydrogen_bonds, "[$event]"),
        )
        # ── Material presets (v1.6) ──
        v.VSelect(
            v_model=("material_preset", "cpk_glossy"),
            items=("material_preset_options",),
            item_title="title",
            item_value="value",
            label="Material Style",
            density="compact",
            hide_details=True,
            update_modelValue=(ctrl.set_material_preset, "[$event]"),
        )
        # Per-element colour override (design refresh 2026): pick an element
        # present in this structure, pick a colour, apply. Hidden when the
        # file carries no structure.
        with v.VRow(
            dense=True,
            classes="align-center mt-1",
            v_if=("element_color_options.length > 0",),
        ):
            with v.VCol(cols=5):
                v.VSelect(
                    v_model=("element_color_z",),
                    items=("element_color_options",),
                    item_title="title",
                    item_value="value",
                    label="Element",
                    density="compact",
                    hide_details=True,
                )
            with v.VCol(cols=4):
                html.Input(
                    type="color",
                    v_model=("element_color_value",),
                    title="Element colour override",
                    aria_label="Element colour override",
                    __properties=[("aria_label", "aria-label")],
                    style="width: 100%; height: 34px; border: none; background: none;",
                )
            with v.VCol(cols=3):
                v.VBtn(
                    "Set",
                    size="small",
                    variant="tonal",
                    block=True,
                    click=(ctrl.set_element_color, "[element_color_z, element_color_value]"),
                )
        v.VBtn(
            "Reset element colours",
            size="small",
            variant="text",
            block=True,
            v_if=("Object.keys(element_colors || {}).length > 0",),
            click=ctrl.reset_element_colors,
        )
        v.VSwitch(
            v_model=("measure_mode",),
            label="Measure mode (click atoms)",
            density="compact",
            color="primary",
            update_modelValue=(ctrl.toggle_measure_mode, "[$event]"),
        )
        # The ambient-occlusion toggle lives in the toolbar Export menu:
        # a renderer pass only shapes server-side renders (screenshots,
        # video frames), never the live viewport, and sitting here among
        # viewport toggles it read as a broken switch (maintainer report
        # 2026-07-24).
        v.VSwitch(
            v_model=("toon_mode",),
            label="Toon / NPR shading",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_toon, "[$event]"),
        )
        v.VSwitch(
            v_model=("orthographic_projection",),
            label="Orthographic projection",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_orthographic, "[$event]"),
        )
        # The figure look (#68), also set together by the Publication view
        # preset: outline hulls on atoms and bonds, and the chrome switch.
        v.VSwitch(
            v_model=("figure_outlines",),
            label="Outline atoms and bonds (figure look)",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_figure_outlines, "[$event]"),
        )
        v.VSwitch(
            v_model=("chrome_hidden",),
            label="Hide viewport chrome (figure look)",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_chrome, "[$event]"),
        )
        v.VSwitch(
            v_model=("file_watcher_enabled",),
            label="Auto-reload on file change",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_file_watcher, "[$event]"),
        )
        # Bond-order colour legend: chips from the table the bond colouring
        # interpolates, so the two cannot drift (#37-R4).
        v.VCardText("Bond order", classes="text-caption mt-1 mb-0")
        with v.VRow(dense=True, classes="mt-0"):
            for _order, _colour in _palette.BOND_ORDER_ANCHORS:
                _label = f"{_order:g}"
                v.VChip(
                    _label,
                    size="x-small",
                    variant="flat",
                    color=_colour,
                    text_color="white",
                    label=True,
                    classes="mr-1",
                )
        v.VBtn(
            "Clear selection",
            v_if=("measure_mode",),
            size="small",
            variant="tonal",
            click=ctrl.clear_picks,
        )

    # ── Symmetry card ──
    with v.VCard(v_if=("point_group",), classes="pa-2 mb-2"):
        v.VCardTitle("Symmetry")
        v.VCardText(
            "Point group: {{ point_group }}",
            classes="text-h6",
        )
        v.VCardText(
            "{{ point_group_details }}",
            classes="text-caption",
            style="white-space: pre; font-family: monospace;",
        )
        v.VBtn(
            "Symmetrize to {{ point_group }}",
            size="small",
            variant="tonal",
            block=True,
            prepend_icon="mdi-mirror",
            title=(
                "Project the geometry exactly onto the detected point group "
                "(undoable)"
            ),
            click=ctrl.edit_symmetrize,
        )

    # ── Edit Elements palette (v1.2) ──
    with v.VCard(v_if=("edit_mode",), classes="pa-2 mb-2"):
        v.VCardTitle("Atom Editor")
        # Periodic-table picker replaces the two element dropdowns
        # (design refresh 2026: "Periodic-table element picker for the
        # editor"). One dialog serves both targets.
        with v.VRow(dense=True, classes="align-center"):
            with v.VCol(cols=6):
                v.VBtn(
                    "New: {{ edit_new_element || 'C' }}",
                    size="small",
                    variant="tonal",
                    block=True,
                    prepend_icon="mdi-periodic-table",
                    title="Element used for newly added atoms",
                    click=(ctrl.open_element_picker, "['new']"),
                )
            with v.VCol(cols=6):
                v.VBtn(
                    "Change to…",
                    size="small",
                    variant="tonal",
                    block=True,
                    prepend_icon="mdi-periodic-table",
                    title="Change every selected atom's element",
                    disabled=("edit_selected.length === 0",),
                    click=(ctrl.open_element_picker, "['change']"),
                )
        with v.VRow(dense=True):
            v.VBtn(
                "Undo",
                icon="mdi-undo",
                size="small",
                variant="tonal",
                click=ctrl.edit_undo,
                disabled=("edit_history.length === 0",),
            )
            v.VBtn(
                "Redo",
                icon="mdi-redo",
                size="small",
                variant="tonal",
                click=ctrl.edit_redo,
                disabled=("edit_future.length === 0",),
            )
        with v.VRow(dense=True, classes="mt-2"):
            v.VBtn(
                "Delete Selected",
                icon="mdi-delete",
                size="small",
                color="error",
                variant="tonal",
                click=ctrl.edit_delete_selected,
                disabled=("edit_selected.length === 0",),
            )
        # B3 freeze-atom constraints — hold selected atoms fixed during live-opt.
        with v.VRow(dense=True, classes="mt-2"):
            v.VBtn(
                "Freeze Selected",
                icon="mdi-lock",
                size="small",
                variant="tonal",
                click=ctrl.edit_freeze_selected,
                disabled=("edit_selected.length === 0",),
            )
            v.VBtn(
                "Unfreeze All",
                icon="mdi-lock-open-variant",
                size="small",
                variant="tonal",
                click=ctrl.edit_unfreeze_all,
                disabled=("frozen_atoms.length === 0",),
            )
        v.VCardText(
            "{{ frozen_atoms.length }} atom(s) frozen"
            " — held fixed while auto-optimize relaxes the rest",
            v_if=("frozen_atoms.length > 0",),
            classes="py-0 text-caption",
        )
        # M3 live geometry optimization — Avogadro-parity auto-relax.
        # v_model already writes live_opt_enabled, so the handler must
        # *set* from the event, not flip (the toggle_clip lesson,
        # UI-OBS-G).
        v.VSwitch(
            v_model=("live_opt_enabled",),
            label="Auto-optimize",
            density="compact",
            hide_details=True,
            color="primary",
            classes="mt-1",
            disabled=("live_opt_available === false",),
            update_modelValue=(ctrl.toggle_live_opt, "[$event]"),
        )
        # Engine picker — only once the probe found more than one engine
        # (MACE appears when vibe-qc's [mace] stack is installed).
        v.VSelect(
            v_if=("live_opt_engine_options.length > 1",),
            v_model=("live_opt_engine",),
            items=("live_opt_engine_options",),
            item_title="title",
            item_value="value",
            label="Engine",
            density="compact",
            hide_details=True,
            update_modelValue=(ctrl.set_live_opt_engine, "[$event]"),
        )
        v.VCardText(
            "{{ live_opt_status }}",
            v_if=("live_opt_status",),
            classes="py-1 text-caption",
        )

    # Keep the modal outside the edit-only card. Its launchers remain gated by
    # edit mode, while an already-open picker retains its dialog semantics if
    # edit mode changes underneath it (for example via a keyboard shortcut).
    with (
        v.VDialog(
            v_model=("element_picker_open",),
            width="min(1000px, 96vw)",
            afterLeave=_dialog_focus_restore("Choose element"),
            aria_label="Choose element",
            __properties=[("aria_label", "aria-label")],
        ),
        v.VCard(classes="pa-3"),
    ):
        v.VCardTitle(
            "{{ element_picker_target === 'change' ? "
            "'Change selected atoms to…' : 'Element for new atoms' }}"
        )
        with html.Div(
            style=(
                "display:grid; grid-template-columns: repeat(18, 1fr); "
                "grid-template-rows: repeat(7, auto) 8px repeat(2, auto); "
                "gap: 3px; padding: 4px;"
            ),
        ):
            for _z, _sym, _row, _col in _periodic_table_cells():
                html.Button(
                    _sym,
                    title=f"{_sym} · Z={_z}",
                    style=_element_button_style(_z, _row, _col),
                    click=(ctrl.pick_element, f"['{_sym}']"),
                )

    # ── Structure Library (design refresh 2026, item 7) ──
    with v.VCard(v_if=("edit_mode && library_names.length > 0",), classes="pa-2 mb-2"):
        v.VCardTitle("Structure Library")
        v.VCardSubtitle(
            "{{ library_names.length }} molecules from the qc input library"
        )
        with v.VRow(dense=True, classes="align-center"):
            with v.VCol(cols=10):
                v.VAutocomplete(
                    v_model=("library_query",),
                    items=("library_names",),
                    label="Molecule name",
                    density="compact",
                    hide_details=True,
                    clearable=True,
                )
            with v.VCol(cols=2):
                v.VBtn(
                    icon=(
                        "(library_favorites || []).includes(library_query) "
                        "? 'mdi-star' : 'mdi-star-outline'",
                    ),
                    size="small",
                    variant="text",
                    title="Pin this molecule to your favourites",
                    disabled=("!library_query",),
                    click=(ctrl.toggle_library_favorite, "[library_query]"),
                )
        # A preview of the pick, rendered in the background once chosen.
        html.Img(
            v_if=("library_query && library_thumbnails[library_query]",),
            src=("library_thumbnails[library_query]",),
            alt=("library_query",),
            classes="vv-library-preview",
        )
        # Pinned favourites first — deliberate picks, persisted with the
        # session — then the implicit recents below. Each chip carries the
        # molecule's thumbnail once it is rendered.
        favourites = html.Div(
            v_if=("library_favorites.length > 0",),
            classes="mt-2",
            style="display: flex; flex-wrap: wrap; gap: 4px;",
        )
        with favourites:
            favourite = v.VChip(
                v_for=("fname in library_favorites",),
                key=("fname",),
                size="x-small",
                variant="tonal",
                color="primary",
                label=True,
                prepend_icon="mdi-star",
                title="Pinned — click to use",
                click=(ctrl.use_library_recent, "[fname]"),
            )
        with favourite:
            html.Img(
                v_if=("library_thumbnails[fname]",),
                src=("library_thumbnails[fname]",),
                alt="",
                classes="vv-chip-thumb",
            )
            html.Span("{{ fname }}")
        # Recently used picks — one click re-selects, instead of retyping into
        # an 83-entry autocomplete for the handful you actually reuse.
        recents = html.Div(
            v_if=("library_recent.length > 0",),
            classes="mt-2",
            style="display: flex; flex-wrap: wrap; gap: 4px;",
        )
        with recents:
            recent = v.VChip(
                v_for=("rname in library_recent",),
                key=("rname",),
                size="x-small",
                variant="tonal",
                label=True,
                title="Use this structure again",
                click=(ctrl.use_library_recent, "[rname]"),
            )
        with recent:
            html.Img(
                v_if=("library_thumbnails[rname]",),
                src=("library_thumbnails[rname]",),
                alt="",
                classes="vv-chip-thumb",
            )
            html.Span("{{ rname }}")
        with v.VRow(dense=True, classes="mt-2"):
            v.VBtn(
                "Insert",
                prepend_icon="mdi-plus",
                size="small",
                variant="tonal",
                title="Insert into the current structure (next to the selection)",
                click=ctrl.insert_library_structure,
                disabled=("!library_query",),
            )
            v.VBtn(
                "Open as file",
                prepend_icon="mdi-file-plus",
                size="small",
                variant="tonal",
                title="Open as a separate file in the Files dropdown",
                click=ctrl.open_library_structure,
                disabled=("!library_query",),
            )

    # ── Fragment Library (v1.3) ──
    with v.VCard(v_if=("edit_mode",), classes="pa-2 mb-2"):
        v.VCardTitle("Fragment Library")
        v.VSelect(
            v_model=("build_fragment",),
            items=("fragment_options",),
            item_title="title",
            item_value="value",
            label="Insert fragment",
            density="compact",
            hide_details=True,
            clearable=True,
            update_modelValue=(ctrl.insert_fragment, "[$event]"),
        )
        v.VBtn(
            "Add Hydrogens",
            icon="mdi-atom-variant",
            size="small",
            block=True,
            variant="tonal",
            classes="mt-2",
            click=ctrl.add_hydrogens,
            disabled=("edit_selected.length === 0",),
        )
        v.VBtn(
            "Supercell Build",
            icon="mdi-grid",
            size="small",
            block=True,
            variant="tonal",
            classes="mt-2",
            click="build_supercell_dialog = true",
        )

    # ── Calculation Parameters (v1.1: shown when structure is present) ──
    with v.VCard(v_if=("show_param_panel",), classes="pa-2 mb-2"):
        v.VCardTitle("Calculation Parameters")
        v.VSelect(
            v_model=("calc_engine",), items=("calc_engine_options",),
            label="Calculation engine", density="compact", hide_details=True,
        )
        with html.Div(v_if=("calc_engine === 'ORCA'",)):
            v.VAlert(
                "Basic ORCA inputs support isolated molecules. Check charge and multiplicity.",
                type="info", density="compact", variant="tonal", classes="my-2",
            )
            v.VSelect(
                v_model=("calc_orca_method",), items=("calc_orca_method_options",),
                label="ORCA method", density="compact", hide_details=True,
            )
            v.VSelect(
                v_model=("calc_orca_basis",), items=("calc_orca_basis_options",),
                label="ORCA basis", density="compact", hide_details=True,
            )
            v.VTextField(
                v_model=("calc_orca_nprocs",), label="Processes (nprocs)",
                type="number", min=1, density="compact", hide_details=True,
            )
            v.VTextField(
                v_model=("calc_orca_maxcore",), label="MaxCore (MB per process)",
                type="number", min=1, density="compact", hide_details=True,
            )
            v.VCheckbox(
                v_model=("calc_orca_raman",), label="Raman activities (ORCA 6.1+)",
                density="compact", hide_details=True,
            )
            v.VCheckbox(
                v_model=("calc_orca_vcd",), label="VCD rotatory strengths",
                density="compact", hide_details=True,
            )
            html.Div(
                "Raman and VCD require a frequency calculation. VCD uses analytical frequencies.",
                classes="text-caption text-medium-emphasis mb-2",
            )
        # Method
        v.VSelect(
            v_if=("calc_engine !== 'ORCA'",),
            v_model=("calc_method",),
            items=("method_options",),
            item_title="title",
            item_value="value",
            label="Method",
            density="compact",
            hide_details=True,
        )
        # Functional (shown only for DFT methods)
        v.VSelect(
            v_if=("calc_engine !== 'ORCA' && (calc_method === 'rks' || calc_method === 'uks')",),
            v_model=("calc_functional",),
            items=("functional_options",),
            item_title="title",
            item_value="value",
            label="Functional",
            density="compact",
            hide_details=True,
        )
        # Basis set
        v.VSelect(
            v_if=("calc_engine !== 'ORCA'",),
            v_model=("calc_basis",),
            items=("basis_options",),
            item_title="title",
            item_value="value",
            label="Basis Set",
            density="compact",
            hide_details=True,
        )
        # Charge
        v.VTextField(
            v_model=("calc_charge", 0),
            label="Charge",
            type="number",
            density="compact",
            hide_details=True,
        )
        # Multiplicity
        v.VTextField(
            v_model=("calc_multiplicity", 1),
            label="Multiplicity",
            type="number",
            min=1,
            density="compact",
            hide_details=True,
        )
        # Calculation type template (molecular systems)
        v.VSelect(
            v_if=("!is_periodic",),
            v_model=("calc_template",),
            items=(
                "calc_engine === 'ORCA'"
                " ? calc_template_options.filter(o => o.value !== 'periodic')"
                " : calc_template_options",
            ),
            item_title="title",
            item_value="value",
            label="Calculation Type",
            density="compact",
            hide_details=True,
        )
        # Calculation type template (periodic systems — periodicsingle-point only)
        v.VSelect(
            v_if=("is_periodic",),
            v_model=("calc_template",),
            items=("periodic_template_options",),
            item_title="title",
            item_value="value",
            label="Calculation Type",
            density="compact",
            hide_details=True,
        )
        # Export as .py button
        v.VBtn(
            "Export vibe-qc input (.py)",
            v_if=("calc_engine !== 'ORCA'",),
            block=True,
            color="primary",
            variant="tonal",
            size="small",
            classes="mt-2",
            click=ctrl.export_py,
        )
        with html.Div(v_if=("calc_engine === 'ORCA'",)):
            v.VBtn(
                "Preview ORCA input", block=True, size="small", classes="mt-2",
                variant="tonal", click=ctrl.preview_orca_input, disabled=("is_periodic",),
            )
            v.VTextarea(
                v_if=("calc_orca_preview",), v_model=("calc_orca_preview",),
                label="Generated input (Preview to refresh)", readonly=True,
                rows=8, density="compact", hide_details=True,
            )
            v.VBtn(
                "Save ORCA input (.inp)", block=True, color="primary", variant="tonal",
                size="small", classes="mt-2", click=ctrl.export_orca_input,
                disabled=("is_periodic",),
            )
            v.VBtn(
                "Submit ORCA to vq", block=True, color="primary", variant="tonal",
                size="small", classes="mt-2", click="vq_submit_dialog = true",
                disabled=("is_periodic",),
            )

    # Compare/overlay mode (Phase D1): overlay all loaded structures, one colour
    # per file. Only shown when more than one file is open.
    with v.VCard(v_if=("file_names.length > 1",), classes="pa-2 mb-2"):
        v.VCardTitle("Compare Files")
        v.VSwitch(
            v_model=("compare_mode",),
            label="Overlay all structures",
            density="compact",
            color="primary",
            update_modelValue=(ctrl.toggle_compare_mode, "[$event]"),
        )
        v.VSwitch(
            v_if=("compare_mode",),
            v_model=("compare_align",),
            label="Align (RMSD fit to first file)",
            density="compact",
            color="primary",
            update_modelValue=(ctrl.toggle_compare_align, "[$event]"),
        )
        v.VSelect(
            v_if=("compare_mode",),
            v_model=("compare_highlight",),
            items=("compare_highlight_options",),
            item_title="title",
            item_value="value",
            label="Highlight",
            density="compact",
            hide_details=True,
            clearable=True,
            update_modelValue=(ctrl.set_compare_highlight, "[$event]"),
        )
        v.VChip(
            "{{ f.label }}",
            v_for=("(f, i) in compare_legend",),
            key=("i",),
            color=("f.color",),
            size="small",
            variant="flat",
            classes="ma-1",
        )

    # Density difference (Phase D2): A - B between two files that both carry a
    # volume.density section.
    with v.VCard(v_if=("density_file_options.length > 1",), classes="pa-2 mb-2"):
        v.VCardTitle("Density Difference")
        v.VSelect(
            v_model=("diff_a",),
            items=("density_file_options",),
            item_title="title",
            item_value="value",
            label="File A",
            density="compact",
        )
        v.VSelect(
            v_model=("diff_b",),
            items=("density_file_options",),
            item_title="title",
            item_value="value",
            label="File B (subtracted)",
            density="compact",
        )
        v.VBtn(
            "Show A − B",
            size="small",
            variant="tonal",
            color="primary",
            click=ctrl.show_density_diff,
        )

    # Atomic charge controls (only when atom_properties is the active section).
    with v.VCard(v_if=("atom_properties_active",), classes="pa-2 mb-2"):
        v.VCardTitle("Population Analysis")
        v.VSelect(
            v_model=("charge_kind",),
            items=("charge_kind_options",),
            item_title="title",
            item_value="value",
            label="Method",
            density="compact",
            update_modelValue=(ctrl.set_charge_kind, "[$event]"),
        )
        v.VSwitch(
            v_model=("color_by_charge",),
            label="Color atoms by charge (red +, blue −)",
            density="compact",
            color="primary",
            update_modelValue=(ctrl.toggle_color_by_charge, "[$event]"),
        )

    # Orbital picker (shown when the file has more than one volume.orbital
    # section — the sidebar collapses them into one "Molecular Orbitals"
    # entry, this dropdown switches between the individual orbitals).
    # Gated on the currently-active volume actually being an orbital so it
    # doesn't show up next to unrelated sections (atom_properties, etc.) and
    # imply a relationship that isn't there.
    with v.VCard(
        v_if=(
            "orbital_options.length > 1 && orbital_options.some(o => o.value === active_volume_id)",
        ),
        classes="pa-2 mb-2",
    ):
        v.VCardTitle("{{ orbital_panel_title }}")
        v.VSelect(
            v_model=("selected_orbital",),
            items=("orbital_options",),
            item_title="title",
            item_value="value",
            label="Orbital",
            update_modelValue=(ctrl.activate_section, "[$event]"),
        )

    # Fermi-surface band selector (shown when a fermi_surface section is active
    # and exposes more than one band sheet). Multi-select; toggling re-renders
    # only the chosen bands.
    with v.VCard(v_if=("fermi_band_options.length > 1",), classes="pa-2 mb-2"):
        v.VCardTitle("Fermi Surface Bands")
        v.VSelect(
            v_model=("fermi_selected_bands",),
            items=("fermi_band_options",),
            item_title="title",
            item_value="value",
            label="Bands shown",
            multiple=True,
            chips=True,
            density="compact",
            update_modelValue=(ctrl.update_fermi_bands, "[$event]"),
        )

    # AO picker (shown when the file has basis.ao sections and a basis.ao
    # section is the active volume — same pattern as the orbital picker).
    with v.VCard(
        v_if=("ao_options.length > 0 && ao_options.some(o => o.value === active_volume_id)",),
        classes="pa-2 mb-2",
    ):
        v.VCardTitle("Basis Functions")
        v.VSelect(
            v_model=("selected_ao",),
            items=("ao_options",),
            item_title="title",
            item_value="value",
            label="AO",
            update_modelValue=(ctrl.activate_section, "[$event]"),
        )

    # Volume controls (shown when a volume section is selected)
    with v.VCard(
        v_if=("active_volume_id",),
        classes="pa-2",
    ):
        v.VCardTitle("{{ vol_mode === 'volume' ? 'Volume Controls' : 'Isosurface Controls' }}")
        # Direct volume rendering (docs/volume_rendering_design.md): the
        # contour is the default; "Volume" draws the field itself. The
        # controls that only shape the contour hide in volume mode.
        v.VSelect(
            v_model=("vol_mode", "isosurface"),
            items=("vol_mode_options",),
            item_title="title",
            item_value="value",
            label="Render as",
            density="compact",
            hide_details=True,
            classes="mb-2",
            update_modelValue=(ctrl.set_volume_mode, "[$event]"),
        )
        with html.Div(v_if=("vol_mode !== 'volume'",)):
            v.VCardText("Isovalue ({{ isovalue_units }})")
            v.VSlider(
                v_model=("isovalue",),
                aria_label="Volume isovalue",
                __properties=[("aria_label", "aria-label")],
                min=0.001,
                max=1.0,
                step=0.001,
                thumb_label=True,
                update_modelValue=(ctrl.update_isovalue, "[$event]"),
            )
        v.VCardText("Colormap")
        # ``items=("colormap_options",)`` binds to the state list; a literal
        # multi-element tuple here would be parsed as a (varname, default)
        # binding pair, leaving the dropdown unusable.
        v.VSelect(
            v_model=("colormap",),
            items=("colormap_options",),
            update_modelValue=(ctrl.update_colormap, "[$event]"),
        )
        with html.Div(v_if=("vol_mode !== 'volume'",)):
            v.VSwitch(
                v_if=("esp_available",),
                v_model=("color_by_esp",),
                label="Map ESP onto surface",
                density="compact",
                color="primary",
                hide_details=True,
                update_modelValue=(ctrl.toggle_color_by_esp, "[$event]"),
            )
            v.VSwitch(
                v_model=("reduce_detail",),
                label="Reduce detail (faster)",
                density="compact",
                color="primary",
                hide_details=True,
                update_modelValue=(ctrl.toggle_reduce_detail, "[$event]"),
            )
            v.VCardText("Extra Isosurfaces", classes="text-caption mt-2")
            v.VBtn(
                "Add layer",
                small=True,
                variant="tonal",
                block=True,
                click=ctrl.add_extra_isosurface,
            )
            v.VChip(
                "iso {{ e.isovalue }} ({{ (e.opacity * 100).toFixed(0) }}%)",
                v_for=("(e, i) in extra_isosurfaces",),
                closable=True,
                size="small",
                classes="mt-1",
                update_modelValue=(ctrl.remove_extra_isosurface, "[i]"),
            )
        with html.Div(v_if=("vol_mode === 'volume'",)):
            # The transfer-function editor: a preset seeds the ramp, the
            # window is the range the levels cover, and six sliders shape
            # the opacity across it (docs/volume_rendering_design.md).
            v.VSelect(
                v_model=("vol_preset", "ramp"),
                items=("vol_preset_options",),
                item_title="title",
                item_value="value",
                label="Preset",
                density="compact",
                hide_details=True,
                classes="mb-2",
                update_modelValue=(ctrl.set_volume_preset, "[$event]"),
            )
            v.VCardText(
                "Window ({{ isovalue_units }}): {{ vol_window ? vol_window[0].toPrecision(3)"
                " + ' to ' + vol_window[1].toPrecision(3) : '' }}",
                classes="text-caption",
            )
            v.VSlider(
                v_model=("vol_window_low",),
                aria_label="Volume window low end",
                __properties=[("aria_label", "aria-label")],
                min=("vol_window_bounds[0]",),
                max=("vol_window_bounds[1]",),
                step=("vol_window_step",),
                hide_details=True,
                dense=True,
                update_modelValue=(ctrl.update_volume_window, "['low', $event]"),
            )
            v.VSlider(
                v_model=("vol_window_high",),
                aria_label="Volume window high end",
                __properties=[("aria_label", "aria-label")],
                min=("vol_window_bounds[0]",),
                max=("vol_window_bounds[1]",),
                step=("vol_window_step",),
                hide_details=True,
                dense=True,
                update_modelValue=(ctrl.update_volume_window, "['high', $event]"),
            )
            # The node-by-node curve editor: page JS draws into this element
            # and commits through the update_volume_nodes trigger
            # (app/tf_editor.py). The six sliders below are the coarse path
            # and show the curve's six-point sampling.
            v.VCardText(
                "Opacity curve: drag a node, click to add, double-click or Delete to"
                " remove; arrow keys move a focused node",
                classes="text-caption",
            )
            html.Div(
                id=_TF_EDITOR_ID,
                role="group",
                aria_label="Opacity curve editor",
                __properties=["role", ("aria_label", "aria-label")],
                classes="px-2 pb-1",
            )
            client.Script(_tf_editor_script())
            v.VCardText("Opacity ramp, start to end of the window", classes="text-caption")
            v.VSlider(
                v_model=("vol_ramp_0",),
                aria_label="Volume opacity at the window's start",
                __properties=[("aria_label", "aria-label")],
                min=0.0,
                max=1.0,
                step=0.05,
                hide_details=True,
                dense=True,
                update_modelValue=(ctrl.update_volume_ramp, "[0, $event]"),
            )
            v.VSlider(
                v_model=("vol_ramp_1",),
                aria_label="Volume opacity at 20% of the window",
                __properties=[("aria_label", "aria-label")],
                min=0.0,
                max=1.0,
                step=0.05,
                hide_details=True,
                dense=True,
                update_modelValue=(ctrl.update_volume_ramp, "[1, $event]"),
            )
            v.VSlider(
                v_model=("vol_ramp_2",),
                aria_label="Volume opacity at 40% of the window",
                __properties=[("aria_label", "aria-label")],
                min=0.0,
                max=1.0,
                step=0.05,
                hide_details=True,
                dense=True,
                update_modelValue=(ctrl.update_volume_ramp, "[2, $event]"),
            )
            v.VSlider(
                v_model=("vol_ramp_3",),
                aria_label="Volume opacity at 60% of the window",
                __properties=[("aria_label", "aria-label")],
                min=0.0,
                max=1.0,
                step=0.05,
                hide_details=True,
                dense=True,
                update_modelValue=(ctrl.update_volume_ramp, "[3, $event]"),
            )
            v.VSlider(
                v_model=("vol_ramp_4",),
                aria_label="Volume opacity at 80% of the window",
                __properties=[("aria_label", "aria-label")],
                min=0.0,
                max=1.0,
                step=0.05,
                hide_details=True,
                dense=True,
                update_modelValue=(ctrl.update_volume_ramp, "[4, $event]"),
            )
            v.VSlider(
                v_model=("vol_ramp_5",),
                aria_label="Volume opacity at the window's end",
                __properties=[("aria_label", "aria-label")],
                min=0.0,
                max=1.0,
                step=0.05,
                hide_details=True,
                dense=True,
                update_modelValue=(ctrl.update_volume_ramp, "[5, $event]"),
            )
            v.VSwitch(
                v_model=("vol_shade",),
                label="Shade by gradient",
                density="compact",
                color="primary",
                hide_details=True,
                update_modelValue=(ctrl.toggle_volume_shade, "[$event]"),
            )
            html.Div(
                "{{ vol_sent_shape ? vol_sent_shape.join('×') + ' voxels sent' : '' }}"
                "{{ vol_range ? ', range ' + vol_range[0].toPrecision(3) + ' to '"
                " + vol_range[1].toPrecision(3) : '' }}",
                classes="text-caption px-4 pb-1",
                v_if=("vol_sent_shape",),
            )
        with html.Div(v_if=("vol_mode !== 'volume'",)):
            v.VCardText("Opacity")
            v.VSlider(
                v_model=("opacity",),
                aria_label="Volume opacity",
                __properties=[("aria_label", "aria-label")],
                min=0.0,
                max=1.0,
                step=0.05,
                thumb_label=True,
                update_modelValue=(ctrl.update_opacity, "[$event]"),
            )
        # Grid-level periodic orbital replication — tiles the scalar field
        # before marching cubes so orbitals repeat seamlessly across cells.
        v.VSelect(
            v_if=("is_periodic && vol_mode !== 'volume'",),
            v_model=("periodic_replication", 0),
            items=("periodic_replication_options",),
            item_title="title",
            item_value="value",
            label="Cell replication",
            density="compact",
            hide_details=True,
            update_modelValue=(ctrl.set_periodic_replication, "[$event]"),
        )
        v.VDivider(v_if=("vol_mode !== 'volume'",))
        v.VCardText("Clip Planes", v_if=("vol_mode !== 'volume'",))
        v.VSwitch(
            v_if=("vol_mode !== 'volume'",),
            v_model=("clip_enabled",),
            label="Enable",
            update_modelValue=(ctrl.toggle_clip, "[$event]"),
        )
        v.VSwitch(
            v_if=("clip_enabled",),
            v_model=("show_slice",),
            label="Show as 2D slice",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_show_slice, "[$event]"),
        )
        with v.VRow(v_if=("clip_enabled",), dense=True):
            with v.VCol(cols=4):
                v.VCardText("X", classes="text-caption")
                v.VSlider(
                    v_model=("clip_x",),
                    aria_label="Clip plane X position",
                    __properties=[("aria_label", "aria-label")],
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    hide_details=True,
                    dense=True,
                    update_modelValue=(ctrl.update_clip, "['x', $event]"),
                )
            with v.VCol(cols=4):
                v.VCardText("Y", classes="text-caption")
                v.VSlider(
                    v_model=("clip_y",),
                    aria_label="Clip plane Y position",
                    __properties=[("aria_label", "aria-label")],
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    hide_details=True,
                    dense=True,
                    update_modelValue=(ctrl.update_clip, "['y', $event]"),
                )
            with v.VCol(cols=4):
                v.VCardText("Z", classes="text-caption")
                v.VSlider(
                    v_model=("clip_z",),
                    aria_label="Clip plane Z position",
                    __properties=[("aria_label", "aria-label")],
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    hide_details=True,
                    dense=True,
                    update_modelValue=(ctrl.update_clip, "['z', $event]"),
                )
        html.Div(
            "{{ clip_position_message }}",
            id="vv-clip-position",
            classes="text-caption px-4 pb-2",
            v_if=("clip_enabled && show_slice && clip_position_message",),
            aria_live="off",
            __properties=[("aria_live", "aria-live")],
        )

    # Cross-fade control (shown when two volume sections are selected)
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("crossfade_active",),
    ):
        v.VCardTitle("Cross-Fade Blend")
        v.VCardText("Blend between two volumes")
        v.VSlider(
            v_model=("crossfade_blend",),
            aria_label="Volume cross-fade blend",
            __properties=[("aria_label", "aria-label")],
            min=0.0,
            max=1.0,
            step=0.01,
            thumb_label=True,
            update_modelValue=(ctrl.update_crossfade, "[$event]"),
        )

    # Replication controls — only meaningful for periodic systems
    # (gated by `is_periodic` flag set in create_app from the structure).
    with v.VCard(classes="pa-2 mt-2", v_if=("is_periodic",)):
        v.VCardTitle("Periodic Replication")
        # One input per PERIODIC axis. A 2D slab (pbc = T,T,F) shows Nx/Ny only:
        # lattice[2] is a synthesized normal, so an Nz control would tile the
        # structure along a direction the calculation never treated as periodic.
        with v.VRow():
            with v.VCol(cols=4, v_if=("pbc_a",)):
                v.VTextField(
                    v_model=("replication_nx",),
                    label="Nx",
                    type="number",
                    min=1,
                )
            with v.VCol(cols=4, v_if=("pbc_b",)):
                v.VTextField(
                    v_model=("replication_ny",),
                    label="Ny",
                    type="number",
                    min=1,
                )
            with v.VCol(cols=4, v_if=("pbc_c",)):
                v.VTextField(
                    v_model=("replication_nz",),
                    label="Nz",
                    type="number",
                    min=1,
                )
        v.VBtn(
            "Apply Replication",
            block=True,
            color="primary",
            click=(
                ctrl.update_replication,
                "[replication_nx, replication_ny, replication_nz]",
            ),
        )
        v.VSwitch(
            v_model=("wrap_periodic_orbital",),
            label="Wrap orbital to cell centre (cyclic cluster)",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_wrap_periodic, "[$event]"),
        )

    # Trajectory controls (shown when trajectory is selected)
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("trajectory_n_frames > 0",),
    ):
        v.VCardTitle("Trajectory Animation")
        v.VCardText("Frame {{ trajectory_frame + 1 }} / {{ trajectory_n_frames }}")
        v.VSlider(
            v_model=("trajectory_frame",),
            aria_label="Trajectory frame",
            __properties=[("aria_label", "aria-label")],
            min=0,
            max=("trajectory_n_frames - 1",),
            step=1,
            thumb_label=True,
            update_modelValue=(ctrl.trajectory_frame, "[$event]"),
        )
        _filmstrip(
            html,
            rows="trajectory_thumbnails",
            index="frame",
            current="trajectory_frame",
            name="Trajectory filmstrip",
            seek=ctrl.trigger_name(ctrl.trajectory_frame),
        )
        with v.VRow(justify="center"):
            v.VBtn(
                icon="mdi-skip-previous",
                small=True,
                title="Previous trajectory frame",
                aria_label="Previous trajectory frame",
                __properties=[("aria_label", "aria-label")],
                click=(ctrl.trajectory_step, "[-1]"),
            )
            v.VBtn(
                icon=("trajectory_playing ? 'mdi-pause' : 'mdi-play'",),
                color="primary",
                title=(
                    "trajectory_playing ? 'Pause trajectory animation' : "
                    "'Start trajectory animation'",
                ),
                aria_label=(
                    "trajectory_playing ? 'Pause trajectory animation' : "
                    "'Start trajectory animation'",
                ),
                __properties=[("aria_label", "aria-label")],
                click=ctrl.trajectory_play_toggle,
            )
            v.VBtn(
                icon="mdi-skip-next",
                small=True,
                title="Next trajectory frame",
                aria_label="Next trajectory frame",
                __properties=[("aria_label", "aria-label")],
                click=(ctrl.trajectory_step, "[1]"),
            )

    # Reaction-path waypoint label (shown when a waypoint exists at the
    # current trajectory frame)
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("reaction_current_label",),
    ):
        v.VCardTitle("Reaction Waypoint")
        v.VChip(
            "{{ reaction_current_label }}",
            color="primary",
            label=True,
            size="small",
        )

    # Wavefunction (MO list) controls (shown when a wavefunction section
    # is the active selection)
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("wf_section_id",),
    ):
        v.VCardTitle("{{ wf_panel_title }}")
        v.VCardText(
            "Pick an MO and click Render. Adjust Orbital isovalue to choose "
            "the surface level (positive lobe blue, negative red)."
        )
        # One dropdown keyed on a composite "{spin}:{index}" value with a
        # rich title (energy / occupation / HOMO-LUMO). Spin is encoded in
        # the value, so there is no separate (desyncable) spin selector —
        # see wavefunction.mo_table / audit finding A2-03.
        v.VSelect(
            v_model=("wf_selected_mo",),
            items=("wf_mo_rows",),
            item_title="title",
            item_value="value",
            label="Molecular orbital",
        )
        v.VSelect(
            v_if=("wf_is_complex",),
            v_model=("wf_component",),
            items=(
                "[{title:'Real part',value:'real'},"
                "{title:'Imaginary part',value:'imag'},"
                "{title:'Magnitude |ψ|',value:'magnitude'}]",
            ),
            label="Orbital component",
        )
        # Re-localize: ask vibe-qc for a criterion this file does not carry.
        # Runs in a subprocess; the result lands as its own sidebar section.
        v.VSelect(
            v_model=("relocalize_source",),
            items=(
                "[{title:'Archived occupied orbitals',value:'supplied'},"
                "{title:'New RHF + localize',value:'fresh_rhf'}]",
            ),
            label="Orbital source",
            density="compact",
        )
        v.VAlert(
            "Runs a new molecular RHF calculation using the current geometry and archived basis. "
            "This is a new state.",
            v_if=("relocalize_source === 'fresh_rhf'",),
            density="compact",
            type="info",
        )
        with v.VRow(dense=True, classes="mt-2", align="center"):
            with v.VCol(cols=7, classes="py-0"):
                v.VSelect(
                    v_model=("relocalize_method",),
                    items=("relocalize_method_options",),
                    item_title="title",
                    item_value="value",
                    label="Re-localize with",
                    disabled=("!relocalize_supported || relocalize_running",),
                    density="compact",
                    hide_details=True,
                )
            with v.VCol(cols=5, classes="py-0"):
                v.VBtn(
                    "{{ relocalize_source === 'fresh_rhf' ? 'Calculate' : 'Localize' }}",
                    click=ctrl.run_relocalize,
                    loading=("relocalize_running",),
                    disabled=(
                        "!relocalize_supported || relocalize_running "
                        "|| relocalize_available === false",
                    ),
                    size="small",
                    block=True,
                )
        with v.VRow(dense=True, classes="ma-1"):
            v.VBtn(
                "Check backend",
                click=ctrl.check_relocalize_backend,
                loading=("relocalize_probe_running",),
                size="small",
            )
            v.VBtn("Backend settings", click="settings_dialog = true", size="small")
            v.VBtn(
                "Cancel", click=ctrl.cancel_relocalize, v_if=("relocalize_running",), size="small"
            )
            v.VBtn(
                "Clear re-localized overlays",
                click=ctrl.clear_relocalization,
                v_if=("relocalize_has_overlay",),
                size="small",
            )
        v.VAlert(
            "{{ relocalize_reason }}",
            v_if=("relocalize_reason",),
            density="compact",
            variant="tonal",
            type="info",
        )
        v.VAlert("{{ relocalize_backend_status }}", density="compact", variant="tonal")
        v.VAlert(
            "{{ relocalize_status }}",
            v_if=("relocalize_status",),
            density="compact",
            variant="tonal",
            type=(
                "relocalize_status.startsWith('Failed') "
                "|| relocalize_status.includes('cannot') "
                "|| relocalize_status.includes('needs vibe-qc') "
                "? 'warning' : 'info'",
            ),
            classes="mt-1 text-caption",
        )
        v.VAlert(
            "{{ relocalize_result_summary }}",
            v_if=("relocalize_result_summary",),
            density="compact",
            variant="tonal",
            classes="mt-1 text-caption",
        )
        with html.Table(v_if=("relocalize_charge_rows.length",), classes="text-caption"):
            with html.Thead(), html.Tr():
                html.Th("Atom")
                html.Th("IAO charge (e)")
            with html.Tbody(), html.Tr(v_for="row in relocalize_charge_rows", key="row.atom"):
                html.Td("{{ row.atom }}")
                html.Td("{{ row.charge }}")
        with v.VRow(dense=True, classes="mt-1"):
            v.VBtn(
                icon="mdi-skip-previous",
                small=True,
                variant="tonal",
                title="Previous molecular orbital",
                aria_label="Previous molecular orbital",
                __properties=[("aria_label", "aria-label")],
                click=(ctrl.step_mo_render, "[-1]"),
            )
            v.VBtn(
                icon=("wf_animating ? 'mdi-pause' : 'mdi-play'",),
                small=True,
                variant="tonal",
                color=("wf_animating ? 'primary' : ''",),
                title=(
                    "wf_animating ? 'Pause molecular orbital animation' : "
                    "'Start molecular orbital animation'",
                ),
                aria_label=(
                    "wf_animating ? 'Pause molecular orbital animation' : "
                    "'Start molecular orbital animation'",
                ),
                __properties=[("aria_label", "aria-label")],
                click=ctrl.toggle_mo_animation,
            )
            v.VBtn(
                icon="mdi-skip-next",
                small=True,
                variant="tonal",
                title="Next molecular orbital",
                aria_label="Next molecular orbital",
                __properties=[("aria_label", "aria-label")],
                click=(ctrl.step_mo_render, "[1]"),
            )
        v.VTextField(
            v_model=("wf_n_per_dim",),
            label="Grid points / axis",
            type="number",
            min=20,
            max=120,
        )
        v.VTextField(
            v_model=("isovalue",),
            label="Orbital isovalue",
            type="number",
            min=0.001,
            step=0.005,
            hint="Click Render MO to apply",
            persistent_hint=True,
        )
        # One-click frontier orbitals (design refresh 2026 roadmap).
        with v.VRow(dense=True, classes="mb-1"):
            v.VBtn(
                "HOMO",
                size="small",
                variant="tonal",
                title="Render the highest occupied MO",
                click=(ctrl.render_frontier, "['HOMO']"),
            )
            v.VBtn(
                "LUMO",
                size="small",
                variant="tonal",
                title="Render the lowest unoccupied MO",
                click=(ctrl.render_frontier, "['LUMO']"),
            )
        v.VBtn(
            "Render MO",
            block=True,
            color="primary",
            click=(ctrl.render_mo, "[wf_selected_mo]"),
        )
        # Lobe opacity. The Opacity slider lives in the volume panel, which is
        # not shown while a wavefunction section is selected — so an orbital
        # could not be made opaque at all from here. Retints the existing
        # actors, so it is instant (no MO grid re-evaluation).
        v.VCardText("Orbital opacity", classes="pb-0")
        v.VSlider(
            v_model=("mo_opacity",),
            aria_label="Molecular orbital opacity",
            __properties=[("aria_label", "aria-label")],
            min=0.1,
            max=1.0,
            step=0.05,
            thumb_label=True,
            hide_details=True,
            update_modelValue=(ctrl.update_mo_opacity, "[$event]"),
        )
        # Phase E3: compute the total electron density ρ = Σ occ_i |ψ_i|² from
        # the MO coefficients and render it as an isosurface (no stored volume
        # needed). The status reports ∫ρ dV as a self-consistency check.
        v.VBtn(
            "Compute total density",
            block=True,
            variant="tonal",
            classes="mt-2",
            click=(ctrl.compute_wf_density, "[false]"),
        )
        # Only meaningful for an unrestricted wavefunction: a restricted one
        # has rho_alpha == rho_beta and the difference is identically zero.
        v.VBtn(
            "Compute spin density",
            block=True,
            variant="tonal",
            classes="mt-1",
            v_if=("wf_spin_unrestricted",),
            click=(ctrl.compute_wf_density, "[true]"),
        )
        v.VBtn(
            "Compute ELF",
            block=True,
            variant="tonal",
            classes="mt-1",
            click=ctrl.compute_wf_elf,
        )
        v.VSlider(
            v_model=("wf_elf_iso",),
            aria_label="ELF isovalue",
            __properties=[("aria_label", "aria-label")],
            min=0.05,
            max=0.95,
            step=0.05,
            label="ELF isovalue",
            thumb_label=True,
            density="compact",
            hide_details=True,
            classes="mt-1",
        )
        v.VBtn(
            "Compute NCI",
            block=True,
            variant="tonal",
            classes="mt-1",
            click=ctrl.compute_wf_nci,
        )
        v.VBtn(
            "Compute Laplacian",
            block=True,
            variant="tonal",
            classes="mt-1",
            click=ctrl.compute_wf_laplacian,
        )
        v.VBtn(
            "Show energy diagram",
            block=True,
            variant="tonal",
            classes="mt-1",
            click=ctrl.show_energy_diagram,
        )
        # Toggle to hide/show whichever computed wavefunction isosurface was
        # rendered most recently without leaving the section.
        v.VSwitch(
            v_model=("mo_visible", True),
            label="Show isosurface",
            density="compact",
            color="primary",
            hide_details=True,
            classes="mt-2",
            v_if=("wf_surface_kind !== null",),
            update_modelValue=(ctrl.toggle_mo_visibility, "[$event]"),
        )

    # Camera bookmarks (from manifest) + user bookmarks
    with v.VCard(classes="pa-2 mt-2"):
        v.VCardTitle("Bookmarks")
        # Manifest-defined bookmarks (read-only)
        v.VSelect(
            v_if=("bookmark_names.length > 0",),
            v_model=("selected_bookmark",),
            items=("bookmark_names",),
            label="Preset",
            update_modelValue=(ctrl.apply_bookmark, "[$event]"),
            density="compact",
        )
        # User bookmarks
        with v.VRow(classes="mt-2", v_if=("user_bookmark_names.length > 0",)):
            v.VSelect(
                v_model=("selected_user_bookmark",),
                items=("user_bookmark_names",),
                label="My bookmarks",
                density="compact",
                update_modelValue=(ctrl.apply_user_bookmark, "[$event]"),
            )
        with v.VRow(classes="mt-1", dense=True):
            v.VTextField(
                v_model=("new_bookmark_name", ""),
                label="Bookmark name",
                density="compact",
                hide_details=True,
                classes="mr-1",
            )
            v.VBtn(
                "Save View",
                color="primary",
                size="small",
                click=(ctrl.save_user_bookmark, "[new_bookmark_name]"),
            )
        # Session save/load
        with v.VRow(classes="mt-2", dense=True):
            v.VBtn(
                "Save Session",
                size="small",
                variant="tonal",
                click=ctrl.save_session,
            )
            v.VBtn(
                "Load Session",
                size="small",
                variant="tonal",
                classes="ml-1",
                click=(ctrl.load_session, "['']"),
            )
            v.VTextField(
                v_model=("session_path",),
                label="Path",
                density="compact",
                hide_details=True,
                classes="ml-1",
            )

    # Vibration controls (shown when vibrations section is selected)
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("vibration_n_modes > 0",),
    ):
        v.VCardTitle("Vibrational Modes")
        v.VSelect(
            v_model=("vibration_mode",),
            items=("vibration_mode_items",),
            label="Mode",
            item_title="title",
            item_value="value",
            update_modelValue=(ctrl.vibration_changed, "[vibration_mode, vibration_amplitude]"),
        )
        v.VCardText("{{ vibration_frequencies[vibration_mode] }} cm\u207b\u00b9")
        v.VCardText(
            "Displacement amplitude: ×{{ Number(vibration_amplitude).toFixed(1) }} "
            "(largest atomic excursion "
            "{{ (vibration_peak_default * vibration_amplitude).toFixed(2) }} Å)"
        )
        v.VSlider(
            v_model=("vibration_amplitude",),
            aria_label="Vibration displacement amplitude",
            __properties=[("aria_label", "aria-label")],
            min=0.0,
            max=3.0,
            step=0.1,
            thumb_label=True,
            update_modelValue=(ctrl.vibration_changed, "[vibration_mode, $event]"),
        )
        # Play/pause animates the displacement through cos(phase); pause
        # freezes it at the nearest scrubber step.
        v.VBtn(
            "{{ vibration_playing ? 'Pause' : 'Play' }}",
            block=True,
            color="primary",
            prepend_icon=("vibration_playing ? 'mdi-pause' : 'mdi-play'",),
            click=ctrl.vibration_play_toggle,
        )
        # The phase scrubber: one period in VIBRATION_PHASE_STEPS steps, and
        # a filmstrip of the mode at every second step.
        v.VCardText(
            "Phase {{ vibration_phase_step }}/" + str(VIBRATION_PHASE_STEPS) + " of the period"
        )
        v.VSlider(
            v_model=("vibration_phase_step",),
            aria_label="Vibration phase",
            __properties=[("aria_label", "aria-label")],
            min=0,
            max=VIBRATION_PHASE_STEPS - 1,
            step=1,
            thumb_label=True,
            update_modelValue=(ctrl.vibration_seek, "[$event]"),
        )
        _filmstrip(
            html,
            rows="vibration_thumbnails",
            index="step",
            current="vibration_phase_step",
            name="Vibration filmstrip",
            seek=ctrl.trigger_name(ctrl.vibration_seek),
        )

    # Bonds measurement result
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("measure_result",),
    ):
        v.VCardTitle("Measurements")
        v.VCardText(
            "{{ measure_result }}",
            classes="text-caption",
            style="white-space: pre-line; font-family: monospace;",
        )
