"""Build kicad/aesa.kicad_pcb from the schematic and place every part, block by block.

Floorplan (front view; all SMT parts on the back, B.Cu; transducers on the front, F.Cu):

    /---------------------------------------------------------\\  y = 0   (3 mm bevels on every corner)
    | 12 V in  |   J1 ribbon   |   J2 ribbon   | +3V3A, +3V3   |  all cables leave the top edge
    | (eFuse)  | clock buffers, header pulls   | (quiet LDOs)  |
    |  16 ADC blocks (2 rows of 8)                             |  fast digital stays up here
    |---------------------------------------------------------|
    | mic |  8 x 8 transducers, 20 mm pitch (front);    | mic |  each channel's front end on the
    | mic |  channel block behind each element (back)   | mic |  back, behind its element
    |---------------------------------------------------------|
    |  16 TX group blocks (2 rows of 8), enable buffers        |  15 V drive edge
    |  power: +-5VA, 3V3_MIC, GND TPs, +-6V5, VDRV, +5V buck   |  switchers far from the ADCs
    \\---------------------------------------------------------/

Stack-up (JLCPCB JLC04161H-7628, 1.6 mm): F.Cu front (transducer pads, RX_OUT runs to the ADCs) /
In1 GND / In2 GND / B.Cu back (all SMT parts, DRV runs from the drivers).

Every schematic block gets one layout, copied to all its instances and grouped (CH01, ADC03, ...).
The channel block is a hand-made template (CHANNEL); the other blocks are placed by connectivity
(place_near): ICs first, then each decoupling capacitor next to the IC pin it serves, then every other
part as close as possible to the parts it connects to. No part comes within EDGE of the board edge
(except the jack, flush with it) or within SOLDER_KEEPOUT of a hand-soldered transducer pad.

  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe scripts\\place.py
"""

import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATE, KICAD_CLI, KICAD_FOOTPRINTS, REV  # noqa: E402
from netlist_to_pcb import build_board  # noqa: E402
from power_sections import D_SPLIT_Y, SECTIONS, SHEET_FILES, section_of  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
NET = REPO / "build" / "aesa.net"
PCB = REPO / "kicad" / "aesa.kicad_pcb"
PRO = REPO / "kicad" / "aesa.kicad_pro"

P = 20.0                    # element pitch (mm): room for a soldering keep-out and less channel coupling
SOLDER_KEEPOUT = 2.5        # no SMT part within this distance of a hand-soldered transducer pad (mm)
EDGE = 3.0                  # no component within this distance of the board edge (mm)
COPPER_EDGE = 0.5           # copper to board edge (mm)
BEVEL = 3.0                 # 45-degree chamfer on every corner (mm)
MARGIN = 16.0               # left/right margin (mics, mounting holes)
W = 8 * P + 2 * MARGIN      # board width
HDR_W, HDR_GAP = 60.0, 6.0  # ribbon header body length, gap between the two headers
HDR_X = (W - 2 * HDR_W - HDR_GAP) / 2
ADC_Y = 26.0                # top of the ADC rows
ADC_H = 18.0
TOP = ADC_Y + 2 * ADC_H + 1.0   # top of the array
X0, Y0 = MARGIN + P / 2, TOP + P / 2          # centre of element 1 (row 0, col 0)
ARRAY_END = TOP + 8 * P
TX_H, PWR_H = 44.0, 30.0
H = ARRAY_END + 1 + TX_H + PWR_H + EDGE
GAP = 0.35                  # extra space around each courtyard when placing
HOLES = [(6, 6), (W - 6, 6), (6, H - 6), (W - 6, H - 6),        # M3 mounting holes: corners (clear of the
         (6, TOP + 4 * P), (W - 6, TOP + 4 * P)]                # bevels) and mid-sides for waveguide/frame
HOLE_KEEP = 3.6                                                 # pad radius 3.2 mm + margin
OBSTACLES = [(x - HOLE_KEEP, y - HOLE_KEEP, 2 * HOLE_KEEP, 2 * HOLE_KEEP) for x, y in HOLES]

mm = pcbnew.FromMM


def v(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def sid_of(ref):
    m = re.match(r"^#?[A-Z]+(\d+)$", ref)
    return int(m.group(1)[:-2]) if m and len(m.group(1)) > 2 else None


def bbox_mm(fp):
    """Courtyard box of a placed footprint, in mm (falls back to the body box)."""
    for layer in (pcbnew.B_CrtYd, pcbnew.F_CrtYd):
        c = fp.GetCourtyard(layer)
        if c.OutlineCount():
            b = c.BBox()
            break
    else:
        b = fp.GetBoundingBox(False)
    return pcbnew.ToMM(b.GetX()), pcbnew.ToMM(b.GetY()), pcbnew.ToMM(b.GetWidth()), pcbnew.ToMM(b.GetHeight())


def to_back(fp):
    if not fp.IsFlipped():
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)


def move_box_to(fp, x, y):
    """Move so the courtyard's top-left corner lands at (x, y)."""
    bx, by, _, _ = bbox_mm(fp)
    pos = fp.GetPosition()
    fp.SetPosition(pcbnew.VECTOR2I(pos.x + mm(x - bx), pos.y + mm(y - by)))


def overlaps(a, b):
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


def is_power(net):
    n = net.split("/")[-1]
    return n == "GND" or n in ("+5V", "+5VA", "-5VA", "+6V5", "-6V5", "+3V3", "+3V3A", "3V3_MIC", "VDRV", "VIN")


