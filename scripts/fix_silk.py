#!/usr/bin/env python3
"""Repair silkscreen geometry on a converted board, at the .kicad_pcb text
level (pcbnew's SWIG proxies go stale under bulk structural edits, so all
bulk geometry work happens on the file text).

What it does, driven by a KiCad DRC report (JSON) listing silk violations:
  * Footprints flagged in silk_over_copper / silk_overlap /
    silk_edge_clearance items are collected.
  * Flagged 2-pad "chip" footprints (0603/0805-style) get their silkscreen
    outlines rebuilt the way KiCad's own libraries draw them: only the two
    long sides parallel to the pad axis remain, placed at
    union(body fab outline, own pads) + 0.08 mm, stroke 0.12 mm, then
    clipped against every other pad (overlap-based, since silk_over_copper
    is an overlap test, not a clearance test).
  * Other flagged footprints keep their artwork, but each silk line is
    clipped against all pads and kept 0.15 mm (edge-based, stroke widths
    counted) away from board edges / slots.
  * A resolve pass then enforces the 0.15 mm silk-to-silk clearance between
    the rebuilt chip sides and everything else: sides are nudged toward
    their own footprint centre when possible, deleted only as a last
    resort.

Usage:
    python3 fix_silk.py <board.kicad_pcb> --drc <drc.json> [--chip-lib NAME ...]

The board file is rewritten in place (a .bak copy is written first).
Re-run DRC afterwards and iterate; finish with sync_library.py so the
library matches the new geometry (lib_footprint_mismatch).
"""
import argparse
import json
import math
import re
import shutil
import uuid


def split_top(text):
    starts = [m.start() + 2 for m in re.finditer(r"\n\t\(", text)]
    if not starts:
        return text, [], ""
    header = text[:starts[0]]
    elems = []
    n = len(text)
    for s in starts:
        depth = 0
        k = s
        instr = False
        esc = False
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
    endpos = text.rfind(elems[-1]) + len(elems[-1])
    return header, elems, text[endpos:]


def split_children(block):
    starts = [m.start() + 3 for m in re.finditer(r"\n\t\t\(", block)]
    if not starts:
        return block, [], ""
    prefix = block[:starts[0]]
    children = []
    n = len(block)
    for s in starts:
        depth = 0
        k = s
        instr = False
        esc = False
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
    endpos = block.rfind(children[-1]) + len(children[-1])
    return prefix, children, block[endpos:]


def fmt(v):
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def make_tf(atx, aty, deg):
    th = math.radians(deg or 0.0)
    c, s = math.cos(th), math.sin(th)

    def to_board(p):
        return (atx + c * p[0] + s * p[1], aty - s * p[0] + c * p[1])

    def to_local(p):
        dx, dy = p[0] - atx, p[1] - aty
        return (c * dx - s * dy, s * dx + c * dy)

    return to_board, to_local


def num2(s):
    p = s.split()
    return (float(p[0]), float(p[1]))


def elem_points_local(child):
    pts = []
    for m in re.finditer(r"\((?:start|end|mid|center) ([-\d.]+ [-\d.]+)\)", child):
        pts.append(num2(m.group(1)))
    for m in re.finditer(r"\(xy ([-\d.]+ [-\d.]+)\)", child):
        pts.append(num2(m.group(1)))
    return pts


def dist_pt_seg(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - a[0], py - a[1])
    t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
    return math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))


def segs_intersect(a, b, c, d):
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return o1 != o2 and o3 != o4


def seg_seg_dist(a, b, c, d):
    if segs_intersect(a, b, c, d):
        return 0.0
    return min(dist_pt_seg(a[0], a[1], c, d), dist_pt_seg(b[0], b[1], c, d),
               dist_pt_seg(c[0], c[1], a, b), dist_pt_seg(d[0], d[1], a, b))


