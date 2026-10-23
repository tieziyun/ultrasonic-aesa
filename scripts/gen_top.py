"""Generate the hierarchy above the cell sheets, and the per-instance references.

Three levels:
  aesa.kicad_sch (page 1)  a block diagram of the signal flow. Its blocks are the sheet symbols
                           of the group sheets below, plus Power and the FPGA headers.
  group sheets             tx.kicad_sch (16 x tx_group + 4 x en_buf), array.kicad_sch
                           (64 x channel), adcs.kicad_sch (8 x adc), mics.kicad_sch (8 x mic),
                           clocks.kicad_sch (7 x clk_buf). Each places its cells and joins their
                           ports to global labels (DRVn, RXn, TX_*, ADC_*, MIC_*).
  cell sheets              channel, adc, tx_group, en_buf, clk_buf, mic (one file, reused),
                           power and headers (one-off, directly under the top sheet).

It then rewrites every cell symbol's (instances ...) block so each instance has its own
reference: <letters><sheet id><number:02d>. Sheet ids: CH n -> n (R101..R6409), ADC k -> 70+k,
TXG k -> 100+k, TXEN j -> 120+j, CLKB j -> 130+j, MIC m -> 140+m, power 95, headers 99.
Run it after editing any sheet:

  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe scripts\\gen_top.py
"""

import re
import uuid
from pathlib import Path
from common import DATE, REV, block_end

KICAD = Path(__file__).resolve().parent.parent / "kicad"
PROJECT = "aesa"
ROOT_UUID = "35d88121-1d5f-4a62-9009-788ea6bf93dd"  # keep stable across runs
NS = uuid.UUID("6f1e3a52-0000-4000-8000-0000000000a5")


def uid(name):
    return str(uuid.uuid5(NS, name))


# ---- s-expression snippets ------------------------------------------------------------------
def label(name, x, y, angle):
    just = "right" if angle == 180 else "left"
    return (f'\t(global_label "{name}"\n\t\t(shape passive)\n\t\t(at {x:.2f} {y:.2f} {angle})\n'
            f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n'
            f'\t\t(uuid "{uid("lbl" + name + str(x) + str(y))}")\n\t)\n')


def wire(x1, y1, x2, y2, key):
    return (f'\t(wire\n\t\t(pts\n\t\t\t(xy {x1:.2f} {y1:.2f}) (xy {x2:.2f} {y2:.2f})\n\t\t)\n'
            f'\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "{uid(key)}")\n\t)\n')


def no_connect(x, y, key):
    return f'\t(no_connect\n\t\t(at {x:.2f} {y:.2f})\n\t\t(uuid "{uid("nc" + key)}")\n\t)\n'


def text(s, x, y, size, key, bold=False, color=None):
    b = "\n\t\t\t\t(bold yes)" if bold else ""
    c = f"\n\t\t\t\t(color {color})" if color else ""
    return (f'\t(text "{s}"\n\t\t(exclude_from_sim no)\n\t\t(at {x:.2f} {y:.2f} 0)\n'
            f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {size} {size}){b}{c}\n\t\t\t)\n\t\t\t(justify left bottom)\n\t\t)\n'
            f'\t\t(uuid "{uid("txt" + key)}")\n\t)\n')


def line(pts, key, width=0.35, dash=False):
    xy = " ".join(f"(xy {x:.2f} {y:.2f})" for x, y in pts)
    return (f'\t(polyline\n\t\t(pts\n\t\t\t{xy}\n\t\t)\n\t\t(stroke\n\t\t\t(width {width})\n'
            f'\t\t\t(type {"dash" if dash else "default"})\n\t\t)\n\t\t(uuid "{uid("pl" + key)}")\n\t)\n')


def arrow(pts, key, label_text=None, label_at=None):
    """Polyline with an open arrowhead at the last point, and an optional caption."""
    out = [line(pts, key)]
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    dx, dy = (x1 > x0) - (x1 < x0), (y1 > y0) - (y1 < y0)
    a, w = 2.5, 1.2
    if dx:
        out.append(line([(x1 - dx * a, y1 - w), (x1, y1), (x1 - dx * a, y1 + w)], key + "h"))
    else:
        out.append(line([(x1 - w, y1 - dy * a), (x1, y1), (x1 + w, y1 - dy * a)], key + "h"))
    if label_text:
        out.append(text(label_text, label_at[0], label_at[1], 1.6, key))
    return "".join(out)