def place_near(fps, rects, label, obstacles=(), step=0.25, fixed=(), pulls=None, ic_angle=None):
    """Connectivity-driven placement into free rectangles [(x, y, w, h), ...] (a single tuple is allowed).

    Order: ICs first (biggest first), then breadth-first outwards from what is already placed: decoupling
    capacitors (one pad on GND, the other on a net that reaches an IC pin) before other parts, parts with
    more connections first. Each part tries every free grid spot and all four rotations and takes the one
    with the smallest summed pad-to-pad distance to the placed pads it shares nets with (GND pads count for
    capacitors, so their GND pad lands at the IC's ground pin and the loop stays small). `fixed` parts take
    part in the nets but do not move (e.g. the transducer). `pulls` = {net regex: (x, y)} pulls pads of nets
    that leave the block towards the side they leave from. `ic_angle(fp)` returns a fixed rotation for an
    IC (or None for automatic). Returns the parts that did not fit."""
    if isinstance(rects, tuple):
        rects = [rects]
    pulls = pulls or {}
    placed_boxes = list(obstacles) + [o for o in OBSTACLES for r in rects if overlaps(o, r)]
    netpts, icpts = {}, {}   # placed pads per net; the subset on ICs (supply/ground pins)

    def add_pads(fp):
        for p in fp.Pads():
            n = p.GetNetname()
            if n:
                pt = (pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y))
                netpts.setdefault(n, []).append(pt)
                if fp.GetReference()[0] == "U":
                    icpts.setdefault(n, []).append(pt)
    for f in fixed:
        add_pads(f)
    ics_all = [f for f in fps if f.GetReference()[0] == "U"]
    ics_known = ics_all + [f for f in fixed if f.GetReference()[0] == "U"]   # fixed ICs count too
    # nets that only feed IC supply/reference pins (AREG, DREG, VREF, NR, regulator inputs ...): treat
    # them like rails: their capacitors belong at the IC pin
    rail_nets = {p.GetNetname() for f in ics_known for p in f.Pads()
                 if p.GetNetname() and re.search(r"(AREG|DREG|VREF|NR_|BOOT|REG|VIN)", p.GetNetname())}
    ic_nets = {p.GetNetname() for f in ics_known for p in f.Pads()} - {"", "GND"}

    def is_decoupling(f):
        nets = [p.GetNetname() for p in f.Pads()]
        return f.GetReference()[0] == "C" and len(nets) == 2 and "GND" in nets and any(n in ic_nets for n in nets)

    def shape(fp, ang):
        """Box (with GAP, plus room for a readable label on test points, jumpers and LEDs) and pad offsets."""
        fp.SetOrientationDegrees(ang)
        bx, by, w, h = bbox_mm(fp)
        r = fp.GetReference()
        labelled = r.startswith(("TP", "JP")) and not 1 <= (sid_of(r) or 0) <= 64   # not the channels' RX pads
        lab = 1.6 if labelled or fp.GetValue() == "green" else 0.0
        bx, by, w, h = bx - lab / 2, by - lab, w + lab, h + lab
        pos = fp.GetPosition()
        ox, oy = pcbnew.ToMM(pos.x) - bx, pcbnew.ToMM(pos.y) - by
        pads = [(p.GetNetname(), pcbnew.ToMM(p.GetPosition().x) - bx, pcbnew.ToMM(p.GetPosition().y) - by)
                for p in fp.Pads() if p.GetNetname()]
        return w + GAP, h + GAP, ox, oy, pads

    def cost(pads, x, y, use_gnd):
        c, hit = 0.0, False
        for n, dx, dy in pads:
            px, py = x + GAP / 2 + dx, y + GAP / 2 + dy
            if n == "GND" and not use_gnd:
                continue
            # rails and ground: measure to the IC pins only, so a decoupling capacitor goes to its pin and
            # not next to another capacitor on the same rail; signal nets may chain part to part
            pts = icpts.get(n) if (is_power(n) or n in rail_nets) and icpts.get(n) else netpts.get(n)
            if pts:
                wgt = 0.5 if n == "GND" else 1.0
                c += wgt * min((px - qx) ** 2 + (py - qy) ** 2 for qx, qy in pts) ** 0.5
                hit = True
            for rx_, pt in pulls.items():
                if re.search(rx_, n):
                    c += 0.15 * ((px - pt[0]) ** 2 + (py - pt[1]) ** 2) ** 0.5
                    hit = True
        return c, hit

    cx0 = sum(r[0] + r[2] / 2 for r in rects) / len(rects)
    cy0 = sum(r[1] + r[3] / 2 for r in rects) / len(rects)
    todo = sorted(ics_all, key=lambda f: -bbox_mm(f)[2] * bbox_mm(f)[3])
    others = [f for f in fps if f.GetReference()[0] != "U"]
    left, done = [], []
    while todo or others:
        if todo:
            fp = todo.pop(0)
        else:
            def links(f):
                return sum(1 for p in f.Pads() if p.GetNetname() and p.GetNetname() != "GND"
                           and p.GetNetname() in netpts)
            def on_switch_node(f):   # inductor / diode of a switcher: the switch loop comes first
                return f.GetReference()[0] in "LD" and any(re.search(r"(^|/)SW", p.GetNetname())
                                                           for p in f.Pads()) and links(f)
            others.sort(key=lambda f: (not on_switch_node(f), not (is_decoupling(f) and links(f)), -links(f),
                                       -bbox_mm(f)[2] * bbox_mm(f)[3]))
            fp = others.pop(0)
        is_ic = fp.GetReference()[0] == "U"
        fixed_ang = ic_angle(fp) if (is_ic and ic_angle) else None
        angles = (fixed_ang,) if fixed_ang is not None else (0, 90, 180, 270)
        use_gnd = fp.GetReference()[0] == "C"
        best = None
        for ang in angles:
            w, h, ox, oy, pads = shape(fp, ang)
            for rx, ry, rw, rh in rects:
                nx, ny = int((rw - w) / step), int((rh - h) / step)
                for j in range(ny + 1):
                    y = ry + j * step
                    for i in range(nx + 1):
                        x = rx + i * step
                        c, hit = cost(pads, x, y, use_gnd)
                        if not hit:
                            c = ((x + w / 2 - cx0) ** 2 + (y + h / 2 - cy0) ** 2) ** 0.5 * 0.05
                        if best is not None and c >= best[0]:
                            continue
                        box = (x, y, w, h)
                        if any(overlaps(box, q) for q in placed_boxes):
                            continue
                        best = (c, ang, x, y, w, h)
        if best is None:
            left.append(fp)
            continue
        _, ang, x, y, w, h = best
        fp.SetOrientationDegrees(ang)
        lab = 1.6 if fp.GetReference().startswith(("TP", "JP")) or fp.GetValue() == "green" else 0.0
        move_box_to(fp, x + GAP / 2 + lab / 2, y + GAP / 2 + lab)
        placed_boxes.append((x, y, w, h))
        add_pads(fp)
        done.append(fp)
    if left:
        print(f"  {label}: {len(left)} parts did not fit: {[f.GetReference() for f in left][:8]}")
    return left


