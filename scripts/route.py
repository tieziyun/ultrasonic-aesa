"""Route kicad/aesa.kicad_pcb in stages (docs/routing_and_emi.md). Each run starts from the placed board
(scripts/place.py output), removes earlier routing and rebuilds it, so the routing always matches the
current placement.

Stages (in this order; best practice: blocks that repeat first, critical and power before the rest):
  channel   the hand-made cell routing (CHANNEL_ROUTE) in all 64 cells
  columns   each column's drive and receive lanes with their fan-outs, the +-5VA rails, the GND guards
  blocks    ADC, TX group, clock buffer, enable buffer and mic blocks: one routed, copied where it fits
  fanout    GND vias for every other part
  supplies  front-copper supply areas: VDRV over the TX zone, +3V3A over the ADC rows
  global    Freerouting for everything still open, the rest locked
  pours     outer GND pours, board-edge via fence and plane stitching (after global)

Front copper of every array column (x from the cell centre; docs/routing_and_emi.md section 6):
  DRV lanes  -9.3 .. -6.5    8 drive tracks 0.2 mm on a 0.4 mm pitch, row 7 innermost
  rails      +5VA at -0.6, -5VA at +0.6 (between the transducer pads)
  RX lanes   +6.5 .. +9.3    8 receive tracks, same size, row 0 innermost
  guard      GND on every column boundary (x = +-10), a via at each cell corner
  cell vias  |x| <= 6.3

  python scripts/route.py [stage ...]
"""

import math
import re
import sys
import time
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
from route_lib import REPO, add_track, add_via, copy_items, local_ref, snapshot, xy  # noqa: E402
from place import P as PITCH  # noqa: E402

PCB = REPO / "kicad" / "aesa.kicad_pcb"
WORK = REPO / "build" / "route"

# front-copper plan of a column (x relative to the cell centre; docs/routing_and_emi.md). Lanes are 0.2 mm
# tracks on a 0.4 mm pitch (0.2 mm gaps: 0.3 A peak drive, 15 V; IPC-2221 asks 0.13 mm under solder mask),
# so the cell keeps a 2 mm via column on each side of its op-amp. Row r's lane keeps its slot all the way.
LANE_W, LANE_P = 0.2, 0.4
DRV_X = [-6.5 - LANE_P * (7 - r) for r in range(8)]  # drive enters from the TX groups below: row 7 innermost
RX_X = [6.5 + LANE_P * r for r in range(8)]          # receive leaves to the ADCs above: row 0 innermost
LANE_VIA_MAX = 6.3                                   # a cell's lane vias sit at |x| <= this, jogging out to the slot
RAIL_X = {"+5VA": -0.6, "-5VA": 0.6}                 # between the transducer pads; +5VA on the side of op-amp pin 8
RAIL_W = 0.5
GUARD_X, GUARD_W = 10.0, 0.3                         # GND guard on every column boundary (DRV next to RX)
GND_VIA = (0.6, 0.3)
MIN_TRACK = 0.127                                    # JLCPCB standard minimum width
HALF = PITCH / 2 - 0.3                               # a cell's own copper stays inside this


def lane_band(side):
    """x ranges (cell coordinates) of the lanes on one side (-1 drive, +1 receive), track edges included."""
    xs = DRV_X if side < 0 else RX_X
    return [(min(xs) - LANE_W / 2, max(xs) + LANE_W / 2)]


def draw_column_worst_case(board, cx, y0, y1, guards=(-1, 1)):
    """The column's front copper with every slot taken (as in the busiest row) as obstacles for the cell
    checks: 7 passing drive and receive lanes (nets of a far column), the rails, the boundary guards."""
    for k in range(7):
        add_track(board, f"DRV{33 + k}", [(cx + DRV_X[k], y0), (cx + DRV_X[k], y1)], LANE_W, pcbnew.F_Cu, False)
        add_track(board, f"RX{33 + k}", [(cx + RX_X[k + 1], y0), (cx + RX_X[k + 1], y1)], LANE_W, pcbnew.F_Cu, False)
    for net, x in RAIL_X.items():
        add_track(board, net, [(cx + x, y0), (cx + x, y1)], RAIL_W, pcbnew.F_Cu, False)
    for s in guards:
        add_track(board, "GND", [(cx + s * GUARD_X, y0), (cx + s * GUARD_X, y1)], GUARD_W, pcbnew.F_Cu, False)
        y = y0
        while y <= y1 + 1e-6:
            add_via(board, "GND", cx + s * GUARD_X, y, *GND_VIA, locked=False)
            y += PITCH


def cell_nets(fps):
    """Token -> net name for one cell: GND and the rails as they are, DRVn / RXn the cell's lane nets,
    anything else the cell's own net with that last name (LIM -> /Transceivers/CH07/LIM)."""
    nets = {"GND": "GND", "+5VA": "+5VA", "-5VA": "-5VA"}
    for f in fps:
        for p in f.Pads():
            n = p.GetNetname()
            if re.fullmatch(r"DRV\d+", n):
                nets["DRVn"] = n
            elif re.fullmatch(r"RX\d+", n):
                nets["RXn"] = n
            elif n:
                nets.setdefault(n.split("/")[-1], n)
            if n:
                nets[f"{local_ref(f.GetReference())}.{p.GetNumber()}"] = n   # e.g. C3.1 for an unnamed net
    return nets


def draw_cell(board, fps, cx, cy, spec, drv_row, rx_row, stub=True, jog=True):
    """Add one cell's hand-made routing (spec: CHANNEL_ROUTE) around the cell centre, with its lane vias
    jogging on the front to the slots of its row and (stub) the lanes' first stretch to the cell edge
    (drive down to the TX groups, receive up to the ADCs)."""
    nets = cell_nets(fps)
    made = []
    for net, x, y in spec["vias"]:
        made.append(add_via(board, nets[net], cx + x, cy + y, *GND_VIA))
    for net, layer, w, pts in spec["tracks"]:
        made += add_track(board, nets[net], [(cx + x, cy + y) for x, y in pts], w,
                          pcbnew.F_Cu if layer == "F" else pcbnew.B_Cu)
    for net, slot, edge in (("DRVn", DRV_X[drv_row], HALF), ("RXn", RX_X[rx_row], -HALF)):
        x, y = spec["lanes"][net]
        made.append(add_via(board, nets[net], cx + x, cy + y, *GND_VIA))
        if not jog:                 # the column's lane starts at this via (stage_columns), bend and all
            continue
        pts = [(cx + x, cy + y), (cx + slot, cy + y)] + ([(cx + slot, cy + edge)] if stub else [])
        made += add_track(board, nets[net], pts, LANE_W, pcbnew.F_Cu)
    return made


