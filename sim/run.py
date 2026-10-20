"""Run the LTspice testbenches and write a Markdown report for each one.

Every simulation goes through this script, so every run leaves a report:
  sim/reports/<name>.md + <name>.png   one report per testbench (results, limits, pass/fail, plot)
  sim/reports/README.md                index with the pass/fail summary of all of them

  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe sim\\run.py            (all)
  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe sim\\run.py rx_chain   (one)

To add a testbench: write sim/<name>.cir (with .meas lines where useful) and add an entry to
SIMS below with its checks and plot.
"""

import cmath
import datetime
import math
import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from ltraw import integrate, read_raw as _read_raw  # noqa: E402


class _CI(dict):
    """LTspice keeps the case of node names: look them up case-insensitively."""
    def __getitem__(self, k):
        if dict.__contains__(self, k):
            return dict.__getitem__(self, k)
        for kk in self:
            if kk.lower() == k.lower():
                return dict.__getitem__(self, kk)
        raise KeyError(k)


def read_raw(path):
    return _CI(_read_raw(path))

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"
LTSPICE = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ADI" / "LTspice" / "LTspice.exe"
KICAD_CLI = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "KiCad" / "10.0" / "bin" / "kicad-cli.exe"
GEN = HERE / "gen"
NODE_MAP = {"GND": "0", "+5VA": "VP5A", "-5VA": "VN5A"}


def export_channel():
    """Channel circuit straight from kicad/channel.kicad_sch (parts carry Sim.* fields set by
    scripts/sch_sim.py), as sim/gen/channel.cir with LTspice-friendly node names:
    /XD -> XD, GND -> 0, +5VA -> VP5A, -5VA -> VN5A."""
    GEN.mkdir(exist_ok=True)
    raw = GEN / "channel_kicad.cir"
    subprocess.run([str(KICAD_CLI), "sch", "export", "netlist", "--format", "spice", "-o", str(raw),
                    str(HERE.parent / "kicad" / "channel.kicad_sch")], check=True, capture_output=True)
    out = ["* Channel exported from kicad/channel.kicad_sch by sim/run.py: do not edit"]
    for line in raw.read_text(encoding="utf-8").splitlines():
        if line.startswith((".title", ".end")) or not line.strip():
            continue
        if line.startswith(".include"):
            out.append(line)
            continue
        tok = line.split()
        tok = [tok[0]] + [NODE_MAP.get(x, x.lstrip("/")) for x in tok[1:]]
        out.append(" ".join(tok))
    (GEN / "channel.cir").write_text("\n".join(out) + "\n", encoding="utf-8")
    return out


# ---- running and parsing ------------------------------------------------------------------
def run_ltspice(cir, timeout=600):
    subprocess.run([str(LTSPICE), "-b", str(cir)], cwd=HERE, timeout=timeout, check=False)
    log = cir.with_suffix(".log")
    text = log.read_bytes().replace(b"\x00", b"").decode("latin-1")
    if "Fatal Error" in text or "Error" in text.split("Total elapsed")[0][-400:]:
        raise RuntimeError(f"{cir.name}: {text[:400]}")
    return text


