# Contributing

Thanks for helping improve this project. The most valuable contributions are:

- **New playbook recipes** — a DRC violation type the pipeline doesn't yet
  reduce to zero, with a board that reproduces it.
- **Test reports** — other KiCad versions (8/9), other operating systems,
  bigger or stranger EasyEDA Pro boards.
- **Bug reports** — please include your KiCad version, the exact command,
  and the board's DRC counts before and after the failing step.

## Ground rules

- The acceptance criterion never changes: a full `--severity-all` DRC with
  refilled zones must read **0 violations / 0 unconnected items**. A fix that
  only hides a category globally (instead of repairing it or scoping an
  exemption to the items that need it) is not a fix.
- Scripts must stay parameterised: no hard-coded paths, board names, or
  per-board data in `scripts/`. Board-specific adjustments belong in the
  delivery report of that board, or in `references/playbook.md` as prose.
- Text-level scripts use the Python standard library only. `pcbnew` scripts
  must run headless under `xvfb-run` and end with `os._exit(0)`.
- If you change a script's behaviour, say what changed in the commit message
  and, when it affects usage, update the README tutorial in the same commit.

## Development

There is no build step. To validate a change, re-run the pipeline on a real
board and compare the DRC count chain (bare import → deterministic pass →
final). A change that makes any stage's count worse needs an explanation in
the pull request.