def angle_facing(fp, pins, direction):
    """Rotation (0/90/180/270) that puts the given pins on the side facing `direction` (dx, dy)."""
    best = None
    for ang in (0, 90, 180, 270):
        fp.SetOrientationDegrees(ang)
        c = fp.GetPosition()
        pts = [p.GetPosition() for p in fp.Pads() if p.GetNumber() in pins]
        mx = sum(pcbnew.ToMM(p.x - c.x) for p in pts) / len(pts)
        my = sum(pcbnew.ToMM(p.y - c.y) for p in pts) / len(pts)
        s = mx * direction[0] + my * direction[1]
        if best is None or s > best[0]:
            best = (s, ang)
    return best[1]


# ---- blocks: one layout per schematic block, copied to every instance -------------------------------
# Channel block (channel.kicad_sch), hand-made, in signal-flow order. Offsets from the element centre in
# mm (front view, x right, y down), rotation in degrees; all parts on the back. The element's pads are at
# (-2.5, 0) XD and (+2.5, 0) GND; nothing comes within SOLDER_KEEPOUT of them.
#   below the element (TX side): DRV from the drivers below -> R1 47R -> D1 crossed diodes -> XD; R2 bleed
#   left pocket, next to the XD pad: R3 4.7k, D2 clamp (input protection right at the element)
#   above the element (RX side): U1 OPA1678; stage 1 (R4 C7 R5 C3, C6 at LIM) left of it; band-pass
#   (R6 R7 C4 C5 R8) and the output resistor R9 right of it; RX_OUT leaves at the top-right corner
#   towards the ADCs. TX and RX parts sit on opposite sides of the element.
CHANNEL = {"R1": (-5.0, 8.4, 0), "D1": (-5.0, 5.3, 0), "R2": (0.0, 4.6, 0),
           "R3": (-7.8, -1.9, 90), "D2": (-7.8, 1.7, 0),
           "U1": (0.0, -6.5, 0),
           "R4": (-5.3, -8.7, 0), "C7": (-8.3, -8.7, 0), "R5": (-5.3, -6.9, 0), "C3": (-8.3, -6.9, 0),
           "C6": (-8.3, -5.1, 0), "C2": (-5.3, -5.1, 0),
           "R6": (5.3, -8.7, 0), "C4": (8.3, -8.7, 0), "R7": (5.3, -6.9, 0), "C5": (8.3, -6.9, 0),
           "R8": (5.3, -5.1, 0), "R9": (8.3, -5.1, 0), "C1": (7.8, -2.2, 0)}


# Hand-made block templates, built from the real (mirrored, back-side) pad positions. Offsets in mm from
# the block's top-left corner; rotation in degrees as KiCad stores it for the flipped part. Passives at 0
# are horizontal, at 90 vertical.
#
# TX group (tx_group.kicad_sch), 19.6 x 21.7 mm. The two UCC27524 (U2, U3) at the top, rotated so their
# outputs and VDD pin face the array; each 100 nF VDRV capacitor (C3, C5) stands right above its VDD pin
# (pin 6) between the two outputs, the 1 uF ones (C2, C4) between the drivers. The 74HCT595 (U1) below,
# QB-QD (pins 1-3) facing the drivers' inputs, its 100 nF (C1) beside VCC (pin 16).
TXG = {"U2": (4.6, 7.0, 90), "U3": (14.6, 7.0, 90),
       "C3": (3.96, 1.6, 90), "C5": (13.96, 1.6, 90), "C2": (8.4, 1.75, 90), "C4": (10.6, 1.75, 90),
       "U1": (9.8, 16.0, 270), "C1": (2.6, 18.5, 0)}
# ADC (adc.kicad_sch), 19.6 x 16.6 mm. TLV320ADC5140 rotated 180: AVDD/AREG/VREF (pins 1-3) on the right
# with their capacitors (C10, C12, C17; bulk C9, C11 further right), IOVDD (pin 19) top-left with C16/C15,
# DREG (pin 24) top-right with C14/C13; R9 (SDOUT 33R) above the chip, leaving a 1.5 mm gap for BCLK and
# FSYNC. The 8 inputs (pins 6-13) face the array; below them one column per channel: coupling capacitor
# (C1-C4, INxP) and INxM capacitor (C5-C8) first, then the 2k (R1-R4) and 1k (R5-R8) divider.
ADC = {"U1": (9.8, 6.0, 180),
       "C10": (14.3, 3.6, 0), "C12": (14.3, 5.4, 0), "C17": (14.6, 7.4, 0), "C9": (17.6, 3.6, 90),
       "C11": (17.6, 7.4, 90), "C14": (11.8, 1.6, 90), "C13": (14.6, 1.2, 0), "C16": (6.2, 3.0, 90),
       "C15": (3.0, 1.3, 0), "R9": (8.7, 1.6, 90)}
for _i, _xc in enumerate((14.7, 10.9, 7.1, 3.3)):        # channel 1 .. 4 columns
    ADC[f"C{_i + 1}"] = (_xc - 0.85, 10.6, 90)
    ADC[f"C{_i + 5}"] = (_xc + 0.85, 10.6, 90)
    ADC[f"R{_i + 1}"] = (_xc - 0.85, 14.4, 90)
    ADC[f"R{_i + 5}"] = (_xc + 0.85, 14.4, 90)
# Channel: the TX parts in the pocket beside the XD pad (relative to the element centre), the op-amp at a
# fixed spot on the receive side; the remaining parts are placed by connectivity around them.
CHANNEL_FIXED = {"D1": (-7.85, -1.6, 0), "R1": (-7.85, 1.8, 90), "R2": (-7.5, 5.3, 0), "U1": (3.6, -6.6, 0),
                 "C2": (-1.9, -8.5, 180)}   # -5VA capacitor beside pin 4, its -5VA pad towards the pin


def local_ref(ref):
    """Reference inside its sheet: R1203 (channel 12) -> R3."""
    m = re.match(r"^(#?[A-Z]+)(\d+)$", ref)
    return f"{m.group(1)}{int(m.group(2)[-2:])}"


def group(board, name, fps):
    g = pcbnew.PCB_GROUP(board)
    g.SetName(name)
    for f in fps:
        g.AddItem(f)
    board.Add(g)


