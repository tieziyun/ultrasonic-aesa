"""Build the reusable cell sheets and the ADC / header sheets from script.

Every block that repeats on the board is one sheet, instantiated N times from the top
sheet by scripts/gen_top.py, so there is exactly one sheet to change per block:

  channel.kicad_sch   x64  (drawn with the MCP server, not rebuilt here)
  adc.kicad_sch       x8   AD7606C-16, software mode, serial 2 DOUT, 33 R source resistors
  tx_group.kicad_sch  x16  one 74HCT595 + two UCC27524: drives 4 elements
  en_buf.kicad_sch    x4   one 74AHCT1G125: TX_EN_FPGA -> TX_ENn (one per 8 drivers)
  clk_buf.kicad_sch   x7   one 74LVC2G34, 100 k pull-down on IN, 33 R per output (<= 4 loads)
  mic.kicad_sch       x8   one SPH0641LU4H-1 + bypass cap + 33 R on DATA
  headers.kicad_sch   x1   AXU3EGB J15/J16 headers, idle pulls (one-off)

Parts are placed with the KiCad MCP server's handlers in-process (no 30 s timeout). Each
pin then gets a 2.54 mm wire stub ending in a power symbol, a label (local, global or
hierarchical) or a no-connect; `tie` joins a run of pins on one side with a short bus.

  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe scripts\\build_cells.py [sheet ...]

Afterwards run scripts/gen_top.py, scripts/sch_lcsc.py kicad/*.kicad_sch and kicad-cli ERC.
"""

import logging
import os
import shutil
from collections import Counter
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KDIR = REPO / "kicad"
MCP_PY = r"C:\GitHub\tools\KiCAD-MCP-Server\python"
KICAD_CLI = r"C:\Users\Tiezi\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe"

sys.path.insert(0, MCP_PY)
_cwd = os.getcwd()
os.chdir(MCP_PY)
logging.disable(logging.WARNING)
from kicad_interface import KiCADInterface  # noqa: E402
from commands.pin_locator import PinLocator  # noqa: E402
os.chdir(_cwd)

K = KiCADInterface()
NS = uuid.UUID("6f1e3a52-0000-4000-8000-0000000000c3")

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"

POWER_SYM = {"GND": "power:GND", "+3V3": "power:+3V3", "+5V": "power:+5V", "+5VA": "power:+5VA",
             "-5VA": "power:-5VA", "+6V5": "power:+6V", "-6V5": "power:-6V", "VIN": "power:+12V",
             "VDRV": "power:+15V", "3V3_MIC": "power:+3V3"}
DATE, REV = "2026-10-20", "2"
# Rotation that points a power symbol away from the pin, by outward pin angle.
GND_ROT = {270: 0, 90: 180, 0: 90, 180: 270}
RAIL_ROT = {90: 0, 270: 180, 180: 90, 0: 270}
STEP = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}


def mcp(cmd, **params):
    r = K.handle_command(cmd, params)
    if isinstance(r, dict) and r.get("success") is False:
        raise RuntimeError(f"{cmd}: {r}")
    return r


def title_block(paper, title):
    """(paper ...) line followed by a filled title block."""
    lines = [f'(paper "{paper}")', "\t(title_block", f'\t\t(title "{title}")', f'\t\t(date "{DATE}")',
             f'\t\t(rev "{REV}")', '\t\t(company "Hybrid 8x8 ultrasonic AESA")', "\t)"]
    return "\n".join(lines)


def upgrade(path):
    subprocess.run([KICAD_CLI, "sch", "upgrade", "--force", str(path)], check=True, capture_output=True)


def f(v):
    return f"{round(v, 2):g}"