def rect(x1, y1, x2, y2, key, dash=False):
    return (f'\t(rectangle\n\t\t(start {x1:.2f} {y1:.2f})\n\t\t(end {x2:.2f} {y2:.2f})\n\t\t(stroke\n'
            f'\t\t\t(width 0.35)\n\t\t\t(type {"dash" if dash else "default"})\n\t\t)\n\t\t(fill\n'
            f'\t\t\t(type none)\n\t\t)\n\t\t(uuid "{uid("rc" + key)}")\n\t)\n')


def schematic(file_uuid, paper, title, body):
    return (f'(kicad_sch\n\t(version 20260101)\n\t(generator "eeschema")\n\t(generator_version "10.0")\n'
            f'\t(uuid "{file_uuid}")\n\t(paper "{paper}")\n\t(title_block\n\t\t(title "{title}")\n'
            f'\t\t(date "{DATE}")\n\t\t(rev "{REV}")\n\t\t(company "Hybrid 8x8 ultrasonic AESA")\n\t)\n'
            f'\t(lib_symbols)\n{body}'
            f'\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n\t(embedded_fonts no)\n)\n')


# ---- sheet symbols --------------------------------------------------------------------------
class Sheet:
    def __init__(self, name, file, sid, x, y, w, h, fill=None):
        self.name, self.file, self.sid = name, file, sid
        self.x, self.y, self.w, self.h = x, y, w, h
        self.uuid = uid("sheet" + name)
        self.pins = []  # (pin name, type, side, net or None)
        self.parent = f"/{ROOT_UUID}"  # instance path of the sheet that holds this symbol
        self.page = None
        self.fill = fill

    @property
    def path(self):
        return f"{self.parent}/{self.uuid}"

    def text(self):
        fill = f"(color {self.fill})" if self.fill else "(color 0 0 0 0.0000)"
        out = [f'\t(sheet\n\t\t(at {self.x:.2f} {self.y:.2f})\n\t\t(size {self.w:.2f} {self.h:.2f})\n'
               f'\t\t(fields_autoplaced yes)\n\t\t(stroke\n\t\t\t(width 0.1524)\n\t\t\t(type solid)\n\t\t)\n'
               f'\t\t(fill\n\t\t\t{fill}\n\t\t)\n\t\t(uuid "{self.uuid}")\n'
               f'\t\t(property "Sheetname" "{self.name}"\n\t\t\t(at {self.x:.2f} {self.y - 0.71:.2f} 0)\n'
               f'\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size {1.8 if self.fill else 1.27} {1.8 if self.fill else 1.27})\n'
               f'\t\t\t\t\t(bold {"yes" if self.fill else "no"})\n\t\t\t\t)\n\t\t\t\t(justify left bottom)\n\t\t\t)\n\t\t)\n'
               f'\t\t(property "Sheetfile" "{self.file}"\n\t\t\t(at {self.x:.2f} {self.y + self.h + 0.59:.2f} 0)\n'
               f'\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left top)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n']
        extra = []
        left = [p for p in self.pins if p[2] == "left"]
        right = [p for p in self.pins if p[2] == "right"]
        for side, pins in (("left", left), ("right", right)):
            for i, (pname, ptype, _, net) in enumerate(pins):
                px = self.x if side == "left" else self.x + self.w
                py = self.y + 2.54 * (i + 1)
                ang = 180 if side == "left" else 0
                just = "left" if side == "left" else "right"
                out.append(f'\t\t(pin "{pname}" {ptype}\n\t\t\t(at {px:.2f} {py:.2f} {ang})\n'
                           f'\t\t\t(uuid "{uid(self.name + pname)}")\n\t\t\t(effects\n\t\t\t\t(font\n'
                           f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify {just})\n\t\t\t)\n\t\t)\n')
                if net:  # 2.54 mm stub so the label doesn't sit on the pin name
                    x2 = px - 2.54 if ang == 180 else px + 2.54
                    extra.append(wire(px, py, x2, py, "w" + self.name + pname) + label(net, x2, py, ang))
                else:
                    extra.append(no_connect(px, py, self.name + pname))
        out.append(f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "{self.parent}"\n'
                   f'\t\t\t\t\t(page "{self.page}")\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n')
        return "".join(out), "".join(extra)