# Channel block routing (cell coordinates, front view, x right, y down; parts on the back, place.py CHANNEL).
# vias: (net, x, y); tracks: (net, "B" | "F", width, [(x, y), ...]). Nets: GND, +5VA, -5VA, DRVn and RXn (the
# cell's lane nets), the cell's own nets by name (XD, LIM, FB1, S1, N2, INV2, OUT2) or by pad (C3.1).
CHANNEL_ROUTE = {
    "lanes": {"DRVn": (-5.4, 8.9), "RXn": (5.9, 1.6)},
    "vias": [("+5VA", -0.6, -8.805), ("-5VA", 0.6, -4.995),         # rails, under the op-amp next to pins 8 / 4
             ("GND", -5.9, -10.0),                                   # C1 (+5VA decoupling), C3 shares it
             ("GND", -6.2, -3.2),                                    # stage-1 input reference: C6, D2
             ("GND", 4.2, -6.4),                                     # stage-2 reference: U1 pin 3
             ("GND", 5.85, -3.2),                                    # C2 (-5VA decoupling)
             ("GND", 5.8, -2.3),                                     # R7
             ("GND", -5.2, 6.75),                                    # R2 bleed
             ("OUT2", 2.6, -10.0), ("OUT2", 5.2, -1.0)],             # OUT2 front-copper jumper to R9
    "tracks": [
        # supplies
        ("+5VA", "B", 0.4, [(-0.6, -8.805), (-4.445, -8.805)]),
        ("-5VA", "B", 0.4, [(0.6, -4.995), (3.9, -4.995), (4.545, -4.35)]),
        ("GND", "B", 0.4, [(-5.995, -9.02), (-5.9, -10.0)]),
        ("GND", "B", 0.3, [(-7.495, -9.02), (-5.995, -9.02)]),
        ("GND", "B", 0.4, [(6.095, -4.3), (5.85, -3.2)]),
        # stage 1
        ("S1", "B", 0.25, [(-2.425, -7.535), (-6.02, -7.535)]),
        ("FB1", "B", 0.25, [(-3.0, -6.1), (-7.57, -6.1)]),
        ("C3.1", "B", 0.25, [(-7.57, -7.545), (-8.3, -8.3), (-9.045, -8.8)]),
        ("LIM", "B", 0.25, [(-2.425, -4.995), (-3.8, -4.995), (-4.445, -4.6)]),
        ("LIM", "B", 0.25, [(-4.445, -4.42), (-4.445, -3.3), (-5.7, -0.9), (-7.75, -0.8)]),
        ("LIM", "B", 0.25, [(-8.425, 1.4), (-8.425, 0.6), (-8.05, 0.0)]),
        ("GND", "B", 0.3, [(-5.995, -4.42), (-6.2, -3.2)]),
        ("GND", "B", 0.3, [(-9.0, -2.538), (-7.1, -2.538), (-6.2, -3.2)]),
        # stage 1 -> 2: S1 under the op-amp, out between pins 3 (GND) and 4 (-5VA)
        ("S1", "B", 0.2, [(-1.9, -7.535), (-0.25, -7.535), (-0.25, -6.0), (0.12, -5.63), (5.6, -5.63),
                          (6.045, -5.85)]),
        # stage 2
        ("GND", "B", 0.3, [(2.525, -6.265), (3.9, -6.265), (4.2, -6.4)]),
        ("INV2", "B", 0.25, [(2.525, -7.535), (3.8, -7.535), (4.0, -7.4), (6.095, -7.4)]),
        ("OUT2", "B", 0.25, [(2.525, -8.805), (3.8, -8.805), (4.0, -8.9), (6.095, -8.9)]),
        ("N2", "B", 0.25, [(7.645, -9.02), (7.67, -7.42), (7.67, -4.325)]),
        ("GND", "B", 0.3, [(7.67, -2.675), (5.8, -2.3)]),
        ("OUT2", "B", 0.25, [(2.6, -8.805), (2.6, -10.0)]),
        ("OUT2", "F", 0.25, [(2.6, -10.0), (2.6, -3.6), (5.2, -1.0)]),
        ("OUT2", "B", 0.25, [(5.2, -1.0), (7.0, -0.225)]),
        ("RXn", "B", 0.25, [(7.0, 1.425), (7.0, 3.3)]),
        ("RXn", "B", 0.25, [(7.0, 1.6), (5.9, 1.6)]),
        # transducer node and TX
        ("XD", "B", 0.3, [(-2.5, 4.662), (-2.5, 0.0)]),
        ("XD", "B", 0.25, [(-5.2, 4.075), (-2.5, 4.075)]),
        ("XD", "B", 0.25, [(-6.775, 1.4), (-3.4, 0.35)]),
        ("GND", "B", 0.3, [(-5.2, 5.725), (-5.2, 6.75)]),
        ("DRV", "B", 0.3, [(-3.45, 7.0), (-1.55, 7.0)]),
        ("DRV", "B", 0.3, [(-2.675, 8.9), (-2.675, 7.0)]),
        ("DRVn", "B", 0.3, [(-4.325, 8.9), (-5.4, 8.9)]),
    ]}


def cells(board):
    out = {}
    for f in board.GetFootprints():
        r = f.GetReference()
        if r.startswith("LS"):
            n = int(r[2:-2])
            out[n] = xy(f.GetPosition())
    return out


def group_fps(board, name):
    g = next(g for g in board.Groups() if g.GetName() == name)
    return [i for i in g.GetItems() if isinstance(i, pcbnew.FOOTPRINT)]


def courtyards(board, side=pcbnew.B_CrtYd):
    out = []
    for f in board.GetFootprints():
        c = f.GetCourtyard(side)
        if c.OutlineCount():
            b = c.BBox()
            out.append((f.GetReference(), pcbnew.ToMM(b.GetX()), pcbnew.ToMM(b.GetY()), pcbnew.ToMM(b.GetRight()),
                        pcbnew.ToMM(b.GetBottom())))
    return out


def in_court(x, y, r, courts, skip=()):
    return any(ref not in skip and x0 - r < x < x1 + r and y0 - r < y < y1 + r for ref, x0, y0, x1, y1 in courts)


def place_via_near(board, cu, courts, net, px, py, xs, ys, track_w, avoid_court=True, made=None):
    """Best via spot among candidate x positions/ranges and y positions: nearest to (px, py) with a clear
    straight B.Cu track from the pad. Adds the via and track; returns (x, y) or None."""
    cands = []
    for x in xs:
        for y in ys:
            cands.append((math.hypot(x - px, y - py), x, y))
    for d, x, y in sorted(cands):
        if d < 0.75:
            continue
        if avoid_court and in_court(x, y, GND_VIA[0] / 2, courts):
            continue
        if not cu.via_ok(x, y, GND_VIA[0] / 2, net):
            continue
        if not cu.seg_ok(pcbnew.B_Cu, px, py, x, y, track_w / 2, net):
            continue
        v = add_via(board, net, x, y, *GND_VIA)
        ts = add_track(board, net, [(px, py), (x, y)], track_w)
        cu.add_track_item(v)
        for t in ts:
            cu.add_track_item(t)
        if made is not None:
            made += [snapshot(i) for i in [v] + ts]
        return x, y
    return None


def sub_route(board, name, keep, keepouts, passes=40, window=None):
    """Route a block on its own: save the board, make a sub-board with only `keep` footprints plus the
    keep-outs, run Freerouting on it (separate processes), return {"items": snapshots, "log": text}."""
    import json
    import subprocess
    d = WORK / name
    d.mkdir(parents=True, exist_ok=True)
    src, sub, spec, out = d / "src.kicad_pcb", d / "sub.kicad_pcb", d / "spec.json", d / "routed.json"
    board.Save(str(src))
    spec.write_text(json.dumps({"keep": keep, "keepouts": keepouts, "window": window}))
    py, helper = sys.executable, str(Path(__file__).resolve().parent / "route_sub.py")
    for args in (["make", str(src), str(sub), str(spec)], ["route", str(sub), str(d), str(passes), str(out)]):
        r = subprocess.run([py, helper, *args], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stdout[-2000:] + r.stderr[-3000:])
    res = json.loads(out.read_text())
    res["items"] = [tuple(s) for s in res["items"]]
    return res


def frange(a, b, step):
    n = int(round((b - a) / step))
    return [a + k * step for k in range(n + 1)]


