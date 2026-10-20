"""Generate the top sheet kicad/aesa.kicad_sch and the per-instance references.

The sub-sheets are drawn with the KiCad MCP server (kicad/*.kicad_sch). This script
writes only the hierarchy:

* 64 instances of channel.kicad_sch (CH01..CH64): DRV_OUT <- global DRV<n>,
  RX_OUT -> global RX<n>. Channel n is element n of the 8x8 grid, row-major.
* 8 instances of adc.kicad_sch (ADC1..ADC8): IN1..IN8 <- RX<8(k-1)+1..8k>,
  DOUTA/DOUTB -> ADC_DOUTA<k>/ADC_DOUTB<k>, BUSY -> ADC_BUSY on ADC1 only
  (no-connect on the others).
* one instance each of tx_shift, tx_drivers, mems, power and headers (global labels only).

It then rewrites every sub-sheet symbol's (instances ...) block so each instance has its
own reference: <letters><sheet id><number:02d>, e.g. R101..R109 in channel 1, U7101 in
ADC1, U8001.. in tx_shift. Run it again after editing any sub-sheet:

  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe scripts\\gen_top.py
"""

import re
import uuid
from pathlib import Path

KICAD = Path(__file__).resolve().parent.parent / "kicad"
PROJECT = "aesa"
ROOT_UUID = "35d88121-1d5f-4a62-9009-788ea6bf93dd"  # keep stable across runs
NS = uuid.UUID("6f1e3a52-0000-4000-8000-0000000000a5")


def uid(name):
    return str(uuid.uuid5(NS, name))


def label(name, x, y, angle):
    just = "right" if angle == 180 else "left"
    return (f'\t(global_label "{name}"\n\t\t(shape passive)\n\t\t(at {x:.2f} {y:.2f} {angle})\n'
            f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n'
            f'\t\t(uuid "{uid("lbl" + name + str(x) + str(y))}")\n\t)\n')


def no_connect(x, y):
    return f'\t(no_connect\n\t\t(at {x:.2f} {y:.2f})\n\t\t(uuid "{uid("nc" + str(x) + str(y))}")\n\t)\n'