# ---- the cells, by group ---------------------------------------------------------------------
def cells():
    """{group file: [cell sheets]} with positions inside each group sheet."""
    g = {}
    arr = []
    for n in range(1, 65):  # 8 x 8, row-major = element order on the panel
        r, c = divmod(n - 1, 8)
        s = Sheet(f"CH{n:02d}", "channel.kicad_sch", n, 38.1 + 68.58 * c, 50.8 + 25.4 * r, 22.86, 5.08)
        s.pins = [("DRV_OUT", "input", "left", f"DRV{n}"), ("RX_OUT", "output", "right", f"RX{n}")]
        arr.append(s)
    g["array.kicad_sch"] = arr

    ab, adcs = "AB", []
    for k in range(1, 9):
        r, c = divmod(k - 1, 4)
        s = Sheet(f"ADC{k}", "adc.kicad_sch", 70 + k, 50.8 + 88.9 * c, 50.8 + 55.88 * r, 25.4, 25.4)
        s.pins = [(f"IN{i}", "input", "left", f"RX{8 * (k - 1) + i}") for i in range(1, 9)]
        s.pins += [("SCLK", "input", "right", f"ADC_SCLK_{ab[(k - 1) // 4]}"),
                   ("CONVST", "input", "right", f"ADC_CONVST_{ab[(k - 1) // 4]}"),
                   ("DOUTA", "output", "right", f"ADC_DOUTA{k}"), ("DOUTB", "output", "right", f"ADC_DOUTB{k}"),
                   ("BUSY", "output", "right", "ADC_BUSY" if k == 1 else None)]
        adcs.append(s)
    g["adcs.kicad_sch"] = adcs

    abcd, tx = "ABCD", []
    for k in range(1, 17):  # 16 groups of 4 elements (one 595 + two UCC27524 each)
        r, c = divmod(k - 1, 4)
        s = Sheet(f"TXG{k:02d}", "tx_group.kicad_sch", 100 + k, 50.8 + 91.44 * c, 50.8 + 33.02 * r, 25.4, 12.7)
        s.pins = [("SDI", "input", "left", f"TX_SDI{k - 1}"),
                  ("SCK", "input", "left", f"TX_SCK_{abcd[(k - 1) // 4]}"),
                  ("LATCH", "input", "left", f"TX_LATCH_{abcd[(k - 1) // 4]}"),
                  ("EN", "input", "left", f"TX_EN{(k - 1) // 4 + 1}")]
        s.pins += [(f"DRV{i}", "output", "right", f"DRV{4 * (k - 1) + i}") for i in range(1, 5)]
        tx.append(s)
    for j in range(1, 5):  # one TX-enable buffer per 4 groups (8 drivers, 16 EN pins)
        s = Sheet(f"TXEN{j}", "en_buf.kicad_sch", 120 + j, 50.8 + 91.44 * (j - 1), 195.58, 25.4, 5.08)
        s.pins = [("IN", "input", "left", "TX_EN_FPGA"), ("OUT", "output", "right", f"TX_EN{j}")]
        tx.append(s)
    g["tx.kicad_sch"] = tx

    clocks = [("TX_SCK", "TX_SCK_A", "TX_SCK_B"), ("TX_SCK", "TX_SCK_C", "TX_SCK_D"),
              ("TX_LATCH", "TX_LATCH_A", "TX_LATCH_B"), ("TX_LATCH", "TX_LATCH_C", "TX_LATCH_D"),
              ("ADC_SCLK", "ADC_SCLK_A", "ADC_SCLK_B"), ("ADC_CONVST", "ADC_CONVST_A", "ADC_CONVST_B"),
              ("MIC_CLK_IN", "MIC_CLK_A", "MIC_CLK_B")]
    clk = []
    for j, (src, o1, o2) in enumerate(clocks, start=1):  # each output drives <= 4 loads
        r, c = divmod(j - 1, 4)
        s = Sheet(f"CLKB{j}", "clk_buf.kicad_sch", 130 + j, 50.8 + 91.44 * c, 50.8 + 33.02 * r, 25.4, 7.62)
        s.pins = [("IN", "input", "left", src), ("OUT1", "output", "right", o1), ("OUT2", "output", "right", o2)]
        clk.append(s)
    g["clocks.kicad_sch"] = clk

    mics = []
    for m in range(1, 9):  # pairs share DATA (SEL high / low); MIC_CLK_A: mics 1-4, B: 5-8
        r, c = divmod(m - 1, 4)
        s = Sheet(f"MIC{m}", "mic.kicad_sch", 140 + m, 50.8 + 91.44 * c, 50.8 + 33.02 * r, 25.4, 7.62)
        s.pins = [("CLK", "input", "left", "MIC_CLK_A" if m <= 4 else "MIC_CLK_B"),
                  ("SEL", "input", "left", "3V3_MIC" if m % 2 else "GND"),
                  ("DATA", "output", "right", f"MIC_DATA{(m - 1) // 2}")]
        mics.append(s)
    g["mics.kicad_sch"] = mics
    return g