# ---- stage: channel ---------------------------------------------------------------------------------------
def stage_channel(board):
    """Every cell gets the same hand-made routing (CHANNEL_ROUTE, checked by scripts/cell_design.py): its
    tracks and vias, and its two lane vias with the front-copper jog to its row's slots."""
    made = 0
    cen = cells(board)
    for n, (cx, cy) in sorted(cen.items()):
        row = (n - 1) % 8
        made += len(draw_cell(board, group_fps(board, f"CH{n:02d}"), cx, cy, CHANNEL_ROUTE, row, row, stub=False,
                              jog=False))
    return [f"{len(cen)} cells, {made} tracks and vias"]


# ---- stage: columns ---------------------------------------------------------------------------------------
FAN_PITCH = 0.4          # spacing of the fan-out levels where the lanes turn towards their block pins
RAIL_TRUNK_Y = {"+5VA": 0.75, "-5VA": 1.75}  # back-copper trunks just under the array (y below its edge)
RAIL_TRUNK_W = 0.8


def lane_end_pad(board, net, pick):
    """The block pad a lane ends at: among the pads on `net`, the one `pick` chooses (min / max by y)."""
    pads = [p for f in board.GetFootprints() for p in f.Pads() if p.GetNetname() == net]
    return pick(pads, key=lambda p: p.GetPosition().y)


def stage_columns(board):
    """Every column's front copper (docs/routing_and_emi.md section 6): the 8 drive lanes from the cells
    down to the TX groups and the 8 receive lanes up to the ADCs, each fanning out to its block pin in a
    staircase (the innermost lane turns first and goes farthest, so nothing crosses), a via beside the pin;
    the +-5VA rails down the column centre to their trunks (back copper) under the array; the GND guard on
    every column boundary of the array."""
    cen = cells(board)
    made = []
    cols = sorted({round(x, 3) for x, _ in cen.values()})
    for c, cx in enumerate(cols):
        ys = [cen[8 * c + r + 1][1] for r in range(8)]
        top, bottom = ys[0] - PITCH / 2, ys[7] + PITCH / 2
        (dvx, dvy), (rvx, rvy) = CHANNEL_ROUTE["lanes"]["DRVn"], CHANNEL_ROUTE["lanes"]["RXn"]
        # receive lanes, up: rows 0-3 to the ADC next to the array, rows 4-7 to the one above it
        for rows in (range(0, 4), range(4, 8)):
            pads = {r: lane_end_pad(board, f"RX{8 * c + r + 1}", min) for r in rows}
            via_y = max(xy(p.GetPosition())[1] for p in pads.values()) + 1.1
            for k, r in enumerate(rows):                 # innermost first: lowest turn, farthest pad
                px, py = xy(pads[r].GetPosition())
                lvl = via_y + 0.6 + FAN_PITCH * (3 - k)
                net = f"RX{8 * c + r + 1}"
                made += add_track(board, net, [(cx + rvx, ys[r] + rvy), (cx + RX_X[r], ys[r] + rvy),
                                               (cx + RX_X[r], lvl), (px, lvl), (px, via_y)], LANE_W, pcbnew.F_Cu)
                made.append(add_via(board, net, px, via_y, *GND_VIA))
                made += add_track(board, net, [(px, via_y), (px, py)], 0.25)
        # drive lanes, down: rows 4-7 to the TX group next to the array, rows 0-3 to the one below it
        for rows in (range(7, 3, -1), range(3, -1, -1)):
            pads = {r: lane_end_pad(board, f"DRV{8 * c + r + 1}", max) for r in rows}
            via_y = min(xy(p.GetPosition())[1] for p in pads.values()) - 1.475
            for k, r in enumerate(rows):
                px, py = xy(pads[r].GetPosition())
                lvl = via_y - 0.6 - FAN_PITCH * (3 - k)
                net = f"DRV{8 * c + r + 1}"
                made += add_track(board, net, [(cx + dvx, ys[r] + dvy), (cx + DRV_X[r], ys[r] + dvy),
                                               (cx + DRV_X[r], lvl), (px, lvl), (px, via_y)], LANE_W, pcbnew.F_Cu)
                made.append(add_via(board, net, px, via_y, *GND_VIA))
                made += add_track(board, net, [(px, via_y), (px, py)], 0.3)
        # rails: from the top cell's rail via down the column to a via on its trunk under the array
        for net, x in RAIL_X.items():
            via = next(v for v in CHANNEL_ROUTE["vias"] if v[0] == net)
            ty = bottom + RAIL_TRUNK_Y[net]
            made += add_track(board, net, [(cx + x, ys[0] + via[2]), (cx + x, ty)], RAIL_W, pcbnew.F_Cu)
            made.append(add_via(board, net, cx + x, ty, *GND_VIA))
        # GND guard on the column's left boundary (and the right one of the last column), a via at each corner
        for s in ((-1, 1) if c == len(cols) - 1 else (-1,)):
            gx = cx + s * GUARD_X
            made += add_track(board, "GND", [(gx, top), (gx, bottom)], GUARD_W, pcbnew.F_Cu)
            for k in range(8):            # not at the array's bottom corners: the +-5VA trunks pass there
                made.append(add_via(board, "GND", gx, top + k * PITCH, *GND_VIA))
    # the two +-5VA trunks (back copper) along the bottom of the array, from the left margin to the last column
    import place
    for net, dy in RAIL_TRUNK_Y.items():
        y = max(cen[8 * c + 8][1] for c in range(len(cols))) + PITCH / 2 + dy
        made += add_track(board, net, [(place.MARGIN - 3.0, y), (cols[-1] + RAIL_X[net], y)], RAIL_TRUNK_W)
    return [f"{len(cols)} columns, {len(made)} tracks and vias"]


# ---- stage: blocks ----------------------------------------------------------------------------------------
# The clock buffer, enable buffer, mic and TX group have hand-made routing (scripts/block_routes.py), drawn in
# every instance from its own pads. Otherwise one instance of a repeated block is routed (the one with the busiest surroundings: the ADC and TX group
# next to the array have the other group's lanes passing), then copied to every instance with its nets
# renamed. GND pads get their own via first (Freerouting does not fan out to planes).
BLOCKS = [("ADC", r"^ADC\d+$", "ADC01"), ("TXG", r"^TXG\d+$", "TXG01"), ("CLKB", r"^CLKB\d+$", None),
          ("TXEN", r"^TXEN\d+$", None), ("MIC", r"^MIC\d+$", None)]


def fp_box(fps, margin=0.0):
    boxes = []
    for f in fps:
        c = f.GetCourtyard(pcbnew.B_CrtYd if f.IsFlipped() else pcbnew.F_CrtYd)
        b = c.BBox() if c.OutlineCount() else f.GetBoundingBox(False)
        boxes.append((pcbnew.ToMM(b.GetX()), pcbnew.ToMM(b.GetY()), pcbnew.ToMM(b.GetRight()), pcbnew.ToMM(b.GetBottom())))
    return (min(b[0] for b in boxes) - margin, min(b[1] for b in boxes) - margin,
            max(b[2] for b in boxes) + margin, max(b[3] for b in boxes) + margin)


def layout_key(fps):
    """Relative layout of a block instance (anchor: its first part), to check that copies match."""
    fs = sorted(fps, key=lambda f: local_ref(f.GetReference()))
    ax, ay = xy(fs[0].GetPosition())
    return fs[0], tuple((local_ref(f.GetReference()), round(xy(f.GetPosition())[0] - ax, 2),
                         round(xy(f.GetPosition())[1] - ay, 2), round(f.GetOrientationDegrees()) % 360,
                         f.IsFlipped()) for f in fs)


