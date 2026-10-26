`wavefunction.bloch` sections (a crystal's orbitals at several k-points) are
evaluated on demand with the lattice phase of each k-point and drawn as real
or imaginary lobes or as |ψ| coloured by phase, chosen in the browser's Bloch
Orbitals card, which bookmarks keep. `vibe-view table --kind
wavefunction.bloch` lists the orbitals, `vibe-view capture` draws one, and
`QVFReader.read_wavefunction_bloch` reads them. Every QVF kind now has a
renderer (#162).