SILK_SHAPES = ("(fp_line", "(fp_arc", "(fp_circle", "(fp_rect", "(fp_poly")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--drc", required=True, help="KiCad DRC JSON report for this board")
    ap.add_argument("--chip-lib", action="append", default=[],
                    help="extra library item names to treat as 2-pad chips")
    args = ap.parse_args()

    path = args.board
    shutil.copy(path, path + ".bak")
    txt = open(path, encoding="utf-8").read()
    header, elems, footer = split_top(txt)

    fp_infos = []
    all_pads = []  # (ref, is_thru_hole, x0, y0, x1, y1) in board coords
    edge_segs = []
    for ei, el in enumerate(elems):
        km = re.match(r"\((\w+)", el)
        kind = km.group(1) if km else ""
        if kind == "gr_line" and '"Edge.Cuts"' in el:
            m1 = re.search(r"\(start ([-\d.]+ [-\d.]+)\)", el)
            m2 = re.search(r"\(end ([-\d.]+ [-\d.]+)\)", el)
            if m1 and m2:
                edge_segs.append((num2(m1.group(1)), num2(m2.group(1))))
        if kind != "footprint":
            continue
        mname = re.match(r'\(footprint "([^"]+)"', el)
        libname = mname.group(1).split(":")[-1] if mname else "?"
        prefix, children, suffix = split_children(el)
        atx = aty = adeg = 0.0
        ref = "?"
        layer = "F.Cu"
        for ch in children:
            if ch.startswith("(at "):
                parts = ch[4:].rstrip(")").split()
                atx, aty = float(parts[0]), float(parts[1])
                adeg = float(parts[2]) if len(parts) > 2 else 0.0
            elif ch.startswith('(property "Reference"'):
                m = re.search(r'\(property "Reference" "([^"]*)"', ch)
                if m:
                    ref = m.group(1)
            elif ch.startswith('(layer "'):
                m = re.match(r'\(layer "([^"]+)"', ch)
                if m:
                    layer = m.group(1)
        to_board, to_local = make_tf(atx, aty, adeg)
        info = {"ei": ei, "ref": ref, "lib": libname, "prefix": prefix,
                "children": children, "suffix": suffix, "to_board": to_board,
                "to_local": to_local, "at": (atx, aty, adeg), "pads_local": [],
                "silk_layer": "B.SilkS" if layer == "B.Cu" else "F.SilkS"}
        fp_infos.append(info)
        for ch in children:
            if ch.startswith("(pad "):
                mp = re.search(r'\(pad "([^"]*)" (\w+) (\w+)', ch)
                ma = re.search(r"\(at ([-\d.]+ [-\d.]+)(?: ([-\d.]+))?\)", ch)
                ms = re.search(r"\(size ([-\d.]+ [-\d.]+)\)", ch)
                if not (mp and ma and ms):
                    continue
                pth = mp.group(2) == "thru_hole"
                cx, cy = num2(ma.group(1))
                prot = float(ma.group(2)) if ma.group(2) else 0.0
                w, h = num2(ms.group(1))
                info["pads_local"].append((cx, cy, w, h, prot, pth))
                phi = math.radians((adeg or 0.0) + prot)
                cp, sp = math.cos(phi), math.sin(phi)
                corners = []
                for (lx, ly) in [(w / 2, h / 2), (w / 2, -h / 2), (-w / 2, h / 2), (-w / 2, -h / 2)]:
                    rx = cx + cp * lx + sp * ly
                    ry = cy - sp * lx + cp * ly
                    corners.append(to_board((rx, ry)))
                xs = [p[0] for p in corners]
                ys = [p[1] for p in corners]
                all_pads.append((ref, pth, min(xs), min(ys), max(xs), max(ys)))
            if '"Edge.Cuts"' in ch and ch.startswith("(fp_"):
                pts = [to_board(p) for p in elem_points_local(ch)]
                for i in range(len(pts) - 1):
                    edge_segs.append((pts[i], pts[i + 1]))
                if ch.startswith("(fp_poly") and len(pts) > 2:
                    edge_segs.append((pts[-1], pts[0]))
        fabpts = []
        for ch in children:
            if '"F.Fab"' in ch and ch.startswith("(fp_"):
                fabpts += elem_points_local(ch)
        if fabpts:
            info["fab_local"] = (min(p[0] for p in fabpts), min(p[1] for p in fabpts),
                                 max(p[0] for p in fabpts), max(p[1] for p in fabpts))
    by_ref = {i["ref"]: i for i in fp_infos}
    padcount = {}
    for (r, *_x) in all_pads:
        padcount[r] = padcount.get(r, 0) + 1
    print(f"footprints: {len(fp_infos)} pads: {len(all_pads)} edge segs: {len(edge_segs)}")

    def clip_segment(p0, p1, width, owner_ref, use_edges, pad_infl_extra=0.015):
        infl = width / 2 + pad_infl_extra
        pads = [(x0 - infl, y0 - infl, x1 + infl, y1 + infl)
                for (r, pth, x0, y0, x1, y1) in all_pads if r != owner_ref or pth]
        L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if L < 1e-6:
            return []
        n = max(2, int(L / 0.02) + 1)
        allowed = []
        for i in range(n + 1):
            t = i / n
            x, y = p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1])
            bad = any(x0 < x < x1 and y0 < y < y1 for (x0, y0, x1, y1) in pads)
            if not bad and use_edges:
                einfl = 0.15 + width / 2 + 0.02
                bad = any(dist_pt_seg(x, y, a, b) < einfl for (a, b) in edge_segs)
            allowed.append(not bad)
        out = []
        i = 0
        while i <= n:
            if allowed[i]:
                j = i
                while j <= n and allowed[j]:
                    j += 1
                j -= 1
                if (j - i) * (L / n) >= 0.08:
                    ta, tb = i / n, j / n
                    out.append(((p0[0] + ta * (p1[0] - p0[0]), p0[1] + ta * (p1[1] - p0[1])),
                                (p0[0] + tb * (p1[0] - p0[0]), p0[1] + tb * (p1[1] - p0[1]))))
                i = j + 1
            else:
                i += 1
        return out

    def fp_line_elem(a_local, b_local, width, layer):
        return (f'(fp_line\n\t\t\t(start {fmt(a_local[0])} {fmt(a_local[1])})\n'
                f'\t\t\t(end {fmt(b_local[0])} {fmt(b_local[1])})\n'
                f'\t\t\t(stroke\n\t\t\t\t(width {fmt(width)})\n\t\t\t\t(type default)\n\t\t\t)\n'
                f'\t\t\t(layer "{layer}")\n\t\t\t(uuid "{uuid.uuid4()}")\n\t\t)')

    # ---- flagged footprints and chip libraries, from the DRC report ----
    report = json.load(open(args.drc))
    flagged = set()
    for x in report.get("violations", []):
        if x["type"] in ("silk_over_copper", "silk_overlap", "silk_edge_clearance"):
            for it in x.get("items", []):
                m = re.search(r"of (\S+)", it.get("description", ""))
                if m:
                    flagged.add(m.group(1))
    chip_libs = set(args.chip_lib)
    for ref in flagged:
        if ref in by_ref and padcount.get(ref) == 2:
            chip_libs.add(by_ref[ref]["lib"])
    print(f"flagged: {len(flagged)} chip libs: {sorted(chip_libs)}")

    def is_silk_shape(ch, layer):
        return ch.startswith(SILK_SHAPES) and f'"{layer}"' in ch

    # ---- rebuild chip long sides (board frame) ----
    movable = []
    for info in fp_infos:
        if info["lib"] not in chip_libs or len(info["pads_local"]) != 2 or "fab_local" not in info:
            continue
        (x1, y1, w1, h1, _r1, _p1), (x2, y2, w2, h2, _r2, _p2) = info["pads_local"]
        ax = (x2 - x1, y2 - y1)
        alen = math.hypot(*ax)
        if alen < 1e-6:
            continue
        ax = (ax[0] / alen, ax[1] / alen)
        perp = (-ax[1], ax[0])
        c = ((x1 + x2) / 2, (y1 + y2) / 2)
        pts = []
        fx0, fy0, fx1, fy1 = info["fab_local"]
        pts += [(fx0, fy0), (fx0, fy1), (fx1, fy0), (fx1, fy1)]
        for (px, py, w, h, _r, _p) in info["pads_local"]:
            pts += [(px - w / 2, py - h / 2), (px - w / 2, py + h / 2),
                    (px + w / 2, py - h / 2), (px + w / 2, py + h / 2)]
        da = [abs((p[0] - c[0]) * ax[0] + (p[1] - c[1]) * ax[1]) for p in pts]
        dp = [abs((p[0] - c[0]) * perp[0] + (p[1] - c[1]) * perp[1]) for p in pts]
        span = max(da) + 0.08
        off = max(dp) + 0.08
        for sgn in (1, -1):
            o = (c[0] + perp[0] * off * sgn, c[1] + perp[1] * off * sgn)
            a_l = (o[0] - ax[0] * span, o[1] - ax[1] * span)
            b_l = (o[0] + ax[0] * span, o[1] + ax[1] * span)
            a_b, b_b = info["to_board"](a_l), info["to_board"](b_l)
            ctr = info["to_board"](c)
            for (ca, cb) in clip_segment(a_b, b_b, 0.12, info["ref"], False):
                movable.append({"ref": info["ref"], "a": ca, "b": cb,
                                "cx": ctr[0], "cy": ctr[1], "w": 0.12, "dead": False})
    print(f"movable chip segments: {len(movable)}")

    # ---- clip non-chip flagged silk; collect fixed obstacles ----
    nonchip_new = {}
    fixed = []
    for info in fp_infos:
        if info["ref"] not in flagged or padcount.get(info["ref"]) == 2:
            continue
        layer = info["silk_layer"]
        new_children = []
        touched = False
        for ch in info["children"]:
            if ch.startswith("(fp_line") and f'"{layer}"' in ch:
                ms = re.search(r"\(start ([-\d.]+ [-\d.]+)\)", ch)
                me = re.search(r"\(end ([-\d.]+ [-\d.]+)\)", ch)
                mw = re.search(r"\(width ([-\d.]+)\)", ch)
                if ms and me:
                    p0 = info["to_board"](num2(ms.group(1)))
                    p1 = info["to_board"](num2(me.group(1)))
                    w = float(mw.group(1)) if mw else 0.12
                    subs = clip_segment(p0, p1, w, info["ref"], True)
                    same = (len(subs) == 1
                            and math.hypot(subs[0][0][0] - p0[0], subs[0][0][1] - p0[1]) < 0.021
                            and math.hypot(subs[0][1][0] - p1[0], subs[0][1][1] - p1[1]) < 0.021)
                    if not same:
                        touched = True
                        for (ca, cb) in subs:
                            new_children.append(fp_line_elem(info["to_local"](ca),
                                                             info["to_local"](cb), w, layer))
                            fixed.append((ca, cb, w))
                        continue
                    fixed.append((p0, p1, w))
            elif is_silk_shape(ch, layer):
                pts = [info["to_board"](p) for p in elem_points_local(ch)]
                mw2 = re.search(r"\(width ([-\d.]+)\)", ch)
                w = float(mw2.group(1)) if mw2 else 0.12
                if ch.startswith("(fp_circle") and len(pts) >= 2:
                    cen = pts[0]
                    r = math.hypot(pts[1][0] - cen[0], pts[1][1] - cen[1])
                    poly = [(cen[0] + r * math.cos(t), cen[1] + r * math.sin(t))
                            for t in [i * math.pi / 6 for i in range(12)]]
                    for i in range(12):
                        fixed.append((poly[i], poly[(i + 1) % 12], w))
                elif ch.startswith("(fp_arc") and len(pts) >= 3:
                    fixed.append((pts[0], pts[1], w))
                    fixed.append((pts[1], pts[2], w))
                elif len(pts) >= 2:
                    for i in range(len(pts) - 1):
                        fixed.append((pts[i], pts[i + 1], w))
            new_children.append(ch)
        if touched:
            nonchip_new[info["ei"]] = info["prefix"] + "\n\t\t".join(new_children) + info["suffix"]
    print(f"non-chip blocks clipped: {len(nonchip_new)} fixed obstacle segs: {len(fixed)}")

    # ---- resolve: enforce 0.15 mm silk clearance, stroke widths counted ----
    own_pads = {}
    for (r, pth, x0, y0, x1, y1) in all_pads:
        own_pads.setdefault(r, []).append((x0, y0, x1, y1))

    def own_pad_ok(m, na, nb):
        infl = m["w"] / 2 + 0.005
        for t in (0, 0.25, 0.5, 0.75, 1.0):
            x = na[0] + t * (nb[0] - na[0])
            y = na[1] + t * (nb[1] - na[1])
            for (x0, y0, x1, y1) in own_pads.get(m["ref"], []):
                if x0 - infl < x < x1 + infl and y0 - infl < y < y1 + infl:
                    return False
        return True

    def try_move(m, deficit):
        mx, my = (m["a"][0] + m["b"][0]) / 2, (m["a"][1] + m["b"][1]) / 2
        dx, dy = m["cx"] - mx, m["cy"] - my
        L = math.hypot(dx, dy)
        if L < 1e-6:
            return False
        ux, uy = dx / L, dy / L
        na = (m["a"][0] + ux * deficit, m["a"][1] + uy * deficit)
        nb = (m["b"][0] + ux * deficit, m["b"][1] + uy * deficit)
        if not own_pad_ok(m, na, nb):
            return False
        m["a"], m["b"] = na, nb
        return True

    for round_ in range(30):
        violations = []
        act = [m for m in movable if not m["dead"]]
        for i, m in enumerate(act):
            for (fa, fb, fw) in fixed:
                need = 0.15 + (m["w"] + fw) / 2 + 0.01
                d = seg_seg_dist(m["a"], m["b"], fa, fb)
                if d < need:
                    violations.append((m, None, need - d))
            for j in range(i + 1, len(act)):
                o = act[j]
                if o["ref"] == m["ref"]:
                    continue
                if math.hypot(o["cx"] - m["cx"], o["cy"] - m["cy"]) > 6:
                    continue
                d = seg_seg_dist(m["a"], m["b"], o["a"], o["b"])
                need = 0.15 + (m["w"] + o["w"]) / 2 + 0.01
                if d < need:
                    violations.append((m, o, need - d))
        if not violations:
            print(f"resolve clean after round {round_}")
            break
        progress = False
        for (m, o, deficit) in violations:
            if m["dead"]:
                continue
            if o is None:
                if not try_move(m, deficit):
                    m["dead"] = True
                progress = True
            else:
                if o["dead"]:
                    continue
                half = deficit / 2
                okm = try_move(m, half)
                oko = try_move(o, half)
                if not okm and not oko:
                    o["dead"] = True
                elif not okm:
                    if not try_move(o, half):
                        o["dead"] = True
                elif not oko:
                    if not try_move(m, half):
                        m["dead"] = True
                progress = True
        if not progress:
            print(f"resolve stalled at round {round_}")
            break
    dead = sum(1 for m in movable if m["dead"])
    print(f"movable dead (deleted sides): {dead} of {len(movable)}")

    # ---- emit ----
    by_ref_segs = {}
    for m in movable:
        if not m["dead"]:
            by_ref_segs.setdefault(m["ref"], []).append(m)
    changed = dict(nonchip_new)
    for info in fp_infos:
        if info["lib"] not in chip_libs or len(info["pads_local"]) != 2:
            continue
        kept = [ch for ch in info["children"] if not is_silk_shape(ch, info["silk_layer"])]
        news = [fp_line_elem(info["to_local"](m["a"]), info["to_local"](m["b"]), 0.12,
                             info["silk_layer"])
                for m in by_ref_segs.get(info["ref"], [])]
        changed[info["ei"]] = info["prefix"] + "\n\t\t".join(kept + news) + info["suffix"]
    for ei, nb in changed.items():
        elems[ei] = nb
    print(f"blocks changed: {len(changed)}")

    out = header + "\n\t".join(elems) + footer
    open(path, "w", encoding="utf-8").write(out)
    print(f"written {len(out)} bytes -> {path}")


if __name__ == "__main__":
    main()
