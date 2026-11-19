#!/usr/bin/env python3
"""Extract every footprint used on the board into a project-local .pretty
library and write the fp-lib-table entry for it.

    xvfb-run -a <kicad-python> -u extract_library.py <board.kicad_pcb> <project_dir>

Why: after conversion the board's footprints must have a library home, or
every later library-sync step (and KiCad's lib_footprint_mismatch check) has
nothing to compare against. One .pretty dir is created per library nickname
found on the board; fp-lib-table is (re)written in the project dir.
"""
import os
import sys

import wx

app = wx.App()
import pcbnew

src, projdir = sys.argv[1], sys.argv[2]
os.makedirs(projdir, exist_ok=True)
board = pcbnew.PCB_IO_MGR.Load(pcbnew.PCB_IO_MGR.KICAD_SEXP, src)
mgr = pcbnew.PCB_IO_MGR
plugin = mgr.FindPlugin(mgr.KICAD_SEXP)

by_nick = {}
for fp in board.GetFootprints():
    fpid = fp.GetFPID()
    nick, item = str(fpid.GetLibNickname()), str(fpid.GetLibItemName())
    if nick and item:
        by_nick.setdefault(nick, {}).setdefault(item, fp)

table_lines = ["(fp_lib_table", "  (version 7)"]
for nick, items in sorted(by_nick.items()):
    pretty = os.path.join(projdir, f"{nick}.pretty")
    os.makedirs(pretty, exist_ok=True)
    ok = 0
    for item, fp in items.items():
        try:
            plugin.FootprintSave(pretty, fp)
            ok += 1
        except Exception as e:
            print("save fail", nick, item, repr(e)[:120], flush=True)
    print(f"{nick}: saved {ok}/{len(items)} footprints -> {pretty}", flush=True)
    table_lines.append(f'  (lib (name "{nick}")(type "KiCad")(uri "${{KIPRJMOD}}/{nick}.pretty")(options "")(descr ""))')
table_lines.append(")")
with open(os.path.join(projdir, "fp-lib-table"), "w") as f:
    f.write("\n".join(table_lines) + "\n")
print("fp-lib-table written", flush=True)
os._exit(0)
