`properties.electric`, `properties.elastic` and `properties.dielectric`
sections open as tables: multipoles and polarizabilities, the elastic
constants with a mechanical-stability check, and the dielectric tensors, Born
charges (with their sum) and piezoelectric tensor. An electric dipole is drawn
as an arrow from its origin, standing in for the root dipole arrow while the
section is open. `vibe-view table --kind properties.*` prints them, `vibe-view
capture` draws the dipole, and `QVFReader.read_properties` reads them (#161).
