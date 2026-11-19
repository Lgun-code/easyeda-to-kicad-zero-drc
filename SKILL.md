# EasyEDA Pro → KiCad conversion with DRC driven to zero

Convert an EasyEDA Pro (嘉立创EDA专业版) project to a native KiCad project, repair the systematic damage the importer does, then work the remaining DRC violations down to **zero errors, zero warnings, zero unconnected items**, and package the result with before/after DRC reports.

## Operating principle

The acceptance criterion is a completely clean DRC report (`--severity-all`: 0 violations, 0 unconnected). Functional preservation is **not** a goal of this workflow: when a violation resists a cheap genuine fix, deleting or modifying the offending component, track, silkscreen or pour is an acceptable, sanctioned fallback — as is a narrowly scoped custom rule (`.kicad_dru`). Whatever was deleted or exempted must be listed in the delivery report; never present an altered board as the untouched original design.

If your project *does* require functional preservation, use only steps 0–3 and the non-destructive recipes in `references/playbook.md`.

## Prerequisites

- KiCad 8/9/10 with its bundled Python (`pcbnew`). All `pcbnew` scripts here must run with that Python, on headless Linux under `xvfb-run -a`, and they end with `os._exit(0)` (pcbnew processes hang on exit otherwise).
- `kicad-cli` for DRC and rendering.
- Plain Python 3 (stdlib only) for the text-level scripts.
- Scripts live in `scripts/` next to this file. `KICAD_CLI` / `KICAD_PYTHON` env vars or `--kicad-cli/--kicad-python` flags point at your install.

## Step 0 — Inspect the input format (do not skip)

KiCad's importer reads only the **V2** export format. Check what you were given:

- `.epro` zip containing `project.json` + `PCB/*.epcb`, where `.epcb` lines are JSON arrays (`["LINE", ...]`) → **V2, convertible**.
- `.epro2` zip containing `project2.json` + `*.epru` (a stream of JSON objects) → **V3, not convertible**. KiCad imports it as an *empty board* without complaining. Repacking the V3 payload into a V2-shaped zip does not help (verified: 0 footprints, 0 tracks). `.eprj2` is an encrypted SQLite database — equally unusable.
- For V3 input: ask the user to re-export from EasyEDA Pro via **File → Save As → "epro (V2 format)"** and send the `.epro`. Do not burn time on format translation; no third-party V3 converter exists. While waiting, `scripts/extract_rules.py` can already read the design rules out of the V3 file.

## Step 1–2 — Deterministic pass (scripted)

Run the orchestrator; it performs conversion, baseline, rule transplant, library extraction, UUID dedup and a zone refill, printing violation counts at each stage:

```bash
python3 scripts/pipeline.py <project.epro> --out <project_dir> \
    [--pcb <pcb_uuid>] [--name <BoardName>] \
    --kicad-cli <kicad-cli> --kicad-python <kicad-python>
```

What each stage does, and why (run stages individually when debugging):

1. `extract_rules.py <project>` — reads the **real** design rules from the source project. The importer throws them away and writes KiCad factory defaults (clearance 0.2 mm etc.), which is the largest single source of phantom violations. **EasyEDA stores rule values in mil even when the record says `"unit": "mm"`** — the script converts mil → mm (4.0157 mil = 0.102 mm).
2. `convert_pcb.py` — `PCB_IO_MGR.Load(EASYEDAPRO, ...)` → `Save(KICAD_SEXP, ...)`. Prints a STATS line; `zero_pad` must be empty (an empty entry means pads were lost — wrong route).
3. Baseline DRC (`kicad-cli pcb drc --severity-all`) → `drc_before.json`. A fresh import typically reports several hundred violations; that is expected.
4. `make_project.py` + `apply_rules.py` — write the extracted rules into `.kicad_pro` (netclass definitions live there) and into the board's design settings.
5. `extract_library.py` — save every used footprint into a project-local `.pretty` library and write `fp-lib-table`. Later library sync and KiCad's `lib_footprint_mismatch` check need this.
6. `dedup_uuids.py --in-place` — the importer writes the **same UUID for every instance of a library footprint**, and repeats UUIDs across sub-shapes, tracks and vias (measured: 7380 UUID elements, 4682 unique). DRC keys items by UUID, so violations are attributed to the wrong components (phantom self-overlaps, silk items blamed on unrelated parts). Regenerate every duplicate at the file-text level. **No DRC attribution is trustworthy before this step.**
7. Zone refill (`kicad-cli pcb drc --refill-zones --save-board`) — imported keepout zones may be ignored until the first refill; refilling clips pours against them.

