"""Check the whole design from the KiCad netlist and write docs/design-check.md.

Exports a fresh netlist and ERC report with kicad-cli, then checks:
  ERC, part counts, footprints (exist, pads match symbol pins), LCSC fields,
  the TX chain of all 64 elements (header -> 595 -> UCC27524 -> DRVn -> channel),
  the RX chain (channel -> RXn -> ADC input) and ADC/mic/clock wiring back to the headers,
  clock fan-out loads, header pin map, logic levels on FPGA pins, IC supply rails,
  decoupling, capacitor voltage ratings, polarity, regulator outputs from the actual
  divider values, rail testability (test point, isolation, LED), single-pin nets.

  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe scripts\\check_design.py
"""

import datetime
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import KICAD_3DMODELS, KICAD_CLI, KICAD_FOOTPRINTS, TXG_Q_FOR_PORT, adc_port, tx_port  # noqa: E402
from pinmap import J1_MAP, J2_MAP  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CLI = KICAD_CLI
FPDIR = KICAD_FOOTPRINTS
NET = REPO / "build" / "aesa.net"
ERC = REPO / "build" / "erc.rpt"
OUT = REPO / "docs" / "design-check.md"

RAIL_V = {"VIN": 12.0, "VIN_JACK": 12.0, "VIN_PROT": 12.0, "VIN65": 5.0, "+5V": 5.0, "5V_REG": 5.07,
          "+6V5": 6.46, "6V5_REG": 6.46, "-6V5": -6.51, "-6V5_REG": -6.51, "+5VA": 5.0, "5VA_REG": 5.0,
          "-5VA": -5.0, "-5VA_REG": -5.0, "VDRV": 15.4, "VDRV_REG": 15.4, "+3V3": 3.3, "3V3_REG": 3.3,
          "3V3_MIC": 3.3, "+3V3A": 3.3, "3V3A_REG": 3.3, "SW_5V": 12.0, "SW_P": 7.0, "SW_N": -7.0, "SW_VDRV": 15.9, "BOOT_5V": 17.0}


# ---- results ------------------------------------------------------------------------------
class Report:
    def __init__(self):
        self.sections = []  # (title, status, lines)

    def add(self, title, ok, lines, warn=False):
        status = "PASS" if ok and not warn else ("WARN" if ok else "FAIL")
        self.sections.append((title, status, lines))
        print(f"[{status}] {title}")

    def write(self):
        n = Counter(s for _, s, _ in self.sections)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        out = ["# Design check", "",
               f"Run {now} by `scripts/check_design.py` on a fresh netlist of `kicad/aesa.kicad_sch`. "
               f"**{n['PASS']} pass, {n['WARN']} warn, {n['FAIL']} fail.**", "",
               "| # | Check | Result |", "|---|---|---|"]
        for i, (t, s, _) in enumerate(self.sections, 1):
            out.append(f"| {i} | [{t}](#{i}) | **{s}** |")
        for i, (t, s, lines) in enumerate(self.sections, 1):
            out += ["", f'<a id="{i}"></a>', f"## {i}. {t}: {s}", ""] + lines
        OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"wrote {OUT.relative_to(REPO)}: {dict(n)}")


# ---- netlist ------------------------------------------------------------------------------
class Design:
    def __init__(self, path):
        t = path.read_text(encoding="utf-8")
        self.comps = {}
        for c in re.split(r"\n\t\t\(comp\n", t)[1:]:
            c = c.split("\n\t)\n\t(libparts")[0]
            g = lambda p: (re.search(p, c, re.S).group(1) if re.search(p, c, re.S) else "")  # noqa: E731
            ref = g(r'\(ref "([^"]+)"')
            self.comps[ref] = dict(
                value=g(r'\(value "([^"]*)"'), fp=g(r'\(footprint "([^"]*)"'),
                lcsc=g(r'\(name "LCSC"\) "([^"]*)"'), lib=g(r'\(lib "([^"]*)"\)'), part=g(r'\(part "([^"]*)"\)'),
                sheet=g(r'\(names "([^"]*)"'), dnp="(property\n\t\t\t\t(name \"dnp\")" in c or '(name "dnp")' in c)
        self.nets = {}
        self.pin_net = {}
        self.pin_info = {}
        for blk in re.split(r"\n\t\t\(net\n", t)[1:]:
            name = re.search(r'\(name "([^"]*)"', blk).group(1)
            nodes = []
            for m in re.finditer(r'\(node\s*\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)'
                                 r'(?:\s*\(pinfunction "([^"]*)"\))?\s*\(pintype "([^"]*)"\)', blk):
                ref, pin, fn, typ = m.groups()
                nodes.append((ref, pin))
                self.pin_net[(ref, pin)] = name
                self.pin_info[(ref, pin)] = (fn or "", typ)
            self.nets[name] = nodes

    def net(self, ref, pin):
        return self.pin_net.get((ref, str(pin)))

    @staticmethod
    def base(net):
        return net.split("/")[-1] if net else net

    def refs_by_value(self, value):
        return sorted(r for r, c in self.comps.items() if c["value"] == value)

    def other_end(self, ref, net):
        """For a two-pin part on `net`, the net at its other pin."""
        pins = [p for (r, p) in self.pin_net if r == ref]
        for p in pins:
            if self.pin_net[(ref, p)] != net:
                return self.pin_net[(ref, p)]
        return None


