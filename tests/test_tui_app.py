"""Interaction tests for the Textual terminal viewer (the [tui] extra)."""

from __future__ import annotations

from pathlib import Path

import pytest

# ── the app ───────────────────────────────────────────────────────────────


def _drive(coro_factory):
    """Run a Textual pilot session without pulling in pytest-asyncio.

    Textual's `run_test` is an async context manager, so the app tests need
    an event loop; asyncio.run gives them one without adding a test-only
    plugin dependency to the [test] extra.
    """
    import asyncio

    return asyncio.run(coro_factory())


def _declared_natural_app():
    """Open the legacy NO fixture with the metadata new writers emit."""
    from dataclasses import replace

    from vibeview.tui.app import VibeViewTUI

    path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
    app = VibeViewTUI(path)
    source = app.reader.read_wavefunction_gto("wf")
    app.reader.set_wavefunction_overlay(
        "wf",
        replace(source, occupation_semantics="electron_occupation"),
    )
    return app


def test_app_starts_and_navigates(sample_qvf):
    async def body():
        from vibeview.tui.app import VibeViewTUI

        app = VibeViewTUI(sample_qvf)
        async with app.run_test(size=(100, 30)) as pilot:
            assert app.current[1] == "structure"
            await pilot.press("tab")
            assert app.current[1] == "volume.density"
            await pilot.press("m")
            assert app.representation != "ball_and_stick"
            await pilot.press("r")
            await pilot.press("t")
            assert app.show_table

    pytest.importorskip("textual")
    _drive(body)


def test_app_starts_when_no_color_is_requested(sample_qvf, monkeypatch):
    """Every custom strip must carry a Style for Textual's monochrome filter."""

    monkeypatch.setenv("NO_COLOR", "1")

    async def body():
        from vibeview.tui.app import VibeViewTUI

        app = VibeViewTUI(sample_qvf)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert "nocolor" in app.screen.pseudo_classes
            assert app.current[1] == "structure"
            await pilot.press("tab")
            assert app.current[1] == "volume.density"

    pytest.importorskip("textual")
    _drive(body)


def test_app_viewport_paints_content(sample_qvf):
    async def body():
        from vibeview.tui.app import VibeViewTUI

        app = VibeViewTUI(sample_qvf)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            grid = app._build_grid(80, 24)
            assert grid is not None
            assert grid.filled.any(), "viewport rendered nothing for a structure"

    pytest.importorskip("textual")
    _drive(body)


def test_app_isovalue_and_replication_keys(sample_qvf):
    async def body():
        from vibeview.tui.app import VibeViewTUI

        app = VibeViewTUI(sample_qvf)
        async with app.run_test(size=(100, 30)) as pilot:
            before = app.isovalue
            await pilot.press("I")
            assert app.isovalue > before
            await pilot.press("x")
            assert app.replication[0] == 2
            await pilot.press("X")
            assert app.replication[0] == 1

    pytest.importorskip("textual")
    _drive(body)


def test_app_chart_toggle(sample_qvf):
    async def body():
        from vibeview.tui.app import VibeViewTUI

        app = VibeViewTUI(sample_qvf)
        async with app.run_test(size=(100, 30)) as pilot:
            assert app.show_chart is False
            await pilot.press("g")
            assert app.show_chart is True

    pytest.importorskip("textual")
    _drive(body)


def test_wavefunction_section_renders_and_selects_orbitals():
    """A wavefunction section is a 3D surface picker, not a read-only pane."""

    async def body():
        app = _declared_natural_app()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("tab")
            await pilot.pause()

            assert app.current[:2] == ("wf", "wavefunction.gto")
            assert app.viewport.display
            assert app.orbital_table.display
            assert app.focused is app.orbital_table
            assert app.wavefunction_surface == "mo:restricted:0"
            assert app._current_scene().meshes

            await pilot.press("n")
            await pilot.pause()
            assert app.wavefunction_surface == "mo:restricted:1"
            assert len(app._current_scene().meshes) == 2

            await pilot.press("D")
            await pilot.pause()
            assert app.wavefunction_surface == "density"
            assert len(app._current_scene().meshes) == 1

            await pilot.press("S")
            assert app.wavefunction_surface == "density"
            assert "unavailable" in app.status_text

            await pilot.press("shift+tab")
            assert app.current[1] == "structure"
            await pilot.press("tab")
            await pilot.pause()
            assert app.wavefunction_surface == "density"
            assert app.orbital_table.cursor_row == 0

            before = app.isovalue
            await pilot.press("I")
            assert app.isovalue > before
            assert app._current_scene().meshes

            await pilot.press("o")
            assert app._current_scene().meshes == []

    pytest.importorskip("textual")
    _drive(body)