def exposed_pad(f):
    """The footprint's exposed GND pad (an SMD GND pad wider than 1.4 mm both ways), or None."""
    for pd in f.Pads():
        bb = pd.GetBoundingBox()
        if pd.GetNetname() == "GND" and pd.GetDrillSize().x == 0 and                 min(pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())) > 1.4:
            return pd
    return None


def stub_to_pad(board, cu, pin, ep, made, w=0.25):
    """A straight track from a ground pin inwards into the exposed pad, if it clears the other pins (the
    datasheets tie AGND/PGND pins to the thermal pad). Returns True when drawn."""
    px, py = xy(pin.GetPosition())
    bb = ep.GetBoundingBox()
    x0, y0, x1, y1 = (pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY()), pcbnew.ToMM(bb.GetRight()),
                      pcbnew.ToMM(bb.GetBottom()))
    if x0 - 0.05 <= px <= x1 + 0.05:          # pin above or below the pad: vertical stub
        ex, ey = px, min(max(py, y0 + 0.15), y1 - 0.15)
    elif y0 - 0.05 <= py <= y1 + 0.05:        # pin beside the pad: horizontal stub
        ex, ey = min(max(px, x0 + 0.15), x1 - 0.15), py
    else:
        return False
    layer = pcbnew.B_Cu if pin.IsOnLayer(pcbnew.B_Cu) else pcbnew.F_Cu
    if not cu.seg_ok(layer, px, py, ex, ey, w / 2, "GND", clr=0.2):
        return False
    for t in add_track(board, "GND", [(px, py), (ex, ey)], w, layer):
        cu.add_track_item(t)
        made.append(t)
    return True


def gnd_fanout(board, fps, made, reach=2.5):
    """A via for every GND SMD pad of the block (not exposed pads that carry their own thermal vias)."""
    box = fp_box(fps, reach + 1)
    from route_lib import Copper
    cu = Copper(board, box)
    courts = [c for c in courtyards(board) if c[1] < box[2] and c[3] > box[0] and c[2] < box[3] and c[4] > box[1]]
    miss = []
    for f in fps:
        ep = exposed_pad(f)
        for pd in f.Pads():
            if pd.GetNetname() != "GND" or pd.GetDrillSize().x > 0:
                continue
            bb = pd.GetBoundingBox()
            if min(pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())) > 1.4:
                continue                                  # exposed pad: its own thermal vias
            px, py = xy(pd.GetPosition())
            if ep and stub_to_pad(board, cu, pd, ep, made):
                continue                                  # a QFN's ground pin goes straight into its exposed pad
            grid = frange(-reach, reach, 0.15)
            xs, ys = [px + d for d in grid], [py + d for d in grid]
            if not (place_via_near(board, cu, courts, "GND", px, py, xs, ys, 0.3, made=made)
                    or place_via_near(board, cu, courts, "GND", px, py, xs, ys, 0.3, avoid_court=False, made=made)):
                miss.append(f"{f.GetReference()}.{pd.GetNumber()}")
    return miss


def strict_net_map(src_fps, dst_fps):
    """Template net -> instance net, or None if the two differ in more than names (one template net would
    become two instance nets, e.g. a mic whose select pin is tied the other way)."""
    dst = {local_ref(f.GetReference()): f for f in dst_fps}
    m = {}
    for f in src_fps:
        g = dst.get(local_ref(f.GetReference()))
        if g is None:
            return None
        gp = {p.GetNumber(): p for p in g.Pads()}
        for p in f.Pads():
            q = gp.get(p.GetNumber())
            if q is None or not p.GetNetname():
                continue
            if m.setdefault(p.GetNetname(), q.GetNetname()) != q.GetNetname():
                return None
    return m if len(set(m.values())) == len(m) else None


def copy_fits(board, items, dx, dy, nets, inst):
    """Would the template's items, moved and renamed, clear the copper and the other parts around this
    instance? A coarse check (pads as boxes, tracks sampled), so only 0.1 mm is asked here: it is meant to
    catch different surroundings, and KiCad's DRC checks the exact copper afterwards. Vias also stay out of
    other parts' courtyards."""
    from route_lib import Copper
    x0, y0, x1, y1 = fp_box(inst, 3.0)
    cu = Copper(board, (x0, y0, x1, y1))
    own = {f.GetReference() for f in inst}
    courts = [c for c in courtyards(board) if c[0] not in own and c[1] < x1 and c[3] > x0 and c[2] < y1 and c[4] > y0]
    for s in items:
        if s[0] == "V":
            _, x, y, net, size, _ = s
            x, y, net = x + dx, y + dy, nets.get(net, net)
            if not cu.via_ok(x, y, size / 2, net, clr=0.1) or in_court(x, y, size / 2, courts):
                return False
        else:
            _, xa, ya, xb, yb, net, w, layer = s
            if not cu.seg_ok(layer, xa + dx, ya + dy, xb + dx, yb + dy, w / 2, nets.get(net, net), clr=0.1):
                return False
    return True


def route_instance(board, name):
    """GND vias and Freerouting for one block instance; returns its items (snapshots) and a report."""
    fps = group_fps(board, name)
    pre = []
    miss = gnd_fanout(board, fps, pre)
    x0, y0, x1, y1 = fp_box(fps, 1.0)
    kos = [[["F.Cu", "B.Cu"], x0 - 40, y0 - 40, x1 + 40, y0, "route:out"],
           [["F.Cu", "B.Cu"], x0 - 40, y1, x1 + 40, y1 + 40, "route:out"],
           [["F.Cu", "B.Cu"], x0 - 40, y0, x0, y1, "route:out"],
           [["F.Cu", "B.Cu"], x1, y0, x1 + 40, y1, "route:out"]]
    res = sub_route(board, name.lower(), [f.GetReference() for f in fps], kos, passes=40,
                    window=[x0 - 3, y0 - 3, x1 + 3, y1 + 3])
    old = set(snapshot(i) for i in board.GetTracks())
    # Freerouting necks tracks down to 0.1 mm at fine-pitch pins; JLCPCB's minimum is 0.127 mm
    routed = [s if s[0] == "V" or s[6] >= MIN_TRACK else s[:6] + (MIN_TRACK,) + s[7:]
              for s in res["items"] if s not in old]
    copy_items(board, routed, 0, 0, {})
    note = f"{name}: {len(routed)} routed, {len(pre) // 2} GND vias" + (f", no GND via for {miss}" if miss else "")
    return [tuple(s) for s in pre] + routed, note