def export():
    subprocess.run([CLI, "sch", "export", "netlist", "-o", str(NET), str(REPO / "kicad" / "aesa.kicad_sch")],
                   check=True, capture_output=True)
    subprocess.run([CLI, "sch", "erc", "-o", str(ERC), str(REPO / "kicad" / "aesa.kicad_sch")],
                   check=False, capture_output=True)


# ---- checks ---------------------------------------------------------------------------------
def check_erc(R):
    t = ERC.read_text(encoding="utf-8", errors="replace")
    v = re.findall(r"^\[(\w+)\]: (.*)$", t, re.M)
    m = re.search(r"\*\* ERC messages: (\d+)\s+Errors (\d+)\s+Warnings (\d+)", t)
    lines = [f"kicad-cli ERC on the full hierarchy: {len(v)} violations."]
    lines += [f"- `{k}`: {msg}" for k, msg in v[:30]]
    R.add("ERC", len(v) == 0, lines)


def check_counts(R, d):
    expect = {"OPA1678IDR": 64, "CUSP-TR80-15-2500-TH": 64, "BAV99": 128, "UCC27524DR": 32, "74HCT595D": 16,
              "74AHCT1G125": 4, "74LVC2G34": 10, "TLV320ADC5140": 16, "SPH0641LU4H-1": 8, "TPS54302DDCR": 1,
              "TPS65131RGER": 1, "TPS7A4700RGWT": 2, "TPS7A3301RGWR": 1, "LM2731XMF": 1, "TLV75733PDBVR": 1}
    vals = Counter(c["value"] for c in d.comps.values())
    bad = [f"- {v}: {vals.get(v, 0)} (expected {n})" for v, n in expect.items() if vals.get(v, 0) != n]
    lines = [f"{len(d.comps)} parts, {len(vals)} distinct values."]
    lines += ["| Part | Count |", "|---|---|"] + [f"| {v} | {vals.get(v, 0)} |" for v in expect]
    lines += (["", "Mismatches:"] + bad) if bad else []
    refs = list(d.comps)
    dup = [r for r, n in Counter(refs).items() if n > 1]
    lines.append(f"\nDuplicate references: {len(dup)}.")
    R.add("Part counts and references", not bad and not dup, lines)


def pads_of(fp):
    lib, name = fp.split(":")
    p = (REPO / "kicad" / "aesa.pretty" / f"{name}.kicad_mod") if lib == "aesa" else FPDIR / f"{lib}.pretty" / f"{name}.kicad_mod"
    if not p.exists():
        return None
    return set(re.findall(r'\(pad "([^"]*)"', p.read_text(encoding="utf-8")))


def check_footprints(R, d):
    missing, nofp, mism = [], [], []
    cache = {}
    pins = defaultdict(set)
    for (r, p) in d.pin_net:
        pins[r].add(p)
    for r, c in d.comps.items():
        if not c["fp"]:
            nofp.append(r)
            continue
        if c["fp"] not in cache:
            cache[c["fp"]] = pads_of(c["fp"])
        pads = cache[c["fp"]]
        if pads is None:
            missing.append(f"{r} {c['fp']}")
            continue
        extra = sorted(pins[r] - pads)
        if extra:
            mism.append(f"{r} ({c['value']}, {c['fp']}): symbol pins {extra} have no pad")
    lines = [f"{len(cache)} distinct footprints, all checked for existence and pad numbers."]
    for title, lst in (("No footprint", nofp), ("Footprint file not found", missing), ("Pin/pad mismatch", mism)):
        if lst:
            seen = sorted(set(lst))
            lines += [f"\n{title} ({len(lst)}):"] + [f"- {x}" for x in seen[:20]]
    R.add("Footprints exist and match the symbol pins", not (nofp or missing or mism), lines)


def check_lcsc(R, d):
    by = defaultdict(list)
    for r, c in d.comps.items():
        by[(c["lcsc"] or "(none)").removesuffix(" (DNP)")].append(r)  # DNP parts carry "Cxxxx (DNP)"
    tbd = Counter(d.comps[r]["value"] for r in by.get("TBD", []))
    lines = [f"Parts with an LCSC number: {sum(len(v) for k, v in by.items() if k.startswith('C') and k[1:].isdigit())}; "
             f"consigned/hand-solder: {sum(len(v) for k, v in by.items() if k.lower().startswith(('consign', 'hand')))}; "
             f"**TBD: {len(by.get('TBD', []))}** ({len(tbd)} distinct values); no field: {len(by.get('(none)', []))}.",
             "", "TBD values (count): " + ", ".join(f"{v} ({n})" for v, n in tbd.most_common())]
    R.add("LCSC numbers", not by.get("(none)"), lines, warn=bool(tbd))


