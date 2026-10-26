`volume.complex` sections (a complex scalar per grid point, such as a complex
orbital) are drawn as their real or imaginary part, or as |ψ| coloured by
phase on a cyclic colour map, chosen in the browser's Complex Field card,
which bookmarks keep. A field below the default isovalue opens at half its
largest |ψ|. `vibe-view capture --part real|imag|modulus` and
`batch --volumes` draw them, the terminal draws the real part, and
`QVFReader.read_volume_complex` reads them (#157).
