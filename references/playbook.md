# Fix playbook — one recipe per DRC violation type

All recipes were validated on real EasyEDA Pro → KiCad conversions (KiCad 10.0.8). Unless a recipe says otherwise, prefer the genuine fix; deletion is the sanctioned fallback when a fix is disproportionate (see the operating principle in `../SKILL.md`).

## clearance / track_width / via sizes (bulk)

Almost always the importer's factory-default rules, not real geometry problems. Transplant the source rules first (`extract_rules.py` → `make_project.py` + `apply_rules.py`). Residual single items: nudge the track/via, reroute the segment, or delete the segment.

## hole_to_hole

Pairs of vias/holes a few hundredths of a mm under the limit (e.g. actual 0.4715–0.4870 against a 0.5 rule). Fix: move one via of the pair **together with the endpoints of every track segment that ends on it** by the deficit plus ~0.01 mm, directly away from the other hole (0.03–0.045 mm moves sufficed in practice). Text-level: rewrite the via `(at ...)` and the matching segment `(start ...)`/`(end ...)` coordinates. Fallback: delete one via and its stub.

## track_dangling

Two shapes:

- Isolated short stub → delete it.
- The stub's end lands *exactly* on the middle of another track (a T-junction the source editor accepts; KiCad's dangling check requires a real endpoint joint). Verify analytically that the point lies on the other segment's centreline, then **split the touched segment into two at that exact point** (the clone gets a fresh UUID). Copper geometry is unchanged; the junction becomes a standard 3-endpoint joint and the warning clears.

## silk_over_copper / silk_overlap / silk_edge_clearance

Semantics first (getting these wrong wastes hours):

- `silk_over_copper` is an **overlap** test: silkscreen vs pad copper/mask opening. There is no clearance margin — touching is failing.
- `silk_overlap` and `silk_edge_clearance` are **0.15 mm clearance** tests measured between **stroke edges** — centreline distance must exceed 0.15 + (w₁ + w₂)/2.

Root cause on EasyEDA imports: LCSC footprint silkscreen outlines are drawn ~0.6 mm larger than the body with fat 0.254 mm strokes, enclosing the pads' bounding box, so in dense areas they cross neighbouring pads and each other by construction.

Fix (automated by `scripts/fix_silk.py`):

- 2-pad chips (0603/0805/resistors/inductors): rebuild the outline as **two long sides parallel to the pad axis only**, at `union(fab body, own pads) extent + 0.08 mm`, stroke 0.12 mm — the way KiCad's own libraries draw them — then clip each side against all other pads (own through-hole pads still count).
- Other footprints: keep the artwork, clip each silk line against all pads and against board edges/slots (edge clearance 0.15 mm + stroke/2 + margin).
- Then a resolve pass for silk-to-silk: nudge a side toward its own footprint centre if its own pads allow, else drop the side (in the reference run: 190 rebuilt sides, 2 dropped).

## courtyards_overlap

- If the source design genuinely places two footprints courtyard-to-courtyard (e.g. parts flanking a large IC, a documented decoupling ring), exempt the exact pair in `.kicad_dru`:

```
(version 1)
(rule "tight_courtyards_by_design"
    (layer "F.Cu")
    (layer "B.Cu")
    (condition "((A.Reference == 'U1' && B.Reference == 'U3') || (A.Reference == 'U3' && B.Reference == 'U1'))")
    (constraint courtyard_clearance (min -0.6mm)))
```

  Cover both reference orders and every layer the courtyards sit on. Keep the condition as narrow as the evidence allows.
- Otherwise: move one footprint, shrink a courtyard, or delete a footprint.

## copper_edge_clearance at connector slots (e.g. USB-C)

The imported board measures copper-to-slot distances below KiCad's default 0.5 mm, while the fabricator's real minimum is 0.2 mm. Scope the relaxation to the slot area only:

```
(version 1)
(area "usbc_slots"
    (rect (24.0 94.0) (34.0 106.0))
    (layer "F.Cu") (layer "B.Cu") (layer "In1.Cu") (layer "In2.Cu"))
(rule "usbc_slot_edge"
    (condition "A.intersectsArea('usbc_slots')")
    (constraint edge_clearance (min 0.2mm)))
```

Coordinates are board mm around the connector's slots. **Warning:** a single malformed constraint silently voids the whole `.kicad_dru` (no error anywhere; suppressed counts bounce back). Re-run DRC after every dru edit and diff the counts.

## starved_thermal

A pad whose zone connection ends up with too few spokes after import. There is no `.kicad_dru` syntax for spoke counts (an invented `min_resolved_spokes` constraint voids the dru file — do not try). Fix at pad level with pcbnew: `pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION.FULL)` (value 3) for the affected pad, then refill.

## lib_footprint_mismatch

Board geometry was edited (silk, courtyards) so instances no longer match the library copy. Run `scripts/sync_library.py`: the largest geometry group keeps the library name, divergent groups become `<Name>_v2`… variants and their instances are repointed via `SetFPID`. Grouping must key on shapes + pads + model + layer + attr with UUIDs and pad `(net ...)` stripped, and must exclude `fp_text` — otherwise reference-designator text and net names shatter one real variant into dozens of fake ones.

## unconnected_items

Usually a pour clipped by a keepout after the first refill, or copper orphaned by an earlier deletion. Re-fill first; then either reconnect (add track/via) or delete the orphaned copper/footprint. Under this workflow's operating principle, deletion is acceptable — log it.

## items_not_allowed / zone issues

Imported keepout zones sometimes lose their "no tracks / no pours" flags or invert. Inspect the zone's properties, restore the intended restrictions, refill, re-run.

## General text-level editing notes

- Split the `.kicad_pcb` into top-level elements on `\n\t(` boundaries and footprint children on `\n\t\t(`, tracking parenthesis depth while respecting quoted strings. All geometry scripts in `scripts/` use this pattern.
- New elements need fresh UUIDs; duplicated UUIDs corrupt DRC attribution (see `dedup_uuids.py`).
- pcbnew (SWIG) proxies go stale after structural modification of a board object — a second `GetFootprints()` may return unresolvable proxies. Do bulk work on text; use pcbnew for one-shot loads/saves and single-object changes, in a fresh process per operation where practical.
