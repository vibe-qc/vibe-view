# CLAUDE.md

The project rules are in `AGENTS.md`, imported here so every Claude Code
session loads them:

@AGENTS.md

## Claude Code notes

These are additions only; `AGENTS.md` wins if the two ever disagree.

- **Worktrees don't isolate the Python import.** `git worktree` isolates the
  index and `HEAD`, but a venv's editable install still points at one
  checkout. From a worktree, set `PYTHONPATH=<worktree>/src`, or install into
  a venv of your own, and confirm `vibeview.__file__` before trusting a run.
- **Don't install into a venv another session owns.** When you need extra
  tools, such as the Sphinx toolchain, create a throwaway venv in your
  scratchpad instead.
- **Check `main` before you push.** Run `git fetch`, then
  `git rev-list --count HEAD..origin/main`. If `main` has moved, rebase and
  re-run the tests your change touches.
- **Answer when another session asks where you are working.** Reply with the
  path; that is how shared checkouts stay safe.