def parse_value(s):
    """'0.633' -> 0.633; '(26.97dB,168.08°)' -> (26.97, 168.08)."""
    s = s.strip()
    m = re.match(r"\(([-\d.e+]+)dB,([-\d.e+]+)", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    return float(s)


def parse_meas(text):
    """{name: [value per step]} from an LTspice log (plain and .step formats)."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^(\w+): (.*)$", line)
        if not m or "=" not in m.group(2):
            continue
        when = re.search(r" AT ([-\d.e+]+)\s*$", m.group(2))  # WHEN results: the value is the AT point
        if when:
            out.setdefault(m.group(1).lower(), []).append(float(when.group(1)))
            continue
        v = re.search(r"=\s*(\([^)]*\)|[-\d.e+]+)", m.group(2))
        if v:
            out.setdefault(m.group(1).lower(), []).append(parse_value(v.group(1)))
    for block in re.finditer(r"^Measurement: (\w+)\n[^\n]*\n((?:\s+\d+\t[^\n]*\n)+)", text, re.M):
        vals = [parse_value(line.split("\t")[1]) for line in block.group(2).strip("\n").split("\n")]
        out[block.group(1).lower()] = vals
    return out


def steps(t):
    s = [0] + [k for k in range(1, len(t)) if t[k] < t[k - 1]]
    return list(zip(s, s[1:] + [len(t)]))


# ---- checks --------------------------------------------------------------------------------
class Check:
    def __init__(self, label, value, unit, lo=None, hi=None, why=""):
        self.label, self.value, self.unit, self.lo, self.hi, self.why = label, value, unit, lo, hi, why

    @property
    def status(self):
        if self.value is None:
            return "FAIL (no value)"
        if (self.lo is not None and self.value < self.lo) or (self.hi is not None and self.value > self.hi):
            return "FAIL"
        return "pass" if (self.lo is not None or self.hi is not None) else "info"

    def limit(self):
        if self.lo is not None and self.hi is not None:
            return f"{self.lo:g} … {self.hi:g}"
        if self.lo is not None:
            return f"≥ {self.lo:g}"
        if self.hi is not None:
            return f"≤ {self.hi:g}"
        return "—"


def fmt(v):
    if v is None:
        return "—"
    a = abs(v)
    return f"{v:.3g}" if (a >= 1e-3 or a == 0) and a < 1e5 else f"{v:.3e}"


# ---- testbench definitions -----------------------------------------------------------------
def rx_chain():
    text = run_ltspice(HERE / "rx_chain.cir")
    m = parse_meas(text)
    db = lambda k: m[k][0][0]  # noqa: E731
    deg = lambda k: m[k][0][1]  # noqa: E731
    checks = [
        Check("Element loading by the front end at 40 kHz", db("r_xd"), "dB", lo=-1.0,
              why="the front end must not load the 2.2 nF element"),
        Check("Gain, open-circuit element voltage → ADC pin, 40 kHz", db("r_adc"), "dB", 25, 30,
              why="design value +27.6 dB amplifier; 1 m echo 120 mV → ~2.7 V at the ADC"),
        Check("Amplifier pass band, low −3 dB", m["f_lo"][0], "Hz", 25e3, 35e3),
        Check("Amplifier pass band, high −3 dB", m["f_hi"][0], "Hz", 45e3, 60e3),
        Check("Amplifier phase change 39 → 41 kHz", abs(deg("r_amp39") - deg("r_amp41")), "°", hi=20,
              why="per-element calibration removes it; small keeps element-to-element spread small"),
        Check("AD7606C-16 filter (220 kHz) loss at 39 kHz", db("r_c39"), "dB", lo=-0.1),
        Check("AD7606C-16 filter (220 kHz) loss at 41 kHz", db("r_c41"), "dB", lo=-0.1),
        Check("AD7606B filter (22.5 kHz) loss at 40 kHz, for comparison", (db("r_b39") + db("r_b41")) / 2, "dB",
              why="why the AD7606B was replaced"),
    ]
    d = read_raw(HERE / "rx_chain.raw")
    f = [abs(x) for x in d["frequency"]]
    ref = d["V(xdo)"]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    for name, lab in (("V(rx_out)", "ADC pin"), ("V(adcc)", "after AD7606C-16 filter (220 kHz)"),
                      ("V(adcb)", "after AD7606B filter (22.5 kHz)")):
        h = [v / r for v, r in zip(d[name], ref)]
        a1.semilogx(f, [20 * math.log10(abs(x)) for x in h], label=lab)
        a2.semilogx(f, [math.degrees(cmath.phase(x)) for x in h], label=lab)
    a1.set_ylabel("gain vs open-circuit element (dB)")
    a1.set_ylim(-40, 35)
    a2.set_ylabel("phase (deg)")
    a2.set_xlabel("frequency (Hz)")
    for a in (a1, a2):
        a.axvline(40e3, color="grey", ls=":")
        a.grid(True, which="both", alpha=0.3)
    a1.legend()
    return checks, fig


def rx_noise():
    run_ltspice(HERE / "rx_noise.cir")
    d = read_raw(HERE / "rx_noise.raw")
    f = [abs(x) for x in d["frequency"]]
    on = [abs(x) for x in d["V(onoise)"]]
    g = read_raw(HERE / "rx_chain.raw")  # gain element -> ADC pin, from the rx_chain run
    gf = [abs(x) for x in g["frequency"]]
    gh = [abs(a / b) for a, b in zip(g["V(rx_out)"], g["V(xdo)"])]

    def gain_at(fx):
        j = min(range(len(gf)), key=lambda i: abs(gf[i] - fx))
        return gh[j]
    inn = [o / gain_at(fx) for o, fx in zip(on, f)]
    k = min(range(len(f)), key=lambda i: abs(f[i] - 40e3))
    band = integrate(f, [x * x for x in on], 30e3, 50e3) ** 0.5
    wide = integrate(f, [x * x for x in on], 1e3, 1e6) ** 0.5
    checks = [
        Check("Output noise density at 40 kHz (ADC pin)", on[k] * 1e9, "nV/√Hz", hi=500),
        Check("Input-referred noise at 40 kHz (element EMF)", inn[k] * 1e9, "nV/√Hz", hi=50,
              why="dominated by the element's Rm and the 4.7 k limiter, not the op-amp"),
        Check("Output noise, 30–50 kHz", band * 1e6, "µV rms", hi=60,
              why="AD7606C-16 noise in the same band is estimated at ~50 µV rms (±5 V range)"),
        Check("Output noise, 1 kHz–1 MHz", wide * 1e6, "µV rms", hi=100),
        Check("SNR of a 1 m echo (2.7 V peak) in 30–50 kHz", 20 * math.log10(2.7 / math.sqrt(2) / band), "dB",
              lo=60),
    ]
    fig, a = plt.subplots(figsize=(9, 4.5))
    a.loglog(f, [x * 1e9 for x in on], label="output (ADC pin)")
    a.loglog(f, [x * 1e9 for x in inn], label="referred to the element EMF")
    a.axvline(40e3, color="grey", ls=":")
    a.set_xlabel("frequency (Hz)")
    a.set_ylabel("noise density (nV/√Hz)")
    a.grid(True, which="both", alpha=0.3)
    a.legend()
    return checks, fig


def tx_burst():
    template = (HERE / "tx_burst.cir").read_text()
    fig, axs = plt.subplots(3, 1, figsize=(9, 8), sharex=True)
    checks = []
    burst_end = 0.1e-3 + 8 / 40e3
    for q in (20, 40):
        cir = HERE / f"tx_burst_q{q}.cir"
        cir.write_text(re.sub(r"QM=\d+", f"QM={q}", template, count=1))
        m = parse_meas(run_ltspice(cir))
        v = lambda k: m[k][0]  # noqa: E731
        if q == 20:
            checks += [
                Check("Element peak voltage", v("xd_pk"), "V", 13, 15.5, why="VDRV 15 V minus one diode"),
                Check("Driver peak current", v("i_pk"), "A", hi=1.0, why="UCC27524 rated 5 A peak"),
                Check("Limiter node, positive peak", v("lim_pk"), "V", hi=0.9, why="OPA1678 input protection"),
                Check("Limiter node, negative peak", v("lim_min"), "V", lo=-0.9),
                Check("Average power in the 47 Ω drive resistor", v("p_rdrv") * 1e3, "mW", hi=50,
                      why="0603 rating 100 mW, keep 2× margin; CW is similar"),
                Check("Average power in the 4.7 kΩ limiter resistor", v("p_rlim") * 1e3, "mW", hi=50),
                Check("ADC pin peak during TX", v("adc_pk"), "V", hi=5.0, why="inside the ±5 V ADC range"),
            ]
        checks.append(Check(f"Recovery to < 100 mV after the burst, element Q = {q}",
                            (v("t_100mv") - burst_end) * 1e3, "ms", hi=2.5,
                            why="element ringing; 2 ms ≈ 0.34 m blind range"))
        d = read_raw(cir.with_suffix(".raw"))
        t = [x * 1e3 for x in d["time"]]
        for a, n in zip(axs, ("V(xd)", "V(lim)", "V(rx_out)")):
            a.plot(t, d[n], lw=0.6, label=f"{n}, Q={q}")
    for a in axs:
        a.set_ylabel("V")
        a.legend(loc="upper right")
        a.grid(True, alpha=0.3)
    axs[2].set_xlabel("time (ms)")
    return checks, fig


def clock_steps(name, var, labels, node, ylabel):
    run_ltspice(HERE / f"{name}.cir")
    d = read_raw(HERE / f"{name}.raw")
    t = d["time"]
    rows, fig = [], None
    fig, a = plt.subplots(figsize=(9, 4.5))
    for (i0, i1), lab in zip(steps(t), labels):
        w = [d[node][k] for k in range(i0, i1) if t[k] > 100e-9]
        cr, st = 0, None
        for k in range(i0, i1):
            if t[k] < 100e-9:
                continue
            x = d[node][k]
            st = x > 1.65 if st is None else st
            if (x > 1.75 and not st) or (x < 1.55 and st):
                st, cr = not st, cr + 1
        rows.append((lab, max(w), min(w), cr))
        a.plot([x * 1e9 for x in t[i0:i1]], d[node][i0:i1], lw=0.8, label=lab)
    a.axhline(1.65, color="grey", ls=":")
    a.set_xlabel("time (ns)")
    a.set_ylabel(ylabel)
    a.grid(True, alpha=0.3)
    a.legend(loc="upper right")
    return rows, fig


def clock_si():
    rows, fig = clock_steps("clock_si", "Rs", ["Rs = 0 Ω", "Rs = 33 Ω (design)"], "V(l4)", "4th load (V)")
    lab, vmax, vmin, cr = rows[1]
    checks = [Check("4th load overshoot, 33 Ω", vmax, "V", hi=3.6, why="74LVC/74HCT abs. max VCC+0.5 V"),
              Check("4th load undershoot, 33 Ω", vmin, "V", lo=-0.3),
              Check("Threshold crossings 100–300 ns (20 MHz → 8)", cr, "", 8, 8, why="no double clocking"),
              Check("4th load overshoot without the resistor (comparison)", rows[0][1], "V"),
              Check("4th load undershoot without the resistor (comparison)", rows[0][2], "V")]
    return checks, fig


def clock_si_drive():
    rows, fig = clock_steps("clock_si_drive", "kRfp", ["DRIVE 8 (~40 Ω)", "~60 Ω", "DRIVE 4 (~80 Ω, design)"],
                            "V(hin)", "buffer input (V)")
    checks = []
    for lab, vmax, vmin, cr in rows:
        design = "design" in lab
        checks += [Check(f"{lab}: overshoot at buffer input", vmax, "V", hi=3.6 if design else None),
                   Check(f"{lab}: undershoot at buffer input", vmin, "V", lo=-0.3 if design else None,
                         why="74LVC2G34 input rating −0.5 V" if design else ""),
                   Check(f"{lab}: threshold crossings (8 expected)", cr, "", 8 if design else None,
                         8 if design else None)]
    return checks, fig


def clock_si_in():
    rows, fig = clock_steps("clock_si_in", "mode",
                            ["none", "series 47 Ω", "RC 100 Ω + 47 pF (DNP fallback)", "RC 100 Ω + 100 pF"],
                            "V(hin)", "buffer input (V)")
    checks = []
    for lab, vmax, vmin, cr in rows:
        fb = "fallback" in lab
        checks += [Check(f"{lab}: overshoot", vmax, "V", hi=3.6 if fb else None),
                   Check(f"{lab}: undershoot", vmin, "V", lo=-0.3 if fb else None)]
    return checks, fig


def rx_mc():
    """Monte Carlo of the whole channel, built from the KiCad export: 200 copies of gen/channel.cir,
    each R and C at a random value inside the tolerance its schematic Value states (1 % if given;
    C0G 5 %, X5R/X7R 10 % otherwise; resistors 1 %). Element and op-amp nominal.
    (LTspice's own mc() + .step misbehaves here, so the copies are generated.)"""
    import random
    rnd = random.Random(20261020)
    sheet = (HERE.parent / "kicad" / "channel.kicad_sch").read_text(encoding="utf-8")
    tol = {}
    for blk in sheet.split("\n\t(symbol\n")[1:]:
        ref = re.search(r'\(property "Reference" "([^"]+)"', blk)
        val = re.search(r'\(property "Value" "([^"]*)"', blk)
        if ref and val:
            v = val.group(1)
            pct = re.search(r"([\d.]+)\s*%", v)
            tol[ref.group(1)] = (float(pct.group(1)) / 100 if pct else 0.10 if re.search("X[57]R", v)
                                 else 0.05 if "C0G" in v else 0.01)
    chan = [l for l in (GEN / "channel.cir").read_text().splitlines() if l and not l.startswith("*")]
    include = [l for l in chan if l.startswith(".include")]
    parts = [l for l in chan if not l.startswith(".")]
    keep = {"0", "VP5A", "VN5A"}
    mult = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "Meg": 1e6}

    def num(s):
        m_ = re.match(r"([\d.]+)(Meg|[pnumk])?$", s)
        return float(m_.group(1)) * mult.get(m_.group(2) or "", 1)

    n, batch = 200, 10  # 10 copies per netlist: LTspice stalls on one netlist with 400 op-amp models
    head = ["* Channel Monte Carlo, written by sim/run.py from gen/channel.cir (do not edit)", *include,
            ".param ECHO_AC=1 QM=20", ".func echo(t)=0", "VP VP5A 0 5", "VN VN5A 0 -5", "XREF xdo 0 CUSA_TR65"]
    lines, m = list(head), {}
    for i in range(n):
        s = f"_{i}"
        for l in parts:
            tok = l.split()
            ref = tok[0]
            if ref[0] in "RC":
                nodes, val = tok[1:3], num(tok[3])
                val *= 1 + rnd.uniform(-tol.get(ref, 0.01), tol.get(ref, 0.01))
                tok = [ref + s] + [x if x in keep else x + s for x in nodes] + [f"{val:.6g}"]
            else:  # subcircuit: X<ref> nodes... MODEL
                tok = [ref + s] + [x if x in keep else x + s for x in tok[1:-1]] + [tok[-1]]
            lines.append(" ".join(tok))
        lines += [f"Rdrv_on DRV_OUT{s} 0 0.4", f"Radc RX_OUT{s} 0 1Meg".replace("Radc", f"Radc{s}"),
                  f".meas AC g{i} FIND V(RX_OUT{s})/V(xdo) AT 40k"]
        lines[-3] = lines[-3].replace("Rdrv_on", f"Rdrv_on{s}")
        if (i + 1) % batch == 0:
            cir = HERE / "rx_mc.cir"
            cir.write_text("\n".join(lines + [".ac lin 1 40k 40k", ".end"]) + "\n")
            m.update(parse_meas(run_ltspice(cir, timeout=120)))
            lines = list(head)
    vals = [m[f"g{i}"][0] for i in range(n)]
    gains = [g for g, _ in vals]
    phases = [p for _, p in vals]
    mean = lambda x: sum(x) / len(x)  # noqa: E731
    sd = lambda x: (sum((a - mean(x)) ** 2 for a in x) / (len(x) - 1)) ** 0.5  # noqa: E731
    checks = [
        Check("Mean gain at 40 kHz", mean(gains), "dB", 26, 29),
        Check("Gain spread, standard deviation", sd(gains), "dB", hi=0.5),
        Check("Gain spread, max - min", max(gains) - min(gains), "dB", hi=2.0,
              why="removed by per-element calibration; small keeps the dynamic range equal"),
        Check("Phase spread, standard deviation", sd(phases), "deg", hi=3.0,
              why="beam pointing error before calibration; 3 deg is about lambda/120"),
        Check("Phase spread, max - min", max(phases) - min(phases), "deg", hi=12.0,
              why="per-element calibration removes it; the brief expected a few degrees with 1 % parts"),
    ]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4))
    a1.hist(gains, bins=25)
    a1.set_xlabel("gain at 40 kHz (dB)")
    a1.set_ylabel("channels")
    a2.hist(phases, bins=25, color="tab:orange")
    a2.set_xlabel("phase at 40 kHz (deg)")
    for a in (a1, a2):
        a.grid(True, alpha=0.3)
    return checks, fig