def place_template(fps, cx, cy, template):
    left = []
    for f in fps:
        t = template.get(local_ref(f.GetReference()))
        if t is None:
            left.append(f)
            continue
        to_back(f)
        f.SetOrientationDegrees(t[2])
        f.SetPosition(v(cx + t[0], cy + t[1]))
    return left


def block_copy(instances):
    """instances: [(name, parts, (x, y) region origin)]. The first instance's layout (already placed)
    is copied to the others at their own origins, part by part via the sheet-local reference."""
    _, parts0, (x0, y0) = instances[0]
    rel = {}
    for f in parts0:
        pos = f.GetPosition()
        rel[local_ref(f.GetReference())] = (pcbnew.ToMM(pos.x) - x0, pcbnew.ToMM(pos.y) - y0,
                                            f.GetOrientationDegrees(), f.IsFlipped())
    for _, parts, (x, y) in instances[1:]:
        for f in parts:
            dx, dy, ang, flipped = rel[local_ref(f.GetReference())]
            if flipped:
                to_back(f)
            f.SetOrientationDegrees(ang)
            f.SetPosition(v(x + dx, y + dy))


def place_block(board, instances, size, label, pulls=None, ic_angle=None):
    """Place the first instance by connectivity in its region of `size` (w, h), copy that layout to the
    rest, group every instance. `pulls` = {net regex: (dx, dy)} relative to the block's top-left corner."""
    _, parts0, (x0, y0) = instances[0]
    for f in parts0:
        to_back(f)
    place_near(parts0, (x0, y0, size[0], size[1]), label, ic_angle=ic_angle,
               pulls={k: (x0 + d[0], y0 + d[1]) for k, d in (pulls or {}).items()})
    block_copy(instances)
    for name, parts, _ in instances:
        group(board, name, parts)


# Switcher cores (offsets from the section's top-left corner). E = LM2731 VDRV boost: inductor L4 left of
# the IC with its SW pad 3.6 mm from SW (pin 1), the SS14 (D8) anode 2.5 mm below SW, the output caps
# C33/C34 beside the cathode with their GND pads towards the IC's GND pin, input cap C31 at VIN (pin 5).
POWER_CORES = {"E": {"U5": (12.0, 8.0, 0), "L4": (5.0, 8.95, 0), "D8": (11.0, 13.4, 90), "C31": (16.5, 8.0, 90),
                     "C33": (14.6, 13.6, 90), "C34": (18.6, 13.6, 90)}}
BOTTOM_ORDER = ["D1", "C", "H", "D2", "E", "G", "B"]  # left to right: hot parts alternate with cool ones


def power_positions():
    """{local reference: (sheet, x, y)} of every part on the two power sheets."""
    pos = {}
    for sheet in SHEET_FILES:
        sch = (REPO / "kicad" / f"{sheet}.kicad_sch").read_text(encoding="utf-8")
        for m in re.finditer(r'\(lib_id "[^"]+"\)\s*\(at ([\d.]+) ([\d.]+)[^)]*\).*?\(property "Reference" "([^"]+)"',
                             sch, re.S):
            pos[m.group(3)] = (sheet, float(m.group(1)), float(m.group(2)))
    return pos


def power_by_section(fps):
    """Power parts by the section box they sit in on the power sheets (scripts/power_sections.py)."""
    pos = power_positions()
    by = {k: [] for k in SECTIONS}
    for fp in fps:
        to_back(fp)
        k = section_of(*pos.get(local_ref(fp.GetReference()), ("", 0, 0)))
        if k is None:
            raise SystemExit(f"{fp.GetReference()} is outside every power-sheet section")
        by[k].append(fp)
    return by


def template_blocks(board, instances, template):
    """Place every instance of a block from a hand-made template (offsets from the block origin) and
    group it; report parts missing from the template."""
    for name, parts, (x, y) in instances:
        rest = place_template(parts, x, y, template)
        if rest:
            print(f"  {name}: not in the template: {[f.GetReference() for f in rest]}")
        group(board, name, parts)


def adc_origin(k):
    col, odd = (k - 1) // 2, (k - 1) % 2 == 0
    return MARGIN + col * P + 0.2, ADC_Y + (ADC_H if odd else 0)


def txg_origin(k):
    col, odd = (k - 1) // 2, (k - 1) % 2 == 0
    return MARGIN + col * P + 0.2, ARRAY_END + 1 + (0 if odd else TX_H / 2)


def split_analog_ldos(sec):
    """Section D holds both +5VA and -5VA LDOs; split it (by schematic position: -5VA is drawn below)
    into D1 and D2 so the two hot LDOs sit in separate regions."""
    pos = power_positions()
    d = sec.pop("D")
    sec["D1"] = [f for f in d if pos[local_ref(f.GetReference())][2] < D_SPLIT_Y]
    sec["D2"] = [f for f in d if f not in sec["D1"]]


def place_jack(jack, x_edge, y):
    """12 V jack flush with the left board edge, opening facing out (the opening is the end of the body
    away from pad 1), so the cable leaves the edge and the plug cannot lever other parts."""
    best = None
    for ang in (0, 90, 180, 270):
        jack.SetOrientationDegrees(ang)
        bx, _, bw, _ = bbox_mm(jack)
        p1 = next(pd for pd in jack.Pads() if pd.GetNumber() == "1").GetPosition()
        dx = pcbnew.ToMM(p1.x) - (bx + bw / 2)        # pad 1 to the right of the body centre
        if best is None or dx > best[0]:
            best = (dx, ang)
    jack.SetOrientationDegrees(best[1])
    _, _, jw, jh = bbox_mm(jack)
    move_box_to(jack, x_edge, y)
    return jw, jh


def outline(board):
    b = BEVEL
    pts = [(b, 0), (W - b, 0), (W, b), (W, H - b), (W - b, H), (b, H), (0, H - b), (0, b)]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(v(x0, y0))
        seg.SetEnd(v(x1, y1))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(mm(0.1))
        board.Add(seg)


def add_fp(board, lib, name, ref, value, x, y, back=False):
    fp = pcbnew.FootprintLoad(str(KICAD_FOOTPRINTS / f"{lib}.pretty"), name)
    fp.SetReference(ref)
    fp.SetValue(value)
    fp.SetPosition(v(x, y))
    board.Add(fp)          # add first: flipping a footprint that is not on a board crashes KiCad
    if back:
        to_back(fp)
    return fp


