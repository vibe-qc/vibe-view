Breaking change for 3.1: `vibe-view animate --mode N` now counts modes from 1,
matching `show --normal-mode`, `open --mode` and the browser. Update scripts
written for 3.0 by adding 1 to every explicit `animate --mode` value (for example,
`--mode 6` becomes `--mode 7`). Omitting `--mode` still selects the first
positive-frequency mode. Zero and out-of-range mode numbers are refused.