def rx_overload():
    # One run per amplitude (LTspice's .step is very slow here, and parameters are not visible in
    # the echo function inside the transducer subcircuit): AMPL is replaced by a literal value.
    template = (HERE / "rx_overload.cir").read_text()
    amps = [0.12, 1, 5]
    checks, ref_late = [], None
    fig, a = plt.subplots(figsize=(9, 4.5))
    for a_ in amps:
        cir = GEN / f"rx_overload_{a_:g}V.cir"
        cir.write_text(template.replace("AMPL", f"{a_ * 1.05:g}").replace(".include gen/", ".include "))
        m = parse_meas(run_ltspice(cir, timeout=600))
        d = read_raw(cir.with_suffix(".raw"))
        t = d["time"]
        late = [x for x, v in zip(t, d["V(rx_out)"]) if 0.35e-3 < x < 1.45e-3 and abs(v) > 0.05]
        rec = (max(late) - 0.35e-3) * 1e3 if late else 0.0
        lab = f"echo {a_} V (open-circuit at the element)"
        checks.append(Check(f"{lab}: ADC pin peak", m["adc_pk"][0], "V", hi=5.0, why="inside the ADC's +-5 V range"))
        checks.append(Check(f"{lab}: recovery to < 50 mV after the echo ends", rec, "ms",
                            hi=1.1, why="the next echo (1.5 ms) must be clean"))
        ref_late = ref_late if a_ != amps[0] else m["late_pk"][0]
        checks.append(Check(f"{lab}: later 10 mV echo at the ADC", m["late_pk"][0], "V"))
        if a_ != amps[0]:
            checks.append(Check(f"{lab}: later echo vs after a weak echo", 100 * (m["late_pk"][0] / ref_late - 1), "%",
                                -5, 5, why="the strong echo leaves no lasting distortion or gain change"))
        a.plot([x * 1e3 for x in t], d["V(rx_out)"], lw=0.6, label=f"echo {a_} V")
    a.set_xlabel("time (ms)")
    a.set_ylabel("ADC pin (V)")
    a.grid(True, alpha=0.3)
    a.legend(loc="upper right")
    return checks, fig