class Sheet:
    def __init__(self, name, paper="A4", title=""):
        self.name, self.paper, self.title = name, paper, title
        self.path = KDIR / f"{name}.kicad_sch"
        self.parts, self.conns, self.ties, self.wires, self.notes = [], [], [], [], []
        self.dnp = []  # references marked Do Not Populate
        self.routes = []  # three-segment wires
        self.polys = []  # wired polylines through pins and points
        self.at_syms = []  # power symbols placed exactly on a pin or point
        self._segs, self._conn_pts = [], []
        self.field_ov = []  # sch_fields overrides "REF=dx,dy,dx2,dy2"
        self._u = 0

    # ---- description ----------------------------------------------------------------
    def part(self, sym, ref, x, y, value, fp="", rot=0, unit=1):
        self.parts.append({"symbol": sym, "reference": ref, "value": value, "footprint": fp,
                           "position": {"x": x, "y": y}, "rotation": rot, "unit": unit})

    def pwr(self, net, *pins, sym_dir=None, hide=False):
        """Power symbol on each pin; sym_dir points it another way than along the stub."""
        for p in pins:
            self.conns.append(("pwr", net, p, (sym_dir, hide)))

    def lbl(self, net, *pins):
        for p in pins:
            self.conns.append(("label", net, p, None))

    def glb(self, net, *pins, shape="passive"):
        for p in pins:
            self.conns.append(("global_label", net, p, shape))

    def hier(self, net, *pins, shape="input"):
        for p in pins:
            self.conns.append(("hierarchical_label", net, p, shape))

    def nc(self, *pins):
        for p in pins:
            self.conns.append(("nc", None, p, None))

    def tie(self, net, *pins, end="first"):
        """Pins in one column (or row): stubs, a bus along their ends, one power symbol."""
        self.ties.append((net, pins, end))

    def wire(self, a, b):
        """Direct wire from pin a to pin b (straight, or horizontal then vertical)."""
        self.wires.append((a, b))

    def route(self, a, b, xmid):
        """Wire from pin a to pin b: horizontal to xmid, vertical, horizontal into b."""
        self.routes.append((a, b, xmid))

    def poly(self, *pts):
        """Wire through pins ("U1.3") and points ((x, y); None takes the previous point's value)."""
        self.polys.append(pts)

    def sym(self, net, *pts):
        """Power symbol right on a pin or point: GND pointing down, rails pointing up."""
        for p in pts:
            self.at_syms.append((net, p))

    def note(self, text, x, y, size=1.8):
        self.notes.append((text, x, y, size))

    # ---- emission ---------------------------------------------------------------------
    def uid(self):
        self._u += 1
        return str(uuid.uuid5(NS, f"{self.name}/{self._u}"))

    def s_wire(self, a, b):
        self._segs.append((tuple(a), tuple(b)))
        return (f'\t(wire\n\t\t(pts\n\t\t\t(xy {f(a[0])} {f(a[1])}) (xy {f(b[0])} {f(b[1])})\n\t\t)\n'
                f'\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "{self.uid()}")\n\t)\n')

    def s_label(self, kind, net, p, ang, shape):
        if kind == "label":
            just = "right bottom" if ang in (180, 270) else "left bottom"
            head = f'\t(label "{net}"\n'
        else:
            just = "right" if ang in (180, 270) else "left"
            head = f'\t({kind} "{net}"\n\t\t(shape {shape})\n'
        return (f'{head}\t\t(at {f(p[0])} {f(p[1])} {ang})\n'
                f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n'
                f'\t\t(uuid "{self.uid()}")\n\t)\n')

    def junctions(self):
        """Junction dots where three or more connections meet (wire ends, wire interiors, pins)."""
        def on(p, a, b):
            if a[0] == b[0] == p[0]:
                return min(a[1], b[1]) < p[1] < max(a[1], b[1])
            if a[1] == b[1] == p[1]:
                return min(a[0], b[0]) < p[0] < max(a[0], b[0])
            return False
        ends = Counter(e for s in self._segs for e in s)
        pins = Counter(self._conn_pts)
        out = []
        for p in sorted(set(ends)):
            n = ends[p] + pins[p] + 2 * sum(on(p, a, b) for a, b in self._segs)
            if n >= 3:
                out.append(f'\t(junction\n\t\t(at {f(p[0])} {f(p[1])})\n\t\t(diameter 0)\n\t\t(color 0 0 0 0)\n'
                           f'\t\t(uuid "{self.uid()}")\n\t)\n')
        return out

    def s_nc(self, p):
        return f'\t(no_connect\n\t\t(at {f(p[0])} {f(p[1])})\n\t\t(uuid "{self.uid()}")\n\t)\n'

    def s_text(self, text, x, y, size):
        return (f'\t(text "{text}"\n\t\t(exclude_from_sim no)\n\t\t(at {f(x)} {f(y)} 0)\n'
                f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {size} {size})\n\t\t\t\t(bold yes)\n\t\t\t)\n'
                f'\t\t\t(justify left bottom)\n\t\t)\n\t\t(uuid "{self.uid()}")\n\t)\n')

    def build(self):
        if self.path.exists():
            shutil.copy(self.path, self.path.with_suffix(".kicad_sch.bak"))
            self.path.unlink()
        mcp("create_schematic", name=self.name, path=str(KDIR))
        tb = title_block(self.paper, self.title or self.name)
        t = self.path.read_text(encoding="utf-8").replace('(paper "A4")', tb)
        self.path.write_text(t, encoding="utf-8")
        mcp("batch_add_components", schematicPath=str(self.path), components=self.parts,
            auto_position_fields=True)

        loc = PinLocator()

        def pin(p):
            ref, num = p.split(".")
            xy = loc.get_pin_location(self.path, ref, num)
            ang = loc.get_pin_angle(self.path, ref, num)
            if xy is None or ang is None:
                raise RuntimeError(f"{self.name}: pin {p} not found")
            return (round(xy[0], 2), round(xy[1], 2)), int(round(ang / 90.0) * 90) % 360

        out, syms, seen = [], [], {}
        npwr = [0]

        hidden = set()

        def power(net, at, ang, hide=False):
            # A rail symbol only reads well pointing up: elsewhere use a labelled flag with the
            # rail name (a global label joins the power net). A sideways or upturned GND keeps
            # its unmistakable shape but hides the text, which would overprint neighbours.
            if net != "GND" and ang != 90:
                out.append(self.s_label("global_label", net, at, ang, "passive"))
                return
            if net == "GND" and ang != 270:
                hide = True
            npwr[0] += 1
            rot = (GND_ROT if net == "GND" else RAIL_ROT)[ang]
            if hide:  # by position: the MCP renumbers power symbols too
                hidden.add((round(at[0], 2), round(at[1], 2)))
            syms.append({"symbol": POWER_SYM[net], "reference": f"#PWR{npwr[0]:03d}", "value": net,
                         "position": {"x": at[0], "y": at[1]}, "rotation": rot})

        for kind, net, p, shape in self.conns:
            xy, ang = pin(p)
            if xy in seen:  # stacked pins (e.g. the AD7606's AGND): one connection is enough
                if seen[xy] != (kind, net):
                    raise RuntimeError(f"{self.name}: {p} stacked with a different net")
                continue
            seen[xy] = (kind, net)
            if kind == "nc":
                out.append(self.s_nc(xy))
                continue
            d = STEP[ang]
            end = (round(xy[0] + 2.54 * d[0], 2), round(xy[1] + 2.54 * d[1], 2))
            out.append(self.s_wire(xy, end))
            if kind == "pwr":
                sym_dir, hide = shape
                power(net, end, ang if sym_dir is None else sym_dir, hide)
            else:
                out.append(self.s_label(kind, net, end, ang, shape))

        for net, pins, end in self.ties:
            ends, angs = [], set()
            for p in pins:
                xy, ang = pin(p)
                if xy in seen:
                    continue
                seen[xy] = ("pwr", net)
                d = STEP[ang]
                e = (round(xy[0] + 2.54 * d[0], 2), round(xy[1] + 2.54 * d[1], 2))
                out.append(self.s_wire(xy, e))
                ends.append(e)
                angs.add(ang)
            assert len(angs) == 1, f"{self.name}: tie {net} spans several sides"
            ang = angs.pop()
            ends.sort(key=lambda e: (e[1], e[0]))
            for a, b in zip(ends, ends[1:]):
                out.append(self.s_wire(a, b))
            # One power symbol at the bus end (top/left end for a rail, bottom/right for GND).
            power(net, ends[0] if net != "GND" else ends[-1], ang)

        for a, b in self.wires:
            pa, _ = pin(a)
            pb, _ = pin(b)
            if pa[0] == pb[0] or pa[1] == pb[1]:
                out.append(self.s_wire(pa, pb))
            else:
                mid = (pb[0], pa[1])
                out.append(self.s_wire(pa, mid))
                out.append(self.s_wire(mid, pb))

        for a, b, xm in self.routes:
            pa, _ = pin(a)
            pb, _ = pin(b)
            pts = [pa, (xm, pa[1]), (xm, pb[1]), pb]
            for u, v in zip(pts, pts[1:]):
                if u != v:
                    out.append(self.s_wire(u, v))

        def resolve(p, prev):
            if isinstance(p, str):
                xy = pin(p)[0]
                self._conn_pts.append(xy)
                return xy
            x, y = p
            return (round(prev[0] if x is None else x, 2), round(prev[1] if y is None else y, 2))

        for pts in self.polys:
            prev = None
            for p in pts:
                q = resolve(p, prev)
                if prev is not None and q != prev:
                    out.append(self.s_wire(prev, q))
                prev = q

        for net, p in self.at_syms:
            q = resolve(p, None)
            self._conn_pts.append(q)
            power(net, q, 270 if net == "GND" else 90)

        out.extend(self.junctions())

        for text, x, y, size in self.notes:
            out.append(self.s_text(text, x, y, size))

        if syms:
            mcp("batch_add_components", schematicPath=str(self.path), components=syms,
                auto_position_fields=False)
        upgrade(self.path)  # KiCad re-save: tab indentation, which the other scripts expect
        t = self.path.read_text(encoding="utf-8")
        k = t.rindex("\t(sheet_instances")
        t = t[:k] + "".join(out) + t[k:]
        self.path.write_text(t, encoding="utf-8")
        fix_refs(self.path, self.parts, self.dnp)
        upgrade(self.path)
        fix_power_fields(self.path, hidden)
        subprocess.run([sys.executable, str(REPO / "scripts" / "sch_fields.py"), str(self.path), *self.field_ov],
                       check=True, capture_output=True)
        upgrade(self.path)
        print(f"{self.name}: {len(self.parts)} parts, {len(self.conns)} pin connections, "
              f"{len(syms)} power symbols, {self.path.stat().st_size} bytes")


def fix_refs(path, parts, dnp=()):
    """Restore the intended references (the MCP renumbers parts of sheets in the hierarchy)
    and set the DNP attribute on the parts listed in dnp."""
    want = {(p["symbol"], round(p["position"]["x"], 2), round(p["position"]["y"], 2)): p["reference"]
            for p in parts}
    lines = path.read_text(encoding="utf-8").split("\n")
    for i, l in enumerate(lines):
        if l == "\t(symbol" and lines[i + 1].startswith("\t\t(lib_id "):
            lib = lines[i + 1].split('"')[1]
            x, y = (round(float(v), 2) for v in lines[i + 2].strip()[4:-1].split()[:2])
            ref = want.get((lib, x, y))
            if ref:
                j = i
                while not lines[j].startswith('\t\t(property "Reference"'):
                    j += 1
                lines[j] = f'\t\t(property "Reference" "{ref}"'
                if ref in dnp:
                    for n in range(i, j):
                        if lines[n] == "\t\t(dnp no)":
                            lines[n] = "\t\t(dnp yes)"
                k = j
                while lines[k] != "\t)":
                    if "(reference " in lines[k]:
                        lines[k] = lines[k][:lines[k].index("(")] + f'(reference "{ref}")'
                    k += 1
    path.write_text("\n".join(lines), encoding="utf-8")


