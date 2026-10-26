"""Hand-made routing of the small repeated blocks (clock buffer, TX enable buffer, mic), written against each
instance's own pads, so every instance gets the same layout and variants (a mic's select pin tied to the
supply or to GND) follow its nets. Used by route.py's `blocks` stage; KiCad's DRC checks the result.

Each function takes an instance's footprints and returns ([(net, layer, width, [(x, y), ...])], [(net, x, y)]):
tracks and vias in board coordinates. Layer "B" is the back (all these parts are on the back), "F" the front.
"""

from route_lib import local_ref, xy


class Pads:
    """Pad positions and nets of one block instance, by local reference and pad number (C1.2 -> pad 2 of C1)."""

    def __init__(self, fps):
        self.p = {}
        for f in fps:
            for pd in f.Pads():
                if pd.GetNumber():
                    self.p[(local_ref(f.GetReference()), pd.GetNumber())] = pd

    def at(self, ref, num, dx=0.0, dy=0.0):
        x, y = xy(self.p[(ref, str(num))].GetPosition())
        return x + dx, y + dy

    def net(self, ref, num):
        return self.p[(ref, str(num))].GetNetname()


SIG, SUP, GNDW = 0.2, 0.3, 0.3       # signal, supply and GND-stub widths (mm)


def clock_buffer(fps):
    """74LVC2G34 (SOT-363: 1 A1, 2 GND, 3 A2 | 4 Y2, 5 VCC, 6 Y1), 100 nF C1 above it, 47R R1/R2 on the outputs,
    100k R3 pull-down on the input. VCC comes from C1 under the chip's body (between its pin rows) into pin 5;
    pin 2 gets a GND via just outside its toe; both inputs are tied on the left with R3."""
    P = Pads(fps)
    t, v = [], []
    ux = (P.at("U1", 1)[0] + P.at("U1", 6)[0]) / 2
    c1 = P.at("C1", 1)
    y5 = P.at("U1", 5)[1]
    t.append((P.net("C1", 1), "B", SIG, [c1, (ux, c1[1] + 0.8), (ux, y5), P.at("U1", 5)]))
    g = P.at("C1", 2, 0, -1.05)
    t.append(("GND", "B", GNDW, [P.at("C1", 2), g]))
    v.append(("GND",) + g)
    g = P.at("U1", 2, -1.08, 0)
    t.append(("GND", "B", SIG, [P.at("U1", 2), g]))
    v.append(("GND",) + g)
    xl = P.at("U1", 2)[0] - 1.75
    inp = P.net("U1", 1)
    t.append((inp, "B", SIG, [P.at("U1", 3), (xl, P.at("U1", 3)[1]), (xl, P.at("U1", 1)[1]), P.at("U1", 1)]))
    t.append((inp, "B", SIG, [(xl, P.at("U1", 1)[1]), (xl, P.at("R3", 1)[1]), P.at("R3", 1)]))
    g = P.at("R3", 2, 0, 1.05)
    t.append(("GND", "B", GNDW, [P.at("R3", 2), g]))
    v.append(("GND",) + g)
    t.append((P.net("U1", 6), "B", SIG, [P.at("U1", 6), P.at("R1", 1)]))
    t.append((P.net("U1", 4), "B", SIG, [P.at("U1", 4), P.at("R2", 1)]))
    return t, v


def enable_buffer(fps):
    """74AHCT1G125 (SOT-23-5: 1 /OE, 2 A, 3 GND | 4 Y, 5 VCC), 100 nF C1 below it: C1 straight up to VCC, its GND
    pad to /OE (tied low) with a via beside pin 1, pin 3 its own via."""
    P = Pads(fps)
    t, v = [], []
    t.append((P.net("C1", 1), "B", SUP, [P.at("C1", 1), P.at("U1", 5)]))
    t.append(("GND", "B", GNDW, [P.at("C1", 2), P.at("U1", 1)]))
    for pin in (1, 3):
        g = P.at("U1", pin, -1.1, 0)
        t.append(("GND", "B", GNDW, [P.at("U1", pin), g]))
        v.append(("GND",) + g)
    return t, v


