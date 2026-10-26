`volume.vector` sections (a vector per grid point, such as a density gradient)
are drawn as arrows coloured by magnitude in the browser, with a Vector Field
card for arrow spacing, length, cutoff and colour map that bookmarks keep;
`vibe-view capture` and `batch --volumes` draw the same arrows and the terminal
a magnitude surface. `QVFReader.read_volume_vector` reads them (#156).