def tx_cw():
    m = parse_meas(run_ltspice(HERE / "tx_cw.cir", timeout=1200))
    v = lambda k: m[k][0]  # noqa: E731
    i_el = v("i_vdrv")
    checks = [
        Check("VDRV current per element (CW, 40 kHz)", i_el * 1e3, "mA", hi=5.0,
              why="C V^2 f alone is 1.3 mA; the RX limiter's 4.7 k takes 19 mW of its own element's drive"),
        Check("VDRV current, all 64 elements in CW", i_el * 64 * 1e3, "mA", hi=900,
              why="LM2731X boost limit about 0.9 A at 15 V from 12 V"),
        Check("Power in the 47 R drive resistor", v("p_rdrv") * 1e3, "mW", hi=50, why="0603: 100 mW"),
        Check("Power in the 4.7 k limiter resistor", v("p_rlim") * 1e3, "mW", hi=50, why="0603: 100 mW"),
        Check("Power in the 33 k bleed resistor", v("p_bleed") * 1e3, "mW", hi=50),
        Check("Acoustic power into the element (Rm)", v("p_elem") * 1e3, "mW",
              why="what actually radiates (model)"),
        Check("Average limiter/clamp current", v("i_clamp") * 1e3, "mA", hi=50, why="BAV99 200 mA continuous"),
        Check("Element voltage, peak to peak", v("xd_pp"), "V", 13, 16),
    ]
    d = read_raw(HERE / "tx_cw.raw")
    t = [x * 1e3 for x in d["time"]]
    fig, a = plt.subplots(figsize=(9, 4))
    a.plot(t, d["V(xd)"], lw=0.7, label="element XD")
    a.plot(t, [x * 100 for x in d["I(R101)"]], lw=0.7, label="drive current x100 (A)")
    a.set_xlim(5.8, 5.9)
    a.set_xlabel("time (ms)")
    a.grid(True, alpha=0.3)
    a.legend(loc="upper right")
    return checks, fig