def mic(fps):
    """SPH0641LU4H-1 (1 DATA, 2 SELECT, 3 GND, 4 CLK, 5 VDD; sound port between pin 3 and the rest), 100 nF C1
    above it, 33R R1 on DATA. C1 straight down to VDD and to the GND pad (one via beside it); SELECT to VDD
    across the pin group, or round the top to the GND pad, whichever this mic's channel needs."""
    P = Pads(fps)
    t, v = [], []
    vdd = P.net("MK1", 5)
    t.append((vdd, "B", SUP, [P.at("C1", 1), P.at("MK1", 5)]))
    gx, gy = P.at("MK1", 3)
    t.append(("GND", "B", GNDW, [P.at("C1", 2), (P.at("C1", 2)[0], gy - 0.9), (gx, gy - 0.9), (gx, gy)]))
    g = (gx - 1.45, gy)
    t.append(("GND", "B", GNDW, [(gx, gy), g]))
    v.append(("GND",) + g)
    t.append((P.net("MK1", 1), "B", SIG, [P.at("MK1", 1), P.at("R1", 1)]))
    if P.net("MK1", 2) == vdd:
        t.append((vdd, "B", SIG, [P.at("MK1", 2), P.at("MK1", 5)]))
    else:
        top = P.at("MK1", 2)[1] + 0.75
        t.append(("GND", "B", SIG, [P.at("MK1", 2), (P.at("MK1", 2)[0], top), (gx, top), (gx, gy)]))
    return t, v


def tx_group(fps):
    """74HCT595 (U1, pins 1-8 facing the drivers, 9-16 facing the bus channel below) and two UCC27524 (U2, U3:
    outputs and VDD on the row towards the array). Inside the block:
    - the four 595 outputs to the driver inputs in board order (QA round the left end under the 595's body to
      U2 INB, QB straight up to U2 INA, QC and QD along two levels to U3 INB / INA; nothing crosses);
    - each driver's EN pins 1 and 8 tied under its body, the two drivers joined under both bodies;
    - VDRV from a via under each 1 uF (C2, C4: the front-copper VDRV area) through the 1 uF to the 100 nF
      above VDD (C3, C5) and on into pin 6, so the supply passes both capacitors before the pin;
    - a GND via at every GND pin and capacitor (the capacitors' above them, clear of the lane fan-out);
    - C1 straight to VCC (pin 16) and a via above it for +5V."""
    P = Pads(fps)
    t, v = [], []
    top_edge = P.at("U1", 1)[1] - 0.975              # 595 pins facing the drivers
    yd = top_edge - 0.45                             # QD level, QC above it
    yc = yd - 0.8
    t.append((P.net("U1", 1), "B", SIG, [P.at("U1", 1), P.at("U2", 2)]))
    for pin, lvl, dst in ((2, yc, ("U3", 4)), (3, yd, ("U3", 2))):
        a, b = P.at("U1", pin), P.at(*dst)
        t.append((P.net("U1", pin), "B", SIG, [a, (a[0], lvl), (b[0], lvl), b]))
    qa, u24 = P.at("U1", 15), P.at("U2", 4)
    body = qa[1] - 2.475                            # middle of the 595's body
    t.append((P.net("U1", 15), "B", SIG, [qa, (qa[0], body), (u24[0], body), u24]))
    # EN: pins 1 and 8 of each driver under its body, both drivers joined at mid-body
    ymid = (P.at("U2", 1)[1] + P.at("U2", 8)[1]) / 2
    for u in ("U2", "U3"):
        t.append((P.net(u, 1), "B", SIG, [P.at(u, 1), P.at(u, 8)]))
    t.append((P.net("U2", 1), "B", SIG, [(P.at("U2", 1)[0], ymid), (P.at("U3", 1)[0], ymid)]))
    # VDRV: via under the 1 uF -> 1 uF -> 100 nF -> VDD pin
    for big, small, u in (("C2", "C3", "U2"), ("C4", "C5", "U3")):
        bx, by = P.at(big, 1)
        via = (bx, by + 1.15)
        lvl = by - 0.65
        t.append((P.net(big, 1), "B", SUP, [via, (bx, by)]))
        t.append((P.net(big, 1), "B", SUP, [(bx, by), (bx, lvl), (P.at(small, 1)[0], lvl), P.at(small, 1)]))
        t.append((P.net(big, 1), "B", 0.4, [P.at(small, 1), P.at(u, 6)]))
        v.append((P.net(big, 1),) + via)
    # GND vias
    d2 = P.at("U2", 3)
    gnd = [(("U2", 3), (d2[0], d2[1] + 2.025)), (("U3", 3), (P.at("U3", 3)[0], P.at("U3", 3)[1] + 1.675)),
           (("U1", 8), (P.at("U1", 8)[0] + 1.1, P.at("U1", 8)[1])),
           (("C1", 2), (P.at("C1", 2)[0] + 0.575, P.at("C1", 2)[1] - 1.1))]     # right of the lanes passing on the left
    gnd += [((c, 2), (P.at(c, 2)[0], P.at(c, 2)[1] - 1.1)) for c in ("C2", "C3", "C4", "C5")]
    for pad, g in gnd:
        t.append(("GND", "B", GNDW, [P.at(*pad), g]))
        v.append(("GND",) + g)
    # +5V: C1 to VCC, a via above C1 for the supply
    c1 = P.at("C1", 1)
    t.append((P.net("C1", 1), "B", SUP, [c1, P.at("U1", 16)]))
    via = (c1[0], c1[1] - 1.3)
    t.append((P.net("C1", 1), "B", SUP, [c1, via]))
    v.append((P.net("C1", 1),) + via)
    return t, v


