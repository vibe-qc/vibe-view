`vibe-view animate` without `--mode` shows the first positive-frequency mode
instead of mode 0, which is usually a translation that does not move. An
out-of-range `--mode` is refused with the valid range instead of being
reported as a missing vibrations section (#152).
