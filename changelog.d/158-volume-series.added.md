`volume.series` sections (several fields on one grid, such as the density
along a trajectory) are stepped and played slab by slab in the browser's
Volume Series card, on each slab's own trajectory frame. `vibe-view capture
--frame N`, `show --frame N`, `batch --volumes` and the TUI's frame keys draw
them, and `QVFReader.read_volume_series` reads them (#158).