def fix_power_fields(path, hidden=()):
    """Put each power symbol's net name just past its tip (the MCP leaves it on the body)."""
    lines = path.read_text(encoding="utf-8").split("\n")
    i = 0
    while i < len(lines):
        if lines[i] == "\t(symbol" and lines[i + 1] == '\t\t(lib_id "power:PWR_FLAG")':
            j = i + 1  # flags: hide the #FLG reference, as KiCad does
            while not lines[j].startswith('\t\t(property "Reference"'):
                j += 1
            if lines[j + 2].strip() != "(hide yes)":
                lines.insert(j + 2, "\t\t\t(hide yes)")
            i = j
        elif lines[i] == "\t(symbol" and lines[i + 1].startswith('\t\t(lib_id "power:') and "PWR_FLAG" not in lines[i + 1]:
            x, y, rot = (float(v) for v in lines[i + 2].strip()[4:-1].split())
            gnd = "power:GND" in lines[i + 1]
            # outward direction of the symbol body
            d = {0: (0, 1), 180: (0, -1), 90: (1, 0), 270: (-1, 0)}[int(rot)] if gnd else \
                {0: (0, -1), 180: (0, 1), 90: (-1, 0), 270: (1, 0)}[int(rot)]
            fx, fy = x + 4.5 * d[0], y + 4.5 * d[1] + (0.5 if d[1] > 0 else 0)
            just = "left" if d[0] > 0 else "right" if d[0] < 0 else None
            j = i + 1
            while not lines[j].startswith('\t\t(property "Value"'):
                j += 1
            lines[j + 1] = f"\t\t\t(at {f(fx)} {f(fy)} 0)"
            if (round(x, 2), round(y, 2)) in hidden and lines[j + 2].strip() != "(hide yes)":
                lines.insert(j + 2, "\t\t\t(hide yes)")
            k = j + 1
            while lines[k] != "\t\t)":
                if lines[k].strip().startswith("(justify"):
                    lines[k] = lines[k][:lines[k].index("(")] + f"(justify {just})" if just else ""
                k += 1
            if just and not any(l.strip().startswith("(justify") for l in lines[j:k]):
                e = next(n for n in range(j, k) if lines[n].strip() == "(effects")
                lines.insert(e + 1, f"\t\t\t\t(justify {just})")
            i = j
        i += 1
    path.write_text("\n".join(l for l in lines if l != ""), encoding="utf-8")


def set_pin_types(path, lib_id, types):
    """Change pin electrical types in a sheet's embedded symbol (local deviation, see brief)."""
    lines = path.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.strip() == f'(symbol "{lib_id}"')
    depth, j = 0, start
    while True:
        depth += lines[j].count("(") - lines[j].count(")")
        if depth == 0:
            break
        j += 1
    n = 0
    for i in range(start, j):
        s = lines[i].strip()
        if s.startswith("(pin "):
            k = i
            while '(number "' not in lines[k]:
                k += 1
            num = lines[k].split('(number "')[1].split('"')[0]
            if num in types:
                lead = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
                tok = lines[i].strip().split(" ")
                tok[1] = types[num]
                lines[i] = lead + " ".join(tok)
                n += 1
    path.write_text("\n".join(lines), encoding="utf-8")
    return n


# =====================================================================================
# ADC: AD7606C-16, software mode (OS2..0 high), serial, 2 DOUT lines, internal reference
# =====================================================================================
def adc():
    s = Sheet("adc", "A3", "ADC (x8): AD7606C-16, software mode, serial 2 DOUT")
    s.note("ADC: AD7606C-16 (pin-compatible with AD7606B; symbol keeps AD7606 pin names)", 30.48, 25.4, 2.5)
    s.note("Software mode: OS0-OS2 high. FPGA writes registers over SDI (pin 29, DB11/SDI) at boot:", 30.48, 33.02)
    s.note("RANGE_CHx = 0x1 (+-5 V), BANDWIDTH = 0xFF (high bandwidth on all 8 channels), "
           "CONFIG DOUT_FORMAT = 2 lines.", 30.48, 38.1)
    s.note("Pin 10 is WR on the B/C parts (parallel register writes only): held high. "
           "RANGE pin is ignored in software mode.", 30.48, 43.18)
    s.note("33 R source resistors on DOUTA/DOUTB/BUSY (ribbon to the FPGA); BUSY is used from ADC1 only.",
           30.48, 48.26)
    s.note("DB9/DB10 are tied to GND: never set DOUT_FORMAT to 4 lines. After power-up the FPGA pulses "
           "ADC_RESET (full reset), then writes the registers.", 30.48, 53.34)
    s.part("Analog_ADC:AD7606", "U1", 190.5, 139.7, "AD7606C-16BSTZ", "Package_QFP:LQFP-64_10x10mm_P0.5mm")
    for i in range(8):
        s.hier(f"IN{i + 1}", f"U1.{49 + 2 * i}", shape="input")
        s.pwr("GND", f"U1.{50 + 2 * i}")
    s.tie("+5VA", "U1.1", "U1.37", "U1.38", "U1.48")
    s.pwr("+3V3", "U1.23")
    s.pwr("GND", "U1.2", "U1.43")
    # Right side, strapping
    s.tie("+3V3", "U1.34", "U1.6", "U1.7", "U1.8", "U1.3", "U1.4", "U1.5")  # REFSEL, SER, /STBY, RANGE, OS0-2
    s.pwr("+3V3", "U1.10")                                                 # WR high (inactive)
    s.hier("CONVST", "U1.9", shape="input")
    s.glb("ADC_CS_N", "U1.13", shape="input")
    s.hier("SCLK", "U1.12", shape="input")
    s.glb("ADC_RESET", "U1.11", shape="input")
    s.nc("U1.15")
    s.glb("ADC_SDI", "U1.29", shape="input")
    s.tie("GND", "U1.16", "U1.17", "U1.18", "U1.19", "U1.20", "U1.21", "U1.22")  # DB0-DB6
    s.tie("GND", "U1.27", "U1.28")                                              # DB9/DOUTC, DB10/DOUTD
    s.tie("GND", "U1.30", "U1.31", "U1.32", "U1.33")                            # DB12-DB15
    # Output source resistors
    for ref, y, upin, port in (("R1", 109.22, "24", "DOUTA"), ("R2", 127.0, "25", "DOUTB"),
                               ("R3", 152.4, "14", "BUSY")):
        s.part("Device:R", ref, 236.22, y, "33 1%", R0603, rot=90)
        s.wire(f"U1.{upin}", f"{ref}.1")
        s.hier(port, f"{ref}.2", shape="output")
    # Reference and regulator caps (left side, labels)
    s.lbl("REGCAP1", "U1.36")
    s.lbl("REGCAP2", "U1.39")
    s.lbl("REFIO", "U1.42")
    s.lbl("REFCAP", "U1.44", "U1.45")
    caps = [("C1", "100n 16V X7R", C0603, "+5VA"), ("C2", "100n 16V X7R", C0603, "+5VA"),
            ("C3", "100n 16V X7R", C0603, "+5VA"), ("C4", "100n 16V X7R", C0603, "+5VA"),
            ("C5", "10u 16V X5R", C0805, "+5VA"), ("C6", "100n 16V X7R", C0603, "+3V3"),
            ("C7", "1u 16V X7R", C0603, "REGCAP1"), ("C8", "1u 16V X7R", C0603, "REGCAP2"),
            ("C9", "10u 16V X7R", C1206, "REFCAP"), ("C10", "100n 16V X7R", C0603, "REFIO")]
    s.note("Decoupling (place at the pins): four 100 nF on the AVCC pins + 10 uF bulk, 100 nF on VDRIVE; "
           "REGCAP 1 uF x2, REFCAP 10 uF, REFIN/OUT 100 nF per datasheet", 30.48, 205.74)
    for k, (ref, val, fp, net) in enumerate(caps):
        x = 38.1 + 20.32 * k
        s.part("Device:C", ref, x, 228.6, val, fp)
        (s.pwr if net in POWER_SYM else s.lbl)(net, f"{ref}.1")
        s.pwr("GND", f"{ref}.2")
    s.build()
    # Library marks DB pins as outputs; here they are tied to GND (serial mode) or driven (SDI).
    types = {str(n): "passive" for n in (16, 17, 18, 19, 20, 21, 22, 27, 28, 30, 31, 32, 33)}
    types["29"] = "input"
    print("  pin types changed:", set_pin_types(s.path, "Analog_ADC:AD7606", types))