def check_datasheets(R, d):
    """Every part with an LCSC number (and the hand-soldered transducer) has a PDF in docs/datasheets/."""
    import json
    idx_path = REPO / "docs" / "datasheets" / "index.json"
    idx = json.loads(idx_path.read_text()) if idx_path.exists() else {}
    have = {k for k, e in idx.items() if e.get("file") and (idx_path.parent / e["file"]).exists()}
    need = {}
    for r, c in d.comps.items():
        code = c["lcsc"].removesuffix(" (DNP)")
        key = code if re.fullmatch(r"C\d+", code) else (c["value"] if code == "hand-solder" else None)
        if key:
            need.setdefault(key, c["value"])
    miss = sorted(f"{v} ({k})" for k, v in need.items() if k not in have)
    R.add("Datasheets in docs/datasheets/ for every part", True,
          [f"{len(need)} distinct parts, {len(need) - len(miss)} with a datasheet PDF "
           f"(scripts/datasheets.py, run by bom.py)."] + [f"- missing: {m}" for m in miss], warn=bool(miss))


def check_3d_models(R, d):
    """Every footprint on the board has a 3D model that exists (pad-only footprints have no body)."""
    pcb = REPO / "kicad" / "aesa.kicad_pcb"
    if not pcb.exists():
        R.add("3D model for every part", True, ["No board yet."], warn=True)
        return
    pad_only = ("TestPoint", "SolderJumper", "Fiducial", "MountingHole")  # footprint names
    env = {"KICAD10_3DMODEL_DIR": str(KICAD_3DMODELS), "KIPRJMOD": str(pcb.parent)}
    miss, n = Counter(), 0
    for blk in pcb.read_text(encoding="utf-8").split("\n\t(footprint ")[1:]:
        lib = blk[1:blk.index('"', 1)]
        models = re.findall(r'\(model "([^"]+)"', blk)
        if not models:
            if not lib.split(":")[-1].startswith(pad_only):
                miss[f"{lib} (no model)"] += 1
            continue
        n += 1
        path = re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), m.group(0)), models[0])
        if not any(Path(path).with_suffix(s).exists() for s in (".step", ".wrl", Path(path).suffix)):
            miss[f"{lib} ({models[0]})"] += 1
    R.add("3D model for every part", not miss,
          [f"{n} footprints with a model that resolves; pad-only footprints (test pads, solder jumpers, fiducials, "
           "holes) need none. Substitutes for models missing from KiCad's library: MODEL_SUBST in place.py; "
           "own parts: scripts/make_3d.py."] + [f"- missing: {k} x{v}" for k, v in sorted(miss.items())])


def check_tx_chain(R, d):
    """Header pin -> 595 SER; 595 Q -> UCC IN; UCC OUT -> DRVn -> channel n."""
    errs = []
    hdr = {v: k for k, v in J1_MAP.items() if v}
    q_pins = {"A": "15", "B": "1", "C": "2", "D": "3"}       # 595 output pins
    out_of_in = {"2": "7", "4": "5"}
    for n in range(1, 65):
        g, i = tx_port(n)
        sr = f"U{100 + g}01"
        ser = d.base(d.net(sr, "14"))
        if ser != f"TX_SDI{g - 1}" or d.net("J9901", hdr[ser]) is None or d.base(d.net("J9901", hdr[ser])) != ser:
            errs.append(f"element {n}: 595 {sr} SER on {ser}, expected TX_SDI{g - 1} at J1 pin {hdr.get(f'TX_SDI{g - 1}')}")
        qnet = d.net(sr, q_pins[TXG_Q_FOR_PORT[i]])
        drv_in = [(r, p) for (r, p) in d.nets.get(qnet, []) if d.comps[r]["value"] == "UCC27524DR"]
        if len(drv_in) != 1 or drv_in[0][1] not in out_of_in:
            errs.append(f"element {n}: {sr} Q{TXG_Q_FOR_PORT[i]} reaches {drv_in}")
            continue
        ucc, pin = drv_in[0]
        drv = d.net(ucc, out_of_in[pin])
        nodes = d.nets.get(drv, [])
        if d.base(drv) != f"DRV{n}" or (f"R{n}01", "1") not in nodes:
            errs.append(f"element {n}: {ucc} pin {out_of_in[pin]} on {drv}, nodes {nodes}")
    R.add("TX chain, all 64 elements (header -> 595 -> UCC27524 -> DRVn -> channel)", not errs,
          [f"Traced 64 elements: header pin, shift-register SER and output, driver input/output, DRVn, "
           f"channel R_drv. Errors: {len(errs)}."] + [f"- {e}" for e in errs[:20]])