def mounting_holes(board):
    for k, (x, y) in enumerate(HOLES, 1):
        fp = add_fp(board, "MountingHole", "MountingHole_3.2mm_M3_Pad_Via", f"H{k}", "M3", x, y)
        for pad in fp.Pads():
            pad.SetNet(board.FindNet("GND"))


def occupied_back(board):
    return [bbox_mm(f) for f in board.GetFootprints() if f.IsFlipped() or f.GetReference().startswith(("J", "H"))]


def free_spot(board, w, h, near, rect):
    """Free back-side spot (top-left corner) for a w x h box nearest `near`, inside rect."""
    occ = occupied_back(board)
    best = None
    rx, ry, rw, rh = rect
    for j in range(int((rh - h) / 0.5) + 1):
        for i in range(int((rw - w) / 0.5) + 1):
            box = (rx + i * 0.5, ry + j * 0.5, w, h)
            if any(overlaps(box, q) for q in occ):
                continue
            d = (box[0] + w / 2 - near[0]) ** 2 + (box[1] + h / 2 - near[1]) ** 2
            if best is None or d < best[0]:
                best = (d, box)
    return best[1] if best else None


def fiducials(board):
    """Three global fiducials on the back (assembly side) for the pick-and-place camera."""
    for k, near in enumerate(((EDGE + 3, TOP + 1.5 * P), (W - EDGE - 3, TOP + 6.5 * P), (W / 2, H - EDGE - 3)), 1):
        spot = free_spot(board, 3.2, 3.2, near, (EDGE, EDGE, W - 2 * EDGE, H - 2 * EDGE))
        add_fp(board, "Fiducial", "Fiducial_1mm_Mask2mm", f"FID{k}", "Fiducial",
               spot[0] + 1.6, spot[1] + 1.6, back=True)


def gnd_planes(board):
    gnd = board.FindNet("GND")
    e, b = COPPER_EDGE, BEVEL
    for layer in (pcbnew.In1_Cu, pcbnew.In2_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(gnd)
        z.SetLocalClearance(mm(0.3))
        z.SetMinThickness(mm(0.25))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)  # PTH: 4 spokes, easy hand soldering
        z.SetThermalReliefGap(mm(0.5))
        z.SetThermalReliefSpokeWidth(mm(0.5))
        ol = z.Outline()
        ol.NewOutline()
        for x, y in ((b + e, e), (W - b - e, e), (W - e, b + e), (W - e, H - b - e), (W - b - e, H - e),
                     (b + e, H - e), (e, H - b - e), (e, b + e)):
            ol.Append(mm(x), mm(y))
        z.SetZoneName(f"GND {board.GetLayerName(layer)}")
        board.Add(z)


def text(board, s, x, y, size=1.0, layer=pcbnew.B_SilkS, bold=False):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetPosition(v(x, y))
    t.SetLayer(layer)
    t.SetTextSize(v(size, size))
    t.SetTextThickness(mm(max(0.15, size * 0.15)))
    t.SetBold(bold)
    if layer in (pcbnew.B_SilkS, pcbnew.B_Fab):
        t.SetMirrored(True)
    board.Add(t)


def silkscreen(board):
    """Readable silkscreen: references only where a person needs them (connectors, test points, jumpers,
    LEDs, holes); test points show their rail name; every block gets a label; the rest moves to Fab."""
    keep = ("J", "TP", "JP", "H", "FID")     # transducers are named by the CH labels on the front
    for fp in board.GetFootprints():
        ref = fp.Reference()
        r = fp.GetReference()
        is_led = fp.GetValue() == "green"
        if r.startswith("TP"):
            ref.SetVisible(False)
            val = fp.Value()
            val.SetLayer(pcbnew.B_SilkS if fp.IsFlipped() else pcbnew.F_SilkS)
            val.SetVisible(True)
            val.SetTextSize(v(0.9, 0.9))
            val.SetTextThickness(mm(0.15))
        elif r.startswith(keep) or is_led:
            ref.SetTextSize(v(1.0, 1.0))
            ref.SetTextThickness(mm(0.15))
        else:
            ref.SetLayer(pcbnew.B_Fab if fp.IsFlipped() else pcbnew.F_Fab)


def place_labels(board):
    """Move each visible silkscreen label (references of connectors, jumpers, LEDs; rail names of test
    points) to the first spot around its part (above, below, left, right, then further out) that is clear
    of every other part's courtyard and of the labels already placed; hide it if there is none."""
    fps = list(board.GetFootprints())
    court = {f.GetReference(): bbox_mm(f) for f in fps}
    taken = []
    for fp in fps:
        for fld in (fp.Reference(), fp.Value()):
            if not fld.IsVisible() or fld.GetLayer() not in (pcbnew.B_SilkS, pcbnew.F_SilkS):
                continue
            tb = fld.GetBoundingBox()
            tw, th = pcbnew.ToMM(tb.GetWidth()) + 0.3, pcbnew.ToMM(tb.GetHeight()) + 0.3
            bx, by, bw, bh = court[fp.GetReference()]
            others = [b for r, b in court.items() if r != fp.GetReference()] + taken
            spot = None
            for d in (0.4, 1.4, 2.6, 4.0, 5.5):
                for cx, cy in ((bx + bw / 2, by - d - th / 2), (bx + bw / 2, by + bh + d + th / 2),
                               (bx - d - tw / 2, by + bh / 2), (bx + bw + d + tw / 2, by + bh / 2),
                               (bx - d - tw / 2, by - d - th / 2), (bx + bw + d + tw / 2, by - d - th / 2),
                               (bx - d - tw / 2, by + bh + d + th / 2), (bx + bw + d + tw / 2, by + bh + d + th / 2)):
                    box = (cx - tw / 2, cy - th / 2, tw, th)
                    inside = EDGE / 2 < box[0] and box[0] + tw < W - EDGE / 2 and EDGE / 2 < box[1] and \
                        box[1] + th < H - EDGE / 2
                    if inside and not any(overlaps(box, o) for o in others):
                        spot = (cx, cy, box)
                        break
                if spot:
                    break
            if spot:
                fld.SetPosition(v(spot[0], spot[1]))
                taken.append(spot[2])
            else:
                fld.SetVisible(False)
                print(f"  label of {fp.GetReference()} hidden: no free spot")