def test_wavefunction_orbital_table_enter_renders_highlighted_row():
    """Arrow navigation plus Enter must activate the row under the cursor."""

    async def body():
        app = _declared_natural_app()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("tab")
            await pilot.pause()

            # Density occupies row zero; the default orbital is highlighted.
            app.orbital_table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause()
            assert app.wavefunction_surface == "density"
            assert "density" in app.status_text.lower()

            app.orbital_table.move_cursor(row=2)
            await pilot.press("enter")
            await pilot.pause()
            assert app.wavefunction_surface == "mo:restricted:1"
            assert len(app._current_scene().meshes) == 2

    pytest.importorskip("textual")
    _drive(body)


def test_wavefunction_orbital_table_first_click_renders_row():
    """A single click must activate the row, not only move its cursor."""

    async def body():
        app = _declared_natural_app()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("tab")
            await pilot.pause()
            assert app.wavefunction_surface == "mo:restricted:0"

            # Header is row zero in screen coordinates; y=3 is MO #1 after
            # the density and default-MO rows. It has not been highlighted.
            assert await pilot.click("#orbital_table", offset=(3, 3))
            await pilot.pause()
            assert app.wavefunction_surface == "mo:restricted:1"
            assert len(app._current_scene().meshes) == 2

    pytest.importorskip("textual")
    _drive(body)