def stage_blocks(board):
    """Each repeated block: route one instance, copy it to every instance where the copy fits (same nets
    up to their names, clear of what is around it); route any other instance on its own and keep it as a
    further template. Small differences in surroundings (clock buffers above the upper ADC row, the mic
    select pin) are why a block can need more than one."""
    from block_routes import BLOCK_ROUTES
    report = []
    for kind, pat, first in BLOCKS:
        names = sorted(g.GetName() for g in board.Groups() if re.match(pat, g.GetName()))
        if not names:
            continue
        if kind in BLOCK_ROUTES:                  # hand-made routing, drawn from each instance's own pads
            n_items = 0
            for name in names:
                tracks, vias = BLOCK_ROUTES[kind](group_fps(board, name))
                for net, layer, w, pts in tracks:
                    n_items += len(add_track(board, net, pts, w, pcbnew.F_Cu if layer == "F" else pcbnew.B_Cu))
                for net, x, y in vias:
                    add_via(board, net, x, y, *GND_VIA)
                    n_items += 1
            report.append(f"{kind}: hand-made routing in {len(names)} instances ({n_items} items)")
            continue
        order = ([first] if first else []) + [n for n in names if n != first]
        templates, copied, own = [], 0, []
        for name in order:
            inst = group_fps(board, name)
            a1, key1 = layout_key(inst)
            done = False
            for tname, tfps, a0, key0, items in templates:
                nets = strict_net_map(tfps, inst) if key1 == key0 else None
                if nets is None:
                    continue
                (ax, ay), (bx, by) = xy(a0.GetPosition()), xy(a1.GetPosition())
                if copy_fits(board, items, bx - ax, by - ay, nets, inst):
                    copy_items(board, items, bx - ax, by - ay, nets)
                    copied += 1
                    done = True
                    break
            if not done:
                items, note = route_instance(board, name)
                templates.append((name, inst, a1, key1, items))
                own.append(note)
        report.append(f"{kind}: {len(templates)} layout(s) routed, {copied} copies. " + "; ".join(own))
    return report


# ---- stage: fanout --------------------------------------------------------------------------------------
def stage_fanout(board):
    """A GND via for every GND pad not yet in a routed block (power sections, headers, loose parts), so the
    global autorouter sees them connected through the planes."""
    done = set()
    for g in board.Groups():
        if any(re.match(pat, g.GetName()) for _, pat, _ in BLOCKS) or g.GetName().startswith("CH"):
            done |= {i.m_Uuid.AsString() for i in g.GetItems()}
    for key in POWER_ROUTED:                  # hand-routed power sections bring their own GND vias
        done |= {f.m_Uuid.AsString() for f in power_section_fps(board, key)}
    rest = [f for f in board.GetFootprints() if f.m_Uuid.AsString() not in done
            and any(p.GetNetname() == "GND" and p.GetDrillSize().x == 0 for p in f.Pads())]
    made, miss = [], []
    for f in rest:
        miss += gnd_fanout(board, [f], made)
    return [f"{len(rest)} parts, {len(made) // 2} GND vias" + (f"; no via for {miss}" if miss else "")]


# ---- stage: TX bus -------------------------------------------------------------------------------------
# The shared 595 inputs (OE, CLR, SCK_x, LATCH_x) as a designed bus: one comb per column, nothing crosses.
#  - Lower 595 (pins 9-16 face down): a back-copper stub straight down from each bus pin to a via on its line.
#  - Upper 595: a comb in the channel under its pins (the pin nearest the gap on the top level, so the stubs
#    never cross), right to the gap beside the column, down the gap on back copper to a via on its line.
#  - The lines: front copper just below the VDRV area (the drive lanes end at the lower drivers, so the front
#    is free across the whole width). OE and CLR run the full width; SCK_A..D share one level and LATCH_A..D
#    another (each spans its pair of columns). Clock lines keep 0.9 mm pitch from the others (0.65 mm gap,
#    kicad/aesa.kicad_dru), the rest 0.45 mm, and 0.65 mm where a line carries vias (a via clears the next line).
# SDI gets the comb and a via below the lines (each SDI goes to one 595); the header, the clock buffers, +5V
# and EN reach the bus through the general router.
TXB_LOWER = {"OE": 0.95, "CLR": 1.6, "SCK": 2.5, "LATCH": 3.15, "SDI": 3.9}        # below the lower pad ends
TXB_UPPER = {"CLR": 0.55, "SCK": 1.45, "LATCH": 1.95, "OE": 2.85, "SDI": 3.3}       # below the upper pad ends
TXB_GAP_X = {"SDI": 0.0, "OE": 0.65, "LATCH": 1.55, "SCK": 2.2, "CLR": 3.1}         # in the gap, from its left; a
# deeper line passing a neighbour's via keeps 0.65 mm pitch
TXB_PIN = {"SDI": 14, "OE": 13, "LATCH": 12, "SCK": 11, "CLR": 10}
# SDI band (front copper under the bus): level k of each SDI line, 0.64 mm apart (a tap via clears the next line).
# Left-margin lines (SDI8-15) and right-margin lines (SDI0-7) interleave; within each side the line on the inner
# lane takes the higher level, so the turns out of the margins never cross.
TRUNK_BAND = {"TX_SDI9": 0, "TX_SDI8": 2, "TX_SDI11": 4, "TX_SDI10": 6, "TX_SDI13": 8, "TX_SDI12": 10,
              "TX_SDI15": 12, "TX_SDI14": 14,
              "TX_SDI7": 1, "TX_SDI6": 3, "TX_SDI5": 5, "TX_SDI4": 7, "TX_SDI3": 9, "TX_SDI2": 11, "TX_SDI1": 13,
              "TX_SDI0": 15}
TRUNK_BAND_PITCH = 0.64
TXB_GAP_FROM_PIN9 = 3.65   # the gap's first line, right of the 595's pin 9


def stage_txbus(board):
    from route_lib import Copper
    out, bad = [], []
    fp = {f.GetReference(): f for f in board.GetFootprints()}

    def pin(k, n):
        p = next(q for q in fp[f"U1{k:02d}01"].Pads() if q.GetNumber() == str(n))
        return xy(p.GetPosition()), p.GetNetname()

    lines = {}                                   # net -> [x of every tap]
    level = {}                                   # net -> y of its line
    cu = Copper(board)
    items = []

    def draw(net, pts, w, via=True):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if not cu.seg_ok(pcbnew.B_Cu, x0, y0, x1, y1, w / 2, net, clr=0.2):
                bad.append(f"{net} B {x0:.2f},{y0:.2f}-{x1:.2f},{y1:.2f}")
        for t in add_track(board, net, pts, w, pcbnew.B_Cu):
            cu.add_track_item(t)
        if via:
            x, y = pts[-1]
            if not cu.via_ok(x, y, 0.3, net):
                bad.append(f"{net} via {x:.2f},{y:.2f}")
            cu.add_track_item(add_via(board, net, x, y))
            lines.setdefault(net, []).append(x)

    courts = courtyards(board)

    from maze import is_clock

    def clk_clr(other):
        return 0.7 if is_clock(other) else 0.0          # DRC: clock tracks 0.65 mm from other tracks

    def sdi_end(net, x, y_lo):
        """The SDI tail below the bus: down to its band level, stepping sideways (just under the bus) to the
        nearest x where the tail and its via clear all copper and parts (clock-buffer blocks sit under some
        taps). Returns the points after (x, top)."""
        top = y_lo + TXB_LOWER["SDI"]
        y = top + TRUNK_BAND_PITCH * TRUNK_BAND[net]
        yj = top + 0.35

        def clear(xx):
            if any(x0 - 0.5 < xx < x1 + 0.5 and y0 - 0.5 < y and y1 + 0.5 > yj for ref, x0, y0, x1, y1 in courts):
                return False
            segs = [(xx, yj, xx, y)] + ([(x, top, x, yj), (x, yj, xx, yj)] if xx != x else [(x, top, x, yj)])
            return all(cu.seg_ok(pcbnew.B_Cu, *sg, 0.1, net, clr=0.25, net_clr=clk_clr) for sg in segs) and                 cu.via_ok(xx, y, 0.3, net, clr=0.25, net_clr=clk_clr)
        if clear(x):
            return [(x, y)]
        for d in [i * 0.1 for i in range(1, 80)]:
            for xx in (x + d, x - d):
                if clear(xx):
                    return [(x, yj), (xx, yj), (xx, y)]
        bad.append(f"{net}: no clear x for the band tap near {x:.1f}")
        return [(x, y)]

    for col in range(8):
        up, lo = 2 * col + 2, 2 * col + 1               # even group in the upper TX row (common.tx_port)
        (p9u, yu), _ = pin(up, 9)
        (_, yl_pin), _ = pin(lo, 9)
        y_up, y_lo = yu + 0.975, yl_pin + 0.975     # pad ends towards the channel
        gap0 = p9u + TXB_GAP_FROM_PIN9
        for key in TXB_LOWER:
            (px, py), net = pin(lo, TXB_PIN[key])
            if key == "SDI":
                draw(net, [(px, py)] + sdi_end(net, px, y_lo), 0.2)
                continue
            y = y_lo + TXB_LOWER[key]
            level.setdefault(net, y)
            draw(net, [(px, py), (px, y)], 0.25 if key in ("SCK", "LATCH") else 0.2)
        for key in TXB_UPPER:
            (px, py), net = pin(up, TXB_PIN[key])
            yc = y_up + TXB_UPPER[key]
            xg = gap0 + TXB_GAP_X[key]
            if key == "SDI":
                draw(net, [(px, py), (px, yc), (xg, yc)] + sdi_end(net, xg, y_lo), 0.2)
                continue
            y = y_lo + TXB_LOWER[key]
            level.setdefault(net, y)
            draw(net, [(px, py), (px, yc), (xg, yc), (xg, y)], 0.25 if key in ("SCK", "LATCH") else 0.2)
    for net, xs in lines.items():
        if net.startswith("TX_SDI"):
            continue                              # one 595 each: the general router brings it to the via
        y = level[net]
        w = 0.25 if re.search(r"_(SCK|LATCH)", net) else 0.2
        x0, x1 = min(xs), max(xs)
        if not cu.seg_ok(pcbnew.F_Cu, x0, y, x1, y, w / 2, net, clr=0.2):
            bad.append(f"{net} F line {x0:.2f}-{x1:.2f} at {y:.2f}")
        for t in add_track(board, net, [(x0, y), (x1, y)], w, pcbnew.F_Cu):
            cu.add_track_item(t)
        out.append(f"{net}: line {x0:.1f}..{x1:.1f} at y {y:.2f}, {len(xs)} taps")
    if bad:
        out.append(f"{len(bad)} clearance problems: " + "; ".join(bad[:12]))
    return out