def check_rx_chain(R, d):
    """Channel n -> RXn -> ADC k, input i (common.adc_port) through 2k / 1k / 100 nF; ADC wiring to J2."""
    errs = []
    for n in range(1, 65):
        k, j = adc_port(n)
        i = j - 1
        sid, u = 70 + k, f"U{70 + k}01"
        rx = d.net(f"R{n}09", "2")
        rt, rb, cc, cm = f"R{sid}{i + 1:02d}", f"R{sid}{i + 5:02d}", f"C{sid}{i + 1:02d}", f"C{sid}{i + 5:02d}"
        div = d.net(rt, "2")
        if d.base(rx) != f"RX{n}" or (rt, "1") not in d.nets.get(rx, []):
            errs.append(f"channel {n}: R{n}09 -> {rx}, expected RX{n} into {rt}")
        if (rb, "1") not in d.nets.get(div, []) or d.base(d.net(rb, "2")) != "GND" or (cc, "1") not in d.nets.get(div, []):
            errs.append(f"channel {n}: divider {rt}/{rb} or coupling {cc} miswired ({div})")
        if d.net(cc, "2") != d.net(u, str(6 + 2 * i)):
            errs.append(f"channel {n}: {cc} does not reach {u} IN{i + 1}P (pin {6 + 2 * i})")
        if d.net(cm, "1") != d.net(u, str(7 + 2 * i)) or d.base(d.net(cm, "2")) != "GND":
            errs.append(f"channel {n}: IN{i + 1}M (pin {7 + 2 * i}) not AC-grounded through {cm}")
    hdr = {v: k for k, v in J2_MAP.items() if v}
    seen = {}
    for k in range(1, 17):
        u, sid, grp, idx = f"U{70 + k}01", 70 + k, (k - 1) // 4, (k - 1) % 4
        port = f"ADC_SDOUT{k}"
        if (f"R{sid}09", "1") not in d.nets.get(d.net(u, "21"), []) or d.base(d.net(f"R{sid}09", "2")) != port                 or d.base(d.net("J9902", hdr[port])) != port:
            errs.append(f"ADC{k} SDOUT: pin 21 -> {d.net(u, '21')}; R{sid}09.2 -> {d.net(f'R{sid}09', '2')}")
        for pin, net in (("14", "ADC_SHDNZ"), ("17", "ADC_SCL"), ("18", f"ADC_SDA{grp + 1}"),
                         ("22", f"ADC_BCLK_{'ABCD'[grp]}"), ("23", f"ADC_FSYNC_{'ABCD'[grp]}"),
                         ("16", "+3V3" if idx & 1 else "GND"), ("15", "+3V3" if idx & 2 else "GND")):
            if d.base(d.net(u, pin)) != net:
                errs.append(f"ADC{k} pin {pin} ({d.pin_info.get((u, pin), ('',))[0]}) on {d.net(u, pin)}, expected {net}")
        addr = (d.base(d.net(u, "18")), d.base(d.net(u, "15")), d.base(d.net(u, "16")))
        if addr in seen:
            errs.append(f"ADC{k} has the same I2C bus and address as ADC{seen[addr]}")
        seen[addr] = k
    R.add("RX chain (channel -> RXn -> divider -> ADC input) and ADC wiring to J2", not errs,
          ["Checked: R_out of each channel into its ADC cell (ADC k = ceil(n/4), input 4k + 1 - n: common.adc_port), the 2k/1k "
           "divider, 100 nF coupling to INxP, INxM AC-grounded; SDOUT through 33 R to its J2 pin; SHDNZ, SCL, SDA of "
           "the group, BCLK/FSYNC of the group, address pins, and that no two ADCs share bus + address. "
           f"Errors: {len(errs)}."] + [f"- {e}" for e in errs[:20]])


def trace_to_header(d, net, seen=None):
    """Follow a clock net upstream through 47 R / 100 R series resistors and 74LVC2G34 gates."""
    seen = seen or set()
    if net in seen:
        return set()
    seen.add(net)
    found = set()
    for r, p in d.nets.get(net, []):
        if r in ("J9901", "J9902"):
            found.add(f"{'J1' if r == 'J9901' else 'J2'}.{p}")
        c = d.comps[r]
        if r.startswith("R") and c["value"].startswith(("33 ", "47 ")):  # series terminators
            found |= trace_to_header(d, d.other_end(r, net), seen)
        if c["value"] == "74LVC2G34" and p in ("6", "4"):
            found |= trace_to_header(d, d.net(r, "1" if p == "6" else "3"), seen)
    return found


