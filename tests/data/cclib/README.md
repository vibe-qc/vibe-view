# Optional real-output regression corpus

`references.json` records four unmodified cclib regression outputs (Gaussian16,
GAMESS-US, NWChem7 and Psi4), immutable upstream revision and SHA256, parsed
frame counts, SCF energies in eV and vibrational frequencies in cm^-1.
The reference arrays are from cclib1.8.1's documented interface. These tests
verify preservation and unit conversion, not the producing program's chemistry.
Upstream source and data are BSD-3-Clause; its full notice is in LICENSE.

Fetch is explicit; normal tests never access the network:

```sh
python tests/data/cclib/fetch.py /tmp/view-result-corpus
VIBEVIEW_CCLIB_CORPUS=/tmp/view-result-corpus python -m pytest tests/test_result_import.py
```

Install `vibeview[results]` to run the parser cases. A missing corpus skips only
these four recorded-output cases; native mapping, missing-dependency and format
precedence tests remain active.