# ---- stage: margin trunks --------------------------------------------------------------------------------
# The long TX lines from the FPGA header (J9901, top) to the TX bus and the SDI band (bottom) as straight lanes
# down the side margins on front copper, 0.42 mm apart, so the margins (narrowest beside the mid-side mounting
# holes H5/H6) are used in order instead of by whatever the autorouter finds first.
#  - Left margin: OE, CLR and SDI8-15 (the left part of the header). The pins fan in as a staircase under the
#    header (the pin further from the margin on the lower level, so the stubs never cross), run left to just
#    right of the 12 V jack, step left onto their lanes below it and run down the margin. OE and CLR leave the
#    header on back copper and come up below the SDI levels: at the bottom they turn into the bus first, so
#    they take the inner lanes. At the bottom each lane turns right, the inner lanes first.
#  - Right margin: SDI0-7 (the right part of the header), the same way mirrored, with no jack to pass.
TRUNK_LEFT = ["TX_OE_N", "TX_CLR_N", "TX_SDI9", "TX_SDI8", "TX_SDI11", "TX_SDI10", "TX_SDI13", "TX_SDI12",
              "TX_SDI15", "TX_SDI14"]                     # inner lane first
TRUNK_RIGHT = ["TX_SDI7", "TX_SDI6", "TX_SDI5", "TX_SDI4", "TX_SDI3", "TX_SDI2", "TX_SDI1", "TX_SDI0"]
TRUNK_PITCH = 0.42
TRUNK_LEFT_X = 15.3                                       # inner lane; the array's GND guard is at x 16
TRUNK_RIGHT_X = 178.26
TRUNK_TOP = 10.6                                          # first fan-in level under the header pads
TRUNK_JACK_SHIFT = 3.4                                    # the left lanes run this far right past the jack
TRUNK_JOG_Y = 23.0                                        # below the jack: the left lanes step onto the margin
TRUNK_BACK = {"TX_CLR_N": 17.5, "TX_OE_N": 18.3}          # OE/CLR: back copper from the header to these levels


def stage_trunks(board):
    from route_lib import Copper
    cu = Copper(board)
    out, bad = [], []
    fp = {f.GetReference(): f for f in board.GetFootprints()}
    hdr = {p.GetNetname(): (xy(p.GetPosition()), p.GetNumber()) for p in fp["J9901"].Pads()}

    def track(net, pts, layer=pcbnew.F_Cu, w=0.2):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if not cu.seg_ok(layer, x0, y0, x1, y1, w / 2, net, clr=0.2):
                bad.append(f"{net} {'F' if layer == pcbnew.F_Cu else 'B'} {x0:.2f},{y0:.2f}-{x1:.2f},{y1:.2f}")
        for t in add_track(board, net, pts, w, layer):
            cu.add_track_item(t)

    def via(net, x, y):
        if not cu.via_ok(x, y, 0.3, net, clr=0.2):
            bad.append(f"{net} via {x:.2f},{y:.2f}")
        cu.add_track_item(add_via(board, net, x, y))

    def stub_x(net, side):
        """x where a header pin's line comes down: odd row straight, even row through the gap beside it."""
        (x, y), num = hdr[net]
        even = int(num) % 2 == 0
        return x + (0 if not even else (-1.27 if side == "L" else 1.27)), (x, y), even

    def ends():
        """Where each trunk ends at the bottom: (level y, x of its far end)."""
        out = {}
        for t in board.GetTracks():                 # the bus lines' near ends and the SDI band taps
            n = t.GetNetname()
            if t.GetClass() == "PCB_VIA" and n.startswith("TX_SDI") and pcbnew.ToMM(t.GetPosition().y) > 279.5:
                out[n] = (pcbnew.ToMM(t.GetPosition().y), pcbnew.ToMM(t.GetPosition().x))
            elif t.GetClass() != "PCB_VIA" and t.GetLayer() == pcbnew.F_Cu and n in ("TX_OE_N", "TX_CLR_N") and \
                    pcbnew.ToMM(t.GetStart().y) > 270:
                xs = [pcbnew.ToMM(t.GetStart().x), pcbnew.ToMM(t.GetEnd().x)]
                out[n] = (pcbnew.ToMM(t.GetStart().y), min(xs) if n not in out else min(min(xs), out[n][1]))
        return out
    end = ends()

    # left: fan-in levels by stub x (OE/CLR come up from the back below the SDI levels)
    sdis = sorted([n for n in TRUNK_LEFT if n not in TRUNK_BACK], key=lambda n: stub_x(n, "L")[0])
    lvl = {n: TRUNK_TOP + i * TRUNK_PITCH for i, n in enumerate(sdis)}
    lvl.update(TRUNK_BACK)
    order = sorted(TRUNK_LEFT, key=lambda n: lvl[n])          # shallowest first = outermost lane
    for i, net in enumerate(TRUNK_LEFT):
        lane = TRUNK_LEFT_X - i * TRUNK_PITCH
        pre = lane + TRUNK_JACK_SHIFT
        jog = TRUNK_JOG_Y + order.index(net) * TRUNK_PITCH
        sx, (px, py), even = stub_x(net, "L")
        y = lvl[net]
        if net in TRUNK_BACK:                                   # back copper down from the pin, via up
            pts = [(px, py)] + ([(sx, py + 1.27)] if even else []) + [(sx, y)]
            track(net, pts, pcbnew.B_Cu)
            via(net, sx, y)
            head = [(sx, y)]
        else:
            head = [(px, py)] + ([(sx, py + 1.275)] if even else []) + [(sx, y)]
        yb, xe = end[net]
        track(net, head + [(pre, y), (pre, jog), (lane, jog), (lane, yb), (xe, yb)])
        out.append(f"{net}: left lane x {lane:.2f}, fan-in level {y:.2f}, ends at ({xe:.1f}, {yb:.2f})")
    # right
    sdis = sorted(TRUNK_RIGHT, key=lambda n: -stub_x(n, "R")[0])
    lvl = {n: TRUNK_TOP + i * TRUNK_PITCH for i, n in enumerate(sdis)}
    for i, net in enumerate(TRUNK_RIGHT):
        lane = TRUNK_RIGHT_X + i * TRUNK_PITCH
        sx, (px, py), even = stub_x(net, "R")
        y = lvl[net]
        yb, xe = end[net]
        head = [(px, py)] + ([(sx, py + 1.275)] if even else []) + [(sx, y)]
        track(net, head + [(lane, y), (lane, yb), (xe, yb)])
        out.append(f"{net}: right lane x {lane:.2f}, fan-in level {y:.2f}, ends at ({xe:.1f}, {yb:.2f})")
    if bad:
        out.append(f"{len(bad)} clearance problems: " + "; ".join(bad[:15]))
    return out