def check_clocks(R, d):
    errs, lines = [], []
    expect = {"TX_SCK": "J1.32", "TX_LATCH": "J1.36", "ADC_BCLK": "J2.32", "ADC_FSYNC": "J2.34", "MIC_CLK_IN": "J1.34"}
    loads = {}
    for name, nodes in d.nets.items():
        b = d.base(name)
        if re.match(r"^(TX_SCK|TX_LATCH|ADC_BCLK|ADC_FSYNC|MIC_CLK)_[A-D]$", b):
            ins = [(r, p) for r, p in nodes if d.pin_info[(r, p)][1] == "input"]
            loads[b] = len(ins)
            src = trace_to_header(d, name)
            want = expect[b.rsplit("_", 1)[0] if not b.startswith("MIC") else "MIC_CLK_IN"]
            if want not in src:
                errs.append(f"{b}: traces to {src}, expected {want}")
            if len(ins) > 4:
                errs.append(f"{b}: {len(ins)} loads (> 4)")
    lines.append("| Buffered clock | Loads | |\n|---|---|---|")
    lines += [f"| {k} | {v} | {'ok' if v <= 4 else 'TOO MANY'} |" for k, v in sorted(loads.items())]
    # every 595 / ADC / mic clock input is on a buffered clock
    for v, pins in (("74HCT595D", ("11", "12")), ("TLV320ADC5140", ("22", "23")), ("SPH0641LU4H-1", ("4",))):
        for r in d.refs_by_value(v):
            for p in pins:
                b = d.base(d.net(r, p))
                if not re.match(r"^\w+_[A-D]$", b or ""):
                    errs.append(f"{r} pin {p} on {b}: not a buffered clock")
    R.add("Clocks: buffered, <= 4 loads per output, traced to the right header pin", not errs,
          lines + [f"\nErrors: {len(errs)}."] + [f"- {e}" for e in errs])


def check_headers(R, d):
    errs = []
    for ref, mp in (("J9901", J1_MAP), ("J9902", J2_MAP)):
        for pin, want in mp.items():
            got = d.net(ref, pin)
            if want is None:
                if got and not got.startswith("unconnected"):
                    errs.append(f"{ref}.{pin}: expected open, on {got}")
            elif d.base(got) != want:
                errs.append(f"{ref}.{pin}: expected {want}, on {got}")
        for clk in (32, 34, 36):
            for nb in (clk - 1, clk + 1):
                if mp[nb] != "GND":
                    errs.append(f"{ref} clock pin {clk}: neighbour {nb} is {mp[nb]}, not GND")
    used = sum(1 for mp in (J1_MAP, J2_MAP) for v in mp.values() if v and v != "GND")
    gnd = sum(1 for mp in (J1_MAP, J2_MAP) for v in mp.values() if v == "GND")
    R.add("FPGA header pin maps (scripts/pinmap.py) and GND around clocks", not errs,
          [f"{used} signal pins, {gnd} GND pins, 6 open (+5V, +3.3V from the FPGA board). Errors: {len(errs)}."]
          + [f"- {e}" for e in errs])


IO_SUPPLY_PIN = {"TLV320ADC5140": "19"}  # outputs swing to IOVDD (+3V3), not AVDD


def supply_of(d, ref):
    """Logic level of an IC's outputs: its I/O supply pin if it has one, else its highest rail."""
    pin = IO_SUPPLY_PIN.get(d.comps[ref]["value"])
    if pin:
        return RAIL_V.get(d.base(d.net(ref, pin)))
    vs = []
    for (r, p), (fn, typ) in d.pin_info.items():
        if r == ref and typ in ("power_in", "power_out"):
            v = RAIL_V.get(d.base(d.pin_net[(r, p)]))
            if v is not None:
                vs.append(v)
    return max(vs) if vs else None


def check_fpga_levels(R, d):
    errs, lines = [], []
    for ref in ("J9901", "J9902"):
        for p in [p for (r, p) in d.pin_net if r == ref]:
            net = d.pin_net[(ref, p)]
            if d.base(net) in ("GND",) or net.startswith("unconnected"):
                continue
            for r2, p2 in d.nets[net]:
                if r2 == ref:
                    continue
                typ = d.pin_info[(r2, p2)][1]
                # outputs reach the header directly or through a 33 R source resistor
                srcs = [(r2, p2, typ)]
                if r2.startswith("R"):
                    other = d.other_end(r2, net)
                    srcs = [(r3, p3, d.pin_info[(r3, p3)][1]) for r3, p3 in d.nets.get(other, []) if r3 != r2]
                for r3, p3, t3 in srcs:
                    if t3 in ("output", "tri_state", "bidirectional", "power_out"):
                        v = supply_of(d, r3)
                        if v is not None and v > 3.6:
                            errs.append(f"{ref}.{p} {d.base(net)}: driven by {r3} pin {p3} at {v} V")
                        lines.append(f"{ref}.{p} {d.base(net)} <- {r3} ({d.comps[r3]['value']}, {v} V)")
    lines = sorted(set(lines))
    R.add("No signal above 3.3 V reaches an FPGA pin", not errs,
          [f"Every board-driven header net and its driver's supply (directly or through the 33 R source "
           f"resistor). Errors: {len(errs)}."] + [f"- {e}" for e in errs] + ["", "<details><summary>"
           f"{len(lines)} driven header pins</summary>", ""] + [f"- {x}" for x in lines] + ["", "</details>"])


