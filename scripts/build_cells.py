"""Build the reusable cell sheets and the ADC / header sheets from script.

Every block that repeats on the board is one sheet, instantiated N times from the top
sheet by scripts/gen_top.py, so there is exactly one sheet to change per block:

  channel.kicad_sch   x64  (drawn with the MCP server, not rebuilt here)
  adc.kicad_sch       x16  TLV320ADC5140, 4 channels, TDM slave, I2C, 33 R on SDOUT
  tx_group.kicad_sch  x16  one 74HCT595 + two UCC27524: drives 4 elements
  en_buf.kicad_sch    x4   one 74AHCT1G125: TX_EN_FPGA -> TX_ENn (one per 8 drivers)
  clk_buf.kicad_sch   x10  one 74LVC2G34, 100 k pull-down on IN, 47 R per output (<= 4 loads)
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
import re
import shutil
from collections import Counter
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

from common import DATE, KICAD_CLI, REV, block_end
from power_sections import BOXES, origin

REPO = Path(__file__).resolve().parent.parent
KDIR = REPO / "kicad"
MCP_PY = r"C:\GitHub\tools\KiCAD-MCP-Server\python"

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
             "VDRV": "power:+15V", "3V3_MIC": "power:+3V3", "+3V3A": "power:+3.3VA"}
# Rotation that points a power symbol away from the pin, by outward pin angle.
GND_ROT = {270: 0, 90: 180, 0: 90, 180: 270}
RAIL_ROT = {90: 0, 270: 180, 180: 90, 0: 270}
STEP = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}


def mcp(cmd, **params):
    r = K.handle_command(cmd, params)
    if isinstance(r, dict) and r.get("success") is False:
        raise RuntimeError(f"{cmd}: {r}")
    return r


def add_components(path, comps, auto):
    """batch_add_components, retrying parts whose write failed: the MCP server rewrites the whole sheet per
    part, and on Windows a file watcher or virus scan sometimes holds the file for a moment (Errno 22)."""
    todo = list(comps)
    for attempt in range(4):
        r = K.handle_command("batch_add_components", {"schematicPath": str(path), "components": todo,
                                                      "auto_position_fields": auto})
        failed = {e["reference"] for e in r.get("errors", [])}
        if not failed:
            return
        todo = [c for c in todo if c["reference"] in failed]
        time.sleep(1 + attempt)
    raise RuntimeError(f"batch_add_components failed for {sorted(failed)}: {r.get('errors')}")


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
        self.at_labels = []  # net-name labels placed on a wire point or pin
        self._segs, self._conn_pts = [], []
        self.field_ov = []  # sch_fields overrides "REF=dx,dy,dx2,dy2"
        self.frames = []  # dashed section frames (x, y, w, h)
        self.pwr_base = 0  # first number of the #PWR references (sheets that share a sheet id need different ones)
        self.hide_gnd_text = False  # dense sheets: GND symbols without the "GND" text
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

    def netname(self, net, pt, ang=0):
        """Name a wired net: a small local label on one of its wire points or pins."""
        self.at_labels.append((net, pt, ang))

    def sym(self, net, *pts):
        """Power symbol right on a pin or point: GND pointing down, rails pointing up."""
        for p in pts:
            self.at_syms.append((net, p))

    def note(self, text, x, y, size=1.8):
        self.notes.append((text, x, y, size))

    def frame(self, x, y, w, h):
        """Dashed rectangle around a section."""
        self.frames.append((x, y, w, h))

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
        add_components(self.path, self.parts, True)

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
            if net == "GND" and (ang != 270 or self.hide_gnd_text):
                hide = True
            npwr[0] += 1
            rot = (GND_ROT if net == "GND" else RAIL_ROT)[ang]
            if hide:  # by position: the MCP renumbers power symbols too
                hidden.add((round(at[0], 2), round(at[1], 2)))
            syms.append({"symbol": POWER_SYM[net], "reference": f"#PWR{self.pwr_base + npwr[0]:03d}", "value": net,
                         "position": {"x": at[0], "y": at[1]}, "rotation": rot})

        for kind, net, p, shape in self.conns:
            xy, ang = pin(p)
            if xy in seen:  # stacked pins (e.g. the TPS26600's two IN pins): one connection is enough
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

        for net, p, ang in self.at_labels:
            out.append(self.s_label("label", net, resolve(p, None), ang, None))

        for net, p in self.at_syms:
            q = resolve(p, None)
            self._conn_pts.append(q)
            power(net, q, 270 if net == "GND" else 90)

        out.extend(self.junctions())

        for text, x, y, size in self.notes:
            out.append(self.s_text(text, x, y, size))
        for x, y, w, h in self.frames:
            out.append(f'\t(rectangle\n\t\t(start {f(x)} {f(y)})\n\t\t(end {f(x + w)} {f(y + h)})\n'
                       f'\t\t(stroke (width 0.254) (type dash))\n\t\t(fill (type none))\n'
                       f'\t\t(uuid "{self.uid()}")\n\t)\n')

        if syms:
            add_components(self.path, syms, False)
        upgrade(self.path)  # KiCad re-save: tab indentation, which the other scripts expect
        t = self.path.read_text(encoding="utf-8")
        k = t.rindex("\t(sheet_instances")
        t = t[:k] + "".join(out) + t[k:]
        self.path.write_text(t, encoding="utf-8")
        fix_refs(self.path, self.parts, self.dnp)
        upgrade(self.path)
        fix_power_fields(self.path, hidden)
        copper_only(self.path)
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


def copper_only(path):
    """Test pads and solder jumpers are copper on the board: out of the BOM, like their footprints."""
    lines = path.read_text(encoding="utf-8").split("\n")
    i = 0
    while i < len(lines):
        if lines[i] == "\t(symbol" and re.match(r'\t\t\(lib_id "(Connector:TestPoint|Jumper:SolderJumper)', lines[i + 1]):
            j = block_end(lines, i)
            lines[i:j + 1] = [ln.replace("(in_bom yes)", "(in_bom no)") for ln in lines[i:j + 1]]
            i = j
        i += 1
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
    j = block_end(lines, start)
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
# ADC: TLV320ADC5140, 4 channels, TDM slave on BCLK/FSYNC, I2C control, AC-coupled single-ended
# =====================================================================================
def adc():
    s = Sheet("adc", "A3", "ADC (x16): TLV320ADC5140, 4 channels, 192 kSPS TDM")
    s.note("ADC: TLV320ADC5140 (TI, 4 channels, up to 768 kSPS). Run at 192 kSPS: flat to about 86 kHz.",
           30.48, 25.4, 2.5)
    s.note("TDM slave: BCLK 18.432 MHz, FSYNC 192 kHz from the FPGA (shared by all 16 ADCs, so all 64 channels "
           "sample at the same instant). 4 slots x 24 bits on SDOUT.", 30.48, 33.02)
    s.note("I2C: SCL shared, SDA per group of 4 ADCs; A1/A0 set the address 0x4C-0x4F inside the group. "
           "Config writes can use I2C broadcast (P0_R2 I2C_BRDCAST_EN).", 30.48, 38.1)
    s.note("Per channel, set at boot: CHx_INSRC = single-ended (01), CHx_IMP = 10 k (01), AC-coupled, fixed 0 dB "
           "gain, DRE and AGC OFF (they change gain on the fly and break channel matching).", 30.48, 43.18)
    s.note("Also: ADC_FSCALE 2.75 V (1 Vrms single-ended full scale), AREG_SELECT = 1, TX_EDGE = 1 (the FPGA "
           "latches SDOUT one BCLK after launch).", 30.48, 48.26)
    s.note("Input: 2k/1k divider (x0.31 with the 10 k input) keeps the receiver's +-4.2 V worst case inside the "
           "1.41 V peak full scale; 100 nF couples to the ADC's own bias (150 Hz high-pass).", 30.48, 53.34)
    s.note("Power-up: SHDNZ (ADC_SHDNZ, pulled low on the header sheet) stays low until the rails settle, "
           "then wait 2 ms before I2C.", 30.48, 58.42)
    s.part("aesa:TLV320ADC5140", "U1", 215.9, 139.7, "TLV320ADC5140",
           "Package_DFN_QFN:Texas_RTW_WQFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.7mm_ThermalVias")
    # Input network per channel: IN -> 2k -> (1k to GND) -> 100 nF -> INxP; INxM -> 100 nF -> GND
    for i in range(4):
        y = 81.28 + 25.4 * i
        rt, rb, cc, cm = f"R{i + 1}", f"R{i + 5}", f"C{i + 1}", f"C{i + 5}"
        s.part("Device:R", rt, 71.12, y, "2k 1%", R0603, rot=90)
        s.part("Device:R", rb, 86.36, y + 3.81, "1k 1%", R0603)
        s.part("Device:C", cc, 104.14, y, "100n 16V X7R", C0603, rot=90)
        s.part("Device:C", cm, 132.08, y + 12.7, "100n 16V X7R", C0603)
        s.hier(f"IN{4 - i}", f"{rt}.1", shape="input")      # ports in element order: IN1 -> channel 4 (below)
        s.poly(f"{rt}.2", f"{rb}.1", f"{cc}.1")
        s.pwr("GND", f"{rb}.2", f"{cm}.2")
        s.lbl(f"INP{i + 1}", f"{cc}.2", f"U1.{6 + 2 * i}")
        s.lbl(f"INM{i + 1}", f"{cm}.1", f"U1.{7 + 2 * i}")
    s.note("Channel inputs: 2k + 1k divider, 100 nF AC coupling (single-ended, INxM AC-grounded)", 50.8, 68.58)
    s.note("Ports IN1-IN4 are the block's four elements in order; IN1 feeds ADC channel 4 ... IN4 channel 1 (the "
           "order the receive lanes reach the inputs on the board). TDM: channel c carries element 4k-4 + (5 - c).",
           50.8, 73.66)
    # Control and audio interface
    s.glb("ADC_SHDNZ", "U1.14", shape="input")
    s.hier("A0", "U1.16", shape="input")
    s.hier("A1", "U1.15", shape="input")
    s.glb("ADC_SCL", "U1.17", shape="input")
    s.hier("SDA", "U1.18", shape="bidirectional")
    s.hier("FSYNC", "U1.23", shape="input")
    s.hier("BCLK", "U1.22", shape="input")
    s.part("Device:R", "R9", 269.24, 127.0, "33 1%", R0603, rot=90)
    s.wire("U1.21", "R9.1")
    s.hier("SDOUT", "R9.2", shape="output")
    s.nc("U1.20", "U1.5")  # GPIO1 and MICBIAS unused
    # Supplies and reference
    s.pwr("+3V3A", "U1.1")
    s.pwr("+3V3", "U1.19")
    s.pwr("GND", "U1.4", "U1.25")
    s.lbl("VREF", "U1.3")
    s.lbl("AREG", "U1.2")
    s.lbl("DREG", "U1.24")
    caps = [("C9", "1u 50V X7R", C0805, "+3V3A"), ("C10", "100n 16V X7R", C0603, "+3V3A"),
            ("C11", "10u 16V X5R", C0805, "AREG"), ("C12", "100n 16V X7R", C0603, "AREG"),
            ("C13", "10u 16V X5R", C0805, "DREG"), ("C14", "100n 16V X7R", C0603, "DREG"),
            ("C15", "10u 16V X5R", C0805, "+3V3"), ("C16", "100n 16V X7R", C0603, "+3V3"),
            ("C17", "1u 50V X7R", C0805, "VREF")]
    s.note("Decoupling at the pins (datasheet Figure 172): AVDD 1 uF + 100 nF, AREG and DREG 10 uF + 100 nF, "
           "IOVDD 10 uF + 100 nF, VREF 1 uF", 50.8, 213.36)
    for k, (ref, val, fp, net) in enumerate(caps):
        x = 58.42 + 22.86 * k
        s.part("Device:C", ref, x, 233.68, val, fp)
        (s.pwr if net in POWER_SYM else s.lbl)(net, f"{ref}.1")
        s.pwr("GND", f"{ref}.2")
    s.build()

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
    # QA..QD to the driver inputs in board order: on the board the 595's QB-QD face the drivers left to right and
    # QA comes round the left end, so QA->U2 INB, QB->U2 INA, QC->U3 INB, QD->U3 INA run side by side without
    # crossing. The outputs are labelled in element order, so DRV1..DRV4 = QA..QD = the group's elements 1..4
    # (common.TXG_Q_FOR_PORT)
    for q, dst, xm in (("15", "U2.4", 142.24), ("1", "U2.2", 139.7), ("2", "U3.4", 142.24), ("3", "U3.2", 139.7)):
        s.route(f"U1.{q}", dst, xm)
    s.nc("U1.4", "U1.5", "U1.6", "U1.7", "U1.9")
    for ref, y, a, b in (("U2", 76.2, 1, 2), ("U3", 129.54, 3, 4)):
        s.part("Driver_FET:UCC27524D", ref, 172.72, y, "UCC27524DR", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
        s.hier("EN", f"{ref}.1", f"{ref}.8", shape="input")
        s.hier(f"DRV{a}", f"{ref}.5", shape="output")      # OUTB: QA (U2) / QC (U3)
        s.hier(f"DRV{b}", f"{ref}.7", shape="output")      # OUTA: QB (U2) / QD (U3)
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
# Clock buffer: one input -> two 47 R series-terminated outputs, <= 4 loads each, clustered at the far end
# =====================================================================================
def clk_buf():
    s = Sheet("clk_buf", "A4", "Clock buffer (x10): 74LVC2G34, 47 R outputs")
    s.note("Clock fan-out: 74LVC2G34, both gates on IN, 47 R series termination per output (with the ~20 R driver: matches the 61 ohm 0.25 mm clock traces)",
           25.4, 25.4)
    s.note("Each output drives at most 4 loads, clustered at the far end of its trace (series termination needs that: sim/reports/mic_clock_si.md).",
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
        s.part("Device:R", ref, 157.48, y, "47 1%", R0603, rot=90)
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
    s.part("Device:C", "C1", 101.6, 101.6, "100n 50V C0G 1206", C1206)
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
           "J2 = board J16 (ADCs: 16 TDM lines, BCLK, FSYNC, I2C).", 30.48, 25.4, 2.5)
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
                s.glb(net, p, shape="bidirectional" if net.startswith(("ADC_SDOUT", "ADC_SDA", "MIC_D")) else "input")
    pulls = [("R1", "TX_EN_FPGA", "GND", "TX drivers off while the FPGA configures"),
             ("R2", "TX_OE_N", "+3V3", "595 outputs off"),
             ("R3", "TX_CLR_N", "GND", "595 cleared"),
             ("R4", "ADC_SHDNZ", "GND", "ADCs in hardware shutdown until the FPGA releases them"),
             ("R5", "ADC_SCL", "+3V3", "I2C pull-up"),
             ("R6", "ADC_SDA1", "+3V3", "I2C pull-up, ADC group 1"),
             ("R7", "ADC_SDA2", "+3V3", "I2C pull-up, ADC group 2"),
             ("R8", "ADC_SDA3", "+3V3", "I2C pull-up, ADC group 3"),
             ("R9", "ADC_SDA4", "+3V3", "I2C pull-up, ADC group 4")]
    s.note("Idle pulls (FPGA unconfigured), 10 k; I2C pull-ups 2.2 k (run the bus at 100 kHz). TX_SCK, TX_LATCH, "
           "ADC_BCLK, ADC_FSYNC and MIC_CLK_IN are pulled down in their clk_buf.", 30.48, 236.22)
    # Ribbon end (sim/clock_si_in.cir, clock_si_drive.cir): with the FPGA at DRIVE 4 (~80 ohm, near source match)
    # every clock arrives at 3.3 V / 0 V with no parts; the RC end terminations once drawn here as a DNP fallback
    # for DRIVE 8 were removed (user, 2026-10-26).
    s.note("FPGA ribbon outputs: DRIVE 4, SLEW SLOW (near source-matched to the 100-ohm ribbon, "
           "sim/reports/clock_si_drive.md): no termination parts needed.", 30.48, 176.53)
    for k, (ref, net, rail, _why) in enumerate(pulls):
        x = 50.8 + 40.64 * k
        s.part("Device:R", ref, x, 256.54, "2.2k 1%" if "ADC_S" in net and net != "ADC_SHDNZ" else "10k 1%", R0603)
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
L6045 = "Inductor_SMD:L_Sunlord_SWPA6045S"
L8040 = "Inductor_SMD:L_Bourns_SRN8040TA"
SMA = "Diode_SMD:D_SMA"
PITCH = 20.32


def power():
    """The two power sheets (A3, like every page), drawn conventionally: each regulator with its parts
    wired around it, one dashed box per section (scripts/power_sections.py, which also says which
    sheet each section is on), power flowing left to right and top to bottom.

    Rails run as wires through the parts that hang off them (input caps, dividers, output caps,
    jumper, LED, test point, flag) and end in a power symbol at the right of the section. Pins that
    only tie to a rail (enables, mode pins, ANY-OUT pins, exposed pads) use power symbols/flags.
    """
    sheets = {"power": Sheet("power", "A3", "Power 1: 12 V input, +5V, VDRV (TX), +3V3, 3V3_MIC"),
              "power_analog": Sheet("power_analog", "A3", "Power 2 (analog): +-6V5, +-5VA, +3V3A")}
    sheets["power_analog"].pwr_base = 500  # both sheets use sheet id 95: keep the power symbols apart
    for sh in sheets.values():
        sh.field_ov.append("--vertical")  # value text beside each upright part
        sh.hide_gnd_text = True
    s = sheets["power"]
    nflag = [0]
    SYM2 = {"C": "Device:C", "CP": "Device:C_Polarized", "R": "Device:R", "L": "Device:L",
            "FB": "Device:L_Ferrite", "F": "Device:Polyfuse", "D": "Device:D_Schottky", "LED": "Device:LED"}

    def box(key, title, *notes):
        """Frame a section on its sheet, write its heading and wrapped notes; returns the drawing origin.
        The helpers below draw on the sheet of the last box."""
        nonlocal s
        sheet, x, y, w, h = BOXES[key]
        s = sheets[sheet]
        s.frame(x, y, w, h)
        ox, oy = origin(key)
        s.note(title, ox, oy, 2.2)
        yy = oy + 5.08
        for n in notes:
            for line in textwrap.wrap(n, int((w - 5.08) / 1.65)):
                s.note(line, ox, yy)
                yy += 4.32
        return ox, oy

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
        s.part("Connector:TestPoint", ref, x, y, net, TPFP)  # value = the silkscreen label on the board
        return f"{ref}.1"

    def flag(x, y):
        nflag[0] += 1
        ref = f"#FLG{nflag[0]:02d}"
        s.part("power:PWR_FLAG", ref, x, y, "PWR_FLAG")
        return f"{ref}.1"

    def jp(ref, xa, y, rail):
        s.part("Jumper:SolderJumper_2_Bridged", ref, xa + 5.08, y, "cut to isolate", JPFP)
        return f"{ref}.1", f"{ref}.2"

    def led(rref, dref, x, y, rval, rail, negative=False):
        """Rail indicator hanging from a rail point (x, y): resistor, then LED to GND."""
        rt, rb = v("R", rref, x, y, rval, R0603)
        dt, db = v("LED", dref, x, y + 7.62, "green", LEDFP, k_top=negative)
        s.sym("GND", db)
        return rt

    def snubber(rref, cref, node, x, ysn):
        """Unfitted RC snubber on a switch node: wire from `node` (a point on its wire) to (x, ysn), then
        R and C in a row to GND. Fitted only if a measurement shows ringing."""
        ra, rb = h("R", rref, x, ysn, "4.7 1% DNP", R0603)
        ca, cb = h("C", cref, x + 17.78, ysn, "470p 50V C0G DNP", C0603)  # apart: the long values stay readable
        s.dnp += [rref, cref]
        s.poly(node, (x, ysn), ra)
        s.poly(rb, ca)
        gx = x + 17.78 + 12.7                    # GND at the end of a stub, clear of the capacitor's value text
        s.poly(cb, (gx, ysn))
        s.sym("GND", (gx, ysn))

    def caps_to_gnd(*pairs):
        for kind, ref, x, y, val, fp in pairs:
            top, bot = v(kind, ref, x, y, val, fp)
            s.sym("GND", bot)

    # ---- A. 12 V input ---------------------------------------------------------------------
    ox, oy = box("A", "1. 12 V INPUT: jack J9501 -> TPS26600 eFuse U9508 -> polyfuse F9501 -> VIN",
                 "eFuse U9508 (resettable, auto-retry): rated +-60 V (reverse polarity), on above 8.8 V, off above "
                 "13.8 V (LM2731 input limit 14 V), current limit 1.6 A. Polyfuse F9501 (2 A) backs it up. "
                 "C9545 - L9505 - C9501 keep the switchers' ripple off the cable.",
                 "UVLO/OVP divider R9522 / R9523 / R9524 (100k / 5.6k / 10k). ILIM R9525 7.5k = 1.6 A. dVdT C9543 "
                 "22 nF: 5 V/ms soft start. IMON R9526 15k = 1.08 V/A (TP9514). FLT low = fault (TP9515). "
                 "MODE open = auto-retry, SHDN open = on.",
                 "RTN (pin 8, PowerPad) and the support parts sit on EFUSE_RTN, apart from GND: the chip's reverse-"
                 "polarity block is between RTN and GND (tying them disables it). Board: an RTN copper island under "
                 "U9508, clear of the GND planes.")
    ry = oy + 30.48
    ux = ox + 86.36                                                              # eFuse centre
    s.field_ov.append("J1=-7.62,-7.62,-7.62,-5.08")
    s.part("Connector:Barrel_Jack", "J1", ox + 10.16, ry + 2.54, "12 V in (5.5x2.1)",
           "Connector_BarrelJack:BarrelJack_CUI_PJ-063AH_Horizontal")
    s.poly("J1.2", (ox + 20.32, ry + 5.08))
    s.sym("GND", (ox + 20.32, ry + 5.08))
    s.field_ov.append("U8=-10.16,-22.86,-10.16,-20.32")
    s.field_ov += [f"R{n}=-11.43,-0.64,-11.43,1.91" for n in (22, 23, 24)]  # divider text left, clear of the pin wires
    s.part("Power_Management:TPS26600PWP", "U8", ux, ry + 12.7, "TPS26600PWPR",
           "Package_SO:HTSSOP-16-1EP_4.4x5mm_P0.65mm_EP3.4x5mm_Mask2.66x2.46mm_ThermalVias")
    c44t, c44b = v("C", "C44", ox + 45.72, ry, "100n 50V X7R", C0603)
    s.sym("GND", c44b)
    r22t, r22b = v("R", "R22", ux - 25.4, ry, "100k 1%", R0603)                   # UVLO/OVP divider, pins in line
    r23t, r23b = v("R", "R23", ux - 25.4, ry + 7.62, "5.6k 1%", R0603)            # with UVLO and OVP
    r24t, r24b = v("R", "R24", ux - 25.4, ry + 15.24, "10k 1%", R0603)
    s.lbl("EFUSE_RTN", r24b)                     # the eFuse's own reference (RTN), not system GND
    s.poly("J1.1", flag(ox + 22.86, ry), tp("TP13", ox + 30.48, ry, "VIN_JACK"), c44t, r22t, "U8.1")
    s.netname("VIN_JACK", (ux - 22.86, ry))
    s.poly(r23t, r22b, "U8.3")                                                   # UVLO
    s.poly(r24t, r23b, "U8.5")                                                   # OVP
    s.nc("U8.6", "U8.7")  # (pins 4 and 13 are no-connect pins of the symbol)
    # RTN (pin 8) and the PowerPad are the eFuse's reference, kept apart from GND: the reverse-polarity block
    # sits between RTN and GND inside the chip, and tying them disables it (TPS2660 datasheet 9.3.5.5)
    s.pwr("GND", "U8.9")
    s.lbl("EFUSE_RTN", "U8.8", "U8.17")
    # control pins on the right: each wire runs to its part, the lowest pin to the nearest column
    c43t, c43b = v("C", "C43", ux + 20.32, ry + 25.4, "22n 50V X7R", C0603)       # dVdT
    r25t, r25b = v("R", "R25", ux + 33.02, ry + 22.86, "7.5k 1%", R0603)          # ILIM
    r27t, r27b = v("R", "R27", ux + 45.72, ry + 5.08, "100k 1%", R0603)           # FLT pull-up to the input
    r26t, r26b = v("R", "R26", ux + 58.42, ry + 15.24, "15k 1%", R0603)           # IMON
    s.lbl("EFUSE_RTN", c43b, r25b, r26b)          # support parts to RTN (datasheet 9.3.5.5)
    s.poly("U8.12", c43t)
    s.poly("U8.11", r25t)
    s.poly("U8.14", tp("TP15", ux + 27.94, ry + 12.7, "FLT"), r27b)
    s.netname("VIN_JACK", r27t)
    s.poly("U8.10", r26t, tp("TP14", ux + 66.04, ry + 15.24, "IMON"))
    f1, f2 = h("F", "F1", ux + 73.66, ry, "SMD1812P200TF16", "Fuse:Fuse_1812_4532Metric")
    s.poly("U8.15", f1)
    s.netname("VIN_PROT", (ux + 60.96, ry))
    # input filter: keeps the switchers' input ripple off the 12 V cable (C45 - L5 - C1, C1's ESR damps it)
    caps_to_gnd(("C", "C45", ux + 91.44, ry, "10u 50V X7R 1210", C1210))
    l5a, l5b = h("L", "L5", ux + 99.06, ry, "1u Isat>=4A", "Inductor_SMD:L_Sunlord_SWPA4030S")
    s.poly(f2, "C45.1", l5a)
    s.netname("VIN_F", (ux + 83.82, ry))
    caps_to_gnd(("CP", "C1", ux + 114.3, ry, "100u 25V", "Capacitor_SMD:CP_Elec_8x10"),
                ("C", "C2", ux + 123.19, ry, "100n 50V X7R", C0603))
    r1 = led("R1", "D2", ux + 130.81, ry, "10k 1%", "VIN")
    s.poly(l5b, "C1.1", "C2.1", r1, flag(ux + 135.89, ry), tp("TP1", ux + 142.24, ry, "VIN"), (ux + 152.4, ry))
    s.sym("VIN", (ux + 152.4, ry))

    # ---- B. +5V: TPS54302 buck --------------------------------------------------------------
    ox, oy = box("B", "2. +5V: TPS54302 buck U9501 (TI reference design), 3 A",
                 "Vout = 0.596 V x (1 + 75k/10k) = 5.07 V (R9504 / R9505). EN UVLO about 7.3 V (R9502 / R9503). "
                 "Load about 1.6 A. The switching loop C9503 - U9501 - L9501 is kept tight on the board. "
                 "R9528 + C9546: switch-node snubber, DNP (fit only if the switch node rings).")
    ry = oy + 30.48
    s.field_ov.append("U1=-10.16,-10.16,-10.16,-7.62")
    s.part("Regulator_Switching:TPS54302", "U1", ox + 58.42, ry + 2.54, "TPS54302DDCR",
           "Package_TO_SOT_SMD:SOT-23-6")
    caps_to_gnd(("C", "C3", ox + 12.7, ry, "10u 50V X7R 1210", C1210), ("C", "C4", ox + 22.86, ry, "100n 50V X7R", C0603))
    r2t, r2b = v("R", "R2", ox + 33.02, ry, "510k 1%", R0603)
    r3t, r3b = v("R", "R3", ox + 33.02, ry + 12.7, "100k 1%", R0603)
    s.poly(r2b, r3t)
    s.sym("GND", r3b)
    s.sym("VIN", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), "C3.1", "C4.1", r2t, "U1.3")
    s.poly(r2b, (ox + 40.64, None), (None, ry + 5.08), "U1.5")                  # EN
    s.netname("EN_5V", (ox + 40.64, ry + 5.08))
    s.sym("GND", "U1.1")
    s.field_ov.append("C5=2.54,-1.27,2.54,1.27")
    c5t, c5b = v("C", "C5", ox + 76.2, ry - 7.62, "100n 16V X7R", C0603)       # BOOT
    s.poly("U1.6", (ox + 71.12, None), (None, ry - 7.62), c5t)
    s.poly(c5b, (None, ry + 2.54))
    l1a, l1b = h("L", "L1", ox + 81.28, ry + 2.54, "10u Isat>=5A", L8040)
    snubber("R28", "C46", (ox + 80.01, ry + 2.54), ox + 80.01, ry - 12.7)            # SW_5V
    s.poly("U1.2", (ox + 76.2, ry + 2.54), l1a)                                 # SW
    s.netname("SW_5V", (ox + 74.93, ry + 2.54), 180)
    s.netname("BOOT_5V", (ox + 71.12, ry - 7.62), 180)
    yo = ry + 2.54
    r4t, r4b = v("R", "R4", ox + 96.52, yo, "75k 1%", R0603)
    c8t, c8b = v("C", "C8", ox + 109.22, yo, "100p 50V C0G", C0603)
    r5t, r5b = v("R", "R5", ox + 96.52, yo + 12.7, "10k 1%", R0603)
    s.poly(r4b, r5t)
    s.sym("GND", r5b)
    caps_to_gnd(("C", "C6", ox + 121.92, yo, "22u 16V X5R 1206", C1206), ("C", "C7", ox + 134.62, yo, "22u 16V X5R 1206", C1206))
    ja, jb = jp("JP1", ox + 144.78, yo, "+5V")
    s.poly(l1b, r4t, c8t, "C6.1", "C7.1", ja)
    s.poly(c8b, r4b)
    s.poly(r4b, (ox + 73.66, None), (None, ry + 5.08), "U1.4")                  # FB
    s.netname("FB_5V", (ox + 73.66, yo + 7.62))
    s.netname("5V_REG", l1b)
    r6 = led("R6", "D3", ox + 165.1, yo, "2.2k 1%", "+5V")
    s.poly(jb, r6, tp("TP2", ox + 185.42, yo, "+5V"), flag(ox + 198.12, yo), (ox + 210.82, yo))
    s.sym("+5V", (ox + 210.82, yo))

    # ---- G. ground test points --------------------------------------------------------------------
    ox, oy = box("G", "GROUND TEST POINTS", "One GND net and plane (inner layers In1, In2). Scope-ground "
                 "test points: TP9509 / TP9510 at the power supply, TP9516 - TP9519 spread over the board (ADC "
                 "row, both array sides, TX row). Power tree and bring-up checklist: Power 2.")
    s.sym("GND", *(tp(f"TP{n}", ox + 7.62 + 15.24 * (k % 4), oy + 38.1 + 17.78 * (k // 4), "GND")
                   for k, n in enumerate((9, 10, 16, 17, 18, 19))), flag(ox + 53.34, oy + 55.88))

    # ---- C. +-6V5: TPS65131 ----------------------------------------------------------------------
    ox, oy = box("C", "6. +-6V5: TPS65131RGER U9502, boost + inverter from +5V (feeds the analog LDOs only)",
                 "Drawn with the pin-identical TPS65130 symbol: order TPS65131RGER (2 A switches). "
                 "PSP/PSN low: fixed-frequency PWM. R9529 + C9547 and R9530 + C9548: switch-node snubbers, DNP "
                 "(fit only if a switch node rings).",
                 "+6V5 = 1.213 V x (1 + 562k/130k) = 6.46 V (R9508 / R9509).   "
                 "-6V5 = -1.213 V x 649k/121k = -6.51 V (R9511 / R9512).")
    cx, cy = ox + 76.2, oy + 50.8
    yi = cy - 27.94
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
    s.poly(flag(cx - 43.18, cy - 2.54), (cx - 33.02, cy - 2.54), "U2.4")
    s.netname("VIN65", (cx - 33.02, cy - 2.54))                             # VIN65 filter
    s.poly(l2b, (cx - 12.7, cy - 15.24), "U2.1")                                  # SW_P
    d4a, d4k = h("D", "D4", cx + 15.24, cy - 20.32, "SS14", SMA)
    s.poly((cx - 12.7, cy - 15.24), (None, cy - 20.32), d4a)
    s.netname("SW_P", (cx - 12.7, cy - 20.32))
    snubber("R29", "C47", (cx + 2.54, cy - 20.32), cx + 2.54, cy - 30.48)            # SW_P
    s.pwr("+5V", "U2.5", "U2.8", "U2.10")                                         # INN, ENP, ENN
    s.pwr("GND", "U2.9", "U2.11")                                                 # PSP, PSN low
    s.nc("U2.7", "U2.12", "U2.20")
    s.tie("GND", "U2.19", "U2.25", "U2.2")
    # positive output, row cy-20.32
    r8t, r8b = v("R", "R8", cx + 96.52 + 15.24, cy - 20.32, "562k 1%", R0603)
    c14t, c14b = v("C", "C14", cx + 111.76 + 15.24, cy - 20.32, "6.8p 50V C0G", C0603)
    r9t, r9b = v("R", "R9", cx + 96.52 + 15.24, cy - 12.7, "130k 1%", R0603)
    s.field_ov += ["R8=2.54,-1.27,2.54,1.27", "R9=2.54,-1.27,2.54,1.27"]
    s.sym("GND", r9b)
    caps_to_gnd(("C", "C15", cx + 124.46 + 15.24, cy - 20.32, "10u 25V X7R 1206", C1206),
                ("C", "C16", cx + 134.62 + 15.24, cy - 20.32, "10u 25V X7R 1206", C1206))
    j5a, j5b = jp("JP5", cx + 144.78 + 15.24, cy - 20.32, "+6V5")
    s.poly(d4k, (cx + 25.4, cy - 20.32), r8t, c14t, "C15.1", "C16.1", j5a)
    s.poly("U2.23", (cx + 25.4, None), (None, cy - 20.32))                        # VPOS sense
    s.netname("6V5_REG", (cx + 25.4, cy - 20.32))
    s.netname("FBP", (cx + 45.72, cy - 12.7))                                   # on its wire, off R8
    s.poly("U2.22", r8b)                                                          # FBP
    s.poly(c14b, r8b)
    r10 = led("R10", "D5", cx + 157.48 + 15.24, cy - 20.32, "3.3k 1%", "+6V5")
    s.poly(j5b, r10, tp("TP3", cx + 170.18 + 15.24, cy - 20.32, "+6V5"), flag(cx + 180.34 + 15.24, cy - 20.32), (cx + 187.96 + 15.24, cy - 20.32))
    s.sym("+6V5", (cx + 187.96 + 15.24, cy - 20.32))
    # VREF, FBN divider (-6V5 -> FBN -> VREF), VNEG sense
    r12t, r12b = v("R", "R12", cx + 58.42, cy - 10.16, "121k 1%", R0603)
    c18t, c18b = v("C", "C18", cx + 91.44, cy - 10.16, "220n 16V X7R", C0603)
    s.field_ov += ["R12=2.54,-1.27,2.54,1.27", "R11=2.54,-1.27,2.54,1.27",   # text right of the part, clear
                   "C17=2.54,-1.27,2.54,1.27", "C18=2.54,-1.27,2.54,1.27"]   # of the divider beside it
    s.sym("GND", c18b)
    s.poly("U2.17", r12t, c18t)                                                   # VREF
    s.netname("VREF65", r12t, 180)
    r11t, r11b = v("R", "R11", cx + 58.42, cy - 2.54, "649k 1%", R0603)
    c17t, c17b = v("C", "C17", cx + 76.2, cy - 2.54, "7.5p 50V C0G", C0603)
    s.poly("U2.16", (cx + 55.88, None), (None, cy - 2.54), r12b, c17t)
    s.netname("FBN", (cx + 55.88, cy - 5.08), 180)          # FBN
    s.poly("U2.15", (cx + 50.8, None), (None, cy + 5.08))                        # VNEG sense
    # inverter: OUTN -> L3 to GND, D6 to -6V5
    l3t, l3b = v("L", "L3", cx + 33.02, cy + 5.08, "4.7u Isat>=2A", L6045)
    s.sym("GND", l3b)
    d6k, d6a = h("D", "D6", cx + 39.37, cy + 5.08, "SS14", SMA, k_left=True)
    s.poly("U2.13", l3t, d6k)
    s.netname("SW_N", l3t)
    snubber("R30", "C48", (cx + 38.1, cy + 5.08), cx + 38.1, cy + 22.86)             # SW_N
    caps_to_gnd(("C", "C21", cx + 124.46 + 15.24, cy + 5.08, "10u 25V X7R 1206", C1206),
                ("C", "C22", cx + 134.62 + 15.24, cy + 5.08, "10u 25V X7R 1206", C1206))
    j6a, j6b = jp("JP6", cx + 144.78 + 15.24, cy + 5.08, "-6V5")
    s.poly(d6a, (cx + 50.8, cy + 5.08), r11b, c17b, "C21.1", "C22.1", j6a)
    s.netname("-6V5_REG", "C22.1")
    c20t, c20b = v("C", "C20", cx + 25.4, cy + 7.62, "4.7n 50V C0G", C0603)     # CN
    c19t, c19b = v("C", "C19", cx + 17.78, cy + 12.7, "10n 16V X7R", C0603)     # CP
    s.sym("GND", c20b, c19b)
    s.poly("U2.18", c20t)
    s.poly("U2.21", c19t)
    r13 = led("R13", "D7", cx + 157.48 + 15.24, cy + 5.08, "3.3k 1%", "-6V5", negative=True)
    s.poly(j6b, r13, tp("TP4", cx + 170.18 + 15.24, cy + 5.08, "-6V5"), flag(cx + 180.34 + 15.24, cy + 5.08), (cx + 187.96 + 15.24, cy + 5.08))
    s.sym("-6V5", (cx + 187.96 + 15.24, cy + 5.08))

    # ---- D. analog LDOs ------------------------------------------------------------------------
    ox, oy = box("D", "7. +-5VA: low-noise LDOs TPS7A4700 U9503 / TPS7A3301 U9504",
                 "+5VA: ANY-OUT 1.4 + 3.2 + 0.4 = 5.0 V.  -5VA = -1.176 V x (1 + 715k/220k) = -5.00 V.",
                 "Load: 64 x OPA1678 (4 mA per channel), about 0.3 A on each rail. Analog rails: no LEDs.")
    cx, cy = ox + 40.64, oy + 40.64
    ry = cy - 15.24
    s.field_ov.append("U3=-7.62,-22.86,-7.62,-20.32")
    s.part("Regulator_Linear:TPS7A4701xRGW", "U3", cx, cy, "TPS7A4700RGWT",
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
    s.netname("5VA_REG", "C25.1")
    c26t, c26b = v("C", "C26", cx + 17.78, cy + 10.16, "1u 16V X7R", C0603)      # NR
    s.sym("GND", c26b)
    s.poly("U3.14", c26t)
    s.netname("NR_5VA", c26t)
    s.poly(j2b, tp("TP5", cx + 71.12, ry, "+5VA"), flag(cx + 83.82, ry), (cx + 96.52, ry))
    s.sym("+5VA", (cx + 96.52, ry))
    cx, cy = ox + 40.64, oy + 81.28
    ry = cy - 5.08
    s.field_ov.append("U4=-7.62,-12.7,-7.62,-10.16")
    s.part("Regulator_Linear:TPS7A3301RGW", "U4", cx, cy, "TPS7A3301RGWR",
           "Package_DFN_QFN:Texas_RGW0020A_VQFN-20-1EP_5x5mm_P0.65mm_EP3.15x3.15mm_ThermalVias")
    caps_to_gnd(("C", "C27", ox + 12.7, ry, "10u 25V X7R 1206", C1206))
    s.sym("-6V5", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), "C27.1", (cx - 15.24, ry), "U4.15")
    s.poly("U4.13", (cx - 15.24, None), (None, ry))                              # EN to IN
    c28t, c28b = v("C", "C28", cx - 25.4, cy + 2.54, "1u 16V X7R", C0603)        # NR/SS
    s.sym("GND", c28b)
    s.poly("U4.14", c28t)
    s.netname("NR_M5VA", c28t)
    s.tie("GND", "U4.21", "U4.7")
    s.field_ov += ["R14=2.54,-1.27,2.54,1.27", "R15=2.54,-1.27,2.54,1.27",  # clear of the FB label
                   "C29=2.54,-1.27,2.54,1.27"]
    r14t, r14b = v("R", "R14", cx + 20.32, ry, "715k 1%", R0603)
    c29t, c29b = v("C", "C29", cx + 38.1, ry, "10n 50V C0G", C0603)
    r15t, r15b = v("R", "R15", cx + 20.32, cy + 7.62, "220k 1%", R0603)
    s.poly(r14b, r15t)
    s.sym("GND", r15b)
    caps_to_gnd(("C", "C30", cx + 53.34, ry, "22u 16V X7R 1210", C1210))
    j3a, j3b = jp("JP3", cx + 60.96, ry, "-5VA")
    s.poly("U4.1", r14t, c29t, "C30.1", j3a)
    s.poly("U4.3", r14b, c29b)                                                   # FB
    s.netname("FB_M5VA", r14b, 180)
    s.netname("-5VA_REG", "C30.1")
    s.poly(j3b, tp("TP6", cx + 81.28, ry, "-5VA"), flag(cx + 93.98, ry), (cx + 106.68, ry))
    s.sym("-5VA", (cx + 106.68, ry))

    # ---- H. 3V3_MIC --------------------------------------------------------------------------------
    ox, oy = box("H", "5. 3V3_MIC: mic supply", "Ferrite-filtered +3V3 for the 8 MEMS mics. "
                 "Remove FB9501 to isolate.")
    ry = oy + 25.4
    fa, fb_ = h("FB", "FB1", ox + 15.24, ry, "600R@100MHz 0603", "Inductor_SMD:L_0603_1608Metric")
    caps_to_gnd(("C", "C38", ox + 33.02, ry, "10u 16V X5R", C0805))
    r21 = led("R21", "D11", ox + 45.72, ry, "1k 1%", "3V3_MIC")
    s.sym("+3V3", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), fa)
    s.poly(fb_, "C38.1", r21, tp("TP11", ox + 66.04, ry, "3V3_MIC"), flag(ox + 78.74, ry), (ox + 91.44, ry))
    s.sym("3V3_MIC", (ox + 91.44, ry))

    # ---- E. VDRV ----------------------------------------------------------------------------------
    ox, oy = box("E", "4. VDRV 15 V: LM2731X boost U9505 from VIN",
                 "1.23 V x (1 + 150k/13k) = 15.4 V (R9517 / R9518). Load, all 64 elements in CW at 40 kHz: about "
                 "0.24 A (sim/reports/tx_cw.md). Remove the J9502 shunt to unpower every TX driver. "
                 "R9531 + C9549: switch-node snubber, DNP (fit only if the switch node rings). C9550 100 uF "
                 "electrolytic: damped bulk on VIN at the power row, the far end of the 280 mm feed from the jack "
                 "(its ESR damps the feed inductance against the ceramic input caps; keeps the switchers' ripple "
                 "current local).")
    cx, cy = ox + 45.72, oy + 33.02
    ry = cy - 12.7
    s.field_ov.append("U5=7.62,10.16,7.62,12.7")
    s.part("Regulator_Switching:LM2731XMF", "U5", cx, cy, "LM2731XMF", "Package_TO_SOT_SMD:SOT-23-5")
    caps_to_gnd(("C", "C31", ox + 12.7, ry, "10u 50V X7R 1210", C1210),
                ("CP", "C50", ox + 19.05, ry, "100u 25V", "Capacitor_SMD:CP_Elec_8x10"))   # damped VIN bulk
    r16t, r16b = v("R", "R16", cx - 20.32, ry, "51k 1%", R0603)
    l4a, l4b = h("L", "L4", cx, ry, "10u Isat>=2A", L6045)
    s.sym("VIN", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), "C31.1", "C50.1", r16t, (cx - 12.7, ry), l4a)
    s.poly((cx - 12.7, ry), (None, cy - 2.54), "U5.5")                           # VIN
    s.poly(r16b, (None, cy + 2.54), "U5.4")                                       # EN
    s.netname("EN_VDRV", (cx - 20.32, cy + 2.54))
    s.sym("GND", "U5.2")
    s.poly(l4b, (None, cy - 2.54))                                                # SW: L4 down to the SW pin
    d8a, d8k = h("D", "D8", cx + 17.78, cy - 2.54, "SS14", SMA)
    s.poly("U5.1", d8a)
    s.netname("SW_VDRV", (cx + 10.16, cy - 2.54))
    snubber("R31", "C49", (cx + 12.7, cy - 2.54), cx + 12.7, cy - 17.78)             # SW_VDRV
    r17t, r17b = v("R", "R17", cx + 27.94, cy - 2.54, "150k 1%", R0603)
    c32t, c32b = v("C", "C32", cx + 40.64, cy - 2.54, "180p 50V C0G", C0603)
    r18t, r18b = v("R", "R18", cx + 27.94, cy + 10.16, "13k 1%", R0603)
    s.poly(r17b, r18t)
    s.sym("GND", r18b)
    caps_to_gnd(("C", "C33", cx + 53.34, cy - 2.54, "10u 50V X7R 1210", C1210),
                ("C", "C34", cx + 66.04, cy - 2.54, "10u 50V X7R 1210", C1210))
    s.part("Connector_Generic:Conn_01x02", "J2", cx + 81.28, cy - 2.54, "TX power (shunt)",
           "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    s.poly(d8k, r17t, c32t, "C33.1", "C34.1", "J2.1")
    s.poly("U5.3", (cx + 22.86, None), (None, cy + 5.08), r17b, c32b)          # FB
    s.netname("FB_VDRV", (cx + 22.86, cy + 5.08), 180)
    s.netname("VDRV_REG", "C33.1")
    caps_to_gnd(("CP", "C35", cx + 86.36, cy + 10.16, "47u 35V", "Capacitor_SMD:CP_Elec_6.3x7.7"))
    r19 = led("R19", "D9", cx + 99.06, cy + 10.16, "15k 1%", "VDRV")
    s.poly("J2.2", (cx + 73.66, None), (None, cy + 10.16), "C35.1", r19, tp("TP7", cx + 116.84, cy + 10.16, "VDRV"),
           flag(cx + 129.54, cy + 10.16), (cx + 139.7, cy + 10.16))
    s.sym("VDRV", (cx + 139.7, cy + 10.16))

    # ---- F. +3V3 -----------------------------------------------------------------------------------
    ox, oy = box("F", "3. +3V3: TLV75733P LDO U9506 from +5V",
                 "1 A, stable with ceramic caps. Load < 0.15 A: ADC IOVDD, clock buffers, logic, mics. "
                 "C9551 100 uF electrolytic: damped bulk at the end of the 300 mm +5V run from the buck, for "
                 "both top LDOs (U9506, U9507).")
    cx, cy = ox + 33.02, oy + 30.48
    ry = cy - 2.54
    s.field_ov.append("U6=-7.62,-10.16,-7.62,-7.62")
    s.part("Regulator_Linear:TLV75733PDBV", "U6", cx, cy, "TLV75733PDBVR", "Package_TO_SOT_SMD:SOT-23-5")
    caps_to_gnd(("CP", "C51", ox + 10.16, ry, "100u 25V", "Capacitor_SMD:CP_Elec_8x10"),   # damped +5V bulk
                ("C", "C36", ox + 17.78, ry, "10u 16V X5R", C0805))
    s.sym("+5V", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), "C51.1", "C36.1", (cx - 10.16, ry), "U6.1")
    s.poly("U6.3", (cx - 10.16, None), (None, ry))                               # EN to IN
    s.nc("U6.4")
    s.sym("GND", "U6.2")
    caps_to_gnd(("C", "C37", cx + 15.24, ry, "10u 16V X7R 1206", C1206))
    j4a, j4b = jp("JP4", cx + 25.4, ry, "+3V3")
    s.poly("U6.5", "C37.1", j4a)
    s.netname("3V3_REG", "C37.1")
    r20 = led("R20", "D10", cx + 45.72, ry, "1k 1%", "+3V3")
    s.poly(j4b, r20, tp("TP8", cx + 66.04, ry, "+3V3"), flag(cx + 78.74, ry), (cx + 91.44, ry))
    s.sym("+3V3", (cx + 91.44, ry))

    # ---- I. +3V3A ----------------------------------------------------------------------------------
    ox, oy = box("I", "8. +3V3A: TPS7A4700 low-noise LDO U9507 from +5V",
                 "AVDD of the 16 ADCs (analog rail: no LED). ANY-OUT 1.4 + 1.6 + 0.2 + 0.1 = 3.3 V. Load about "
                 "0.45 A; 0.8 W in the LDO, thermal vias under the pad.")
    cx, cy = ox + 40.64, oy + 40.64
    ry = cy - 15.24
    s.field_ov.append("U7=-7.62,-22.86,-7.62,-20.32")
    s.part("Regulator_Linear:TPS7A4701xRGW", "U7", cx, cy, "TPS7A4700RGWT",
           "Package_DFN_QFN:Texas_RGW0020A_VQFN-20-1EP_5x5mm_P0.65mm_EP3.15x3.15mm_ThermalVias")
    caps_to_gnd(("C", "C39", ox + 12.7, ry, "10u 25V X7R 1206", C1206))
    s.sym("+5V", (ox + 5.08, ry))
    s.poly((ox + 5.08, ry), "C39.1", (cx - 15.24, ry), "U7.15")
    s.poly("U7.13", (cx - 15.24, None), (None, ry))                              # EN to IN
    s.pwr("GND", "U7.8", "U7.11", "U7.12")                                       # ANY-OUT 1.6 + 0.2 + 0.1 V
    s.nc(*(f"U7.{n}" for n in ("4", "5", "6", "9", "10")))
    s.tie("GND", "U7.21", "U7.7")
    caps_to_gnd(("C", "C40", cx + 22.86, ry, "10u 16V X7R 1206", C1206), ("C", "C41", cx + 33.02, ry, "10u 16V X7R 1206", C1206))
    j7a, j7b = jp("JP7", cx + 45.72, ry, "+3V3A")
    s.poly("U7.1", (cx + 15.24, ry), "C40.1", "C41.1", j7a)
    s.poly("U7.3", (cx + 15.24, None), (None, ry))                               # SENSE to OUT
    s.netname("3V3A_REG", "C41.1")
    c42t, c42b = v("C", "C42", cx + 17.78, cy + 10.16, "1u 16V X7R", C0603)      # NR
    s.sym("GND", c42b)
    s.poly("U7.14", c42t)
    s.netname("NR_3V3A", c42t)
    s.poly(j7b, tp("TP12", cx + 71.12, ry, "+3V3A"), flag(cx + 83.82, ry), (cx + 96.52, ry))
    s.sym("+3V3A", (cx + 96.52, ry))

    # ---- N. power tree and bring-up --------------------------------------------------------------
    box("N", "POWER TREE, BRING-UP",
        "Power 1: sections 1-5 (input, +5V, +3V3, VDRV, 3V3_MIC) and the ground test points. "
        "Power 2: sections 6-8 (+-6V5, +-5VA, +3V3A).",
        "1. 12 V jack J9501 -> eFuse U9508 -> F9501 -> VIN.",
        "2. VIN -> buck U9501 -> +5V.   4. VIN -> boost U9505 -> VDRV.",
        "3. +5V -> LDO U9506 -> +3V3 -> 5. FB9501 -> 3V3_MIC.",
        "6. +5V -> U9502 -> +-6V5 -> 7. LDOs U9503 / U9504 -> +-5VA (receivers).",
        "8. +5V -> LDO U9507 -> +3V3A (ADC AVDD).",
        "First power-up: J9502 shunt off, bench supply 12 V limited to 0.3 A. Expect at the test points:",
        "VIN 12 V, +5V 5.07 V, +3V3 3.30 V, 3V3_MIC 3.3 V,",
        "+6V5 6.46 V, -6V5 -6.51 V, +5VA 5.00 V,",
        "-5VA -5.00 V, +3V3A 3.30 V.",
        "IMON (TP9514) 1.08 V per A in. FLT (TP9515) high (low = eFuse fault).",
        "Then fit the J9502 shunt: VDRV 15.4 V.",
        "A wrong rail: cut its jumper, measure the regulator side (*_REG) and the load side apart.")
    for sh in sheets.values():
        sh.build()


SHEETS = {"adc": adc, "tx_group": tx_group, "en_buf": en_buf, "clk_buf": clk_buf, "mic": mic,
          "headers": headers, "power": power}

if __name__ == "__main__":
    for name in (sys.argv[1:] or SHEETS):
        SHEETS[name]()
