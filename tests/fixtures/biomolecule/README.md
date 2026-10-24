# Recorded public database responses

Retrieved 2026-10-22, unmodified response bodies:

- `1crn.cif`: https://files.rcsb.org/download/1CRN.cif — RCSB/wwPDB, CC0.
- `alphafold-P69905.json`: https://alphafold.ebi.ac.uk/api/prediction/P69905
- `AF-P69905-F1.cif`: https://alphafold.ebi.ac.uk/files/AF-P69905-F1-model_v6.cif
  — AlphaFold Protein Structure Database, Google DeepMind / EMBL-EBI,
  CC BY 4.0. Attribution: Varadi et al., Nucleic Acids Research (2024),
  doi:10.1093/nar/gkad1011. Human hemoglobin subunit alpha, UniProt P69905.
- `ethanol.sdf`: https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/ethanol/SDF?record_type=3d
  — NCBI PubChem, CID 702. Public chemical structure data.

The JSON checksum map records these exact bytes. Tests replay these bodies;
they do not contact the services. The existing inspection/1CRN.pdb fixture
provides independent-format parity. There are no personal search terms.