def check_rails(R, d):
    expect = {"OPA1678IDR": {"+5VA", "-5VA"}, "TLV320ADC5140": {"+3V3A", "+3V3"}, "74HCT595D": {"+5V"},
              "UCC27524DR": {"VDRV"}, "74AHCT1G125": {"+5V"}, "74LVC2G34": {"+3V3"}, "SPH0641LU4H-1": {"3V3_MIC"}}
    errs = []
    for v, rails in expect.items():
        for r in d.refs_by_value(v):
            got = {d.base(d.pin_net[(r, p)]) for (rr, p), (fn, typ) in d.pin_info.items()
                   if rr == r and typ == "power_in"} - {"GND"}
            if got != rails:
                errs.append(f"{r} ({v}): supply pins on {sorted(got)}, expected {sorted(rails)}")
    R.add("IC supply rails", not errs, [f"Every OPA1678, TLV320ADC5140, 74HCT595, UCC27524, 74AHCT1G125, 74LVC2G34 "
                                        f"and SPH0641 checked. Errors: {len(errs)}."] + [f"- {e}" for e in errs[:20]])


def check_decoupling(R, d):
    """Every IC power pin on a rail has a capacitor rail-to-GND in the same sheet instance."""
    errs, n = [], 0
    caps = defaultdict(set)  # (sheet, rail) with a decoupling cap
    for r, c in d.comps.items():
        if r.startswith("C"):
            nets = {d.base(d.pin_net.get((r, "1"))), d.base(d.pin_net.get((r, "2")))}
            if "GND" in nets and len(nets) == 2:
                caps[(c["sheet"], (nets - {"GND"}).pop())].add(r)
    for (r, p), (fn, typ) in d.pin_info.items():
        if typ != "power_in" or not r.startswith("U"):
            continue
        rail = d.base(d.pin_net[(r, p)])
        if rail == "GND" or rail not in RAIL_V:
            continue
        n += 1
        if not caps.get((d.comps[r]["sheet"], rail)):
            errs.append(f"{r} ({d.comps[r]['value']}) pin {p} {fn} on {rail}: no cap to GND on this sheet")
    R.add("Decoupling on every IC supply pin", not errs,
          [f"{n} IC supply pins checked. Errors: {len(errs)}."] + [f"- {e}" for e in sorted(set(errs))[:30]])


def check_cap_ratings(R, d):
    errs, warns = [], []
    for r, c in d.comps.items():
        if not r.startswith("C") or c["dnp"]:
            continue
        nets = [d.base(d.pin_net.get((r, p))) for p in ("1", "2")]
        vs = [abs(RAIL_V.get(x, 0.0)) for x in nets]
        if "GND" not in nets:
            span = abs(RAIL_V.get(nets[0], 0) - RAIL_V.get(nets[1], 0)) if all(x in RAIL_V for x in nets) else max(vs)
        else:
            span = max(vs)
        m = re.search(r"(\d+)V", c["value"])
        if not m or span == 0:
            continue
        rating = float(m.group(1))
        if rating < span * 1.2:
            errs.append(f"{r} {c['value']} on {nets}: {span:.1f} V, rating {rating:.0f} V")
        elif rating < span * 1.5 and "X5R" in c["value"] or rating < span * 1.5 and "X7R" in c["value"]:
            warns.append(f"{r} {c['value']} on {nets}: {span:.1f} V, rating {rating:.0f} V (< 1.5x)")
    R.add("Capacitor voltage ratings vs rail voltage", not errs,
          [f"Rule: rating >= 1.2 x voltage (fail), >= 1.5 x for ceramics (warn). Errors: {len(errs)}, "
           f"warnings: {len(warns)}."] + [f"- FAIL {e}" for e in sorted(set(errs))[:20]]
          + [f"- warn {w}" for w in sorted(set(warns))[:20]], warn=bool(warns))


def check_polarity(R, d):
    errs, lines = [], []
    for r, c in d.comps.items():
        if c["part"] == "C_Polarized":
            p, n = d.base(d.net(r, "1")), d.base(d.net(r, "2"))
            ok = RAIL_V.get(p, 0) > RAIL_V.get(n, 0)
            lines.append(f"- {r} {c['value']}: + on {p}, - on {n}: {'ok' if ok else 'REVERSED'}")
            if not ok:
                errs.append(r)
        if c["part"] in ("D_Schottky", "LED"):
            k, a = d.base(d.net(r, "1")), d.base(d.net(r, "2"))
            lines.append(f"- {r} {c['value']}: anode {a}, cathode {k}")
    expect = {"D9504": ("SW_P", "6V5_REG"), "D9506": ("-6V5_REG", "SW_N"), "D9508": ("SW_VDRV", "VDRV_REG")}
    for r, (a, k) in expect.items():
        if (d.base(d.net(r, "2")), d.base(d.net(r, "1"))) != (a, k):
            errs.append(f"{r}: anode {d.net(r, '2')}, cathode {d.net(r, '1')}; expected {a} -> {k}")
    # rail LEDs: rail -> resistor -> LED -> GND; on a negative rail the LED points the other way
    for r in d.refs_by_value("green"):
        a, k = d.net(r, "2"), d.net(r, "1")
        mid = a if d.base(k) == "GND" else k
        res = [x for x, _ in d.nets.get(mid, []) if x.startswith("R")]
        rail = d.base(d.other_end(res[0], mid)) if len(res) == 1 else None
        negative = rail is not None and rail.startswith("-")
        ok = rail is not None and (d.base(k) == "GND" and mid == a and not negative
                                   or d.base(a) == "GND" and mid == k and negative)
        if not ok:
            errs.append(f"{r}: LED anode {d.base(a)}, cathode {d.base(k)}, fed from {rail}")
    R.add("Polarity: electrolytics, rectifiers, LEDs", not errs, [f"Errors: {len(errs)}."] + [f"- {e}" for e in errs]
          + [""] + lines)