def adc(fps):
    """TLV320ADC5140 block (place.py ADC). Inside it:
    - the 8 inputs fan out from the chip's 0.5 mm pins to their capacitors in two staircases (the pin nearest
      the middle turns lowest), nothing crossing; INM2 drops straight down;
    - per channel: coupling capacitor down to the 2k, the divider node across to the bottom of the 1k, one GND
      via shared by the INxM capacitor and the 1k (the channel's input reference);
    - AVDD pin -> 100 nF -> 1 uF with a via into the front-copper +3V3A area; AREG pin -> 100 nF -> 10 uF;
      VREF pin -> 1 uF; DREG pin -> 100 nF -> 10 uF; IOVDD pin -> 100 nF -> 10 uF; every capacitor its own GND
      via; GND pins into the exposed pad; address straps to GND (exposed pad) or +3V3 (front-copper hop);
    - SHDNZ, SCL, SDA, +3V3, SDOUT (after its 33R), BCLK and FSYNC brought to the block's top edge, where the
      buses run (the header band for the upper row, the channel between the rows for the lower one)."""
    P = Pads(fps)
    t, v = [], []
    IN = 0.15                                        # input fan-out width (0.5 mm pin pitch)
    cap_top = P.at("C1", 2)[1] - 0.45                # top edge of the input capacitors' upper pads
    L1, L2, L3, L4 = (cap_top - 0.525 - 0.45 * k for k in range(4))
    top = P.at("C14", 2)[1] - 0.85                   # the block's top edge: external lines end here

    def fan(pin, lvl, cap, cap_pin):
        a, b = P.at("U1", pin), P.at(cap, cap_pin)
        t.append((P.net("U1", pin), "B", IN, [a, (a[0], lvl), (b[0], lvl), b]))

    fan(8, L1, "C2", 2)                              # right staircase: INP2, INM1, INP1
    fan(7, L2, "C5", 1)
    p6 = P.at("U1", 6)
    t.append((P.net("U1", 6), "B", IN, [p6, (p6[0] + 0.29, p6[1]), (p6[0] + 0.29, L3), (P.at("C1", 2)[0], L3),
                                       P.at("C1", 2)]))
    t.append((P.net("U1", 9), "B", IN, [P.at("U1", 9), P.at("C6", 1)]))      # INM2 straight down
    fan(10, L1, "C3", 2)                             # left staircase: INP3, INM3, INP4, INM4
    fan(11, L2, "C7", 1)
    fan(12, L3, "C4", 2)
    p13 = P.at("U1", 13)
    t.append((P.net("U1", 13), "B", IN, [p13, (p13[0] - 0.89, p13[1]), (p13[0] - 0.89, L4), (P.at("C8", 1)[0], L4),
                                        P.at("C8", 1)]))
    for j in range(1, 5):                            # dividers and input-reference GND vias
        cp, cm, r2, r1 = f"C{j}", f"C{j + 4}", f"R{j}", f"R{j + 4}"
        t.append((P.net(cp, 1), "B", SIG, [P.at(cp, 1), P.at(r2, 2), P.at(r1, 1)]))
        g = (P.at(cm, 2)[0], (P.at(cm, 2)[1] + P.at(r1, 2)[1]) / 2)
        t.append(("GND", "B", GNDW, [P.at(cm, 2), g, P.at(r1, 2)]))
        v.append(("GND",) + g)
    # analog supplies (right side)
    p1, c10 = P.at("U1", 1), P.at("C10", 1)
    t.append((P.net("U1", 1), "B", 0.25, [p1, (p1[0] + 0.7, p1[1]), c10]))
    ya = c10[1] - 0.9
    t.append((P.net("U1", 1), "B", SUP, [c10, (c10[0], ya), (P.at("C9", 1)[0], ya), P.at("C9", 1)]))
    va = (P.at("C10", 2)[0] + 1.075, ya)
    v.append((P.net("U1", 1),) + va)                 # into the front-copper +3V3A area
    t.append((P.net("U1", 2), "B", 0.25, [P.at("U1", 2), P.at("C12", 1)]))
    c12, c11 = P.at("C12", 1), P.at("C11", 1)
    yr = c12[1] + 0.87
    t.append((P.net("U1", 2), "B", 0.25, [c12, (c12[0], yr), (c11[0], yr)]))
    p3, c17 = P.at("U1", 3), P.at("C17", 1)
    xv = p3[0] + 0.858
    t.append((P.net("U1", 3), "B", 0.2, [p3, (xv, p3[1]), (xv, c17[1] - 0.3), (c17[0] - 0.3, c17[1] - 0.3)]))
    for pin in (4, 15, 16):                          # GND pins into the exposed pad; straps to +3V3 by a hop
        p = P.at("U1", pin)
        if P.net("U1", pin) == "GND":
            t.append(("GND", "B", 0.2, [p, (p[0] + (-0.8 if pin == 4 else 0.8), p[1])]))
    straps = [pin for pin in (15, 16) if P.net("U1", pin) != "GND"]
    p19, c16 = P.at("U1", 19), P.at("C16", 1)
    yi = p19[1] - 0.54
    if straps:
        v1 = (P.at("U1", 16)[0] - 0.99, P.at("U1", 16)[1] + 0.25)
        v2 = (c16[0] + 1.05, yi)
        net = P.net("U1", straps[0])
        for pin in straps:
            p = P.at("U1", pin)
            t.append((net, "B", 0.2, [p, (p[0] - 0.69, p[1]), v1]))
        t.append((net, "F", 0.25, [v1, v2]))
        v += [(net,) + v1, (net,) + v2]
    # digital supplies and outputs (top side)
    t.append((P.net("U1", 19), "B", 0.25, [p19, (p19[0], yi), (c16[0] + 0.3, yi)]))
    c15 = P.at("C15", 1)
    xl = c16[0] - 1.25
    t.append((P.net("U1", 19), "B", SUP, [c16, (xl, c16[1]), (xl, c15[1]), c15]))
    t.append((P.net("U1", 19), "B", SUP, [c15, (c15[0], top)]))                 # +3V3 to the top edge
    p24, c14, c13 = P.at("U1", 24), P.at("C14", 1), P.at("C13", 1)
    t.append((P.net("U1", 24), "B", 0.2, [p24, (p24[0], p24[1] - 0.44), (c14[0], c14[1] + 0.225)]))
    t.append((P.net("U1", 24), "B", SUP, [c14, (c13[0] - 0.3, c13[1] + 0.5)]))
    p21, r9 = P.at("U1", 21), P.at("R9", 1)
    t.append((P.net("U1", 21), "B", 0.2, [p21, (p21[0], p21[1] - 0.84), (r9[0] + 0.3, r9[1] + 0.2)]))
    t.append((P.net("R9", 2), "B", SIG, [P.at("R9", 2), (P.at("R9", 2)[0], top)]))
    for pin in (22, 23):                             # BCLK, FSYNC straight up
        p = P.at("U1", pin)
        t.append((P.net("U1", pin), "B", SIG, [p, (p[0], top)]))
    for pin, dx in ((14, -7.69), (17, -7.29), (18, -6.89)):                    # SHDNZ, SCL, SDA: left, then up
        p = P.at("U1", pin)
        t.append((P.net("U1", pin), "B", SIG, [p, (p[0] + dx, p[1]), (p[0] + dx, top)]))
    # GND vias of the capacitors
    gv = {"C10": (1.075, 0), "C12": (1.075, 0), "C17": (0.95, 0), "C13": (1.1, -0.2), "C14": (0, -0.95),
          "C16": (0, -1.0), "C15": (0, -1.3)}
    for c, (dx, dy) in gv.items():
        a = P.at(c, 2)
        g = (a[0] + dx, a[1] + dy)
        t.append(("GND", "B", GNDW, [a, g]))
        v.append(("GND",) + g)
    t.append(("GND", "B", GNDW, [P.at("C9", 2), (P.at("C12", 2)[0] + 1.075, P.at("C12", 2)[1])]))
    g = (P.at("C11", 2)[0] - 1.3, P.at("C11", 2)[1] + 0.45)
    t.append(("GND", "B", GNDW, [P.at("C11", 2), g]))
    v.append(("GND",) + g)
    return t, v


