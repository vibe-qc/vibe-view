# Sample renderer plugin

A complete vibe-view renderer plugin (extension API v1) for the vendor
section kind `x_example.timings`: a list of calculation steps with their
wall-clock time, shown as a table in the browser's details panel.

    pip install docs/examples/renderer-plugin
    python docs/examples/renderer-plugin/make_archive.py timings.qvf
    vibe-view info timings.qvf        # the timings section is "rendered via plugin"
    vibe-view doctor                  # lists the plugin under "Renderer plugins"
    vibe-view open timings.qvf        # the "Step Timings" row opens the table

`pyproject.toml` declares the entry point, named after the kind;
`src/vibeview_example_renderer/__init__.py` holds the renderer class and the
`renderer()` factory; `make_archive.py` writes a water archive carrying the
section with the standard library alone. The contract is documented in the
Python SDK page under "Extending vibe-view", and the candidate pipeline
installs this package and runs `scripts/smoke_renderer_plugin.py` against it.

Licensed under MPL-2.0, like vibe-view.