# ---- stage: power sections ------------------------------------------------------------------------------
POWER_ROUTED = {"A", "C"}                # sections with hand-made routing (block_routes); the rest: stage_oneoff
RTN_ISLAND = (3.4, 27.4, 15.8, 38.0)     # the eFuse's RTN copper (back), around U8 and its RTN-side parts


def power_section_fps(board, key):
    import place
    from power_sections import section_of
    pos = place.power_positions()
    return [f for f in board.GetFootprints() if f.GetReference()[-4:-2] == "95"
            and section_of(*pos.get(local_ref(f.GetReference()), ("", 0, 0))) == key]


def stage_power(board):
    """Hand-made routing of the power sections in POWER_ROUTED (scripts/block_routes.py), and the eFuse's RTN
    island: a back-copper area on EFUSE_RTN under U8 and its RTN-side parts, apart from GND (TPS2660
    datasheet 9.3.5.5: the reverse-polarity block sits between RTN and GND)."""
    from block_routes import power_input
    out = []
    fps = power_section_fps(board, "A")
    tracks, vias = power_input(fps)
    for net, layer, w, pts in tracks:
        add_track(board, net, pts, w, pcbnew.F_Cu if layer == "F" else pcbnew.B_Cu)
    for net, x, y in vias:
        add_via(board, net, x, y, *GND_VIA)
    from block_routes import power_pm6v5
    fc = power_section_fps(board, "C")
    tc, vc = power_pm6v5(fc)
    for net, layer, w, pts in tc:
        add_track(board, net, pts, w, pcbnew.F_Cu if layer == "F" else pcbnew.B_Cu)
    for net, x, y in vc:
        add_via(board, net, x, y, *GND_VIA)
    out.append(f"section C (+-6V5): {len(tc)} tracks, {len(vc)} vias")
    rtn = next(p.GetNetname() for f in fps if f.GetReference() == "U9508" for p in f.Pads() if p.GetNumber() == "8")
    z = supply_zone(board, rtn, pcbnew.B_Cu, *RTN_ISLAND, priority=2)
    z.SetZoneName("EFUSE_RTN island")
    out.append(f"section A (12 V input): {len(tracks)} tracks, {len(vias)} vias, RTN island on {rtn}")
    return out


# ---- stage: one-off blocks -------------------------------------------------------------------------------
def internal_nets(board, group_names):
    """Nets whose pads all belong to one of the named groups (GND excepted: vias to the planes). The Power group
    counts each of its sections (scripts/power_sections.py) as a block of its own: the rails between sections
    (VIN, +6V5, -6V5, ...) are inter-block nets, laid out with the other block connections, not here."""
    import place
    from power_sections import section_of
    pos = place.power_positions()
    own = {}
    for g in board.Groups():
        if g.GetName() in group_names:
            for it in g.GetItems():
                if it.GetClass() == "FOOTPRINT":
                    ref = it.Cast().GetReference()
                    home = g.GetName()
                    if home == "Power":
                        home += ":" + str(section_of(*pos.get(local_ref(ref), ("", 0, 0))))
                    own[ref] = home
    nets = {}
    for f in board.GetFootprints():
        for p in f.Pads():
            n = p.GetNetname()
            if n and n != "GND":
                nets.setdefault(n, set()).add(own.get(f.GetReference()))
    return sorted(n for n, homes in nets.items() if None not in homes and len(homes) == 1)


def stage_oneoff(board):
    """The one-off blocks (power sections, header filters) routed inside themselves before any block is
    connected to another: the grid router (scripts/maze.py) on their internal nets only, fine-pitch pins
    fanned out first, rip-up for pins boxed in."""
    import maze
    import place
    keep = [(place.MARGIN - 0.5, place.TOP - 0.3, place.W - place.MARGIN + 0.5, place.ARRAY_END + 0.3)]
    r = maze.Router(board, keep, src=None)
    nets = maze.order(internal_nets(board, {"Power", "Headers"}))
    out = [f"{len(nets)} internal nets; escape stubs {maze.escape_stubs(r, set(nets))}"]
    made = failed = 0
    for i, net in enumerate(nets):
        t0 = time.time()
        m, f = r.route_net(net)
        r.routed.add(net)
        made += m
        failed += f
        print(f"  oneoff {i + 1}/{len(nets)} {net}: {m} made, {f} failed, {time.time() - t0:.1f} s", flush=True)
    left = {n: v for n, v in maze.open_nets(board).items() if n in set(nets)}
    for net in maze.order(list(left)):              # one more go for what a rip-up left open
        r.route_net(net)
    left = {n: v for n, v in maze.open_nets(board).items() if n in set(nets)}
    out.append(f"{made} connections, {failed} failed on the first pass; still open: "
               + (", ".join(sorted(left)) if left else "none"))
    return out


# ---- stage: 45-degree corners ------------------------------------------------------------------------------
def stage_chamfer(board):
    """No right angles: every plain 90-degree bend becomes two 45-degree bends where it clears (route_lib)."""
    from route_lib import chamfer_corners, right_angles
    n = chamfer_corners(board)
    left = right_angles(board)
    out = [f"{n} right-angle corners cut to 45 degrees; {len(left)} left (no room to cut)"]
    out += [f"  {net} {layer} ({x:.2f}, {y:.2f})" for net, layer, x, y in left[:20]]
    return out


# ---- stage: supply pours --------------------------------------------------------------------------------
def supply_zone(board, net, layer, x0, y0, x1, y1, priority=1):
    """A copper area for a supply on one layer (solid to SMD pads, spokes on THT, islands removed)."""
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(board.FindNet(net))
    z.SetLocalClearance(pcbnew.FromMM(0.3))
    z.SetMinThickness(pcbnew.FromMM(0.25))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)
    z.SetThermalReliefGap(pcbnew.FromMM(0.5))
    z.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.5))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    z.SetAssignedPriority(priority)
    ol = z.Outline()
    ol.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        ol.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
    z.SetZoneName(f"{net} {board.GetLayerName(layer)}")
    board.Add(z)
    return z