GROUP_INFO = {  # file: (sheet name, paper, title, note)
    "tx.kicad_sch": ("TX drive", "A3", "TX drive: 16 shift-register/driver groups, 4 enable buffers",
                     "TX drive. The FPGA streams 16 serial lines (TX_SDI0-15) into 16 tx_group cells; each "
                     "drives 4 elements (DRV). TX_EN_FPGA reaches the drivers through 4 en_buf cells."),
    "array.kicad_sch": ("Transceivers", "A2", "Transceivers: 64 channels (8 x 8 grid)",
                        "Transceiver array. Channel n = element n of the 8 x 8 grid, row-major. Each channel "
                        "takes DRVn from the TX drive and returns its amplified echo RXn to the ADCs."),
    "adcs.kicad_sch": ("ADCs", "A3", "ADCs: 8 x AD7606C-16",
                       "ADCs. ADC k digitises RX 8(k-1)+1 ... 8k and returns 2 serial lines (DOUTA/B). "
                       "ADC1-4 use ADC_SCLK_A/CONVST_A, ADC5-8 the _B outputs."),
    "mics.kicad_sch": ("MEMS mics", "A3", "MEMS microphones: 8 x SPH0641LU4H-1",
                       "MEMS mics. Mics 2k-1 and 2k share MIC_DATAk-1 (SEL high on the odd one). "
                       "MIC_CLK_A feeds mics 1-4, MIC_CLK_B mics 5-8."),
    "clocks.kicad_sch": ("Clock buffers", "A3", "Clock buffers: 7 x 74LVC2G34 at the FPGA headers",
                         "Clock buffers. Each FPGA clock enters one or two clk_buf cells; every output "
                         "drives at most 4 loads through 33 R."),
}


# ---- top sheet: block diagram -----------------------------------------------------------------
def top_blocks():
    """Sheet symbols on the top page, as the boxes of the block diagram (x, y, w, h)."""
    blue, green, orange, grey = "225 236 255 1.0000", "226 245 226 1.0000", "255 238 214 1.0000", "238 238 238 1.0000"
    return {
        "headers.kicad_sch": Sheet("FPGA headers J1/J2", "headers.kicad_sch", 99, 86.36, 60.96, 40.64, 121.92, grey),
        "clocks.kicad_sch": Sheet("Clock buffers", "clocks.kicad_sch", 0, 157.48, 104.14, 48.26, 20.32, grey),
        "tx.kicad_sch": Sheet("TX drive", "tx.kicad_sch", 0, 236.22, 60.96, 55.88, 25.4, orange),
        "array.kicad_sch": Sheet("Transceivers", "array.kicad_sch", 0, 320.04, 60.96, 55.88, 121.92, blue),
        "adcs.kicad_sch": Sheet("ADCs", "adcs.kicad_sch", 0, 236.22, 157.48, 55.88, 25.4, green),
        "mics.kicad_sch": Sheet("MEMS mics", "mics.kicad_sch", 0, 157.48, 205.74, 48.26, 20.32, grey),
        "power.kicad_sch": Sheet("Power", "power.kicad_sch", 95, 86.36, 220.98, 40.64, 25.4, grey),
    }


