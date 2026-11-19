#!/usr/bin/env python3
"""Transplant the source design rules (from extract_rules.py) into a
converted board: BOARD_DESIGN_SETTINGS minimums + the Default netclass.

    xvfb-run -a <kicad-python> -u apply_rules.py <board.kicad_pcb> <rules.json>

The board file is updated in place (a .bak copy is written first).
Field names are KiCad 10's introspected BOARD_DESIGN_SETTINGS attributes;
missing attributes are reported, not fatal.
"""
import json
import os
import shutil
import sys

import wx

app = wx.App()
import pcbnew

path, rules_path = sys.argv[1], sys.argv[2]
rules = json.load(open(rules_path))
shutil.copy(path, path + ".bak")

board = pcbnew.PCB_IO_MGR.Load(pcbnew.PCB_IO_MGR.KICAD_SEXP, path)
ds = board.GetDesignSettings()
MM = pcbnew.FromMM

# (design-settings attribute, rules.json key, fallback)
DS_MAP = [
    ("m_MinClearance", "clearance_track_track", None),
    ("m_TrackMinWidth", "track_min", None),
    ("m_ViasMinSize", "via_diameter_min", None),
    ("m_MinThroughDrill", "via_drill_min", None),
    ("m_HoleClearance", "hole_clearance", None),
    ("m_HoleToHoleMin", "hole_to_hole", None),
    ("m_ViasMinAnnularWidth", "via_annular_min", None),
    ("m_CopperEdgeClearance", "copper_edge_clearance", None),
]
for attr, key, _ in DS_MAP:
    value = rules.get(key)
    if value is None:
        continue
    if hasattr(ds, attr):
        setattr(ds, attr, MM(float(value)))
        print(f"ds.{attr} = {value} mm", flush=True)
    else:
        print(f"ds.{attr} MISSING on this KiCad version", flush=True)

nc = board.GetAllNetClasses()["Default"]
if rules.get("clearance") is not None:
    nc.SetClearance(MM(float(rules["clearance"])))
if rules.get("track_default") is not None:
    nc.SetTrackWidth(MM(float(rules["track_default"])))
if rules.get("via_diameter_default") is not None:
    nc.SetViaDiameter(MM(float(rules["via_diameter_default"])))
if rules.get("via_drill_default") is not None:
    nc.SetViaDrill(MM(float(rules["via_drill_default"])))
print(f"netclass Default: clearance={rules.get('clearance')} track={rules.get('track_default')} "
      f"via={rules.get('via_diameter_default')} drill={rules.get('via_drill_default')}", flush=True)

pcbnew.PCB_IO_MGR.Save(pcbnew.PCB_IO_MGR.KICAD_SEXP, path, board)
print("saved:", path, flush=True)
os._exit(0)