def divider(d, fb, top_rail):
    """(R_top, R_bot) of a feedback divider: resistor FB->top_rail, resistor FB->(GND or other)."""
    top = bot = None
    for r, p in d.nets.get(fb, []):
        if not r.startswith("R"):
            continue
        o = d.base(d.other_end(r, fb))
        val = parse_r(d.comps[r]["value"])
        if o == top_rail:
            top = val
        else:
            bot = (val, o)
    return top, bot


def parse_r(v):
    m = re.match(r"([\d.]+)([kKM]?)", v)
    x = float(m.group(1))
    return x * {"": 1, "k": 1e3, "K": 1e3, "M": 1e6}[m.group(2)]


def check_regulators(R, d):
    rows, errs = [], []
    fb = {k: n for n in d.nets for k in ("FB_5V", "FBP", "FBN", "FB_VDRV", "FB_M5VA") if d.base(n) == k}
    t, (b, _) = divider(d, fb["FB_5V"], "5V_REG")
    rows.append(("+5V TPS54302", 0.596 * (1 + t / b), 5.0))
    t, (b, _) = divider(d, fb["FBP"], "6V5_REG")
    rows.append(("+6V5 TPS65131 boost", 1.213 * (1 + t / b), 6.5))
    t, (b, o) = divider(d, fb["FBN"], "-6V5_REG")
    rows.append(("-6V5 TPS65131 inverter (to VREF)", -1.213 * t / b, -6.5))
    t, (b, _) = divider(d, fb["FB_VDRV"], "VDRV_REG")
    rows.append(("VDRV LM2731X", 1.23 * (1 + t / b), 15.0))
    t, (b, _) = divider(d, fb["FB_M5VA"], "-5VA_REG")
    rows.append(("-5VA TPS7A3301", -1.176 * (1 + t / b), -5.0))
    anyout = {"4": 6.4, "5": 6.4, "6": 3.2, "8": 1.6, "9": 0.8, "10": 0.4, "11": 0.2, "12": 0.1}
    for u in d.refs_by_value("TPS7A4700RGWT"):  # ANY-OUT: 1.4 V + the grounded pins
        out = d.base(d.net(u, "1"))
        v = 1.4 + sum(x for p, x in anyout.items() if d.base(d.net(u, p)) == "GND")
        rows.append((f"{out.replace('_REG', '')} TPS7A4700 ANY-OUT", v, {"5VA_REG": 5.0, "3V3A_REG": 3.3}.get(out, 0.0)))
    # EN UVLO of the TPS54302: 1.23 V rising threshold (datasheet typ), divider R2/R3
    en = next(n for n in d.nets if d.base(n) == "EN_5V")
    t, (b, _) = divider(d, en, "VIN")
    uvlo = 1.23 * (1 + t / b)
    lines = ["| Rail | From the netlist | Target | Error |", "|---|---|---|---|"]
    for name, got, want in rows:
        e = (got - want) / abs(want) * 100
        lines.append(f"| {name} | {got:.3f} V | {want} V | {e:+.1f} % |")
        if abs(e) > 3:
            errs.append(name)
    lines.append(f"\nTPS54302 EN UVLO start-up threshold: {uvlo:.2f} V (needs < 11.5 V at the jack after the diode: "
                 f"{'ok' if uvlo < 11 else 'TOO HIGH'}).")
    R.add("Regulator outputs computed from the divider values in the netlist", not errs and uvlo < 11, lines)


def check_testability(R, d):
    rails = ["VIN", "+5V", "+6V5", "-6V5", "+5VA", "-5VA", "+3V3A", "VDRV", "+3V3", "3V3_MIC", "GND"]
    lines = ["| Rail | Test point | Isolation | LED |", "|---|---|---|---|"]
    errs = []
    for rail in rails:
        net = next((n for n in d.nets if d.base(n) == rail), None)
        nodes = d.nets.get(net, [])
        tp = [r for r, p in nodes if r.startswith("TP")]
        iso = [r for r, p in nodes if r.startswith("JP") or r in ("J9502",) or r.startswith("FB")]
        led = [r for r, p in nodes if r.startswith("R") and any(
            d.comps[x]["value"] == "green" for x, _ in d.nets.get(d.other_end(r, net), []))]
        need_iso = rail not in ("VIN", "GND")
        need_led = rail not in ("+5VA", "-5VA", "+3V3A", "GND")  # analog LDOs: no LED
        ok = bool(tp) and (bool(iso) or not need_iso) and (bool(led) or not need_led)
        if rail == "VIN":
            iso = ["(input)"]
        lines.append(f"| {rail} | {', '.join(tp) or '**none**'} | {', '.join(iso) or ('**none**' if need_iso else '-')} "
                     f"| {', '.join(led) or ('**none**' if need_led else '- (by rule)')} |")
        if not ok:
            errs.append(rail)
    R.add("Testability rule: every rail has a test point, isolation and an LED (not the analog LDOs)", not errs,
          lines + ([f"\nMissing on: {errs}"] if errs else []))