def power_input(fps):
    """12 V input section (place.py POWER_A): jack -> TPS26600 eFuse U8 -> polyfuse F1 -> C45 - L5 - C1 -> VIN.
    The 12 V feed (1 mm) drops straight from the jack's pin 1 onto the eFuse's IN pins, past C44 (bypass, its
    own GND via), R27 (FLT pull-up) and R22 (top of the UVLO/OVP divider); the divider runs down beside the
    IN-side pins to RTN; FLT drops through a via and returns on the front copper to R27 and TP15; dVdT, ILIM and IMON drop to their
    parts below (TP14 on IMON), whose other ends, R24's and the RTN pin sit on the RTN copper island (route.py
    stage_power); OUT (1 mm) runs down the board edge to F1, then the filter; the GND pin gets a via beside the
    island. Returns (tracks, vias); VIN leaves through a via to the front copper (the run down the margin)."""
    P = Pads(fps)
    t, v = [], []
    PW = 1.0                                          # 12 V path width
    j1 = P.at("J1", 1)
    in1, in2 = P.at("U8", 1), P.at("U8", 2)
    fx = in1[0] - 0.26                                # feed centre: covers both IN pads, clear of UVLO
    t.append((P.net("J1", 1), "B", PW, [(j1[0], j1[1]), (j1[0], j1[1] + 3.0), (fx, j1[1] + 4.0),
                                        (fx, in1[1] + 0.3)]))
    for ref, pad in (("C44", 1), ("R27", 1), ("R22", 1), ("TP13", 1)):     # the feed's taps
        a = P.at(ref, pad)
        t.append((P.net("J1", 1), "B", 0.4, [a, (fx, a[1])]))
    g = P.at("C44", 2, 0, -1.25)
    t.append(("GND", "B", GNDW, [P.at("C44", 2), g]))
    v.append(("GND",) + g)
    # UVLO / OVP divider: R22 - (UVLO) - R23 - (OVP) - R24 - RTN, the pins tapping in between
    r22b, r23t, r23b, r24t = P.at("R22", 2), P.at("R23", 1), P.at("R23", 2), P.at("R24", 1)
    t.append((P.net("R22", 2), "B", SIG, [r22b, r23t]))
    t.append((P.net("U8", 3), "B", SIG, [P.at("U8", 3), (r22b[0], P.at("U8", 3)[1])]))
    t.append((P.net("R23", 2), "B", SIG, [r23b, r24t]))
    t.append((P.net("U8", 5), "B", SIG, [P.at("U8", 5), (r23b[0] - 0.6, P.at("U8", 5)[1]), r23b]))
    # FLT: boxed in between OUT (pins 15-16, leaving left) and the RTN exposed pad, so it drops through a via
    # beside pin 13 and comes back up on the front copper at R27 / TP15
    f14 = P.at("U8", 14)
    fv1 = (f14[0] - 1.39, f14[1] + 0.33)
    fv2 = (P.at("R27", 2)[0] - 1.18, P.at("R27", 2)[1])
    t.append((P.net("U8", 14), "B", SIG, [f14, (f14[0] - 0.94, f14[1]), fv1]))
    t.append((P.net("U8", 14), "F", SIG, [fv1, (fv1[0], fv2[1] + 0.7), (fv1[0] + 0.7, fv2[1]), fv2]))
    t.append((P.net("U8", 14), "B", SIG, [fv2, P.at("R27", 2)]))
    t.append((P.net("U8", 14), "B", SIG, [fv2, (fv2[0], P.at("TP15", 1)[1] + 0.8), P.at("TP15", 1)]))
    v += [(P.net("U8", 14),) + fv1, (P.net("U8", 14),) + fv2]
    # dVdT, ILIM, IMON out to the left in lanes ordered like their parts (C43, R25, R26: nothing crosses);
    # the lowest, IMON, turns right above R25 to R26 and TP14
    x12 = P.at("C43", 1)[0] + 0.2
    a = P.at("U8", 12)
    t.append((P.net("U8", 12), "B", SIG, [a, (x12, a[1]), (x12, P.at("C43", 1)[1] - 0.2)]))
    a, b = P.at("U8", 11), P.at("R25", 1)
    x11 = b[0] - 0.6
    t.append((P.net("U8", 11), "B", SIG, [a, (x11, a[1]), (x11, b[1] - 0.88), (b[0], b[1] - 0.28)]))
    a, b = P.at("U8", 10), P.at("R26", 1)
    x10 = b[0] - 1.65
    t.append((P.net("U8", 10), "B", SIG, [a, (x10, a[1]), (x10, b[1] - 1.38), (x10 + 0.4, b[1] - 0.98),
                                          (b[0] - 0.4, b[1] - 0.98), (b[0], b[1] - 0.58), b]))
    t.append((P.net("R26", 1), "B", SIG, [P.at("R26", 1), (P.at("TP14", 1)[0], P.at("R26", 1)[1]),
                                          P.at("TP14", 1)]))
    # GND pin: a 45-degree stub to a via just below the exposed pad (the pad is RTN, not GND)
    g9 = P.at("U8", 9)
    gv = (g9[0] + 0.86, g9[1] + 0.86)
    t.append(("GND", "B", GNDW, [g9, gv]))
    v.append(("GND",) + gv)
    # OUT down the board edge to F1, then the input filter
    o1, o2 = P.at("U8", 16), P.at("U8", 15)
    oy = (o1[1] + o2[1]) / 2
    f1a, f1b = P.at("F1", 1), P.at("F1", 2)
    t.append((P.net("U8", 16), "B", PW, [(o1[0], oy), (2.6, oy), (2.6, f1a[1]), f1a]))
    c45a, l5a = P.at("C45", 1), P.at("L5", 1)
    t.append((P.net("F1", 2), "B", PW, [f1b, c45a]))
    t.append((P.net("F1", 2), "B", PW, [(f1b[0], (f1b[1] + c45a[1]) / 2), (l5a[0], (f1b[1] + c45a[1]) / 2), l5a]))
    g = (P.at("C45", 2)[0] - 1.9, P.at("C45", 2)[1])
    t.append(("GND", "B", GNDW, [P.at("C45", 2), g]))
    v.append(("GND",) + g)
    c1a = P.at("C1", 1)
    t.append((P.net("L5", 2), "B", PW, [P.at("L5", 2), c1a]))
    for dx, dy in ((-2.4, 0.8), (0.0, 1.85)):          # C1's GND pad: two vias (left, below; VIN runs on the right)
        g = (P.at("C1", 2)[0] + dx, P.at("C1", 2)[1] + dy)
        t.append(("GND", "B", PW, [P.at("C1", 2), g]))
        v.append(("GND",) + g)
    # VIN: a via up to the front copper (the run down the margin), a second one down to C2, R1/D2, TP1
    vin = P.net("C1", 1)
    va = (c1a[0] + 2.5, c1a[1] - 1.0)
    t.append((vin, "B", PW, [c1a, va]))
    v.append((vin,) + va)
    vb = (va[0], P.at("TP1", 1)[1] - 2.0)
    t.append((vin, "F", PW, [va, vb]))
    v.append((vin,) + vb)
    for ref in ("TP1", "R1", "C2"):
        a = P.at(ref, 1)
        t.append((vin, "B", 0.5, [vb, (a[0], vb[1]), a]))
    t.append((P.net("R1", 2), "B", SIG, [P.at("R1", 2), P.at("D2", 2)]))
    for ref, pad, d in (("C2", 2, (0, 1.2)), ("D2", 1, (0, 1.2))):
        a = P.at(ref, pad)
        g = (a[0] + d[0], a[1] + d[1])
        t.append(("GND", "B", GNDW, [a, g]))
        v.append(("GND",) + g)
    return t, v


