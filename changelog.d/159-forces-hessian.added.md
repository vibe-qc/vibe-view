`forces` sections are drawn as arrows along F = −∇E (the file stores the
gradient) with a Forces card and per-atom table, and `hessian` sections
show their harmonic frequencies; `vibe-view table --kind forces|hessian`
print both and `vibe-view capture` draws the arrows. `QVFReader.read_forces`
and `read_hessian` read them (#159).
