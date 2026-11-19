#!/usr/bin/env python3
"""Write a .kicad_pro for a converted board, with the design-rule values and
the Default netclass taken from extract_rules.py output.

    python3 make_project.py <rules.json> <out.kicad_pro>

Values not present in the source rules keep KiCad's defaults. Note both
stores matter: kicad-cli reads rule minimums from the board file (set those
with apply_rules.py), while netclass definitions live in the .kicad_pro.
"""
import json
import os
import sys

rules_path, out_path = sys.argv[1], sys.argv[2]
r = json.load(open(rules_path))

def g(key, default):
    v = r.get(key)
    return default if v is None else v

project = {
    "board": {
        "design_settings": {
            "rules": {
                "max_error": 0.005,
                "min_clearance": g("clearance_track_track", 0.2),
                "min_connection": 0.0,
                "min_copper_edge_clearance": g("copper_edge_clearance", 0.5),
                "min_groove_width": 0.0,
                "min_hole_clearance": g("hole_clearance", 0.25),
                "min_hole_to_hole": g("hole_to_hole", 0.25),
                "min_microvia_diameter": 0.2,
                "min_microvia_drill": 0.1,
                "min_resolved_spokes": 2,
                "min_silk_clearance": 0.15,
                "min_text_height": 1.0,
                "min_text_thickness": 0.15,
                "min_through_hole_diameter": g("via_drill_min", 0.3),
                "min_track_width": g("track_min", 0.2),
                "min_via_annular_width": g("via_annular_min", 0.15),
                "min_via_diameter": g("via_diameter_min", 0.6),
                "solder_mask_to_copper_clearance": 0.0,
                "use_height_for_length_calcs": True,
            }
        }
    },
    "meta": {"filename": os.path.basename(out_path), "version": 3},
    "net_settings": {
        "classes": [
            {
                "bus_width": 12,
                "clearance": g("clearance", 0.2),
                "diff_pair_gap": 0.25,
                "diff_pair_via_gap": 0.25,
                "diff_pair_width": 0.2,
                "line_style": 0,
                "microvia_diameter": 0.3,
                "microvia_drill": 0.1,
                "name": "Default",
                "pcb_color": "rgba(0, 0, 0, 0.000)",
                "priority": 2147483647,
                "schematic_color": "rgba(0, 0, 0, 0.000)",
                "track_width": g("track_default", 0.2),
                "tuning_profile": "",
                "via_diameter": g("via_diameter_default", 0.6),
                "via_drill": g("via_drill_default", 0.3),
                "wire_width": 6,
            }
        ],
        "meta": {"version": 4},
        "net_colors": None,
        "netclass_assignments": None,
        "netclass_patterns": [],
    },
}
with open(out_path, "w") as f:
    json.dump(project, f, indent=2)
print("wrote", out_path)
