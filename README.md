# easyeda-to-kicad-zero-drc

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![KiCad](https://img.shields.io/badge/KiCad-10-blue.svg)](https://www.kicad.org/)
[![Python](https://img.shields.io/badge/Python-3.x-green.svg)](https://www.python.org/)

An agent skill (and standalone script pipeline) that converts an **EasyEDA Pro** （嘉立创EDA专业版） project to a native **KiCad** project and drives its DRC report to **zero errors, zero warnings, zero unconnected items**.

A bare KiCad import of a real EasyEDA Pro board typically reports *hundreds* of violations — almost none of them real design problems. The importer discards the source design rules, duplicates UUIDs across footprint instances (corrupting DRC attribution), drops keepout behaviour until the first zone refill, and imports silkscreen artwork that overlaps pads by construction. This project repairs that systematic damage deterministically, then gives the agent (or you) a validated playbook for the remainder.

## Features

- **Design-rule transplant** — reads the real rules out of the EasyEDA source (V2 `.epcb` and V3 `.epru`), converts mil → mm, and writes them into the KiCad board setup + project file, instead of leaving KiCad factory defaults in place.
- **Project library extraction** — builds a project-local `.pretty` library from the board so footprint checks have something true to compare against.
- **Full-file UUID deduplication** — the importer reuses UUIDs across footprint instances *and* their sub-shapes/tracks/vias, which makes DRC blame the wrong parts. Every duplicated UUID in the file is regenerated.
- **Silkscreen repair engine** — rebuilds, clips, nudges and resolves silkscreen geometry against pads and neighbouring artwork with real clearance math (stroke-edge semantics, not centrelines).
- **Library synchronisation** — groups placements by true geometry and extracts per-instance variants (`Name_v2`, `Name_v3`, …) where EasyEDA allowed per-instance divergence, so `lib_footprint_mismatch` can actually reach zero.
- **A playbook, not a prayer** — `references/playbook.md` has one validated recipe per DRC violation type, including scoped `.kicad_dru` exemption patterns and the pitfalls that silently void them.

## Case study

Mustang "Board3" — 144 footprints, 107 nets, 4 layers, a real-world EasyEDA Pro project:

| Stage | DRC total |
|---|---|
| Bare import | 835 |
| Deterministic pass (rules, library, UUID dedup, refill) | ~250 |
| Silkscreen repair + library sync | 59 |
| Scoped rule exemptions + micro-fixes | **0** |

The result matches the source authors' own hand-finished KiCad port, which also sits at 0.

## Prerequisites

| Requirement | Notes |
|---|---|
| KiCad 10 | Validated on 10.0.8. You need both `kicad-cli` and KiCad's bundled Python (`pcbnew`). KiCad 8/9 are expected to work but are untested. |
| Python 3 | Standard library only, for the text-level scripts. |
| `xvfb-run` | On headless Linux, every `pcbnew` script must run under a virtual display (see the tutorial). |
| EasyEDA Pro | Only to export the source project in V2 format (step 1 of the tutorial). |

## Installation

As a standalone pipeline — just clone it:

```bash
git clone https://github.com/Lgun-code/easyeda-to-kicad-zero-drc.git
cd easyeda-to-kicad-zero-drc
```

As an agent skill — copy or clone the repository into your agent's skills directory (the entry point is `SKILL.md`, the descriptor is `skill.yaml`), or simply point your agent at `SKILL.md` and let it drive the scripts.

No dependencies to install: the scripts use Python's standard library plus KiCad's own Python.

## Tutorial

### Step 1 — Export your project from EasyEDA Pro in V2 format

KiCad's importer reads only the **V2** EasyEDA Pro format. A V3 project (`.epro2`) loads as an *empty board* — this is the single most common failure, so check it first.

In EasyEDA Pro: **File → Save As → `epro (V2 format)`** （文件 → 另存为 → epro (V2 格式）), and send the resulting `.epro` file to the pipeline.

> Format check: unzip the file. V2 contains `project.json` plus `*.epcb` boards stored as JSON arrays; V3 contains `project2.json` plus `*.epru` object streams. `scripts/extract_rules.py` understands both (handy for reading a V3 project's rules), but conversion requires V2.

### Step 2 — Run the deterministic pipeline

```bash
python3 scripts/pipeline.py MyProject.epro --out MyProject-kicad \
    --kicad-cli /path/to/kicad-cli --kicad-python /path/to/kicad-python
```

`--kicad-cli` / `--kicad-python` default to `kicad-cli` and `kicad-python` on `PATH`; pass explicit paths when KiCad is not installed system-wide (e.g. an extracted AppImage).

The pipeline performs, in order:

1. **Convert** — imports the chosen PCB with KiCad's EasyEDA Pro importer (`convert_pcb.py`).
2. **Baseline DRC** — saves `drc_before.json` so every later improvement is measurable.
3. **Rule transplant** — `extract_rules.py` reads the source rules; `make_project.py` writes the `.kicad_pro`; `apply_rules.py` writes the board design settings and the Default netclass.
4. **Library extraction** — `extract_library.py` writes the project `.pretty` library and `fp-lib-table`.
5. **UUID deduplication** — `dedup_uuids.py` regenerates every duplicated UUID in the board file.
6. **Zone refill + DRC** — saves `drc_after_skill.json`.

On the case-study board this pass alone took the report from 835 down to ~250, and every remaining item is a *real* category with a recipe — not importer noise.

### Step 3 — Run the silkscreen repair

```bash
python3 scripts/fix_silk.py MyProject-kicad/<Board>.kicad_pcb \
    --drc MyProject-kicad/drc_after_skill.json
```

This is a pure text-level script (fast, no KiCad needed). It uses the DRC report to find the footprints whose silkscreen is actually flagged, rebuilds standard chip outlines from pad geometry, clips artwork against foreign pads, and resolves silk-to-silk conflicts by nudging, shrinking or — as a last resort — deleting individual sides. It reports exactly what it changed.

### Step 4 — Synchronise the library

```bash
xvfb-run -a <kicad-python> -u scripts/sync_library.py \
    MyProject-kicad/<Board>.kicad_pcb MyProject-kicad
```

Two things bite everyone here:

- This script needs **KiCad's Python under `xvfb-run`** — running it with the system `python3` fails with `No module named 'wx'`.
- The second argument is the **project directory**, *not* the `.pretty` directory. The script appends `<nickname>.pretty` itself; passing the `.pretty` path creates a nested `X.pretty/X.pretty` and the board ends up pointing at footprints that "don't exist".

Placements whose geometry genuinely diverged in EasyEDA are split into library variants (`Name_v2`, `Name_v3`, …) and their instances repointed, which is what allows `lib_footprint_mismatch` to reach a true zero instead of being suppressed.

### Step 5 — Work the remainder with the playbook

Whatever is left after steps 2–4 is a short list of specific categories: thermal-relief starvation, courtyard overlaps designed into the source, slot/edge clearances, a handful of micro-clearances. Work them with `references/playbook.md`:

- Genuine geometry problems get genuine micro-fixes (pad zone-connection overrides, sub-0.05 mm nudges, T-junction segment splits).
- Behaviour the source design deliberately uses gets a **scoped** `.kicad_dru` exemption — one rule, named, with a condition that matches only the intended items.

> ⚠️ One malformed constraint silently voids the **entire** `.kicad_dru` file — KiCad exits 0 and says nothing. Re-run DRC after every `.dru` edit and confirm a known-suppressed item is still suppressed.

### Step 6 — Acceptance: the full DRC must read exactly zero

```bash
kicad-cli pcb drc --refill-zones --save-board --format json --severity-all \
    -o drc_final.json MyProject-kicad/<Board>.kicad_pcb
```

Acceptance is `--severity-all` (errors *and* warnings) with zones refilled: **0 violations, 0 unconnected items**. Anything less is not done.

### Step 7 — Package the delivery

Ship the KiCad project (`.kicad_pcb`, `.kicad_pro`, `.kicad_dru` if any, the `.pretty` library, `fp-lib-table`) together with:

- `drc_before.json` and `drc_final.json` — the measurable before/after;
- a short repair report listing the count chain, **every deletion and every rule exemption**, and anything that was *not* converted (see *Scope*, below).

## Script reference

| Script | Runtime | Purpose |
|---|---|---|
| `pipeline.py` | python3 | Orchestrates steps 2–6 of the deterministic pass |
| `extract_rules.py` | python3 | EasyEDA source rules (V2 + V3) → `rules.json` (mil → mm) |
| `convert_pcb.py` | kicad-python + xvfb | Runs KiCad's EasyEDA Pro importer for one PCB |
| `make_project.py` | python3 | Writes `.kicad_pro` design-rule values from `rules.json` |
| `apply_rules.py` | kicad-python + xvfb | Writes board design settings + Default netclass |
| `extract_library.py` | kicad-python + xvfb | Extracts the project `.pretty` library |
| `dedup_uuids.py` | python3 | Regenerates every duplicated UUID in the board file |
| `fix_silk.py` | python3 | Silkscreen rebuild / clip / resolve engine |
| `sync_library.py` | kicad-python + xvfb | Splits diverged placements into library variants |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Converted board is empty (0 footprints) | The source is V3 (`.epro2`). Re-export as V2 (tutorial step 1). There is no V3→V2 converter; KiCad's importer is V2-only. |
| `No module named 'wx'` | You ran a `pcbnew` script with system Python. Use KiCad's bundled Python under `xvfb-run -a`. |
| `Footprint 'X_v2' not found in library` after sync | The variants were written to a nested `X.pretty/X.pretty` — you passed the `.pretty` dir instead of the project dir (tutorial step 4). Move the files up one level. |
| A `.kicad_dru` exemption "does nothing" | One bad constraint voids the whole file, silently. Validate by re-running DRC after each edit (tutorial step 5). |
| DRC blames the wrong footprint (e.g. a part "overlapping itself") | Duplicate UUIDs from the importer. Run `dedup_uuids.py` — it must cover *all* elements, not just footprints. |
| A `pcbnew` script hangs at exit | Known KiCad headless behaviour; the scripts in this repo end with `os._exit(0)`. Judge success by the saved output file. |
| Silkscreen flags return after a fix round | DRC reports are capped per type (199/499). Re-run `fix_silk.py` against the *new* report; it converges in a few rounds. |

## Scope and operating principle

The acceptance criterion is a completely clean DRC. Functional preservation is **not** a goal of this workflow: intractable items may be deleted or modified, and every deletion and exemption must be disclosed in the delivery report (tutorial step 7). Schematic conversion is out of scope — KiCad's EasyEDA schematic importer is GUI-only — and that, too, must be disclosed rather than silently omitted. See `SKILL.md` for the full contract an agent follows when running this pipeline.

## 中文简介

把嘉立创专业版（EasyEDA Pro）工程转为 KiCad 工程并将 DRC 清零的 Agent Skill + 脚本流水线。裸导入通常有数百条报错，绝大多数是导入器造成的系统性损伤：源设计规则被丢弃、UUID 在封装实例间重复导致报错归属错乱、禁布区未重灌不生效、丝印按构造压焊盘。本项目先用脚本确定性修复（移植真实规则、抽封装库、UUID 全量去重、重灌铺铜），再按 playbook 逐项清完剩余报错，最终以 `--severity-all` 全量 DRC = 0 验收交付。使用步骤见上方 Tutorial：先在立创里「文件 → 另存为 → epro (V2 格式)」导出，再跑 `pipeline.py`，然后依次跑丝印修复与库同步，最后按 playbook 处理剩余项并验收打包。

## Contributing

Issues and pull requests are welcome — especially DRC categories the playbook doesn't cover yet, and test reports from other KiCad versions. Please include the board's DRC counts before/after and your KiCad version. See `CONTRIBUTING.md`.

## License

MIT — see `LICENSE`.