def power_pm6v5(fps):
    """TPS65131 +-6V5 section (place.py POWER_CORES["C"]), routed by hand. Coordinates are drawn for U2 at
    (44.67, 301.2) on the back and move with the template (u()).
    - GND pins 2, 3, 9, 11, 19 straight into the exposed pad; C13's GND onto pin 3.
    - SW_P: 0.3 mm necks out of pins 1 and 24 (0.5 mm pitch), then 0.8 mm into L2's switch pad and on to D4's
      anode; the DNP snubber R29/C47 hangs off pin 24's neck.
    - VPOS (pin 23), FBP (22) and CP (21) each in their own lane straight down: VPOS to the top of the FBP
      divider (R8 / C14), which a 0.3 mm run joins to D4's cathode; FBP over R8 to R8 / C14 / R9.
    - 6V5_REG: D4's cathode along C15, C16 to JP5 (0.8 mm).
    - SW_N: one 0.5 mm run over pins 13-14 into L3, L3 to D6's cathode; -6V5_REG: D6's anode along C21, C22
      to JP6. VNEG (pin 15) runs under L3 to x 60.4 and up into that run; a branch at x 55.5 feeds the low end
      of the FBN divider (R11, C17). FBN goes round the right of R12, VREF to C18 and R12, CN to C20.
    - +5V: pins 5-6 tied, round the corner over the top pins to 8 and 10, and to R7, C12, L2 and C11; VIN65:
      R7 - C13 - pin 4.
    - GND vias at the capacitors and at L3's GND pad."""
    P = Pads(fps)
    cx, cy = xy(P.p[("U2", "1")].GetParentFootprint().GetPosition())
    if abs(P.at("U2", 1)[0] - cx + 1.98) > 0.05:          # the drawing assumes U2 at 0 degrees on the back
        raise ValueError("power_pm6v5: U2 orientation differs from the template")

    def u(x, y):
        return (round(x - 44.67 + cx, 3), round(y - 301.2 + cy, 3))

    def run(net, w, *pts):
        t.append((net, "B", w, [u(*q) for q in pts]))
    t, v = [], []
    gnd_vias = []
    n = lambda ref, pad: P.net(ref, pad)
    SWP, SWN, POS, NEG, P5, VIN65 = n("U2", 1), n("U2", 13), n("D4", 1), n("D6", 2), n("U2", 5), n("U2", 4)
    VPOS_FB, FBP, CP, FBN, VREF, CN = n("U2", 23), n("U2", 22), n("U2", 21), n("U2", 16), n("U2", 17), n("U2", 18)
    # ground pins into the exposed pad
    for x, y, x2, y2 in ((42.69, 301.95, 43.35, 301.95), (42.69, 301.45, 43.35, 301.45),
                         (44.42, 299.21, 44.42, 299.9), (45.42, 299.21, 45.42, 299.9), (45.92, 303.19, 45.92, 302.5)):
        run("GND", 0.25, (x, y), (x2, y2))
    run("GND", 0.3, (40.93, 301.68), (42.3, 301.68), (42.53, 301.45), (42.69, 301.45))      # C13 to pin 3
    # boost switch node
    run(SWP, 0.3, (42.69, 302.45), (42.4, 302.45), (41.9, 302.95), (41.5, 303.35))
    run(SWP, 0.8, (41.5, 303.35), (38.9, 303.35))
    run(SWP, 0.25, (43.42, 303.19), (43.42, 303.55), (43.07, 303.9), (42.0, 303.9), (41.6, 303.5))
    run(SWP, 0.8, (38.52, 306.5), (41.37, 309.35))                                           # L2 to D4 anode
    run(SWP, 0.3, (41.0, 303.35), (41.0, 304.48))                                            # snubber R29
    run(n("R29", 2), SIG, (41.0, 306.12), (42.7, 305.63))
    gnd_vias.append(((42.7, 307.18), (42.7, 308.1)))
    # boost output, sense and feedback
    run(POS, 0.8, (37.37, 309.85), (37.0, 310.27), (25.08, 310.27), (25.08, 310.0))          # D4 - C15 - C16 - JP5
    run(VPOS_FB, 0.25, (43.92, 303.19), (43.92, 309.1), (45.31, 309.1), (45.36, 310.7))
    run(POS, 0.3, (37.8, 310.6), (38.6, 311.4), (44.7, 311.4), (45.36, 310.74))              # D4 cathode to R8 / C14
    run(FBP, SIG, (44.42, 303.19), (44.42, 308.2), (46.96, 308.2), (46.96, 309.1), (46.91, 310.7))
    run(FBP, SIG, (46.96, 309.1), (48.73, 309.07))
    run(CP, SIG, (44.92, 303.19), (44.92, 304.3), (45.23, 304.6), (45.23, 304.82))
    # inverter switch node and output
    run(SWN, 0.5, (46.66, 300.2), (48.82, 300.2))
    run(SWN, 0.8, (48.6, 295.4), (47.9, 294.7), (47.67, 292.5))                              # L3 to D6 cathode
    run(SWN, 0.3, (46.6, 292.5), (45.26, 292.3))                                             # snubber R30
    run(n("R30", 2), SIG, (43.61, 292.3), (42.11, 292.3))
    run(NEG, 0.8, (51.67, 292.5), (52.2, 292.32), (61.78, 292.32))                           # D6 - C21 - C22 - JP6
    run(NEG, 0.25, (46.66, 300.95), (47.25, 301.0), (60.0, 301.0), (60.4, 300.6), (60.4, 292.32))   # VNEG
    run(NEG, 0.25, (55.5, 301.0), (55.5, 308.0), (55.0, 308.5), (51.93, 308.5))             # to R11, C17
    run(FBN, SIG, (46.66, 301.45), (52.4, 301.45), (52.8, 301.85), (52.8, 305.0), (52.37, 305.43), (51.93, 305.43),
        (51.93, 306.88), (53.73, 306.93))
    run(VREF, SIG, (46.66, 301.95), (49.6, 301.95), (50.13, 302.48), (50.13, 304.12), (51.58, 303.77), (51.93, 303.77))
    run(CN, SIG, (46.66, 302.45), (47.3, 302.45), (48.33, 303.48), (48.33, 304.12))
    # +5V and the VIN65 filter
    run(P5, 0.25, (42.69, 300.45), (42.69, 299.95))
    run(P5, 0.3, (42.69, 299.95), (42.4, 299.95), (42.15, 299.7), (42.15, 297.5), (41.65, 297.0), (40.93, 297.0))
    run(P5, 0.3, (42.15, 298.6), (42.45, 298.3), (44.62, 298.3))
    run(P5, 0.2, (43.92, 298.3), (43.92, 299.21))
    run(P5, 0.2, (44.62, 298.3), (44.92, 298.6), (44.92, 299.21))
    run(P5, 0.6, (40.93, 296.98), (38.93, 296.98), (38.23, 297.68))
    run(P5, 0.8, (38.0, 298.2), (34.3, 301.9), (34.02, 302.2))
    run(P5, 0.8, (31.17, 304.27), (34.02, 304.27))
    run(VIN65, 0.3, (40.93, 298.62), (40.93, 300.12), (41.76, 300.95), (42.2, 300.95))
    run(VIN65, 0.2, (42.2, 300.95), (42.69, 300.95))                    # narrow beside the +5V pin (0.25 mm rule)
    # ground vias: capacitors, snubbers, divider, L3's GND pad
    gnd_vias += [((31.17, 301.32), (31.17, 300.0)), ((38.23, 294.73), (38.23, 293.4)),
                 ((31.27, 307.32), (31.27, 306.02)), ((28.67, 307.32), (28.67, 306.02)),
                 ((56.07, 295.27), (56.07, 296.6)), ((58.87, 295.27), (58.87, 296.6)),
                 ((45.23, 306.38), (46.43, 306.38)), ((48.73, 310.73), (49.93, 310.73)),
                 ((48.33, 305.68), (48.33, 306.9)), ((50.13, 305.68), (50.13, 306.9)),
                 ((40.56, 292.3), (40.56, 291.1)), ((53.32, 297.75), (54.75, 296.4)), ((53.32, 297.75), (54.75, 299.1))]
    for a, g in gnd_vias:
        run("GND", GNDW, a, g)
        v.append(("GND",) + u(*g))
    return t, v


BLOCK_ROUTES = {"CLKB": clock_buffer, "TXEN": enable_buffer, "MIC": mic, "TXG": tx_group, "ADC": adc}