def labels(board, groups):
    for n in range(1, 65):
        c, r = divmod(n - 1, 8)
        text(board, f"CH{n:02d}", X0 + c * P, Y0 + r * P + P / 2 - 0.6, 0.8, pcbnew.F_SilkS)  # front, under the can
    for k in range(1, 17):
        x, y = adc_origin(k)
        text(board, f"ADC{k:02d}", x + P / 2, y + ADC_H - 0.9, 0.8, pcbnew.F_SilkS)
        x, y = txg_origin(k)
        text(board, f"TXG{k:02d}", x + P / 2, y + TX_H / 2 - 0.9, 0.8, pcbnew.F_SilkS)
    # front silkscreen is free apart from the transducers: board name and the safety notes go there
    text(board, f"Hybrid 8x8 ultrasonic AESA  rev {REV}  {DATE}", W / 2, H - EDGE - 2, 1.5, pcbnew.F_SilkS, True)
    text(board, "JLCJLCJLCJLC", W - 38, H - EDGE - 6, 1.0, pcbnew.F_SilkS)  # JLCPCB prints the order number here
    text(board, "12 V DC only, centre +, 1.5 A min.  Input protected: 8.8-13.8 V, 1.6 A", W / 2, H - EDGE - 5,
         1.0, pcbnew.F_SilkS)
    text(board, "TX drive 15 V: remove shunt J9502 to switch all transmitters off", W / 2, H - EDGE - 7.5,
         1.0, pcbnew.F_SilkS)
    text(board, "<- 12 V in", 16, ADC_Y - 6, 1.0, pcbnew.F_SilkS)


# JLCPCB JLC04161H-7628 (1.6 mm): 1 oz outer, 0.5 oz inner copper, one 7628 prepreg each side of a 1.065 mm
# core; ENIG (user, 2026-10-24: flat pads for the 0.5 mm-pitch QFNs and the hand-soldered transducers).
STACKUP = [("F.SilkS", "Top Silk Screen", None, None), ("F.Paste", "Top Solder Paste", None, None),
           ("F.Mask", "Top Solder Mask", 0.01, None), ("F.Cu", "copper", 0.035, None),
           ("dielectric 1", "prepreg", 0.2104, ("7628", 4.4)), ("In1.Cu", "copper", 0.0152, None),
           ("dielectric 2", "core", 1.065, ("FR4", 4.6)), ("In2.Cu", "copper", 0.0152, None),
           ("dielectric 3", "prepreg", 0.2104, ("7628", 4.4)), ("B.Cu", "copper", 0.035, None),
           ("B.Mask", "Bottom Solder Mask", 0.01, None), ("B.Paste", "Bottom Solder Paste", None, None),
           ("B.SilkS", "Bottom Silk Screen", None, None)]


def write_stackup(path):
    """The board's physical stackup (impedance calculator, 3D thickness, fabrication notes)."""
    s = ["\t\t(stackup"]
    for name, typ, th, diel in STACKUP:
        s.append(f'\t\t\t(layer "{name}"\n\t\t\t\t(type "{typ}")')
        if diel:
            s.append('\t\t\t\t(color "FR4 natural")')
        if th:
            s.append(f"\t\t\t\t(thickness {th})")
        if diel:
            s.append(f'\t\t\t\t(material "{diel[0]}")\n\t\t\t\t(epsilon_r {diel[1]})\n\t\t\t\t(loss_tangent 0.02)')
        s.append("\t\t\t)")
    s += ['\t\t\t(copper_finish "ENIG")', "\t\t\t(dielectric_constraints no)", "\t\t)"]
    t = path.read_text(encoding="utf-8")
    assert "(stackup" not in t
    t = t.replace("\t(setup\n", "\t(setup\n" + "\n".join(s) + "\n", 1)
    path.write_text(t, encoding="utf-8")


def netclasses():
    """Net classes and the rules JLCPCB can build, written into the project file."""
    pro = json.loads(PRO.read_text(encoding="utf-8"))
    base = pro["net_settings"]["classes"][0]

    def cls(name, track, clear, via_d=0.6, via_drill=0.3):
        c = dict(base)
        c.update(name=name, track_width=track, clearance=clear, via_diameter=via_d, via_drill=via_drill,
                 priority=len(pro["net_settings"]["classes"]))
        return c
    base.update(track_width=0.2, clearance=0.15)
    pro["net_settings"]["classes"] = [
        base,
        cls("HighCurrent", 1.0, 0.25, 0.8, 0.4),  # up to 1.6 A (3 A peak on SW_5V): 1 mm keeps the rise and loss low
        cls("Power", 0.5, 0.2, 0.8, 0.4),       # rails and regulator nodes
        cls("Drive", 0.35, 0.3),                # 15 V gate-driver outputs to the elements
        cls("Analog", 0.25, 0.25),              # receiver outputs to the ADCs
        cls("Clock", 0.2, 0.2),                 # buffered clocks (QFN pads are 0.2 mm from the EP)
    ]
    pro["net_settings"]["netclass_patterns"] = [
        {"netclass": "HighCurrent", "pattern": p} for p in
        ("VIN", "+5V", "/Power/VIN_JACK", "/Power/VIN_PROT", "/Power/5V_REG", "/Power/SW_5V")] + [
        # the other rails, regulator outputs and switch nodes; feedback, enable and NR nodes stay thin (Default)
        {"netclass": "Power", "pattern": p} for p in
        ("+5VA", "-5VA", "+6V5", "-6V5", "+3V3", "+3V3A", "3V3_MIC", "VDRV", "/Power/*_REG", "/Power/SW_*",
         "/Power/VIN65")] + [
        {"netclass": "Drive", "pattern": "DRV*"},
        {"netclass": "Drive", "pattern": "/Transceivers/CH*/XD"},
        {"netclass": "Analog", "pattern": "RX*"},
        {"netclass": "Clock", "pattern": "*_BCLK*"}, {"netclass": "Clock", "pattern": "*_FSYNC*"},
        {"netclass": "Clock", "pattern": "*_SCK*"}, {"netclass": "Clock", "pattern": "*_LATCH*"},
        {"netclass": "Clock", "pattern": "MIC_CLK*"}]
    rules = pro["board"]["design_settings"]["rules"]
    rules.update(min_track_width=0.127, min_clearance=0.127, min_via_diameter=0.45, min_through_hole_diameter=0.2,
                 min_hole_clearance=0.25, min_copper_edge_clearance=COPPER_EDGE, min_text_height=0.8,
                 min_text_thickness=0.15)
    PRO.write_text(json.dumps(pro, indent=2), encoding="utf-8")