def top_drawing():
    o = []
    o.append(text("Hybrid 8x8 ultrasonic AESA: how the signal flows", 25.4, 27.94, 3.0, "t0", bold=True))
    steps = ["1. The FPGA streams one TX bit per element (16 serial lines, 10 MHz) and the clocks.",
             "2. TX drive: shift registers latch the bits; gate drivers switch each element between 0 and 15 V.",
             "3. Transceivers: the 64 CUSA-TR65 elements emit 40 kHz; echoes return to the same elements.",
             "4. The diode T/R switch and limiter protect the receiver; amplifier + 40 kHz band-pass give x22.",
             "5. ADCs: 8 x AD7606C-16 sample all 64 channels at 200 kSPS (raw waveform, amplitude and phase).",
             "6. The samples go back to the FPGA over the ribbon, which forms the beams. 8 MEMS mics add wideband audio."]
    for i, s in enumerate(steps):
        o.append(text(s, 25.4, 35.56 + 4.32 * i, 1.8, f"s{i}"))
    # FPGA board (off-board) and ultrasound/air (off-board): dashed boxes
    o.append(rect(25.4, 60.96, 66.04, 182.88, "fpga", dash=True))
    o.append(text("ALINX AXU3EGB", 28.0, 70.0, 2.0, "f1", bold=True))
    for i, s in enumerate(["Zynq UltraScale+", "(off-board)", "", "beamforming,", "TX timing,", "ADC readout"]):
        o.append(text(s, 28.0, 76.0 + 4.0 * i, 1.6, f"f2{i}"))
    o.append(rect(386.08, 60.96, 403.86, 182.88, "air", dash=True))  # inside the A3 frame (x < 410)
    o.append(text("Air /", 388.0, 70.0, 2.0, "a1", bold=True))
    o.append(text("target", 388.0, 75.0, 2.0, "a2", bold=True))
    # Ribbon between the FPGA board and the headers (both directions)
    o.append(arrow([(66.04, 73.66), (86.36, 73.66)], "r1"))
    o.append(arrow([(86.36, 172.72), (66.04, 172.72)], "r2"))
    o.append(text("2 ribbons", 67.5, 70.0, 1.5, "r3"))
    # TX path
    o.append(arrow([(127.0, 73.66), (236.22, 73.66)], "tx1", "TX_SDI0-15, TX_EN, /OE, /CLR", (130.0, 71.12)))
    o.append(arrow([(127.0, 114.3), (157.48, 114.3)], "ck1", "clocks", (130.0, 111.76)))
    o.append(arrow([(205.74, 109.22), (264.16, 109.22), (264.16, 86.36)], "ck2", "SCK, LATCH", (210.0, 106.68)))
    o.append(arrow([(205.74, 119.38), (264.16, 119.38), (264.16, 157.48)], "ck3", "SCLK, CONVST", (210.0, 124.46)))
    o.append(arrow([(200.66, 124.46), (200.66, 205.74)], "ck4", "PDM clock", (202.5, 160.0)))
    o.append(arrow([(292.1, 73.66), (320.04, 73.66)], "dr", "DRV1-64", (294.0, 71.12)))
    o.append(text("0 / 15 V", 294.0, 78.74, 1.5, "dr2"))
    # Acoustic path
    o.append(arrow([(375.92, 99.06), (386.08, 99.06)], "us1", "40 kHz", (376.5, 96.52)))
    o.append(arrow([(386.08, 139.7), (375.92, 139.7)], "us2", "echo", (376.5, 137.16)))
    # RX path
    o.append(arrow([(320.04, 170.18), (292.1, 170.18)], "rx", "RX1-64", (294.0, 167.64)))
    o.append(text("echo x22", 294.0, 175.26, 1.5, "rx2"))
    o.append(arrow([(236.22, 170.18), (127.0, 170.18)], "ad", "ADC_DOUTA/B 1-8, BUSY", (130.0, 167.64)))
    o.append(arrow([(157.48, 215.9), (106.68, 215.9), (106.68, 182.88)], "mc", "MIC_DATA0-3 (PDM)", (110.0, 213.36)))
    # (captions sit inside the blocks, which are otherwise empty)
    # Captions under the boxes
    caps = [("16 x 74HCT595 + 32 x UCC27524", 238.76, 71.12), ("4 x enable buffer", 238.76, 74.93),
            ("64 x: T/R diodes, CUSA-TR65,", 322.58, 73.66), ("limiter, x6 amp, 40 kHz band-pass", 322.58, 77.47),
            ("8 x AD7606C-16, 200 kSPS", 238.76, 167.64), ("7 x 74LVC2G34, 33 R", 160.02, 114.3),
            ("8 x SPH0641 PDM", 160.02, 215.9), ("J1 = AXU3EGB J15", 88.9, 96.52), ("(TX, mics)", 88.9, 100.33),
            ("J2 = AXU3EGB J16", 88.9, 106.68), ("(ADCs)", 88.9, 110.49)]
    for i, (s, x, y) in enumerate(caps):
        o.append(text(s, x, y, 1.5, f"c{i}"))
    o.append(text("12 V jack -> +5V, +-6V5, +-5VA (analog), VDRV 15 V (TX), +3V3, 3V3_MIC.", 132.08, 240.03, 1.6, "p1"))
    o.append(text("Every rail: test point, isolation jumper, LED (not on the analog LDOs).", 132.08, 243.84, 1.6, "p2"))
    o.append(text("Click a block to open it. Repeated parts are one sheet each: channel x64, adc x8, "
                  "tx_group x16, en_buf x4, clk_buf x7, mic x8.", 25.4, 255.27, 1.6, "h1"))
    return "".join(o)