# =====================================================================================
# TX group: one 74HCT595 (4 of its 8 bits) feeding two UCC27524 -> 4 elements
# =====================================================================================
def tx_group():
    s = Sheet("tx_group", "A4", "TX group (x16): 74HCT595 + 2 x UCC27524, 4 elements")
    s.note("TX group: 4 elements. 74HCT595 QA-QD -> UCC27524 inputs; QE-QH unused (16 data lines, 10 MHz SRCLK)",
           25.4, 25.4)
    s.note("EN from one 74AHCT1G125 per 8 drivers (en_buf). SCK/LATCH from a clk_buf output (<= 4 loads).",
           25.4, 30.48)
    s.part("74xx:74HC595", "U1", 88.9, 101.6, "74HCT595D", "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm")
    s.hier("SDI", "U1.14", shape="input")
    s.hier("SCK", "U1.11", shape="input")
    s.hier("LATCH", "U1.12", shape="input")
    s.glb("TX_CLR_N", "U1.10", shape="input")
    s.glb("TX_OE_N", "U1.13", shape="input")
    s.pwr("+5V", "U1.16")
    s.pwr("GND", "U1.8")
    # QA..QD wired straight to the driver inputs (QA->U2 INA, QB->U2 INB, QC->U3 INA, QD->U3 INB)
    for q, dst, xm in (("15", "U2.2", 139.7), ("1", "U2.4", 142.24), ("2", "U3.2", 142.24), ("3", "U3.4", 139.7)):
        s.route(f"U1.{q}", dst, xm)
    s.nc("U1.4", "U1.5", "U1.6", "U1.7", "U1.9")
    for ref, y, a, b in (("U2", 76.2, 1, 2), ("U3", 129.54, 3, 4)):
        s.part("Driver_FET:UCC27524D", ref, 172.72, y, "UCC27524DR", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
        s.hier("EN", f"{ref}.1", f"{ref}.8", shape="input")
        s.hier(f"DRV{a}", f"{ref}.7", shape="output")
        s.hier(f"DRV{b}", f"{ref}.5", shape="output")
        s.pwr("VDRV", f"{ref}.6")
        s.pwr("GND", f"{ref}.3")
    caps = [("C1", "100n 16V X7R", C0603, "+5V", 55.88, 101.6),
            ("C2", "1u 50V X7R", C0805, "VDRV", 210.82, 76.2), ("C3", "100n 50V X7R", C0603, "VDRV", 226.06, 76.2),
            ("C4", "1u 50V X7R", C0805, "VDRV", 210.82, 129.54), ("C5", "100n 50V X7R", C0603, "VDRV", 226.06, 129.54)]
    for ref, val, fp, net, x, y in caps:
        s.part("Device:C", ref, x, y, val, fp)
        s.pwr(net, f"{ref}.1")
        s.pwr("GND", f"{ref}.2")
    s.build()


# =====================================================================================
# Enable buffer: TX_EN_FPGA (3.3 V) -> TX_ENn (5 V) for 8 drivers (16 EN pins)
# =====================================================================================
def en_buf():
    s = Sheet("en_buf", "A4", "TX enable buffer (x4): 74AHCT1G125")
    s.field_ov.append("U1=7.62,-7.62,7.62,-5.08")
    s.note("TX enable buffer: 74AHCT1G125, 3.3 V in (TTL threshold), 5 V out to 8 UCC27524 (16 EN pins)",
           25.4, 25.4)
    s.part("74xGxx:74LVC1G125", "U1", 127.0, 101.6, "74AHCT1G125", "Package_TO_SOT_SMD:SOT-23-5")
    s.hier("IN", "U1.2", shape="input")
    s.hier("OUT", "U1.4", shape="output")
    s.pwr("GND", "U1.1", "U1.3")
    s.pwr("+5V", "U1.5")
    s.part("Device:C", "C1", 157.48, 101.6, "100n 16V X7R", C0603)
    s.pwr("+5V", "C1.1")
    s.pwr("GND", "C1.2")
    s.build()


# =====================================================================================
# Clock buffer: one input -> two 33 R series-terminated outputs, <= 4 loads each
# =====================================================================================
def clk_buf():
    s = Sheet("clk_buf", "A4", "Clock buffer (x7): 74LVC2G34, 33 R outputs")
    s.note("Clock fan-out: 74LVC2G34 at the header, both gates on IN, 33 R series termination per output",
           25.4, 25.4)
    s.note("Each output drives at most 4 loads (series termination works for a lumped load at the far end).",
           25.4, 30.48)
    s.note("100 k pull-down keeps the clock low while the FPGA is unconfigured.", 25.4, 35.56)
    fp = "Package_TO_SOT_SMD:SOT-363_SC-70-6"
    s.part("74xGxx:74LVC2G34", "U1", 127.0, 76.2, "74LVC2G34", fp, unit=1)
    s.part("74xGxx:74LVC2G34", "U1", 127.0, 101.6, "74LVC2G34", fp, unit=2)
    s.part("74xGxx:74LVC2G34", "U1", 127.0, 139.7, "74LVC2G34", fp, unit=3)
    s.hier("IN", "U1.1", "U1.3", shape="input")
    s.pwr("+3V3", "U1.5")
    s.pwr("GND", "U1.2")
    for ref, y, upin, port in (("R1", 76.2, "6", "OUT1"), ("R2", 101.6, "4", "OUT2")):
        s.part("Device:R", ref, 157.48, y, "33 1%", R0603, rot=90)
        s.wire(f"U1.{upin}", f"{ref}.1")
        s.hier(port, f"{ref}.2", shape="output")
    s.part("Device:R", "R3", 96.52, 116.84, "100k 1%", R0603)
    s.hier("IN", "R3.1", shape="input")
    s.pwr("GND", "R3.2")
    s.part("Device:C", "C1", 152.4, 139.7, "100n 16V X7R", C0603)
    s.pwr("+3V3", "C1.1")
    s.pwr("GND", "C1.2")
    s.build()


# =====================================================================================
# MEMS mic: SPH0641LU4H-1, PDM. SEL from the parent (3V3_MIC or GND) picks the clock edge.
# =====================================================================================
def mic():
    s = Sheet("mic", "A4", "MEMS mic (x8): SPH0641LU4H-1")
    s.note("MEMS mic SPH0641LU4H-1 (PDM). Two mics share DATA: SEL high on one, low on the other.",
           25.4, 25.4)
    s.note("Mounted on the back, sound port through a board hole. 3V3_MIC is the ferrite-filtered rail (power sheet).",
           25.4, 30.48)
    s.part("Sensor_Audio:SPH0641LU4H-1", "MK1", 127.0, 101.6, "SPH0641LU4H-1",
           "Sensor_Audio:Knowles_LGA-5_3.5x2.65mm")
    s.hier("CLK", "MK1.4", shape="input")
    s.hier("SEL", "MK1.2", shape="input")
    s.pwr("3V3_MIC", "MK1.5")
    s.pwr("GND", "MK1.3")
    s.part("Device:R", "R1", 157.48, 104.14, "33 1%", R0603, rot=90)
    s.wire("MK1.1", "R1.1")
    s.hier("DATA", "R1.2", shape="output")
    s.part("Device:C", "C1", 101.6, 101.6, "100n 50V C0G", C0805)
    s.pwr("3V3_MIC", "C1.1")
    s.pwr("GND", "C1.2")
    s.build()


# =====================================================================================
# AXU3EGB headers (one-off). J1 = EX_IO1 (board J15, banks 43/44), J2 = EX_IO2 (J16, 45/46).
# Board pins: GND 1, 37, 38; +5V 2; +3.3V 39, 40 (left open: we have our own supply).
# Every clock has GND on both neighbouring ribbon conductors (spare FPGA pins, left as inputs).
# =====================================================================================
from pinmap import J1_MAP, J2_MAP  # noqa: E402  (shared with check_design.py)


def headers():
    s = Sheet("headers", "A3", "AXU3EGB headers J15/J16, idle pulls")
    s.note("AXU3EGB expansion headers (40-pin IDC, 2.54 mm). J1 = board J15 (TX, mics), "
           "J2 = board J16 (ADCs).", 30.48, 25.4, 2.5)
    s.note("Pin 2 (+5V) and 39/40 (+3.3V) of the FPGA board are left open. Clocks sit between GND "
           "conductors (pins 31-37).", 30.48, 33.02)
    s.note("Spare FPGA pins tied to GND must stay unused (inputs) in the XDC.", 30.48, 38.1)
    for ref, x, val, mp in (("J1", 101.6, "EX_IO1 (AXU3EGB J15)", J1_MAP),
                            ("J2", 254.0, "EX_IO2 (AXU3EGB J16)", J2_MAP)):
        s.part("Connector_Generic:Conn_02x20_Odd_Even", ref, x, 139.7, val,
               "Connector_IDC:IDC-Header_2x20_P2.54mm_Vertical")
        for pn, net in mp.items():
            p = f"{ref}.{pn}"
            if net is None:
                s.nc(p)
            elif net == "GND":
                s.pwr("GND", p, hide=True)
            else:
                s.glb(net, p, shape="bidirectional" if net.startswith(("ADC_D", "ADC_B", "MIC_D")) else "input")
    pulls = [("R1", "TX_EN_FPGA", "GND", "TX drivers off while the FPGA configures"),
             ("R2", "TX_OE_N", "+3V3", "595 outputs off"),
             ("R3", "TX_CLR_N", "GND", "595 cleared"),
             ("R4", "ADC_CS_N", "+3V3", "ADCs deselected"),
             ("R5", "ADC_RESET", "GND", "ADCs idle until the FPGA pulses a full reset"),
             ("R11", "ADC_SDI", "GND", "register line idle")]
    s.note("Idle pulls (FPGA unconfigured). TX_SCK, TX_LATCH, ADC_SCLK, ADC_CONVST and MIC_CLK_IN are "
           "pulled down in their clk_buf.", 30.48, 236.22)
    # Ribbon end (sim/clock_si_in.cir, clock_si_drive.cir): with the FPGA at DRIVE 8 (~40 ohm)
    # the unterminated 100-ohm ribbon rings to 3.7-4.0 V / -0.4..-0.7 V at the buffer input;
    # DRIVE 4 (~80 ohm, near source match) gives 3.3 V / 0 V on every line with no parts.
    # Fallback, not fitted: 100 R + 47 pF to GND per clock line (do not combine with DRIVE 4).
    terms = ["TX_SCK", "TX_LATCH", "ADC_SCLK", "ADC_CONVST", "MIC_CLK_IN"]
    s.note("FPGA ribbon outputs: DRIVE 4, SLEW SLOW (near source-matched to the 100-ohm ribbon). "
           "DNP fallback: RC end termination 100 R + 47 pF per clock, fit only with DRIVE 8.",
           30.48, 176.53)
    for k, net in enumerate(terms):
        x = 50.8 + 40.64 * k
        s.part("Device:R", f"R{6 + k}", x, 205.74, "100 1% DNP", R0603)
        s.part("Device:C", f"C{1 + k}", x, 218.44, "47p 50V C0G DNP", C0603)
        s.dnp += [f"R{6 + k}", f"C{1 + k}"]
        s.glb(net, f"R{6 + k}.1", shape="input")
        s.wire(f"R{6 + k}.2", f"C{1 + k}.1")
        s.pwr("GND", f"C{1 + k}.2")
    for k, (ref, net, rail, _why) in enumerate(pulls):
        x = 50.8 + 40.64 * k
        s.part("Device:R", ref, x, 256.54, "10k 1%", R0603)
        if rail == "GND":
            s.glb(net, f"{ref}.1", shape="input")
            s.pwr("GND", f"{ref}.2")
        else:
            s.pwr(rail, f"{ref}.1")
            s.glb(net, f"{ref}.2", shape="input")
    s.build()


# =====================================================================================
# Power (one-off). 12 V jack -> +5V buck -> +-6.5 V -> low-noise +-5VA; VDRV boost; +3V3;
# 3V3_MIC. Every rail: regulator node *_REG -> bridged solder jumper (or J2 shunt for VDRV)
# -> rail with test point, LED (not on the analog LDO outputs) and PWR_FLAG.
# =====================================================================================
R1206 = "Resistor_SMD:R_1206_3216Metric"
C1210 = "Capacitor_SMD:C_1210_3225Metric"
LEDFP = "LED_SMD:LED_0603_1608Metric"
TPFP = "TestPoint:TestPoint_Pad_D1.5mm"
JPFP = "Jumper:SolderJumper-2_P1.3mm_Bridged_RoundedPad1.0x1.5mm"
L6045 = "Inductor_SMD:L_Bourns_SRN6045TA"
L8040 = "Inductor_SMD:L_Bourns_SRN8040TA"
SMA = "Diode_SMD:D_SMA"
PITCH = 20.32


def power():
    """Power sheet, drawn conventionally: each regulator with its parts wired around it.

    Rails run as wires through the parts that hang off them (input caps, dividers, output caps,
    jumper, LED, test point, flag) and end in a power symbol at the edge of the section. Pins that
    only tie to a rail (enables, mode pins, ANY-OUT pins, exposed pads) use power symbols/flags.
    """
    s = Sheet("power", "A2", "Power: 12 V in, +5V, +-6V5, +-5VA, VDRV, +3V3, 3V3_MIC")
    s.field_ov.append("--vertical")  # value text beside each upright part
    nflag = [0]
    SYM2 = {"C": "Device:C", "CP": "Device:C_Polarized", "R": "Device:R", "L": "Device:L",
            "FB": "Device:FerriteBead", "F": "Device:Polyfuse", "D": "Device:D_Schottky", "LED": "Device:LED"}

    def v(kind, ref, x, ytop, val, fp, k_top=False):
        """Upright part with its top pin at (x, ytop). Returns (top pin, bottom pin).
        Diodes/LEDs: anode on top, or cathode on top with k_top."""
        if kind in ("D", "LED"):
            s.part(SYM2[kind], ref, x, ytop + 3.81, val, fp, rot=270 if k_top else 90)
            s.field_ov.append(f"{ref}=12.7,-0.64,12.7,1.91")
            return (f"{ref}.1", f"{ref}.2") if k_top else (f"{ref}.2", f"{ref}.1")
        s.part(SYM2[kind], ref, x, ytop + 3.81, val, fp)
        return f"{ref}.1", f"{ref}.2"

    def h(kind, ref, xleft, y, val, fp, k_left=False):
        """Horizontal part with its left pin at (xleft, y). Returns (left pin, right pin).
        Diodes: anode on the left, or cathode on the left with k_left."""
        if kind == "D":
            s.part(SYM2[kind], ref, xleft + 3.81, y, val, fp, rot=0 if k_left else 180)
            return (f"{ref}.1", f"{ref}.2") if k_left else (f"{ref}.2", f"{ref}.1")
        s.part(SYM2[kind], ref, xleft + 3.81, y, val, fp, rot=90)
        return f"{ref}.1", f"{ref}.2"

    def tp(ref, x, y, net):
        s.part("Connector:TestPoint", ref, x, y, f"TP {net}", TPFP)
        return f"{ref}.1"

    def flag(x, y):
        nflag[0] += 1
        ref = f"#FLG{nflag[0]:02d}"
        s.part("power:PWR_FLAG", ref, x, y, "PWR_FLAG")
        return f"{ref}.1"

    def jp(ref, xa, y, rail):
        s.part("Jumper:SolderJumper_2_Bridged", ref, xa + 5.08, y, "cut to isolate", JPFP)
        return f"{ref}.1", f"{ref}.2"

    def led(rref, dref, x, y, rval, negative=False):
        """Rail indicator hanging from a rail point (x, y): resistor, then LED to GND."""
        rt, rb = v("R", rref, x, y, rval, R0603)
        dt, db = v("LED", dref, x, y + 7.62, "green", LEDFP, k_top=negative)
        s.sym("GND", db)
        return rt

    def caps_to_gnd(*pairs):
        for kind, ref, x, y, val, fp in pairs:
            top, bot = v(kind, ref, x, y, val, fp)
            s.sym("GND", bot)

    # ---- A. 12 V input ---------------------------------------------------------------------
    ox, oy = 20.32, 30.48
    ry = oy + 33.02
    s.note("A. 12 V INPUT: jack, polyfuse (16 V), SS54 reverse-polarity diode -> VIN", ox, oy, 2.2)
    s.note("Use a regulated 12 V adapter only (LM2731 VIN max 14 V). Jack 5.5 x 2.1 mm, >= 1.5 A.", ox, oy + 5.08)
    s.field_ov.append("J1=-7.62,-7.62,-7.62,-5.08")
    s.part("Connector:Barrel_Jack", "J1", ox + 10.16, ry + 2.54, "12 V in (5.5x2.1)",
           "Connector_BarrelJack:BarrelJack_CUI_PJ-063AH_Horizontal")
    s.poly("J1.2", (ox + 20.32, ry + 5.08))
    s.sym("GND", (ox + 20.32, ry + 5.08))
    f1, f2 = h("F", "F1", ox + 22.86, ry, "1812L200/16DR", "Fuse:Fuse_1812_4532Metric")
    da, dk = h("D", "D1", ox + 43.18, ry, "SS54", "Diode_SMD:D_SMC")
    caps_to_gnd(("CP", "C1", ox + 60.96, ry, "100u 25V", "Capacitor_SMD:CP_Elec_8x10"),
                ("C", "C2", ox + 73.66, ry, "100n 50V X7R", C0603))
    r1 = led("R1", "D2", ox + 86.36, ry, "10k 1%")
    s.poly("J1.1", f1)
    s.poly(f2, da)
    s.poly(dk, "C1.1", "C2.1", r1, tp("TP1", ox + 106.68, ry, "VIN"), flag(ox + 119.38, ry), (ox + 132.08, ry))
    s.sym("VIN", (ox + 132.08, ry))

    # ---- B. +5V: TPS54202 buck --------------------------------------------------------------
    ox, oy = 309.88, 30.48
    ry = oy + 30.48
    s.note("B. +5V: TPS54202 buck (TI reference design), 2 A", ox, oy, 2.2)
    s.note("Vout = 0.596 V x (1 + 100k/13.3k) = 5.08 V. EN UVLO about 7 V. Load about 1.4 A.", ox, oy + 5.08)
    s.field_ov.append("U1=-10.16,-10.16,-10.16,-7.62")
    s.part("Regulator_Switching:TPS54202DDC", "U1", ox + 58.42, ry + 2.54, "TPS54202DDCR",
           "Package_TO_SOT_SMD:SOT-23-6")
    caps_to_gnd(("C", "C3", ox + 12.7, ry, "10u 50V X7R 1210", C1210), ("C", "C4", ox + 22.86, ry, "100n 50V X7R", C0603))
    r2t, r2b = v("R", "R2", ox + 33.02, ry, "511k 1%", R0603)
    r3t, r3b = v("R", "R3", ox + 33.02, ry + 12.7, "105k 1%", R0603)
    s.poly(r2b, r3t)
    s.sym("GND", r3b)
    s.sym("VIN", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), "C3.1", "C4.1", r2t, "U1.3")
    s.poly(r2b, (ox + 40.64, None), (None, ry + 5.08), "U1.5")                  # EN
    s.sym("GND", "U1.1")
    c5t, c5b = v("C", "C5", ox + 76.2, ry - 7.62, "100n 16V X7R", C0603)       # BOOT
    s.poly("U1.6", (ox + 71.12, None), (None, ry - 7.62), c5t)
    s.poly(c5b, (None, ry + 2.54))
    l1a, l1b = h("L", "L1", ox + 81.28, ry + 2.54, "15u Isat>=3.5A", L8040)
    s.poly("U1.2", (ox + 76.2, ry + 2.54), l1a)                                 # SW
    yo = ry + 2.54
    r4t, r4b = v("R", "R4", ox + 96.52, yo, "100k 1%", R0603)
    c8t, c8b = v("C", "C8", ox + 109.22, yo, "75p 50V C0G", C0603)
    r5t, r5b = v("R", "R5", ox + 96.52, yo + 12.7, "13.3k 1%", R0603)
    s.poly(r4b, r5t)
    s.sym("GND", r5b)
    caps_to_gnd(("C", "C6", ox + 121.92, yo, "22u 16V X5R 1206", C1206), ("C", "C7", ox + 134.62, yo, "22u 16V X5R 1206", C1206))
    ja, jb = jp("JP1", ox + 144.78, yo, "+5V")
    s.poly(l1b, r4t, c8t, "C6.1", "C7.1", ja)
    s.poly(c8b, r4b)
    s.poly(r4b, (ox + 73.66, None), (None, ry + 5.08), "U1.4")                  # FB
    r6 = led("R6", "D3", ox + 165.1, yo, "2.2k 1%")
    s.poly(jb, r6, tp("TP2", ox + 185.42, yo, "+5V"), flag(ox + 198.12, yo), (ox + 210.82, yo))
    s.sym("+5V", (ox + 210.82, yo))

    # ---- C. +-6V5: TPS65131 ----------------------------------------------------------------------
    ox, oy = 20.32, 124.46
    cx, cy = ox + 76.2, oy + 50.8
    yi = cy - 27.94
    s.note("C. +-6V5: TPS65131RGER boost + inverter from +5V (feeds the analog LDOs only)", ox, oy, 2.2)
    s.note("Drawn with the pin-identical TPS65130 symbol: order TPS65131RGER (2 A switches). "
           "PSP/PSN low: fixed-frequency PWM.", ox, oy + 5.08)
    s.note("+6V5 = 1.213 V x (1 + 562k/130k) = 6.46 V.   -6V5 = -1.213 V x 649k/121k = -6.51 V.", ox, oy + 9.4)
    s.part("Regulator_Switching:TPS65130RGE", "U2", cx, cy, "TPS65131RGER",
           "Package_DFN_QFN:VQFN-24-1EP_4x4mm_P0.5mm_EP2.45x2.45mm_ThermalVias")
    s.field_ov.append("U2=-22.86,17.78,-22.86,20.32")
    caps_to_gnd(("C", "C11", ox + 12.7, yi, "10u 16V X7R 1206", C1206), ("C", "C12", ox + 22.86, yi, "10u 16V X7R 1206", C1206))
    s.sym("+5V", (ox + 5.08, yi))
    r7t, r7b = v("R", "R7", cx - 33.02, cy - 17.78, "100 1%", R0603)
    c13t, c13b = v("C", "C13", cx - 33.02, cy + 2.54, "100n 16V X7R", C0603)
    s.sym("GND", c13b)
    l2a, l2b = h("L", "L2", cx - 22.86, cy - 15.24, "4.7u Isat>=2A", L6045)
    s.poly((ox + 5.08, yi), "C11.1", "C12.1", (cx - 33.02, yi), (cx - 22.86, yi), l2a)
    s.poly((cx - 33.02, yi), r7t)
    s.poly(r7b, (None, cy - 2.54), c13t)
    s.poly(flag(cx - 43.18, cy - 2.54), (cx - 33.02, cy - 2.54), "U2.4")                             # VIN65 filter
    s.poly(l2b, (cx - 12.7, cy - 15.24), "U2.1")                                  # SW_P
    d4a, d4k = h("D", "D4", cx + 15.24, cy - 20.32, "SS14", SMA)
    s.poly((cx - 12.7, cy - 15.24), (None, cy - 20.32), d4a)
    s.pwr("+5V", "U2.5", "U2.8", "U2.10")                                         # INN, ENP, ENN
    s.pwr("GND", "U2.9", "U2.11")                                                 # PSP, PSN low
    s.nc("U2.7", "U2.12", "U2.20")
    s.tie("GND", "U2.19", "U2.25", "U2.2")
    # positive output, row cy-20.32
    r8t, r8b = v("R", "R8", cx + 91.44, cy - 20.32, "562k 1%", R0603)
    c14t, c14b = v("C", "C14", cx + 104.14, cy - 20.32, "6.8p 50V C0G", C0603)
    r9t, r9b = v("R", "R9", cx + 91.44, cy - 12.7, "130k 1%", R0603)
    s.field_ov += ["R8=2.54,-1.27,2.54,1.27", "R9=2.54,-1.27,2.54,1.27"]
    s.sym("GND", r9b)
    caps_to_gnd(("C", "C15", cx + 116.84, cy - 20.32, "10u 25V X7R 1206", C1206),
                ("C", "C16", cx + 129.54, cy - 20.32, "10u 25V X7R 1206", C1206))
    j5a, j5b = jp("JP5", cx + 139.7, cy - 20.32, "+6V5")
    s.poly(d4k, (cx + 25.4, cy - 20.32), r8t, c14t, "C15.1", "C16.1", j5a)
    s.poly("U2.23", (cx + 25.4, None), (None, cy - 20.32))                        # VPOS sense
    s.poly("U2.22", r8b)                                                          # FBP
    s.poly(c14b, r8b)
    r10 = led("R10", "D5", cx + 160.02, cy - 20.32, "3.3k 1%")
    s.poly(j5b, r10, tp("TP3", cx + 180.34, cy - 20.32, "+6V5"), flag(cx + 193.04, cy - 20.32), (cx + 205.74, cy - 20.32))
    s.sym("+6V5", (cx + 205.74, cy - 20.32))
    # VREF, FBN divider (-6V5 -> FBN -> VREF), VNEG sense
    r12t, r12b = v("R", "R12", cx + 58.42, cy - 10.16, "121k 1%", R0603)
    c18t, c18b = v("C", "C18", cx + 83.82, cy - 10.16, "220n 16V X7R", C0603)
    s.field_ov += ["R12=2.54,-1.27,2.54,1.27", "R11=2.54,-1.27,2.54,1.27"]
    s.sym("GND", c18b)
    s.poly("U2.17", r12t, c18t)                                                   # VREF
    r11t, r11b = v("R", "R11", cx + 58.42, cy - 2.54, "649k 1%", R0603)
    c17t, c17b = v("C", "C17", cx + 71.12, cy - 2.54, "7.5p 50V C0G", C0603)
    s.poly("U2.16", (cx + 55.88, None), (None, cy - 2.54), r12b, c17t)          # FBN
    s.poly("U2.15", (cx + 50.8, None), (None, cy + 5.08))                        # VNEG sense
    # inverter: OUTN -> L3 to GND, D6 to -6V5
    l3t, l3b = v("L", "L3", cx + 30.48, cy + 5.08, "4.7u Isat>=2A", L6045)
    s.sym("GND", l3b)
    d6k, d6a = h("D", "D6", cx + 36.83, cy + 5.08, "SS14", SMA, k_left=True)
    s.poly("U2.13", l3t, d6k)
    caps_to_gnd(("C", "C21", cx + 116.84, cy + 5.08, "10u 25V X7R 1206", C1206),
                ("C", "C22", cx + 129.54, cy + 5.08, "10u 25V X7R 1206", C1206))
    j6a, j6b = jp("JP6", cx + 139.7, cy + 5.08, "-6V5")
    s.poly(d6a, (cx + 50.8, cy + 5.08), r11b, c17b, "C21.1", "C22.1", j6a)
    c20t, c20b = v("C", "C20", cx + 22.86, cy + 7.62, "4.7n 50V C0G", C0603)    # CN
    c19t, c19b = v("C", "C19", cx + 15.24, cy + 12.7, "10n 16V X7R", C0603)     # CP
    s.sym("GND", c20b, c19b)
    s.poly("U2.18", c20t)
    s.poly("U2.21", c19t)
    r13 = led("R13", "D7", cx + 160.02, cy + 5.08, "3.3k 1%", negative=True)
    s.poly(j6b, r13, tp("TP4", cx + 180.34, cy + 5.08, "-6V5"), flag(cx + 193.04, cy + 5.08), (cx + 205.74, cy + 5.08))
    s.sym("-6V5", (cx + 205.74, cy + 5.08))

    # ---- D. analog LDOs ------------------------------------------------------------------------
    ox, oy = 309.88, 124.46
    s.note("D. ANALOG RAILS: TPS7A4701 +5VA, TPS7A3301 -5VA (low noise, no LEDs)", ox, oy, 2.2)
    s.note("+5VA: ANY-OUT 1.4 + 3.2 + 0.4 = 5.0 V.   -5VA = -1.176 V x (1 + 715k/220k) = -5.00 V.", ox, oy + 5.08)
    s.note("Load: 64 x OPA1678 (4 mA per channel) + 8 x AD7606C AVCC: about 0.45 A on +5VA, 0.3 A on -5VA.",
           ox, oy + 9.4)
    cx, cy = ox + 40.64, oy + 40.64
    ry = cy - 15.24
    s.part("Regulator_Linear:TPS7A4701xRGW", "U3", cx, cy, "TPS7A4701RGWR",
           "Package_DFN_QFN:Texas_RGW0020A_VQFN-20-1EP_5x5mm_P0.65mm_EP3.15x3.15mm_ThermalVias")
    caps_to_gnd(("C", "C23", ox + 12.7, ry, "10u 25V X7R 1206", C1206))
    s.sym("+6V5", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), "C23.1", (cx - 15.24, ry), "U3.15")
    s.poly("U3.13", (cx - 15.24, None), (None, ry))                              # EN to IN
    s.pwr("GND", "U3.10", "U3.6")                                                # ANY-OUT 0.4 V + 3.2 V
    s.nc(*(f"U3.{n}" for n in ("4", "5", "8", "9", "11", "12")))
    s.tie("GND", "U3.21", "U3.7")
    caps_to_gnd(("C", "C24", cx + 22.86, ry, "10u 16V X7R 1206", C1206), ("C", "C25", cx + 33.02, ry, "10u 16V X7R 1206", C1206))
    j2a, j2b = jp("JP2", cx + 45.72, ry, "+5VA")
    s.poly("U3.1", (cx + 15.24, ry), "C24.1", "C25.1", j2a)
    s.poly("U3.3", (cx + 15.24, None), (None, ry))                               # SENSE to OUT
    c26t, c26b = v("C", "C26", cx + 17.78, cy + 10.16, "1u 16V X7R", C0603)      # NR
    s.sym("GND", c26b)
    s.poly("U3.14", c26t)
    s.poly(j2b, tp("TP5", cx + 71.12, ry, "+5VA"), flag(cx + 83.82, ry), (cx + 96.52, ry))
    s.sym("+5VA", (cx + 96.52, ry))
    cx, cy = ox + 40.64, oy + 86.36
    ry = cy - 5.08
    s.part("Regulator_Linear:TPS7A3301RGW", "U4", cx, cy, "TPS7A3301RGWR",
           "Package_DFN_QFN:Texas_RGW0020A_VQFN-20-1EP_5x5mm_P0.65mm_EP3.15x3.15mm_ThermalVias")
    caps_to_gnd(("C", "C27", ox + 12.7, ry, "10u 25V X7R 1206", C1206))
    s.sym("-6V5", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), "C27.1", (cx - 15.24, ry), "U4.15")
    s.poly("U4.13", (cx - 15.24, None), (None, ry))                              # EN to IN
    c28t, c28b = v("C", "C28", cx - 17.78, cy + 2.54, "1u 16V X7R", C0603)       # NR/SS
    s.sym("GND", c28b)
    s.poly("U4.14", c28t)
    s.tie("GND", "U4.21", "U4.7")
    r14t, r14b = v("R", "R14", cx + 20.32, ry, "715k 1%", R0603)
    c29t, c29b = v("C", "C29", cx + 33.02, ry, "10n 50V C0G", C0603)
    r15t, r15b = v("R", "R15", cx + 20.32, cy + 7.62, "220k 1%", R0603)
    s.poly(r14b, r15t)
    s.sym("GND", r15b)
    caps_to_gnd(("C", "C30", cx + 45.72, ry, "22u 16V X7R 1210", C1210))
    j3a, j3b = jp("JP3", cx + 55.88, ry, "-5VA")
    s.poly("U4.1", r14t, c29t, "C30.1", j3a)
    s.poly("U4.3", r14b, c29b)                                                   # FB
    s.poly(j3b, tp("TP6", cx + 81.28, ry, "-5VA"), flag(cx + 93.98, ry), (cx + 106.68, ry))
    s.sym("-5VA", (cx + 106.68, ry))

    # ---- E. VDRV ----------------------------------------------------------------------------------
    ox, oy = 20.32, 233.68
    s.note("E. VDRV 15 V: LM2731X boost from VIN. Remove the J2 shunt to unpower all TX drivers", ox, oy, 2.2)
    s.note("1.23 V x (1 + 150k/13.3k) = 15.1 V. Load: 64 elements CW at 40 kHz, CV^2f = 1.3 W, about 0.12 A.",
           ox, oy + 5.08)
    cx, cy = ox + 45.72, oy + 33.02
    ry = cy - 12.7
    s.field_ov.append("U5=7.62,10.16,7.62,12.7")
    s.part("Regulator_Switching:LM2731XMF", "U5", cx, cy, "LM2731XMF", "Package_TO_SOT_SMD:SOT-23-5")
    caps_to_gnd(("C", "C31", ox + 12.7, ry, "10u 50V X7R 1210", C1210))
    r16t, r16b = v("R", "R16", cx - 20.32, ry, "51k 1%", R0603)
    l4a, l4b = h("L", "L4", cx, ry, "10u Isat>=2A", L6045)
    s.sym("VIN", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), "C31.1", r16t, (cx - 12.7, ry), l4a)
    s.poly((cx - 12.7, ry), (None, cy - 2.54), "U5.5")                           # VIN
    s.poly(r16b, (None, cy + 2.54), "U5.4")                                       # EN
    s.sym("GND", "U5.2")
    s.poly(l4b, (None, cy - 2.54))                                                # SW: L4 down to the SW pin
    d8a, d8k = h("D", "D8", cx + 12.7, cy - 2.54, "SS14", SMA)
    s.poly("U5.1", d8a)
    r17t, r17b = v("R", "R17", cx + 27.94, cy - 2.54, "150k 1%", R0603)
    c32t, c32b = v("C", "C32", cx + 40.64, cy - 2.54, "180p 50V C0G", C0603)
    r18t, r18b = v("R", "R18", cx + 27.94, cy + 10.16, "13.3k 1%", R0603)
    s.poly(r17b, r18t)
    s.sym("GND", r18b)
    caps_to_gnd(("C", "C33", cx + 53.34, cy - 2.54, "10u 50V X7R 1210", C1210),
                ("C", "C34", cx + 66.04, cy - 2.54, "10u 50V X7R 1210", C1210))
    s.part("Connector_Generic:Conn_01x02", "J2", cx + 81.28, cy - 2.54, "TX power (shunt)",
           "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    s.poly(d8k, r17t, c32t, "C33.1", "C34.1", "J2.1")
    s.poly("U5.3", (cx + 22.86, None), (None, cy + 5.08), r17b, c32b)          # FB
    caps_to_gnd(("CP", "C35", cx + 86.36, cy + 10.16, "47u 35V", "Capacitor_SMD:CP_Elec_6.3x7.7"))
    r19 = led("R19", "D9", cx + 99.06, cy + 10.16, "15k 1%")
    s.poly("J2.2", (cx + 73.66, None), (None, cy + 10.16), "C35.1", r19, tp("TP7", cx + 119.38, cy + 10.16, "VDRV"),
           flag(cx + 132.08, cy + 10.16), (cx + 144.78, cy + 10.16))
    s.sym("VDRV", (cx + 144.78, cy + 10.16))

    # ---- G. ground test points --------------------------------------------------------------------
    ox, oy = 223.52, 233.68
    s.note("G. GROUND: one GND net and plane", ox, oy, 2.2)
    s.note("Test points for scope grounds.", ox, oy + 5.08)
    s.sym("GND", tp("TP9", ox + 5.08, oy + 22.86, "GND"), tp("TP10", ox + 20.32, oy + 22.86, "GND"),
          flag(ox + 35.56, oy + 22.86))

    # ---- F. +3V3 -----------------------------------------------------------------------------------
    ox, oy = 309.88, 241.3
    s.note("F. +3V3: TLV75733P LDO from +5V (1 A, stable with ceramic caps)", ox, oy, 2.2)
    s.note("Load < 0.15 A: ADC VDRIVE, clock buffers, logic, mics.", ox, oy + 5.08)
    cx, cy = ox + 33.02, oy + 25.4
    ry = cy - 2.54
    s.part("Regulator_Linear:TLV75733PDBV", "U6", cx, cy, "TLV75733PDBVR", "Package_TO_SOT_SMD:SOT-23-5")
    caps_to_gnd(("C", "C36", ox + 15.24, ry, "10u 16V X5R", C0805))
    s.sym("+5V", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), "C36.1", (cx - 10.16, ry), "U6.1")
    s.poly("U6.3", (cx - 10.16, None), (None, ry))                               # EN to IN
    s.nc("U6.4")
    s.sym("GND", "U6.2")
    caps_to_gnd(("C", "C37", cx + 15.24, ry, "10u 16V X7R", C0805))
    j4a, j4b = jp("JP4", cx + 25.4, ry, "+3V3")
    s.poly("U6.5", "C37.1", j4a)
    r20 = led("R20", "D10", cx + 45.72, ry, "1k 1%")
    s.poly(j4b, r20, tp("TP8", cx + 66.04, ry, "+3V3"), flag(cx + 78.74, ry), (cx + 91.44, ry))
    s.sym("+3V3", (cx + 91.44, ry))

    # ---- H. 3V3_MIC --------------------------------------------------------------------------------
    ox, oy = 309.88, 294.64
    ry = oy + 20.32
    s.note("H. 3V3_MIC: ferrite-filtered supply for the 8 MEMS mics (remove FB1 to isolate)", ox, oy, 2.2)
    fa, fb_ = h("FB", "FB1", ox + 10.16, ry, "220R@100MHz 0603", "Inductor_SMD:L_0603_1608Metric")
    caps_to_gnd(("C", "C38", ox + 27.94, ry, "10u 16V X5R", C0805))
    r21 = led("R21", "D11", ox + 40.64, ry, "1k 1%")
    s.sym("+3V3", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), fa)
    s.poly(fb_, "C38.1", r21, tp("TP11", ox + 60.96, ry, "3V3_MIC"), flag(ox + 73.66, ry), (ox + 86.36, ry))
    s.sym("3V3_MIC", (ox + 86.36, ry))
    s.build()


SHEETS = {"adc": adc, "tx_group": tx_group, "en_buf": en_buf, "clk_buf": clk_buf, "mic": mic,
          "headers": headers, "power": power}

if __name__ == "__main__":
    for name in (sys.argv[1:] or SHEETS):
        SHEETS[name]()