def stage_supplies(board):
    """Supply areas on the front copper (the GND plane In1 lies 0.2 mm under them): VDRV over the TX drivers
    (the TX groups take it through the via under each 1 uF), +3V3A over the two ADC rows. They fill around
    the lanes and their fan-outs; the outer GND pours (stage pours) take the rest of the front."""
    import place
    a, e = place.ARRAY_END, place.MARGIN
    # VDRV ends just above the lower 595 row's pins: the front copper below is the TX bus band (the drive lanes
    # stop at the lower drivers, so lines run the full width there), reached by a via under each 595 pin
    vdrv_end = a + place.TX_TOP + 2 * place.TXG_H + place.TX_CH - 0.35
    supply_zone(board, "VDRV", pcbnew.F_Cu, e - 1.0, a + place.TX_TOP - 0.6, place.W - e + 1.0, vdrv_end)
    supply_zone(board, "+3V3A", pcbnew.F_Cu, e - 1.0, place.ADC_Y + 0.5, place.W - e + 1.0, place.TOP - 0.5)
    fill(board)
    return ["VDRV area over the TX zone, +3V3A area over the ADC rows (front copper)"]


# ---- stage: pours ---------------------------------------------------------------------------------------
STITCH = 5.0             # plane stitching grid and board-edge via fence pitch (docs/routing_and_emi.md: <= 6 mm)
FENCE_IN = 1.2           # fence vias this far inside the board edge


def outer_pours(board):
    """GND pours on F.Cu and B.Cu with the inner planes' outline: thermal spokes on THT pads (hand
    soldering), solid on SMD pads, islands removed."""
    import place
    gnd = board.FindNet("GND")
    e, b, W, H = place.COPPER_EDGE, place.BEVEL, place.W, place.H
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(gnd)
        z.SetLocalClearance(pcbnew.FromMM(0.3))
        z.SetMinThickness(pcbnew.FromMM(0.25))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)
        z.SetThermalReliefGap(pcbnew.FromMM(0.5))
        z.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.5))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        ol = z.Outline()
        ol.NewOutline()
        for x, y in ((b + e, e), (W - b - e, e), (W - e, b + e), (W - e, H - b - e), (W - b - e, H - e),
                     (b + e, H - e), (e, H - b - e), (e, b + e)):
            ol.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
        z.SetZoneName(f"GND {board.GetLayerName(layer)}")
        board.Add(z)


def fill(board):
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())


def stage_pours(board):
    """Outer GND pours, then GND vias on a grid and along the board edge wherever one fits clear of other
    copper and parts and lands in both outer pours, so all four GND layers are stitched together."""
    import place
    from route_lib import Copper
    for z in [z for z in board.Zones() if z.GetZoneName() in ("GND F.Cu", "GND B.Cu")]:
        board.Remove(z)
    outer_pours(board)
    for f in board.GetFootprints():      # thermal vias in exposed pads carry heat: solid to every pour
        for pd in f.Pads():
            if pd.GetNetname() == "GND" and 0 < pcbnew.ToMM(pd.GetDrillSize().x) <= 0.4:
                pd.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
    fill(board)
    W, H, b = place.W, place.H, place.BEVEL
    cu = Copper(board)
    courts = courtyards(board) + courtyards(board, pcbnew.F_CrtYd)
    pours = [z for z in board.Zones() if z.GetZoneName() in ("GND F.Cu", "GND B.Cu")]

    def in_pours(x, y):
        pt = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
        return all(z.HitTestFilledArea(z.GetLayer(), pt, pcbnew.FromMM(0.35)) for z in pours)

    def try_via(x, y):
        if in_court(x, y, 0.3, courts) or not cu.via_ok(x, y, 0.3, "GND", clr=0.3) or not in_pours(x, y):
            return False
        cu.add_track_item(add_via(board, "GND", x, y, *GND_VIA))
        return True

    fence, grid = 0, 0
    f = FENCE_IN
    corners = [(b + f, f), (W - b - f, f), (W - f, b + f), (W - f, H - b - f), (W - b - f, H - f), (b + f, H - f),
               (f, H - b - f), (f, b + f)]
    for (x0, y0), (x1, y1) in zip(corners, corners[1:] + corners[:1]):
        L = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(L // STITCH))
        for k in range(n):
            x, y = x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n
            # slide along the edge to find room, at most half a pitch
            for s in (0, 0.5, -0.5, 1.0, -1.0, 1.5, -1.5, 2.0, -2.0):
                if try_via(x + (x1 - x0) / L * s, y + (y1 - y0) / L * s):
                    fence += 1
                    break
    y = STITCH
    while y < H - 2:
        x = STITCH
        while x < W - 2:
            for dx, dy in ((0, 0), (0.6, 0), (-0.6, 0), (0, 0.6), (0, -0.6), (1.2, 0), (-1.2, 0), (0, 1.2), (0, -1.2)):
                if try_via(x + dx, y + dy):
                    grid += 1
                    break
            x += STITCH
        y += STITCH
    fill(board)
    return [f"outer GND pours on F.Cu and B.Cu, {fence} edge-fence vias, {grid} stitching vias"]


def stage_global(board, passes=30):
    """Freerouting for everything still open, with everything routed so far locked and the finished array
    (cells, lanes, rails) a keep-out, so it works only in the margins, the bus channels, the header band
    and the power area."""
    import place
    keep = [f.GetReference() for f in board.GetFootprints()]
    a0, a1 = place.TOP - 0.3, place.ARRAY_END + 0.3
    kos = [[["F.Cu", "B.Cu"], place.MARGIN, a0, place.W - place.MARGIN, a1, "route:array"]]
    res = sub_route(board, "global", keep, kos, passes=passes)
    old = set(snapshot(i) for i in board.GetTracks())
    new = [s for s in res["items"] if s not in old]
    copy_items(board, new, 0, 0, {})
    return [f"Freerouting: {len(new)} new tracks and vias; " + " | ".join(res["log"].strip().splitlines()[-1:])]




STAGES = {"channel": stage_channel, "columns": stage_columns, "blocks": stage_blocks, "txbus": stage_txbus,
          "chamfer": stage_chamfer, "trunks": stage_trunks, "fanout": stage_fanout, "oneoff": stage_oneoff,
          "power": stage_power,
          "supplies": stage_supplies, "global": stage_global, "pours": stage_pours}


def run(board, names):
    for name in names:
        print(f"== {name}")
        for line in STAGES[name](board):
            print("  " + line)


def main(names):
    """Stages in order. A run that includes `channel` starts from the bare placed board (all earlier routing
    removed); otherwise the stages add to what is there."""
    order = ["channel", "columns", "blocks", "txbus", "power", "fanout", "oneoff", "chamfer", "trunks", "supplies",
             "global", "pours"]
    names = [n for n in order if n in (names or order)]
    board = pcbnew.LoadBoard(str(PCB))
    if "channel" in names:
        for t in [i.Cast() for i in board.GetTracks()]:
            board.Remove(t)
        for z in list(board.Zones()):
            if z.GetIsRuleArea() and z.GetZoneName().startswith("route:") or                     not z.GetIsRuleArea() and z.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu):
                board.Remove(z)
    run(board, names)
    board.Save(str(PCB))
    print(f"saved {PCB.relative_to(REPO)}: {len(list(board.GetTracks()))} tracks/vias")


if __name__ == "__main__":
    main(sys.argv[1:])
