`vibe-view animate -o movie.gif` writes a GIF instead of failing in ffmpeg:
without `--format`, the format follows a `.gif` or `.mp4` output name, and a
`--format` that contradicts the name is refused before any frame renders (#148).
