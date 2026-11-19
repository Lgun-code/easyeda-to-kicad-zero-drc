#!/usr/bin/env python3
"""Deterministic half of the EasyEDA Pro -> KiCad zero-DRC pipeline.

    python3 pipeline.py <project.epro> --out <dir> [--pcb <uuid>]
                        [--kicad-cli PATH] [--kicad-python PATH]

Steps: extract source rules -> convert PCB -> write .kicad_pro -> baseline
DRC -> apply rules to the board -> extract footprint library -> dedup UUIDs
-> refill zones + DRC. The board is left ready for the agent-driven fix loop
(see SKILL.md step 4 and references/playbook.md).

KiCad executables: --kicad-cli/--kicad-python, else $KICAD_CLI/$KICAD_PYTHON,
else PATH lookup ("kicad-cli"; the python bundled with KiCad, which has no
stable PATH name -- pass it explicitly, or point KICAD_PYTHON at a wrapper).
pcbnew steps run under xvfb-run when available.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def drc_counts(path):
    d = json.load(open(path))
    v = d.get("violations", [])
    from collections import Counter
    return len(v), len(d.get("unconnected_items", [])), Counter(x["type"] for x in v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("epro")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pcb", default=None, help="pcb uuid (multi-PCB projects)")
    ap.add_argument("--name", default=None, help="board base name (default: from --pcb or 'board')")
    ap.add_argument("--kicad-cli", default=os.environ.get("KICAD_CLI") or shutil.which("kicad-cli"))
    ap.add_argument("--kicad-python", default=os.environ.get("KICAD_PYTHON"))
    args = ap.parse_args()
    if not args.kicad_cli or not args.kicad_python:
        sys.exit("need --kicad-cli and --kicad-python (or KICAD_CLI/KICAD_PYTHON env)")

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    name = args.name or "board"
    board = os.path.join(out, f"{name}.kicad_pcb")
    xvfb = ["xvfb-run", "-a"] if shutil.which("xvfb-run") else []

    def kpython(script, *sargs):
        run(xvfb + [args.kicad_python, "-u", os.path.join(HERE, script), *sargs])

    def kicad_drc(report, *extra):
        run([args.kicad_cli, "pcb", "drc", "--format", "json", "--severity-all",
             *extra, "-o", report, board])

    # 1. source rules
    rules_json = os.path.join(out, "rules.json")
    run([sys.executable, os.path.join(HERE, "extract_rules.py"), args.epro, "-o", rules_json],
        stdout=subprocess.DEVNULL)
    print("rules:", open(rules_json).read().replace("\n", " "))

    # 2. convert
    conv = [args.epro, board] + ([args.pcb] if args.pcb else [])
    kpython("convert_pcb.py", *conv)

    # 3. baseline DRC on the bare import (no .kicad_pro yet, so the count is
    # the honest "before" number; kicad-cli picks up project rules once the
    # .kicad_pro exists)
    before = os.path.join(out, "drc_before.json")
    kicad_drc(before)
    n, u, _ = drc_counts(before)
    print(f"BASELINE: {n} violations, {u} unconnected")

    # 4. project file with real rules/netclass
    run([sys.executable, os.path.join(HERE, "make_project.py"), rules_json,
         os.path.join(out, f"{name}.kicad_pro")])

    # 5-7. rules into board, library extraction, uuid dedup
    kpython("apply_rules.py", board, rules_json)
    kpython("extract_library.py", board, out)
    run([sys.executable, os.path.join(HERE, "dedup_uuids.py"), board, "--in-place"])

    # 8. refill + DRC
    after = os.path.join(out, "drc_after_skill.json")
    kicad_drc(after, "--refill-zones", "--save-board")
    n, u, kinds = drc_counts(after)
    print(f"AFTER SKILL PASS: {n} violations, {u} unconnected")
    for k, c in kinds.most_common():
        print(f"  {k}: {c}")
    print("Next: agent fix loop per SKILL.md step 4 / references/playbook.md, "
          "then sync_library.py, then final DRC must read 0 / 0.")


if __name__ == "__main__":
    main()