# Library footprints whose 3D model is missing from KiCad 10's library get the stock model of a part with the
# same body (sizes from the datasheets). Pad-only footprints (test pads, solder jumpers, fiducials, mounting
# holes) have no body. The project's own footprints carry their models (kicad/aesa.3dshapes, make_3d.py).
MODEL_SUBST = {
    "Fuse_1812_4532Metric": "Resistor_SMD.3dshapes/R_1812_4532Metric.step",  # 4.5 x 3.2 mm chip
    "L_Bourns_SRN8040TA": "Inductor_SMD.3dshapes/L_Sunlord_SWPA8040S.step",  # 8 x 8 x 4 mm
    "VQFN-24-1EP_4x4mm_P0.5mm_EP2.45x2.45mm_ThermalVias":
        "Package_DFN_QFN.3dshapes/Texas_RGE0024C_VQFN-24-1EP_4x4mm_P0.5mm_EP2.1x2.1mm.step",
    "Knowles_LGA-5_3.5x2.65mm": "Sensor_Audio.3dshapes/Knowles_SPH0645LM4H-6_3.5x2.65mm.step",  # 3.5 x 2.65 x 1
}


def fix_models(board):
    for fp in board.GetFootprints():
        sub = MODEL_SUBST.get(str(fp.GetFPID().GetLibItemName()))
        if not sub:
            continue
        if not len(fp.Models()):
            fp.Models().push_back(pcbnew.FP_3DMODEL())
        fp.Models()[0].m_Filename = "${KICAD10_3DMODEL_DIR}/" + sub