def check_single_nets(R, d):
    single = [n for n, nodes in d.nets.items() if len(nodes) == 1 and not n.startswith("unconnected")]
    unc = [n for n in d.nets if n.startswith("unconnected")]
    allowed = []
    bad = [n for n in single if n not in allowed]
    kinds = Counter(re.sub(r"unconnected-\(\w+?-(.*)-Pad\d+\)", r"\1", n) for n in unc)
    R.add("Single-pin and unconnected nets", not bad,
          [f"Single-pin nets: {len(single)}. Unexpected: {bad[:20]}",
           f"Intentionally unconnected pins (no-connect flags): {len(unc)}: " +
           ", ".join(f"{k} x{v}" for k, v in kinds.most_common(15))])


def check_power_budget(R, d):
    # typical / max currents from the datasheets (mA)
    rows = [("+5VA", "OPA1678 (dual, 2 x 2.0 mA typ, 2.5 max)", 64, 4.0, 5.0),
            ("+3V3A", "TLV320ADC5140 AVDD (21 mA at 48 kSPS, about 28 mA estimated at 192 kSPS)", 16, 28.0, 35.0),
            ("-5VA", "OPA1678", 64, 4.0, 5.0),
            ("+3V3", "ADC IOVDD + 74LVC2G34 + mics + LEDs + I2C pull-ups", 1, 60.0, 100.0),
            ("+5V", "74HCT595 (static ~0.1 mA, dynamic at 10 MHz ~2 mA)", 16, 2.0, 4.0),
            ("+5V", "TPS65131 input for +-6V5 (2 x 0.26 A typ / 0.32 A max at 6.5 V, 85 %)", 1, 800.0, 980.0),
            ("+5V", "TPS7A4700 input for +3V3A (= its load)", 1, 450.0, 560.0),
            ("+5V", "TLV75733P input (+3V3 load)", 1, 60.0, 100.0),
            ("VDRV", "64 elements CW at 40 kHz, 3.73 mA each (sim/reports/tx_cw.md: C V^2 f, 47 R, 4.7 k limiter, element)", 64, 3.73, 4.0),
            ("VDRV", "UCC27524 quiescent", 32, 1.0, 1.5)]
    tot = defaultdict(lambda: [0.0, 0.0])
    lines = ["| Rail | Load | Qty | Typ mA | Max mA |", "|---|---|---|---|---|"]
    for rail, what, q, typ, mx in rows:
        tot[rail][0] += q * typ
        tot[rail][1] += q * mx
        lines.append(f"| {rail} | {what} | {q} | {q * typ:.0f} | {q * mx:.0f} |")
    cap = {"+5VA": 1000, "-5VA": 1000, "+3V3A": 1000, "+3V3": 1000, "+5V": 3000, "VDRV": 900}
    lines += ["", "| Rail | Total typ | Total max | Regulator limit | Margin |", "|---|---|---|---|---|"]
    errs = []
    for rail, (t, m) in tot.items():
        lines.append(f"| {rail} | {t:.0f} mA | {m:.0f} mA | {cap[rail]} mA | {100 * (1 - m / cap[rail]):.0f} % |")
        if m > 0.85 * cap[rail]:
            errs.append(rail)
    lines.append("\nVIN (12 V): +5V buck input about 5 V x 1.7 A / 0.9 / 12 V = 0.8 A, plus VDRV boost 15 V x 0.12 A "
                 "/ 0.85 / 12 V = 0.35 A (all 64 elements in CW): about 1.15 A of the 1.5 A adapter, under the 2 A polyfuse hold current.")
    R.add("Power budget (estimates from datasheet currents)", not errs, lines, warn=False)


def main():
    export()
    d = Design(NET)
    R = Report()
    for f in (check_erc, check_counts, check_footprints, check_lcsc, check_datasheets, check_3d_models, check_tx_chain, check_rx_chain, check_clocks,
              check_headers, check_fpga_levels, check_rails, check_decoupling, check_cap_ratings, check_polarity,
              check_regulators, check_testability, check_single_nets, check_power_budget):
        try:
            f(R, d) if f is not check_erc else f(R)
        except Exception as e:  # a crashing check is a failed check, not a missing one
            import traceback
            R.add(f.__name__, False, ["Check crashed:", "```", traceback.format_exc()[-1500:], "```"])
    R.write()


if __name__ == "__main__":
    main()