def mic_supply():
    m = parse_meas(run_ltspice(HERE / "mic_supply.cir"))
    db = lambda k: m[k][0][0]  # noqa: E731
    checks = [
        Check("Peaking of the bead + 10 uF resonance", m["h_pk"][0][0], "dB", hi=3.0,
              why="no noise gain in the mics' ultrasonic band"),
        Check("Resonance frequency", m["f_pk"][0] / 1e3, "kHz"),
        Check("Attenuation at the PDM clock (3.072 MHz)", db("h_3m"), "dB", hi=-40),
        Check("Attenuation at 100 MHz", db("h_100m"), "dB", hi=-40),
        Check("Transfer at 40 kHz (in-band; the mic's PSRR handles it)", db("h_40k"), "dB"),
    ]
    d = read_raw(HERE / "mic_supply.raw")
    f = [abs(x) for x in d["frequency"]]
    fig, a = plt.subplots(figsize=(9, 4))
    a.semilogx(f, [20 * math.log10(abs(x)) for x in d["V(out)"]])
    a.set_xlabel("frequency (Hz)")
    a.set_ylabel("+3V3 noise -> 3V3_MIC (dB)")
    a.grid(True, which="both", alpha=0.3)
    return checks, fig


SIMS = {
    "rx_chain": dict(fn=rx_chain, files=["rx_chain.cir"],
                     title="RX chain: gain, band and ADC filter",
                     what="AC response from the transducer's open-circuit voltage through the limiter, the OPA1678 ×6 "
                          "stage and the 40 kHz band-pass to the ADC pin, and through the AD7606C-16 (220 kHz) and "
                          "AD7606B (22.5 kHz) input filters."),
    "rx_noise": dict(fn=rx_noise, files=["rx_noise.cir"],
                     title="RX chain: noise",
                     what="Noise at the ADC pin and referred to the element, including the element's own Rm, the "
                          "limiter resistor and the OPA1678 noise model."),
    "tx_burst": dict(fn=tx_burst, files=["tx_burst.cir"],
                     title="TX burst and listen-after-transmit",
                     what="8 cycles at 40 kHz from a 15 V UCC27524 half through 47 Ω and the crossed diodes into the "
                          "element, then the same element listens. Run for element Q = 20 and 40 (the datasheet's "
                          "1.8 ms response time lies between)."),
    "rx_mc": dict(fn=rx_mc, files=["rx_mc.cir"],
                  title="RX chain: channel-to-channel spread (Monte Carlo)",
                  what="200 copies of the amplifier with 1 % resistors, 1 % C0G band-pass caps, 5 % Cf1 and 10 % "
                       "X7R coupling cap, uniform distribution: gain and phase at 40 kHz. Tells how much per-element "
                       "calibration has to remove."),
    "rx_overload": dict(fn=rx_overload, files=["rx_overload.cir"],
                        title="RX overload: strong near echo, recovery",
                        what="A 10-cycle echo of 0.12 V (1 m), 1 V and 5 V open-circuit at the element, then a 10 mV "
                             "echo 1.4 ms later: clamp, saturation, recovery and whether the later echo is clean."),
    "tx_cw": dict(fn=tx_cw, files=["tx_cw.cir"],
                  title="TX continuous wave: current and dissipation",
                  what="One element driven continuously at 40 kHz from 15 V (split mode / Doppler), steady state: "
                       "supply current per element, power in each resistor and the clamp diodes."),
    "mic_supply": dict(fn=mic_supply, files=["mic_supply.cir"],
                       title="3V3_MIC ferrite filter",
                       what="+3V3 -> 220 R/100 MHz ferrite -> 10 uF + 8 x 100 nF: noise transfer, attenuation at "
                            "the PDM clock and whether the bead/cap resonance peaks."),
    "clock_si": dict(fn=clock_si, files=["clock_si.cir"],
                     title="Clock fan-out: buffer to 4 loads",
                     what="74LVC2G34 output through the series resistor into a 60 Ω trace with four 5 pF loads every "
                          "30 mm, 20 MHz. Compares 0 Ω and 33 Ω."),
    "clock_si_drive": dict(fn=clock_si_drive, files=["clock_si_drive.cir"],
                           title="Ribbon end: FPGA drive strength",
                           what="FPGA output through 6 cm of board trace and a 15 cm, 100 Ω ribbon to the clk_buf input, "
                                "no termination, for FPGA output impedances 40/60/80 Ω (DRIVE 8 … 4)."),
    "clock_si_in": dict(fn=clock_si_in, files=["clock_si_in.cir"],
                        title="Ribbon end: termination options at DRIVE 8",
                        what="Same path with the FPGA at DRIVE 8, comparing no termination, a series resistor and RC "
                             "end terminations at the buffer input."),
}