class Sheet:
    def __init__(self, name, file, sid, x, y, w, h):
        self.name, self.file, self.sid = name, file, sid
        self.x, self.y, self.w, self.h = x, y, w, h
        self.uuid = uid("sheet" + name)
        self.pins = []  # (pin name, type, side, net or None)

    def text(self, page):
        out = [f'\t(sheet\n\t\t(at {self.x:.2f} {self.y:.2f})\n\t\t(size {self.w:.2f} {self.h:.2f})\n'
               f'\t\t(fields_autoplaced yes)\n\t\t(stroke\n\t\t\t(width 0.1524)\n\t\t\t(type solid)\n\t\t)\n'
               f'\t\t(fill\n\t\t\t(color 0 0 0 0.0000)\n\t\t)\n\t\t(uuid "{self.uuid}")\n'
               f'\t\t(property "Sheetname" "{self.name}"\n\t\t\t(at {self.x:.2f} {self.y - 0.71:.2f} 0)\n'
               f'\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left bottom)\n\t\t\t)\n\t\t)\n'
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
                extra.append(label(net, px, py, ang) if net else no_connect(px, py))
        out.append(f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n\t\t\t\t(path "/{ROOT_UUID}"\n'
                   f'\t\t\t\t\t(page "{page}")\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n')
        return "".join(out), "".join(extra)


def build_sheets():
    sheets = []
    for n in range(1, 65):  # 8 x 8 grid of channel boxes
        r, c = divmod(n - 1, 8)
        s = Sheet(f"CH{n:02d}", "channel.kicad_sch", n, 38.1 + 68.58 * c, 30.48 + 20.32 * r, 22.86, 5.08)
        s.pins = [("DRV_OUT", "input", "left", f"DRV{n}"), ("RX_OUT", "output", "right", f"RX{n}")]
        sheets.append(s)
    ab = "AB"
    for k in range(1, 9):
        s = Sheet(f"ADC{k}", "adc.kicad_sch", 70 + k, 38.1 + 68.58 * (k - 1), 205.74, 22.86, 25.4)
        s.pins = [(f"IN{i}", "input", "left", f"RX{8 * (k - 1) + i}") for i in range(1, 9)]
        s.pins += [("SCLK", "input", "right", f"ADC_SCLK_{ab[(k - 1) // 4]}"),
                   ("CONVST", "input", "right", f"ADC_CONVST_{ab[(k - 1) // 4]}"),
                   ("DOUTA", "output", "right", f"ADC_DOUTA{k}"), ("DOUTB", "output", "right", f"ADC_DOUTB{k}"),
                   ("BUSY", "output", "right", "ADC_BUSY" if k == 1 else None)]
        sheets.append(s)
    # TX: 16 groups of 4 elements (one 595 + two UCC27524 each)
    abcd = "ABCD"
    for k in range(1, 17):
        r, c = divmod(k - 1, 8)
        s = Sheet(f"TXG{k:02d}", "tx_group.kicad_sch", 100 + k, 38.1 + 68.58 * c, 248.92 + 22.86 * r, 22.86, 12.7)
        s.pins = [("SDI", "input", "left", f"TX_SDI{k - 1}"),
                  ("SCK", "input", "left", f"TX_SCK_{abcd[(k - 1) // 4]}"),
                  ("LATCH", "input", "left", f"TX_LATCH_{abcd[(k - 1) // 4]}"),
                  ("EN", "input", "left", f"TX_EN{(k - 1) // 4 + 1}")]
        s.pins += [(f"DRV{i}", "output", "right", f"DRV{4 * (k - 1) + i}") for i in range(1, 5)]
        sheets.append(s)
    # One TX-enable buffer per 4 groups (8 drivers, 16 EN pins)
    for j in range(1, 5):
        s = Sheet(f"TXEN{j}", "en_buf.kicad_sch", 120 + j, 38.1 + 68.58 * (j - 1), 299.72, 22.86, 5.08)
        s.pins = [("IN", "input", "left", "TX_EN_FPGA"), ("OUT", "output", "right", f"TX_EN{j}")]
        sheets.append(s)
    # Clock fan-out at the headers: each output drives <= 4 loads
    clocks = [("TX_SCK", "TX_SCK_A", "TX_SCK_B"), ("TX_SCK", "TX_SCK_C", "TX_SCK_D"),
              ("TX_LATCH", "TX_LATCH_A", "TX_LATCH_B"), ("TX_LATCH", "TX_LATCH_C", "TX_LATCH_D"),
              ("ADC_SCLK", "ADC_SCLK_A", "ADC_SCLK_B"), ("ADC_CONVST", "ADC_CONVST_A", "ADC_CONVST_B"),
              ("MIC_CLK_IN", "MIC_CLK_A", "MIC_CLK_B")]
    for j, (src, o1, o2) in enumerate(clocks, start=1):
        s = Sheet(f"CLKB{j}", "clk_buf.kicad_sch", 130 + j, 38.1 + 68.58 * ((j - 1) % 8), 320.04, 22.86, 7.62)
        s.pins = [("IN", "input", "left", src), ("OUT1", "output", "right", o1), ("OUT2", "output", "right", o2)]
        sheets.append(s)
    # MEMS mics: pairs share DATA (SEL high / low); MIC_CLK_A feeds mics 1-4, MIC_CLK_B 5-8
    for m in range(1, 9):
        s = Sheet(f"MIC{m}", "mic.kicad_sch", 140 + m, 38.1 + 68.58 * (m - 1), 345.44, 22.86, 7.62)
        s.pins = [("CLK", "input", "left", "MIC_CLK_A" if m <= 4 else "MIC_CLK_B"),
                  ("SEL", "input", "left", "3V3_MIC" if m % 2 else "GND"),
                  ("DATA", "output", "right", f"MIC_DATA{(m - 1) // 2}")]
        sheets.append(s)
    for i, (name, file, sid) in enumerate((("Power", "power.kicad_sch", 95),
                                           ("AXU3EGB headers", "headers.kicad_sch", 99))):
        sheets.append(Sheet(name, file, sid, 38.1 + 101.6 * i, 370.84, 63.5, 25.4))
    return sheets


def write_top(sheets):
    body, labels = [], []
    for page, s in enumerate(sheets, start=2):
        a, b = s.text(page)
        body.append(a)
        labels.append(b)
    note = ('\t(text "Hybrid 8x8 ultrasonic AESA: 64 transceiver channels (CH01..CH64, one sheet '
            'reused 64 times), 8 MEMS mics, 8 ADCs. Source of truth: this KiCad hierarchy; '
            'skidl/channel.py is the reference spec."\n\t\t(exclude_from_sim no)\n\t\t(at 38.1 20.32 0)\n'
            '\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 2 2)\n\t\t\t)\n\t\t\t(justify left bottom)\n\t\t)\n'
            f'\t\t(uuid "{uid("note")}")\n\t)\n')
    text = (f'(kicad_sch\n\t(version 20260101)\n\t(generator "eeschema")\n\t(generator_version "10.0")\n'
            f'\t(uuid "{ROOT_UUID}")\n\t(paper "A2")\n\t(title_block\n\t\t(title "Ultrasonic AESA front end")\n\t)\n'
            f'\t(lib_symbols)\n{note}{"".join(labels)}{"".join(body)}'
            f'\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n\t(embedded_fonts no)\n)\n')
    (KICAD / "aesa.kicad_sch").write_text(text, encoding="utf-8")


def renumber(ref, sid):
    m = re.match(r"^(#?[A-Za-z]+)(\d+)$", ref)
    if not m:
        raise ValueError(f"unexpected reference {ref}")
    return f"{m.group(1)}{sid}{int(m.group(2)):02d}"


def write_instances(sheets):
    by_file = {}
    for s in sheets:
        by_file.setdefault(s.file, []).append(s)
    for file, insts in by_file.items():
        path = KICAD / file
        lines = path.read_text(encoding="utf-8").split("\n")
        out, i, count = [], 0, 0
        while i < len(lines):
            line = lines[i]
            out.append(line)
            if line == "\t(symbol":
                # Find this symbol's reference and unit, then replace its instances block.
                depth, j = 0, i
                while True:
                    depth += lines[j].count("(") - lines[j].count(")")
                    if depth == 0:
                        break
                    j += 1
                blk = "\n".join(lines[i:j + 1])
                ref = re.search(r'\(property "Reference" "([^"]+)"', blk).group(1)
                unit = re.search(r"\n\t\t\(unit (\d+)\)", blk).group(1)
                # Remove every (instances ...) block: the MCP server can add a second one
                # with its own numbering, and KiCad then uses the stray references.
                s0 = None
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
                    s0 = k if s0 is None else min(s0, k)
                paths = "".join(
                    f'\n\t\t\t\t(path "/{ROOT_UUID}/{s.uuid}"\n\t\t\t\t\t(reference "{renumber(ref, s.sid)}")'
                    f'\n\t\t\t\t\t(unit {unit})\n\t\t\t\t)' for s in insts)
                new_inst = f'\n\t\t(instances\n\t\t\t(project "{PROJECT}"{paths}\n\t\t\t)\n\t\t)'
                blk = blk[:s0] + new_inst + blk[s0:]
                out[-1:] = blk.split("\n")
                count += 1
                i = j + 1
                continue
            i += 1
        path.write_text("\n".join(out), encoding="utf-8")
        print(f"{file}: {count} symbols x {len(insts)} instances")


if __name__ == "__main__":
    sheets = build_sheets()
    write_top(sheets)
    write_instances(sheets)
