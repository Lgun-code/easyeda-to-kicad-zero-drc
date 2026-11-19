#!/usr/bin/env python3
"""Convert one PCB of an EasyEDA Pro V2 project (.epro) to .kicad_pcb using
KiCad's own EASYEDAPRO importer, headlessly.

Run with KiCad's bundled python, under a virtual display:
    xvfb-run -a <kicad-python> -u convert_pcb.py <project.epro> <out.kicad_pcb> [pcb_uuid]

pcb_uuid: from project.json "boards"/"pcbs" in the .epro. Required when the
project holds several PCBs; with a single PCB it may be omitted.
Prints a STATS line: footprints / tracks / nets / zero-pad footprints (must
be 0 -- the PADS export route loses through-hole pads; this route must not).
Ends with os._exit(0): pcbnew processes hang on exit otherwise.
"""
import os
import sys

import wx

app = wx.App()
import pcbnew

src, out = sys.argv[1], sys.argv[2]
pcb_id = sys.argv[3] if len(sys.argv) > 3 else None

props = None
if pcb_id:
    props = pcbnew.str_utf8_Map()
    props["pcb_id"] = pcbnew.UTF8(pcb_id)
board = pcbnew.PCB_IO_MGR.Load(pcbnew.PCB_IO_MGR.EASYEDAPRO, src, None, props)
if board is None:
    print("LOAD FAILED (is this a V2 .epro? V3 .epro2 imports as an empty board)", flush=True)
    os._exit(2)
fps = list(board.GetFootprints())
zero_pad = [fp.GetReference() for fp in fps if len(list(fp.Pads())) == 0]
print(f"STATS footprints={len(fps)} tracks={len(list(board.GetTracks()))} "
      f"nets={board.GetNetCount()} zero_pad={zero_pad}", flush=True)
pcbnew.PCB_IO_MGR.Save(pcbnew.PCB_IO_MGR.KICAD_SEXP, out, board)
print("saved:", out, os.path.getsize(out), flush=True)
os._exit(0)
