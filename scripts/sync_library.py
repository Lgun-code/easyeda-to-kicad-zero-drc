#!/usr/bin/env python3
"""Synchronise the project library with the board's actual footprint
geometry, creating per-instance variants where placements diverged
(e.g. silkscreen clipped differently per placement).

    xvfb-run -a <kicad-python> -u sync_library.py <board.kicad_pcb> <project_dir>

Grouping key = footprint shapes + pads + 3D model + layer + attributes,
with uuids and pad net assignments stripped. fp_text elements are EXCLUDED:
their content/position is per-instance (reference designators) and would
shatter the grouping into dozens of fake variants. The largest group keeps
the original library name; other groups become <Name>_v2, <Name>_v3, ... and
their instances are repointed with SetFPID. The board is saved in place
(.bak copy first). Run KiCad DRC afterwards: lib_footprint_mismatch must
reach 0.
"""
import hashlib
import os
import re
import shutil
import sys

import wx

app = wx.App()
import pcbnew

SHAPE_PREFIXES = ("(fp_line", "(fp_arc", "(fp_circle", "(fp_rect", "(fp_poly")


def split_top(text):
    starts = [m.start() + 2 for m in re.finditer(r"\n\t\(", text)]
    elems, n = [], len(text)
    for s in starts:
        depth, k, instr, esc = 0, s, False, False
        while k < n:
            c = text[k]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            else:
                if c == '"':
                    instr = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
            k += 1
        elems.append(text[s:k + 1])
    return elems


def split_children(block):
    starts = [m.start() + 3 for m in re.finditer(r"\n\t\t\(", block)]
    children, n = [], len(block)
    for s in starts:
        depth, k, instr, esc = 0, s, False, False
        while k < n:
            c = block[k]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            else:
                if c == '"':
                    instr = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
            k += 1
        children.append(block[s:k + 1])
    return children


def geometry_key(block):
    parts = []
    for ch in split_children(block):
        if ch.startswith(SHAPE_PREFIXES) or ch.startswith(("(pad ", "(model", "(layer", "(attr")):
            ch = re.sub(r'\(uuid "[^"]+"\)', "", ch)
            ch = re.sub(r'\(net "[^"]*"\)', "", ch)  # pad net assignment is per-instance
            parts.append(ch)
    return hashlib.md5("\n".join(sorted(parts)).encode()).hexdigest()[:10]


def main():
    path, projdir = sys.argv[1], sys.argv[2]
    shutil.copy(path, path + ".bak")
    text = open(path, encoding="utf-8").read()
    groups = {}  # (nick, lib) -> {key: [refs]}
    for el in split_top(text):
        if not el.startswith("(footprint"):
            continue
        m = re.match(r'\(footprint "([^"]+)"', el)
        if ":" not in m.group(1):
            print("skip footprint without library nickname:", m.group(1), flush=True)
            continue
        nick, lib = m.group(1).split(":", 1)
        ref = "?"
        for ch in split_children(el):
            if ch.startswith('(property "Reference"'):
                mm = re.search(r'\(property "Reference" "([^"]*)"', ch)
                if mm:
                    ref = mm.group(1)
        groups.setdefault((nick, lib), {}).setdefault(geometry_key(el), []).append(ref)

    board = pcbnew.PCB_IO_MGR.Load(pcbnew.PCB_IO_MGR.KICAD_SEXP, path)
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
    mgr = pcbnew.PCB_IO_MGR
    plugin = mgr.FindPlugin(mgr.KICAD_SEXP)
    for (nick, lib), gs in sorted(groups.items()):
        pretty = os.path.join(projdir, f"{nick}.pretty")
        os.makedirs(pretty, exist_ok=True)
        ordered = sorted(gs.items(), key=lambda kv: -len(kv[1]))
        for idx, (key, refs) in enumerate(ordered):
            vname = lib if idx == 0 else f"{lib}_v{idx + 1}"
            rep = fps[refs[0]]
            if idx > 0:
                for r in refs:
                    fps[r].SetFPID(pcbnew.LIB_ID(nick, vname))
            plugin.FootprintSave(pretty, rep)
            print(f"saved {vname} (rep {refs[0]}, {len(refs)} instances)", flush=True)
    pcbnew.PCB_IO_MGR.Save(pcbnew.PCB_IO_MGR.KICAD_SEXP, path, board)
    print("board saved", flush=True)
    os._exit(0)


main()