def test_help_is_scrollable_and_takes_focus_at_small_terminal_size():
    """Wavefunction keys in the lower help block remain reachable at 80x24."""

    async def body():
        from vibeview.tui.app import VibeViewTUI

        path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
        app = VibeViewTUI(path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("tab")
            await pilot.press("?")
            await pilot.pause()

            assert app.focused is app.text_scroll
            assert app.text_scroll.max_scroll_y > 0
            before = app.text_scroll.scroll_y
            await pilot.press("pagedown")
            await pilot.pause()
            assert app.text_scroll.scroll_y > before

            await pilot.press("t")
            assert app.focused is app.orbital_table
            before_row = app.orbital_table.cursor_row
            await pilot.press("down")
            assert app.orbital_table.cursor_row != before_row

    pytest.importorskip("textual")
    _drive(body)


def test_text_only_section_fills_body_without_blank_graphics_region():
    """Hiding the viewport also removes its layout allocation."""

    async def body():
        from vibeview.tui.app import VibeViewTUI

        path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
        app = VibeViewTUI(path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("tab", "tab")
            await pilot.pause()

            assert app.current[:2] == ("citations0", "citations")
            assert not app.content.display
            assert app.text_scroll.display
            body_height = app.query_one("#body").region.height
            assert app.text_scroll.region.height >= body_height - 1

    pytest.importorskip("textual")
    _drive(body)


def test_localized_wavefunction_rows_all_render_without_fake_energies():
    """Localized coefficients use atom labels, and every row remains drawable."""

    async def body():
        from dataclasses import replace

        import numpy as np

        from vibeview.tui.app import VibeViewTUI

        path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
        app = VibeViewTUI(path)
        source = app.reader.read_wavefunction_gto("wf")
        n_mo = source.mo_coefficients.shape[0]
        populations = np.tile(np.array([[0.55, 0.45]]), (n_mo, 1))
        app.reader.set_wavefunction_overlay(
            "wf",
            replace(
                source,
                orbital_kind="localized",
                atom_populations=populations,
                n_centres=np.full(n_mo, 2),
                localization_method="ibo",
            ),
        )

        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("tab")
            await pilot.pause()

            assert app.wavefunction_surface == "mo:restricted:0"
            for row in app._rows_for_wavefunction("wf"):
                assert "Eh" not in row["title"]
                app._select_wavefunction_surface(f"mo:{row['value']}")
                assert app._current_scene().meshes

    pytest.importorskip("textual")
    _drive(body)


def test_unrestricted_wavefunction_routes_beta_mos_and_spin_density():
    """Composite spin:index rows must reach beta MOs and signed spin density."""

    async def body():
        from dataclasses import replace

        import numpy as np

        from vibeview.tui.app import VibeViewTUI

        path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
        app = VibeViewTUI(path)
        source = app.reader.read_wavefunction_gto("wf")
        coeffs = source.mo_coefficients
        n_mo = coeffs.shape[0]
        occupations = np.zeros(n_mo)
        occupations[0] = 1.0
        app.reader.set_wavefunction_overlay(
            "wf",
            replace(
                source,
                spin="unrestricted",
                orbital_kind="canonical",
                energies=None,
                occupations=None,
                mo_coefficients=None,
                alpha_energies=np.arange(n_mo, dtype=float),
                alpha_occupations=occupations,
                beta_energies=np.arange(n_mo, dtype=float) + 0.1,
                beta_occupations=occupations.copy(),
                mo_coefficients_alpha=coeffs,
                mo_coefficients_beta=coeffs[[1, 0, 2, 3]],
            ),
        )

        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("tab")
            await pilot.pause()

            keys = app._wavefunction_table_keys
            assert "mo:alpha:0" in keys and "mo:beta:0" in keys
            assert "spin_density" in keys

            app._select_wavefunction_surface("mo:beta:1")
            assert app.wavefunction_surface == "mo:beta:1"
            assert app._current_scene().meshes

            await pilot.press("S")
            await pilot.pause()
            assert app.wavefunction_surface == "spin_density"
            assert app._current_scene().meshes

    pytest.importorskip("textual")
    _drive(body)


def test_natural_transition_weights_do_not_masquerade_as_total_density():
    """NTO weights sum to about one and cannot define electron density."""

    async def body():
        from dataclasses import replace

        import numpy as np

        from vibeview.tui.app import VibeViewTUI

        path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
        app = VibeViewTUI(path)
        source = app.reader.read_wavefunction_gto("wf")
        app.reader.set_wavefunction_overlay(
            "wf",
            replace(
                source,
                occupations=np.array([1.0, 0.0043, 0.0, 0.0]),
            ),
        )

        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("tab")
            await pilot.pause()

            assert "density" not in app._wavefunction_table_keys
            assert app.wavefunction_surface == "mo:restricted:0"
            assert not any(
                "HONO" in row["title"] or "LUNO" in row["title"]
                for row in app._rows_for_wavefunction("wf")
            )
            assert all(
                "occ " not in row["title"]
                for row in app._rows_for_wavefunction("wf")
            )
            assert "value 1.00" in app._rows_for_wavefunction("wf")[0]["title"]

            before = app.wavefunction_surface
            await pilot.press("D")
            assert app.wavefunction_surface == before
            assert "transition weights" in app.status_text

    pytest.importorskip("textual")
    _drive(body)


def test_wavefunction_without_occupations_does_not_offer_density():
    """Missing occupations cannot be interpreted as a zero-electron density."""

    async def body():
        from dataclasses import replace

        from vibeview.tui.app import VibeViewTUI

        path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
        app = VibeViewTUI(path)
        source = app.reader.read_wavefunction_gto("wf")
        app.reader.set_wavefunction_overlay(
            "wf",
            replace(source, orbital_kind="canonical", occupations=None),
        )

        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("tab")
            await pilot.pause()

            assert "density" not in app._wavefunction_table_keys
            before = app.wavefunction_surface
            await pilot.press("D")
            assert app.wavefunction_surface == before
            assert "no occupations" in app.status_text

    pytest.importorskip("textual")
    _drive(body)


def test_arbitrarily_named_one_electron_nto_never_masquerades_as_density():
    """A value sum of one can equal N_e, so unmarked natural data is unsafe."""

    from dataclasses import replace

    import numpy as np

    from vibeview.renderers.wavefunction import WavefunctionRenderer
    from vibeview.tui.app import VibeViewTUI

    path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
    app = VibeViewTUI(path)
    source = app.reader.read_wavefunction_gto("wf")
    weights = np.zeros_like(source.occupations)
    weights[0] = 1.0
    nto = replace(
        source,
        occupations=weights,
        orbital_kind="natural",
        occupation_semantics=None,
    )
    app.reader.provenance["n_electrons"] = 1
    app.reader.set_wavefunction_overlay("excited_orbitals", nto)

    assert not app._wavefunction_density_is_physical("excited_orbitals", nto)
    assert "does not declare" in app._wavefunction_density_notes["excited_orbitals"]

    renderer = WavefunctionRenderer(
        app.reader.get_section("excited_orbitals"), app.reader
    )
    rows = renderer.mo_table()
    assert all("occ " not in row["title"] for row in rows)
    assert "value 1.00" in rows[0]["title"]
    assert not any("HONO" in row["title"] or "LUNO" in row["title"] for row in rows)

    marked = replace(nto, occupation_semantics="transition_weight")
    app.reader.set_wavefunction_overlay("excited_transition", marked)
    assert not app._wavefunction_density_is_physical(
        "excited_transition", marked
    )
    marked_rows = WavefunctionRenderer(
        app.reader.get_section("excited_transition"), app.reader
    ).mo_table()
    assert "weight 1.00" in marked_rows[0]["title"]


def test_wavefunction_sampling_warning_survives_mesh_cache(monkeypatch):
    """An incomplete g+ evaluation stays disclosed on cached contours."""

    async def body():
        from vibeview.tui.app import VibeViewTUI

        path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
        app = VibeViewTUI(path)
        renderer = app._wavefunction_renderer("wf")
        evaluate = renderer.evaluate_mo

        def diagnosed(*args, **kwargs):
            grid, values = evaluate(*args, **kwargs)
            renderer.last_dropped_l_fraction = 0.24
            renderer.last_dropped_l_max = 4
            return grid, values

        monkeypatch.setattr(renderer, "evaluate_mo", diagnosed)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("tab")
            await pilot.pause()
            app._current_scene()
            assert "24%" in app.status_text and "incomplete" in app.status_text

            # Second assembly uses both caches and must retain the warning.
            app._current_scene()
            assert "24%" in app.status_text and "cached" in app.status_text

            # Field sampling has a larger, independently bounded cache. Its
            # eviction must not strip accuracy notes from a surviving mesh.
            app._wavefunction_field_cache.clear()
            app._current_scene()
            assert "24%" in app.status_text and "incomplete" in app.status_text

    pytest.importorskip("textual")
    _drive(body)


def test_empty_wavefunction_contour_stays_empty_on_cache_hit():
    """A cached no-crossing result must never become a rendered claim."""

    async def body():
        from vibeview.tui.app import VibeViewTUI

        path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
        app = VibeViewTUI(path)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("tab")
            await pilot.pause()
            app.isovalue = 999.0
            app._mesh_cache.clear()

            assert app._current_scene().meshes == []
            assert "no surface crosses" in app.status_text
            assert app._current_scene().meshes == []
            assert "no surface crosses" in app.status_text
            assert "cached" in app.status_text
            assert not app.status_text.startswith("rendered")

    pytest.importorskip("textual")
    _drive(body)


def test_wavefunction_contour_failures_are_retried_not_cached(monkeypatch):
    """A transient contour exception remains visible and can recover."""

    async def body():
        from vibeview.tui import scene as scenelib
        from vibeview.tui.app import VibeViewTUI

        path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
        app = VibeViewTUI(path)
        calls = 0

        def fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("transient contour failure")

        monkeypatch.setattr(scenelib, "sampled_field_meshes", fail)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("tab")
            await pilot.pause()
            app._mesh_cache.clear()
            calls = 0

            assert app._current_scene().meshes == []
            assert "transient contour failure" in app.status_text
            assert app._current_scene().meshes == []
            assert "transient contour failure" in app.status_text
            assert calls == 2

    pytest.importorskip("textual")
    _drive(body)


def test_mesh_cache_is_bounded():
    """Several large contours cannot accumulate for the life of the TUI."""

    from vibeview.tui.app import _MESH_CACHE_SIZE, VibeViewTUI

    path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
    app = VibeViewTUI(path)
    for index in range(_MESH_CACHE_SIZE + 3):
        app._remember_meshes(("test", index), [])

    assert len(app._mesh_cache) == _MESH_CACHE_SIZE
    assert ("test", 0) not in app._mesh_cache


def test_hidden_orbital_table_never_steals_structure_arrow_keys():
    """Leaving a wavefunction restores arrow-key camera rotation."""

    async def body():
        import numpy as np

        from vibeview.tui.app import VibeViewTUI

        path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
        app = VibeViewTUI(path)
        async with app.run_test(size=(80, 24)) as pilot:
            assert app.current[1] == "structure"
            assert app.focused is not app.orbital_table
            before = app.camera.rotation.copy()
            await pilot.press("right")
            assert not np.allclose(app.camera.rotation, before)

            await pilot.press("tab")
            assert app.current[1] == "wavefunction.gto"
            assert app.focused is app.orbital_table

            await pilot.press("shift+tab")
            assert app.current[1] == "structure"
            assert app.focused is not app.orbital_table
            before = app.camera.rotation.copy()
            await pilot.press("right")
            assert not np.allclose(app.camera.rotation, before)

    pytest.importorskip("textual")
    _drive(body)