MODELS = ("Models: OPA1678 = TI OPA167x macro-model (`sim/models/OPA167x.lib`); BAV99 = 1N4148 die model, "
          "Cj 1.5 pF; CUSA-TR65 = Butterworth–Van Dyke, C0 2.2 nF and Rm 3 kΩ from the datasheet, Q assumed; "
          "drivers, buffers and the FPGA are behavioural (values in each netlist header).")


def write_report(name, spec, checks, fig, err=None):
    REPORTS.mkdir(exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# {spec['title']}", "",
             f"Run {now} with LTspice (`sim/run.py {name}`). Netlist: " +
             ", ".join(f"[`sim/{f}`](../{f})" for f in spec["files"]) + ".", "", spec["what"], "", MODELS, ""]
    if err:
        lines += ["## Result: **ERROR**", "", "```", str(err)[:2000], "```"]
    else:
        fails = [c for c in checks if c.status.startswith("FAIL")]
        lines += [f"## Result: **{'FAIL' if fails else 'PASS'}** "
                  f"({sum(c.status == 'pass' for c in checks)} pass, {len(fails)} fail, "
                  f"{sum(c.status == 'info' for c in checks)} info)", "",
                  "| Quantity | Value | Unit | Limit | Status | Note |", "|---|---|---|---|---|---|"]
        for c in checks:
            lines.append(f"| {c.label} | {fmt(c.value)} | {c.unit} | {c.limit()} | {c.status} | {c.why} |")
        if fig is not None:
            fig.suptitle(spec["title"])
            fig.tight_layout()
            fig.savefig(REPORTS / f"{name}.png", dpi=100)
            plt.close(fig)
            lines += ["", f"![{spec['title']}]({name}.png)"]
    (REPORTS / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "ERROR" if err else ("FAIL" if any(c.status.startswith("FAIL") for c in checks) else "PASS")


def write_index():
    rows = []
    for name, spec in SIMS.items():
        p = REPORTS / f"{name}.md"
        if not p.exists():
            rows.append(f"| {spec['title']} | not run | — |")
            continue
        text = p.read_text(encoding="utf-8")
        res = re.search(r"## Result: \*\*(\w+)\*\*([^\n]*)", text)
        when = re.search(r"Run ([\d\- :]+) with", text)
        rows.append(f"| [{spec['title']}]({name}.md) | **{res.group(1)}**{res.group(2)} | {when.group(1)} |")
    index = ["# Simulation reports", "",
             "Written by `sim/run.py` after every simulation run. One report per testbench, with limits and "
             "pass/fail.", "", "| Testbench | Result | Run |", "|---|---|---|", *rows, ""]
    (REPORTS / "README.md").write_text("\n".join(index), encoding="utf-8")


def main(names):
    if not LTSPICE.exists():
        raise SystemExit(f"LTspice not found at {LTSPICE}")
    export_channel()
    print("channel exported from KiCad -> sim/gen/channel.cir")
    for name in names or SIMS:
        spec = SIMS[name]
        try:
            checks, fig = spec["fn"]()
            status = write_report(name, spec, checks, fig)
        except Exception as e:  # report the failure instead of losing it
            status = write_report(name, spec, [], None, err=e)
        print(f"{name}: {status} -> sim/reports/{name}.md")
    write_index()
    print("index: sim/reports/README.md")


if __name__ == "__main__":
    main(sys.argv[1:])