Expect this pass to remove roughly 70–90 % of the baseline count.

## Step 3 — Scoped rule exemptions (.kicad_dru)

Only for geometry that is wrong *by source design* (the source project's own authors accepted it). Patterns and exact syntax are in `references/playbook.md` (USB-C slot edge clearance via a named rule area; footprint-courtyard contact via a two-reference condition).

**Critical:** one malformed constraint silently voids the *entire* `.kicad_dru` file — no error is printed, and previously suppressed violations bounce back (observed: totals jumping 2 → 36). After every `.kicad_dru` edit, re-run DRC and compare counts before continuing. Do not invent syntax for constraints that have no documented `.kicad_dru` form (e.g. thermal spoke counts) — use pad-level settings instead.

## Step 4 — Agent fix loop (until zero)

Loop: edit → re-run full DRC → compare per-type counts. Work type by type; `references/playbook.md` has a recipe per violation type. The heavy tools:

- `scripts/fix_silk.py <board> --drc <report.json>` — repairs the silkscreen classes (`silk_over_copper`, `silk_overlap`, `silk_edge_clearance`), usually the largest remaining block on EasyEDA imports. It rebuilds 2-pad chip outlines KiCad-library-style (two long sides only), clips other flagged artwork against pads and board edges, and resolves silk-to-silk clearances. Semantics that matter: `silk_over_copper` is an **overlap** test against pad copper; `silk_overlap`/`silk_edge_clearance` are **0.15 mm clearance tests measured between stroke edges**, not centrelines.
- `scripts/sync_library.py <board> <project_dir>` — after any board-side geometry edit, sync the library to the board (largest instance group keeps the name; divergent groups become `<Name>_v2` variants and are repointed). Brings `lib_footprint_mismatch` to 0. Grouping keys must use shapes+pads only — including `fp_text` or pad `(net ...)` assignments shatters grouping into fake variants.
- Micro-fixes from the playbook: via nudges for `hole_to_hole`, splitting a track at a T-junction point for `track_dangling`, pad-level full zone connection for `starved_thermal`.
- Deletion fallback (sanctioned by the operating principle): delete the offending footprint/track/pour/silk element; if the board outline is damaged, redraw it around the content bounding box. Log every deletion for the report.

Working discipline:

- Do **bulk geometry edits at the `.kicad_pcb` text level** (parenthesis-balanced element splitting). pcbnew's SWIG proxies go stale after structural edits (`SwigPyObject` errors on `GetFootprints()`/`GraphicalItems()`); reserve pcbnew for read-only snapshots and single-object saves (SetFPID, FootprintSave, pad settings).
- Back up the `.kicad_pcb` before every destructive pass. Verify each step by DRC counts, never by "the script ran without error".
- Local→board transform for text-level work: `board = pos + [[cosθ, sinθ], [−sinθ, cosθ]] · local` (verified against pcbnew on rotated footprints).

## Step 5 — Acceptance and delivery

- Acceptance: `kicad-cli pcb drc --refill-zones --save-board --format json --severity-all -o drc_final.json <board>.kicad_pcb` must report **0 violations and 0 unconnected items**. Anything else is not done — do not report completion early.
- Render a preview: `kicad-cli pcb render -o board_render.png -w 1400 -h 1000 --background transparent <board>.kicad_pcb`.
- Package: the board/project files (`.kicad_pcb`, `.kicad_pro`, `.kicad_dru` if any), the `.pretty` library dir, `fp-lib-table`, `rules.json`, `drc_before.json`, `drc_final.json`, the render, and a `README` report stating: the count chain (baseline → after deterministic pass → final), the disposition of each violation class, every scoped exemption, and every deletion.
- Out of scope: the schematic. KiCad's EasyEDA schematic importer is GUI-only; there is no headless path. Say so in the delivery instead of silently omitting it.

## Case study

Mustang "Board3" (144 footprints, 107 nets, 4 layers, real-world EasyEDA Pro project): bare import **835** violations → deterministic pass **~250** → silk repair + library sync **59** → scoped exemptions + micro-fixes **0** (0 errors, 0 warnings, 0 unconnected), matching the source authors' own hand-finished KiCad port, which also sits at 0.
