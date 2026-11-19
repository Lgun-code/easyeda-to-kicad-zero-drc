#!/usr/bin/env python3
"""Regenerate every duplicated (uuid ...) in a .kicad_pcb file.

KiCad's EasyEDA Pro importer writes the SAME uuid for every instance of a
library footprint, and repeats uuids across footprint sub-shapes, tracks and
vias as well (measured on a real board: 7380 uuid elements, only 4682
unique). KiCad's DRC reporter keys items by uuid, so violations get
attributed to the wrong components (phantom "R48 overlaps itself", silk
items blamed on unrelated parts). Until this is fixed, no DRC item
attribution can be trusted.

The first occurrence of each uuid keeps its value; every later duplicate is
replaced with a fresh uuid4. Pure text operation, formatting preserved.

Usage:
    python3 dedup_uuids.py <board.kicad_pcb> [--in-place]
Without --in-place the result goes to <board>.dedup.kicad_pcb.
"""
import re
import sys
import uuid

UUID_RE = re.compile(r'\(uuid "([0-9a-fA-F-]{36})"\)')


def main():
    path = sys.argv[1]
    in_place = "--in-place" in sys.argv
    text = open(path, encoding="utf-8").read()
    seen = set()
    stats = {"total": 0, "duplicated_values": 0, "regenerated": 0}

    def repl(m):
        stats["total"] += 1
        value = m.group(1)
        if value in seen:
            stats["regenerated"] += 1
            return f'(uuid "{uuid.uuid4()}")'
        seen.add(value)
        return m.group(0)

    out = UUID_RE.sub(repl, text)
    stats["duplicated_values"] = stats["regenerated"]
    dst = path if in_place else path.replace(".kicad_pcb", ".dedup.kicad_pcb")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"uuid elements: {stats['total']}, duplicates regenerated: {stats['regenerated']} -> {dst}")


if __name__ == "__main__":
    main()