# ---- writing ----------------------------------------------------------------------------------
def write_all():
    groups = cells()
    top = top_blocks()
    page = 1
    order = ["headers.kicad_sch", "clocks.kicad_sch", "tx.kicad_sch", "array.kicad_sch", "adcs.kicad_sch",
             "mics.kicad_sch", "power.kicad_sch"]
    for f in order:
        page += 1
        top[f].page = page
        for c in groups.get(f, []):
            page += 1
            c.parent = top[f].path
            c.page = page
    # top sheet
    body = [top_drawing()]
    for f in order:
        a, b = top[f].text()
        body += [b, a]
    (KICAD / "aesa.kicad_sch").write_text(
        schematic(ROOT_UUID, "A3", "Hybrid 8x8 ultrasonic AESA: block diagram", "".join(body)), encoding="utf-8")
    # group sheets
    for f, cs in groups.items():
        name, paper, title, note = GROUP_INFO[f]
        body = [text(note, 38.1, 30.48, 1.8, "gn" + f, bold=True)]
        for c in cs:
            a, b = c.text()
            body += [b, a]
        (KICAD / f).write_text(schematic(uid("file" + f), paper, title, "".join(body)), encoding="utf-8")
    # references in the cell sheets
    insts = {}
    for f, cs in groups.items():
        for c in cs:
            insts.setdefault(c.file, []).append(c)
    for f in ("power.kicad_sch", "headers.kicad_sch"):
        insts[f] = [top[f]]
    write_instances(insts)
    print(f"top + {len(groups)} group sheets, {page} pages")


def renumber(ref, sid):
    m = re.match(r"^(#?[A-Za-z]+)(\d+)$", ref)
    if not m:
        raise ValueError(f"unexpected reference {ref}")
    return f"{m.group(1)}{sid}{int(m.group(2)):02d}"


def write_instances(by_file):
    for file, insts in by_file.items():
        path = KICAD / file
        lines = path.read_text(encoding="utf-8").split("\n")
        out, i, count = [], 0, 0
        while i < len(lines):
            line_ = lines[i]
            out.append(line_)
            if line_ == "\t(symbol":
                # Find this symbol's reference and unit, then replace its instances block.
                j = block_end(lines, i)
                blk = "\n".join(lines[i:j + 1])
                ref = re.search(r'\(property "Reference" "([^"]+)"', blk).group(1)
                # A KiCad re-save (kicad-cli sch upgrade) stores the first instance's reference
                # (e.g. U101 for CH01's U1) as the symbol's own: strip it back to the base (U1).
                m0 = re.match(r"^(#?[A-Za-z]+)(\d+)$", ref)
                s0 = str(insts[0].sid)
                if m0 and m0.group(2).startswith(s0) and len(m0.group(2)) == len(s0) + 2:
                    ref = f"{m0.group(1)}{int(m0.group(2)[len(s0):])}"
                unit = re.search(r"\n\t\t\(unit (\d+)\)", blk).group(1)
                # Remove every (instances ...) block: the MCP server can add a second one
                # with its own numbering, and KiCad then uses the stray references.
                at = None  # where the (first) instances block was
                while "(instances" in blk:
                    start = blk.index("(instances")
                    k = start  # strip only whitespace before it: the MCP writes ")(instances"
                    while k > 0 and blk[k - 1] in " \t\n":
                        k -= 1
                    depth, e = 0, start
                    while True:
                        ch = blk[e]
                        depth += (ch == "(") - (ch == ")")
                        e += 1
                        if depth == 0:
                            break
                    blk = blk[:k] + blk[e:]
                    at = k if at is None else min(at, k)
                if at is None:  # no instances block yet: add one before the symbol's closing ")"
                    at = blk.rindex("\n\t)")
                paths = "".join(
                    f'\n\t\t\t\t(path "{s.path}"\n\t\t\t\t\t(reference "{renumber(ref, s.sid)}")'
                    f'\n\t\t\t\t\t(unit {unit})\n\t\t\t\t)' for s in insts)
                new_inst = f'\n\t\t(instances\n\t\t\t(project "{PROJECT}"{paths}\n\t\t\t)\n\t\t)'
                blk = blk[:at] + new_inst + blk[at:]
                out[-1:] = blk.split("\n")
                count += 1
                i = j + 1
                continue
            i += 1
        path.write_text("\n".join(out), encoding="utf-8")
        print(f"{file}: {count} symbols x {len(insts)} instances")


if __name__ == "__main__":
    write_all()