def main():
    subprocess.run([KICAD_CLI, "sch", "export", "netlist", "-o", str(NET), str(REPO / "kicad" / "aesa.kicad_sch")],
                   check=True, capture_output=True)
    board, info, missing = build_board(NET)
    if missing:
        raise SystemExit(f"missing footprints: {missing}")
    board.SetCopperLayerCount(4)
    for layer in (pcbnew.In1_Cu, pcbnew.In2_Cu):  # GND planes: "power" layers, so no autorouter cuts them
        board.SetLayerType(layer, pcbnew.LT_POWER)
    board.GetDesignSettings().SetBoardThickness(mm(1.6))
    outline(board)
    groups = {}
    for fp in board.GetFootprints():
        groups.setdefault(sid_of(fp.GetReference()), []).append(fp)

    # ---- channel blocks. Channels are numbered down the columns (CH n: column (n-1)//8, row (n-1)%8), so
    # each column of 8 elements is driven by the two TX groups below it and read by the two ADCs above it:
    # every DRV and RX run is a straight vertical line in its own column. On F.Cu each column has a DRV
    # lane on its left (x - 9 .. - 5 mm from the element centre) and an RX lane on its right (+5 .. +9 mm),
    # about 10 mm apart over the GND plane; the transducer pads (+-2.5 mm) sit between them.
    # Back of each cell: the TX parts (R1 47R, D1 crossed diodes) in the pocket beside the XD pad, next to
    # the DRV lane and far from any amplifier input; everything else (protection, OPA1678, filters, output
    # resistor) around it, placed by connectivity with the transducer's pads as fixed anchors.
    h = P / 2 - 0.2
    ky, kx = 1.0 + SOLDER_KEEPOUT, 2.5 + 1.0 + SOLDER_KEEPOUT     # 2.0 mm transducer pads

    def cell(n):
        c, r = divmod(n - 1, 8)
        return X0 + c * P, Y0 + r * P

    cx, cy = cell(1)
    parts1 = groups.get(1, [])
    ls1 = next(f for f in parts1 if f.GetReference().startswith("LS"))
    ls1.SetPosition(v(cx, cy))
    smd1 = [f for f in parts1 if f is not ls1]
    for f in smd1:
        to_back(f)
    fixed1 = [f for f in smd1 if local_ref(f.GetReference()) in CHANNEL_FIXED]
    place_template(fixed1, cx, cy, CHANNEL_FIXED)
    tp1 = [f for f in smd1 if f.GetReference().startswith("TP")]   # RX_OUT test pad: last, in the space left
    rx1 = [f for f in smd1 if f not in fixed1 and f not in tp1]
    rx_rects = [(cx - h, cy - h, 2 * h, h - ky), (cx - 5.4, cy + ky, h + 5.4, h - ky), (cx + kx, cy - ky, h - kx, 2 * ky)]
    tx_clear = [(cx - h, cy - h, 4.2, 2 * h)]   # TX side clear
    left = place_near(rx1, rx_rects, "channel RX", obstacles=[bbox_mm(f) for f in fixed1] + tx_clear,
                      fixed=[ls1] + fixed1, pulls={r"RX\d": (cx + 7.0, cy)})
    left += place_near(tp1, rx_rects, "channel RX test pad", obstacles=[bbox_mm(f) for f in smd1 if f not in tp1] + tx_clear,
                       fixed=[ls1] + [f for f in smd1 if f not in tp1])
    if left:
        raise SystemExit("channel block does not fit: the copy to 64 channels would be wrong")
    insts = []
    for n in range(1, 65):
        x, y = cell(n)
        for f in groups.get(n, []):
            if f.GetReference().startswith("LS"):
                f.SetPosition(v(x, y))
        insts.append((f"CH{n:02d}", [f for f in groups.get(n, []) if not f.GetReference().startswith("LS")], (x, y)))
    block_copy(insts)
    for n in range(1, 65):
        group(board, f"CH{n:02d}", groups.get(n, []))

    # ---- top edge: ribbon headers side by side in the middle (cables leave straight), their pull
    # resistors and the clock buffers right below them
    hdr = groups.get(99, [])
    for fp in hdr:
        to_back(fp)
    jh = {}
    for ref, x in (("J9901", HDR_X), ("J9902", HDR_X + HDR_W + HDR_GAP)):
        fp = next(f for f in hdr if f.GetReference() == ref)
        fp.SetOrientationDegrees(90)
        move_box_to(fp, x, EDGE)
        jh[ref] = bbox_mm(fp)
    hdr_bottom = max(b[1] + b[3] for b in jh.values())
    obstacles = [bbox_mm(f) for f in hdr if f.GetReference().startswith("J")]
    place_near([f for f in hdr if not f.GetReference().startswith("J")],
               (HDR_X, hdr_bottom + 0.3, 2 * HDR_W + HDR_GAP, 5.5), "header pulls", obstacles)
    group(board, "Headers", hdr)
    cw = (2 * HDR_W + HDR_GAP) / 9
    place_block(board, [(f"CLKB{j}", groups.get(130 + j, []), (HDR_X + (j - 1) * cw, hdr_bottom + 6.0))
                        for j in range(1, 10)], (cw - 0.3, ADC_Y - hdr_bottom - 6.3), "clk_buf")
    # ADC k reads CH 4k-3 .. 4k = column (k-1)//2; the odd one (rows 0-3) sits in the row next to the array
    template_blocks(board, [(f"ADC{k:02d}", groups.get(70 + k, []), adc_origin(k)) for k in range(1, 17)], ADC)

    # ---- power: 12 V input in the top-left corner (all cables on the top edge), the quiet +3V3A and
    # +3V3 LDOs in the top-right corner next to the ADCs they feed, everything else at the bottom
    pwr = groups.get(95, [])
    sec = power_by_section(pwr)
    jack = next(f for f in sec["A"] if f.GetReference() == "J9501")
    sec["A"].remove(jack)
    jw, jhh = place_jack(jack, 0.0, 6 + HOLE_KEEP + 0.5)     # below the corner mounting hole
    jb = bbox_mm(jack)
    place_near(sec["A"], [(EDGE, EDGE, HDR_X - EDGE - 0.5, ADC_Y - EDGE - 0.5),   # corner, plus the free left
                          (EDGE, ADC_Y - 0.5, MARGIN - EDGE - 0.5, TOP + 0.4 * P - ADC_Y - 0.5)],  # margin to MIC1
               "power A",
               [(jb[0] - GAP, jb[1] - GAP, jb[2] + 2 * GAP, jb[3] + 2 * GAP)])
    xr = HDR_X + 2 * HDR_W + HDR_GAP + 0.5
    wr = (W - EDGE - xr - 4.0) / 2           # +3V3A and +3V3 LDOs in separate halves, 4 mm apart (heat)
    place_near(sec["I"], (xr, EDGE, wr, ADC_Y - EDGE - 0.5), "power I (+3V3A)")
    place_near(sec["F"], (xr + wr + 4.0, EDGE, wr, ADC_Y - EDGE - 0.5), "power F (+3V3)")
    yt = ARRAY_END + 1
    yp = yt + TX_H + 0.5
    split_analog_ldos(sec)
    # scope-ground test points spread over the board: between the ribbon headers, both array sides (in the
    # gap between mics 1 and 2; the mid-side holes sit between mics 2 and 3), right margin of the TX rows
    for ref, (x, y) in {"TP9516": (W / 2, EDGE + 4.0), "TP9517": (MARGIN / 2, TOP + 2.0 * P),
                        "TP9518": (W - MARGIN / 2, TOP + 2.0 * P), "TP9519": (W - MARGIN / 2, yt + TX_H / 2)}.items():
        fp = next(f for f in sec["G"] if f.GetReference() == ref)
        sec["G"].remove(fp)
        fp.SetPosition(v(x, y))
    area ={k: sum(bbox_mm(f)[2] * bbox_mm(f)[3] for f in sec[k]) for k in BOTTOM_ORDER}
    gap = 3.0
    avail = W - 2 * EDGE - gap * (len(BOTTOM_ORDER) - 1)
    widths = {k: max(10.0, avail * area[k] / sum(area.values())) for k in BOTTOM_ORDER}
    scale = avail / sum(widths.values())
    x = EDGE
    for k in BOTTOM_ORDER:
        w = widths[k] * scale
        if k in POWER_CORES:   # switcher with a hand-made core (switch loop per the datasheet layout)
            core = [f for f in sec[k] if local_ref(f.GetReference()) in POWER_CORES[k]]
            place_template(core, x, yp, POWER_CORES[k])
            place_near([f for f in sec[k] if f not in core], (x, yp, w, H - EDGE - yp), f"power {k}",
                       obstacles=[bbox_mm(f) for f in core], fixed=core)
        else:
            place_near(sec[k], (x, yp, w, H - EDGE - yp), f"power {k}")
        x += w + gap
    group(board, "Power", pwr)

    # ---- mic blocks: 4 down each side of the array (bottom-port: the port hole goes through the board)
    place_block(board, [(f"MIC{m}", groups.get(140 + m, []),
                         (EDGE if m <= 4 else W - MARGIN + 0.5, TOP + (2 * ((m - 1) % 4) + 0.4) * P))
                        for m in range(1, 9)], (MARGIN - EDGE - 0.5, P * 1.2), "mic")

    # ---- TX group blocks (2 rows of 8) and the enable buffers in the left margin
    # TX group k drives CH 4k-3 .. 4k = column (k-1)//2; the odd one (rows 0-3) next to the array.
    # Drivers' DRV outputs pulled towards the array (top), the 595's serial inputs towards the bottom.
    template_blocks(board, [(f"TXG{k:02d}", groups.get(100 + k, []), txg_origin(k)) for k in range(1, 17)], TXG)
    place_block(board, [(f"TXEN{j}", groups.get(120 + j, []), (EDGE, yt + (j - 1) * TX_H / 4)) for j in range(1, 5)],
                (MARGIN - EDGE - 0.5, TX_H / 4 - 0.3), "en_buf")

    leftovers = [f.GetReference() for k, fs in groups.items() if k is None for f in fs]
    if leftovers:
        print("  not placed (no sheet id):", leftovers[:10])
    mounting_holes(board)
    fiducials(board)
    silkscreen(board)
    place_labels(board)
    labels(board, groups)
    gnd_planes(board)
    fix_models(board)
    board.Save(str(PCB))
    write_stackup(PCB)
    netclasses()
    print(f"{PCB.relative_to(REPO)}: {len(board.GetFootprints())} footprints, board {W:.0f} x {H:.0f} mm, "
          f"pitch {P} mm")


if __name__ == "__main__":
    main()
