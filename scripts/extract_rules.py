#!/usr/bin/env python3
"""Extract the real design rules from an EasyEDA Pro project.

The KiCad importer discards EasyEDA's design rules and writes KiCad factory
defaults, which is the single largest source of phantom DRC violations after
conversion. This script reads the source rules directly:

  * V2 export  (.epro, array-format .epcb RULE lines)
  * V3 export  (.epro2 zip or a bare .epru object stream)

WARNING: EasyEDA stores rule values in mil even when the record claims
"unit": "mm". Every value is converted mil -> mm here (verified on real
projects: 4.0157 -> 0.102 mm track clearance, 5.9843 -> 0.152 mm, etc.).

Usage:
    python3 extract_rules.py <project.epro|.epro2|.epru> [-o rules.json]

Output: JSON with the values the KiCad side needs (all mm):
    clearance_track_track, clearance (other pairs), track_min, track_default,
    via_diameter_default, via_drill_default, via_diameter_min, via_drill_min,
    hole_clearance, hole_to_hole
"""
import json
import sys
import zipfile

MIL = 0.0254


def mm(mil):
    return None if mil is None else round(float(mil) * MIL, 4)


def rules_from_v2_epcb(text):
    """Parse ["RULE", ...] lines of a V2 .epcb document."""
    safe = track = other = via = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('["RULE"'):
            continue
        rec = json.loads(line)
        # ["RULE", type, name, active, payload]
        rtype, name, active, payload = rec[1], rec[2], rec[3], rec[4]
        if not active:
            continue
        if rtype == "1":      # SAFE spacing matrix (triangular, mil)
            safe = payload[1]
        elif rtype == "3":    # TRACK: ["mm", min, default, max]
            track = payload
        elif rtype == "2":    # OTHER clearances (dict)
            other = payload
        elif rtype == "5":    # VIA size (dict of radii, mil)
            via = payload
    return safe, track, other, via


def rules_from_v3_objects(objs):
    """Parse RULE header+payload object pairs of a V3 .epru stream."""
    safe = track = other = via = None
    for i, o in enumerate(objs):
        if not (isinstance(o, dict) and o.get("type") == "RULE"):
            continue
        rid = json.loads(o["id"]) if isinstance(o.get("id"), str) else []
        payload = objs[i + 1] if i + 1 < len(objs) else {}
        ctx = payload.get("ruleContext", {}) if isinstance(payload, dict) else {}
        if payload.get("ruleState") not in (None, "DEFAULT"):
            continue
        kind = rid[1] if len(rid) > 1 else ""
        if kind == "SAFE":
            spacing = ctx.get("safeSpacing", [])
            if spacing:
                safe = spacing[0].get("content")
        elif kind == "TRACK":
            content = ctx.get("track", {}).get("content", [])
            if content:
                c = content[0]
                track = ["mm", c.get("stroMin"), c.get("stroDef"), c.get("stroMax")]
        elif kind == "OTHER":
            other = ctx
        elif kind == "RADIUS":
            via = ctx
    return safe, track, other, via


def summarize(safe, track, other, via):
    out = {}
    if safe:
        flat = [v for row in safe for v in row]
        out["clearance_track_track"] = mm(safe[0][0])
        # the common non-track pair clearance is the matrix's modal value
        common = max(set(flat), key=flat.count)
        out["clearance"] = mm(common)
        # hole-related pairs carry the distinctly larger value (e.g. 11.811 mil)
        out["hole_to_hole"] = mm(max(flat))
    if track:
        out["track_min"] = mm(track[1])
        out["track_default"] = mm(track[2])
    if other:
        out["hole_clearance"] = mm(other.get("holeClearance"))
    if via:
        out["via_diameter_min"] = mm(2 * float(via["minRadius"])) if via.get("minRadius") else None
        out["via_diameter_default"] = mm(2 * float(via["defRadius"])) if via.get("defRadius") else None
        out["via_drill_min"] = mm(2 * float(via["minInner"])) if via.get("minInner") else None
        out["via_drill_default"] = mm(2 * float(via["defInner"])) if via.get("defInner") else None
    return out


def project_zip(path):
    """Open the project archive, descending one level into an inner .epro2
    when the user zipped the export itself (a common way to send it)."""
    import io
    z = zipfile.ZipFile(path)
    names = z.namelist()
    if any(n.endswith((".epcb", ".epru")) for n in names):
        return z
    inner = [n for n in names if n.endswith(".epro2")]
    if inner:
        return zipfile.ZipFile(io.BytesIO(z.read(inner[0])))
    return z


def load_v3_objects(path):
    if zipfile.is_zipfile(path):
        with project_zip(path) as z:
            name = [n for n in z.namelist() if n.endswith(".epru")][0]
            data = z.read(name).decode("utf-8")
    else:
        data = open(path, encoding="utf-8").read()
    dec = json.JSONDecoder()
    objs, i, n = [], 0, len(data)
    while i < n:
        while i < n and (data[i] in " \t\r\n" or data[i] == "|"):
            i += 1
        if i >= n:
            break
        o, j = dec.raw_decode(data, i)
        objs.append(o)
        i = j
    return objs


def main():
    src = sys.argv[1]
    out_path = None
    if "-o" in sys.argv:
        out_path = sys.argv[sys.argv.index("-o") + 1]
    if zipfile.is_zipfile(src):
        with project_zip(src) as z:
            names = z.namelist()
            epcb = [n for n in names if n.endswith(".epcb")]
            if epcb:
                text = z.read(epcb[0]).decode("utf-8", "replace")
                rules = summarize(*rules_from_v2_epcb(text))
            else:
                rules = summarize(*rules_from_v3_objects(load_v3_objects(src)))
    else:
        rules = summarize(*rules_from_v3_objects(load_v3_objects(src)))
    print(json.dumps(rules, indent=2))
    if out_path:
        with open(out_path, "w") as f:
            json.dump(rules, f, indent=2)
    # KiCad-side mapping hint
    print("\nKiCad mapping: board constraints min_clearance=clearance_track_track..clearance, "
          "min_track=track_min, min_via_diameter=via_diameter_min, min_drill=via_drill_min, "
          "hole_clearance, hole_to_hole; Default netclass clearance=clearance, "
          "track=track_default, via=via_diameter_default, drill=via_drill_default", file=sys.stderr)


if __name__ == "__main__":
    main()
