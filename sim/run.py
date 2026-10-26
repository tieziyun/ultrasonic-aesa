"""Run the LTspice testbenches and write a plain-language report for each one.

Every simulation goes through this script, so every run leaves a report:
  sim/reports/<name>.md + <name>.png   question, answer, why it matters, how to read the plot, numbers
  sim/reports/README.md                overview of all tests, in signal-path order

  %LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe sim\\run.py            (all)
  %LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe sim\\run.py rx_chain   (one)

Before running, the transceiver channel is exported from kicad/channel.kicad_sch (sim/gen/channel.cir),
so the channel tests always simulate the current schematic. To add a test: write sim/<name>.cir and
add an entry to SIMS with its question, explanation and checks.
"""

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

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"
GEN = HERE / "gen"
LTSPICE = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ADI" / "LTspice" / "LTspice.exe"
KICAD_CLI = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "KiCad" / "10.0" / "bin" / "kicad-cli.exe"
NODE_MAP = {"GND": "0", "+5VA": "VP5A", "-5VA": "VN5A"}
SOUND = 343.0          # m/s in air
WAVELEN = SOUND / 40e3  # 8.6 mm at 40 kHz
ADC_FS = 1.414         # TLV320ADC5140 single-ended full scale, V peak (1 Vrms)
ADC_DIV = 909 / (2000 + 909)  # 2k / (1k || 10 k ADC input): RX_OUT -> ADC pin
ECHO_1M = 0.12         # V at the element terminals for a target at 1 m: CUSA-TR65 datasheet figure, kept as
                       # the reference (the CUSP-TR80 sheet gives none; it transmits 5 dB stronger)
RED, GREEN, GREY = "#d62728", "#2ca02c", "#7f7f7f"


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


# ---- export, run, parse --------------------------------------------------------------------
def export_channel():
    """Channel circuit straight from kicad/channel.kicad_sch (parts carry Sim.* fields set by
    scripts/sch_sim.py), as sim/gen/channel.cir: /XD -> XD, GND -> 0, +5VA -> VP5A, -5VA -> VN5A."""
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
        out.append(" ".join([tok[0]] + [NODE_MAP.get(x, x.lstrip("/")) for x in tok[1:]]))
    (GEN / "channel.cir").write_text("\n".join(out) + "\n", encoding="utf-8")


def run_ltspice(cir, timeout=600):
    subprocess.run([str(LTSPICE), "-b", str(cir)], cwd=HERE, timeout=timeout, check=False)
    text = cir.with_suffix(".log").read_bytes().replace(b"\x00", b"").decode("latin-1")
    if "Fatal Error" in text or "Error" in text.split("Total elapsed")[0][-400:]:
        raise RuntimeError(f"{cir.name}: {text[:400]}")
    return text


def parse_value(s):
    """'0.633' -> 0.633; '(26.97dB,168.08°)' -> (26.97, 168.08)."""
    s = s.strip()
    m = re.match(r"\(([-\d.e+]+)dB,([-\d.e+]+)", s)
    return (float(m.group(1)), float(m.group(2))) if m else float(s)


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
        out[block.group(1).lower()] = [parse_value(l.split("\t")[1]) for l in block.group(2).strip("\n").split("\n")]
    return out


def steps(t):
    s = [0] + [k for k in range(1, len(t)) if t[k] < t[k - 1]]
    return list(zip(s, s[1:] + [len(t)]))


def db2lin(db):
    return 10 ** (db / 20)


# ---- checks ----------------------------------------------------------------------------------
class Check:
    def __init__(self, label, value, unit, lo=None, hi=None, why=""):
        self.label, self.value, self.unit, self.lo, self.hi, self.why = label, value, unit, lo, hi, why

    @property
    def status(self):
        if self.value is None:
            return "FAIL"
        if (self.lo is not None and self.value < self.lo) or (self.hi is not None and self.value > self.hi):
            return "FAIL"
        return "pass" if (self.lo is not None or self.hi is not None) else "info"

    def limit(self):
        if self.lo is not None and self.hi is not None:
            return f"{self.lo:.3g} … {self.hi:.3g}"
        if self.lo is not None:
            return f"≥ {self.lo:.3g}"
        if self.hi is not None:
            return f"≤ {self.hi:.3g}"
        return "—"


def fmt(v):
    if v is None:
        return "—"
    a = abs(v)
    return f"{v:.3g}" if (a >= 1e-3 or a == 0) and a < 1e5 else f"{v:.3e}"


def finish(ax, xlabel=None, ylabel=None, legend=True):
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.grid(True, which="both", alpha=0.3)
    if legend:
        ax.legend(loc="best", fontsize=8)


# ---- receiver --------------------------------------------------------------------------------
def rx_chain():
    m = parse_meas(run_ltspice(HERE / "rx_chain.cir"))
    db = lambda k: m[k][0][0]  # noqa: E731
    deg = lambda k: m[k][0][1]  # noqa: E731
    g = db("r_adc")
    lin = db2lin(g)
    pk = ECHO_1M * lin
    dbfs = 20 * math.log10(pk / ADC_FS)
    checks = [
        Check("Gain from the element's echo voltage to the ADC input pin, 40 kHz", g, "dB", 14, 19,
              why=f"×{lin:.1f}: a 1 m echo (0.12 V) arrives as {pk:.2f} V peak"),
        Check("1 m echo relative to the ADC full scale (±1.41 V)", dbfs, "dBFS", -12, -3,
              why="room above it for closer, stronger echoes; the receiver itself clips at ±4.2 V = ±1.27 V here"),
        Check("Gain of the amplifier (element side of the divider), 40 kHz", db("r_out"), "dB", 25, 30),
        Check("ADC input divider at 40 kHz (2k/1k, 100 nF, 10 k input)", db("r_div"), "dB", -11.5, -9.5,
              why=f"×{db2lin(db('r_div')):.2f}"),
        Check("Phase shift of the 100 nF coupling at 40 kHz", abs(deg("r_div")), "°", hi=1.0,
              why="its 150 Hz corner is far below the band; any spread between channels is calibrated out"),
        Check("Signal lost because the receiver loads the transducer", db("r_xd"), "dB", lo=-1.0,
              why="the 2.2 nF transducer is a weak source; the receiver must not load it"),
        Check("Pass band, lower edge (−3 dB)", m["f_lo"][0] / 1e3, "kHz", 25, 35, why="rejects low-frequency noise"),
        Check("Pass band, upper edge (−3 dB)", m["f_hi"][0] / 1e3, "kHz", 45, 60,
              why="wide enough for short pings (range resolution)"),
        Check("Phase change from 39 to 41 kHz", abs(deg("r_amp39") - deg("r_amp41")), "°", hi=20,
              why="fixed per channel, removed by calibration"),
    ]
    answer = (f"A 1 m echo (0.12 V at the transducer) reaches the ADC as **{pk:.2f} V peak**, {abs(dbfs):.1f} dB below "
              f"its ±1.41 V full scale: the receiver amplifies ×{lin:.1f} ({g:.1f} dB) at 40 kHz after the 2k/1k divider and "
              f"passes {m['f_lo'][0] / 1e3:.0f}–{m['f_hi'][0] / 1e3:.0f} kHz. The ADC's own filter is digital and flat "
              f"to about 86 kHz, so nothing is lost at 40 kHz.")
    d = read_raw(HERE / "rx_chain.raw")
    f = [abs(x) for x in d["frequency"]]
    ref = d["V(xdo)"]
    fig, ax = plt.subplots(figsize=(9, 5))
    for name, lab, col in (("V(rx_out)", "amplifier output (RX_OUT)", "tab:blue"), ("V(adc)", "at the ADC input pin", GREEN)):
        ax.semilogx(f, [20 * math.log10(abs(v / r)) for v, r in zip(d[name], ref)], label=lab, color=col)
    ax.axvspan(m["f_lo"][0], m["f_hi"][0], color=GREEN, alpha=0.08, label="pass band")
    ax.axvline(40e3, color=GREY, ls=":")
    ax.annotate(f"40 kHz: ×{lin:.1f} (+{g:.0f} dB)\n1 m echo → {pk:.2f} V ({dbfs:.0f} dBFS)", xy=(40e3, g),
                xytext=(1.5e5, g + 6), arrowprops=dict(arrowstyle="->"))
    ax.set_ylim(-40, 35)
    finish(ax, "frequency (Hz)", "signal ÷ echo at transducer (dB)")
    return checks, fig, answer


def rx_noise():
    run_ltspice(HERE / "rx_noise.cir")
    d = read_raw(HERE / "rx_noise.raw")
    f = [abs(x) for x in d["frequency"]]
    on = [abs(x) for x in d["V(onoise)"]]
    g = read_raw(HERE / "rx_chain.raw")  # gain transducer -> ADC pin, from the rx_chain run
    gf = [abs(x) for x in g["frequency"]]
    gh = [abs(a / b) for a, b in zip(g["V(adc)"], g["V(xdo)"])]

    def gain_at(fx):
        return gh[min(range(len(gf)), key=lambda i: abs(gf[i] - fx))]
    inn = [o / gain_at(fx) for o, fx in zip(on, f)]
    k = min(range(len(f)), key=lambda i: abs(f[i] - 40e3))
    band = integrate(f, [x * x for x in on], 30e3, 50e3) ** 0.5
    # TLV320ADC5140: SNR 107 dB (A-weighted, 2 Vrms differential, DRE off); single-ended 1 Vrms full scale
    # taken 6 dB worse -> about 9 uV rms in a 20 kHz band; same width as 30-50 kHz.
    adc_noise = 1.0 / db2lin(101)
    total = (band ** 2 + adc_noise ** 2) ** 0.5
    echo_adc = ECHO_1M * gain_at(40e3)
    snr = 20 * math.log10(echo_adc / math.sqrt(2) / total)
    e_min = total / gain_at(40e3) * math.sqrt(2) * db2lin(10)  # echo 10 dB above the noise, at the transducer
    checks = [
        Check("Receiver noise at the ADC input pin, 30–50 kHz", band * 1e6, "µV rms", hi=20,
              why="the FPGA's digital filter keeps only this band"),
        Check("ADC's own noise in the same band (estimate, not simulated)", adc_noise * 1e6, "µV rms",
              why="TLV320ADC5140 SNR 107 dB differential, taken 6 dB worse single-ended"),
        Check("Noise density at 40 kHz, at the ADC input pin", on[k] * 1e9, "nV/√Hz", hi=150),
        Check("Same, referred back to the transducer", inn[k] * 1e9, "nV/√Hz", hi=50,
              why="mostly the transducer's own loss resistance and the 4.7 kΩ protection resistor"),
        Check("1 m echo above the total noise (receiver + ADC)", snr, "dB", lo=60),
        Check("Weakest echo that is still 10 dB above the noise", e_min * 1e6, "µV",
              why=f"{ECHO_1M / e_min:.0f}× weaker than a 1 m echo"),
    ]
    answer = (f"In the band that matters (30–50 kHz) the receiver adds **{band * 1e6:.0f} µV rms** of noise at the ADC input, "
              f"about the same as the ADC itself (~{adc_noise * 1e6:.0f} µV). A 1 m echo is **{snr:.0f} dB** above "
              f"the total. An echo is still clearly detectable (10 dB above noise) down to about "
              f"{e_min * 1e6:.0f} µV at the transducer, {ECHO_1M / e_min:.0f}× weaker than a 1 m echo.")
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.loglog(f, [x * 1e9 for x in on], label="receiver noise at the ADC input pin", color="tab:blue")
    ax.loglog(f, [x * 1e9 for x in inn], label="same noise, referred back to the transducer", color="tab:orange")
    adc_density = adc_noise / math.sqrt(20e3)
    ax.axhline(adc_density * 1e9, color=RED, ls="--", label="ADC's own noise (estimate)")
    ax.axvspan(30e3, 50e3, color=GREEN, alpha=0.1, label="band that matters (30–50 kHz)")
    finish(ax, "frequency (Hz)", "noise (nV per √Hz)")
    return checks, fig, answer


def rx_mc():
    """200 copies of the exported channel, each R and C at a random value inside the tolerance its
    schematic Value states (1 % if given; C0G 5 %, X5R/X7R 10 % otherwise; resistors 1 %).
    LTspice's own mc() + .step misbehaves on this netlist, so the copies are generated here, 10 per run."""
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

    n, batch = 200, 10
    head = ["* Channel Monte Carlo, written by sim/run.py from gen/channel.cir (do not edit)", *include,
            ".param ECHO_AC=1 QM=20", ".func echo(t)=0", "VP VP5A 0 5", "VN VN5A 0 -5", "XREF xdo 0 CUSP_TR80"]
    lines, m = list(head), {}
    for i in range(n):
        s = f"_{i}"
        for l in parts:
            tok = l.split()
            ref = tok[0]
            if ref[0] in "RC":
                val = num(tok[3]) * (1 + rnd.uniform(-tol.get(ref, 0.01), tol.get(ref, 0.01)))
                tok = [ref + s] + [x if x in keep else x + s for x in tok[1:3]] + [f"{val:.6g}"]
            else:
                tok = [ref + s] + [x if x in keep else x + s for x in tok[1:-1]] + [tok[-1]]
            lines.append(" ".join(tok))
        u = lambda t: 1 + rnd.uniform(-t, t)  # noqa: E731
        # ADC input network (sim/models/adc_in.lib) with its tolerances: 1 % resistors, 10 % X7R coupling cap,
        # and the ADC's 10 k input impedance, assumed +-20 % (the datasheet gives it as typical only)
        lines += [f"Rdrv_on{s} DRV_OUT{s} 0 0.4", f"Rtop{s} RX_OUT{s} DIV{s} {2e3 * u(0.01):.6g}",
                  f"Rbot{s} DIV{s} 0 {1e3 * u(0.01):.6g}", f"Ccpl{s} DIV{s} ADC{s} {100e-9 * u(0.1):.6g}",
                  f"Rin{s} ADC{s} 0 {10e3 * u(0.2):.6g}", f".meas AC g{i} FIND V(ADC{s})/V(xdo) AT 40k"]
        if (i + 1) % batch == 0:
            cir = HERE / "rx_mc.cir"
            cir.write_text("\n".join(lines + [".ac lin 1 40k 40k", ".end"]) + "\n")
            m.update(parse_meas(run_ltspice(cir, timeout=120)))
            lines = list(head)
    gains = [m[f"g{i}"][0][0] for i in range(n)]
    phases = [m[f"g{i}"][0][1] for i in range(n)]
    mean = lambda x: sum(x) / len(x)  # noqa: E731
    sd = lambda x: (sum((a - mean(x)) ** 2 for a in x) / (len(x) - 1)) ** 0.5  # noqa: E731
    mm = sd(phases) / 360 * WAVELEN * 1e3
    checks = [
        Check("Average gain at 40 kHz, element to ADC input pin", mean(gains), "dB", 15, 18.5,
              why="27 dB amplifier, then the 2k/1k divider (−10 dB)"),
        Check("Gain spread between channels (standard deviation)", sd(gains), "dB", hi=0.5),
        Check("Gain spread, best to worst channel", max(gains) - min(gains), "dB", hi=2.0),
        Check("Phase spread between channels (standard deviation)", sd(phases), "°", hi=3.0,
              why=f"= the wave arriving {mm:.2f} mm early or late; calibration removes it"),
        Check("Phase spread, best to worst channel", max(phases) - min(phases), "°", hi=12.0),
    ]
    answer = (f"With real part tolerances the 64 channels differ by **{sd(gains):.2f} dB in gain** and "
              f"**{sd(phases):.1f}° in phase** (standard deviation; {max(phases) - min(phases):.1f}° from best to worst "
              f"of 200). {sd(phases):.1f}° is like the echo arriving {mm:.2f} mm early or late, small next to the "
              f"{WAVELEN * 1e3:.1f} mm wavelength, and fixed for each channel, so a one-time calibration in the FPGA "
              f"removes it.")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4.2))
    a1.hist(gains, bins=25, color="tab:blue")
    a1.set_title(f"gain: spread {sd(gains):.2f} dB (std)")
    a2.hist(phases, bins=25, color="tab:orange")
    a2.set_title(f"phase: spread {sd(phases):.1f}° (std)")
    finish(a1, "gain at 40 kHz (dB)", "number of channels (of 200)", legend=False)
    finish(a2, "phase at 40 kHz (°)", None, legend=False)
    return checks, fig, answer


def rx_overload():
    """Echo amplitudes are the open-circuit peak at the element terminals (XREF in the netlist): the
    EMF scale is calibrated first, then one run per amplitude (LTspice's .step is very slow here)."""
    template = (HERE / "rx_overload.cir").read_text()

    def run(ampl, late, tag):
        cir = GEN / f"rx_overload_{tag}.cir"
        cir.write_text(template.replace("AMPL", f"{ampl:.6g}").replace("LATE", f"{late:.6g}")
                       .replace(".include gen/", ".include ")
                       .replace(".include models/", ".include ../models/"))
        return parse_meas(run_ltspice(cir, timeout=900)), cir
    cal, _ = run(0.1, 0.1, "cal")
    k_main = cal["echo_pk"][0] / 0.1   # terminal peak per volt of EMF, first echo
    k_late = cal["late_ref"][0] / 0.1
    late_v = 0.01                      # weak echo afterwards: 10 mV at the element (12x weaker than 1 m)
    cases = [(ECHO_1M, "0.12 V: object at 1 m"), (1.2, "1.2 V: 10× stronger (near object)"),
             (6.0, "6 V: 50× stronger (very near)")]
    checks, ref_late, curves = [], None, []
    expect_late = late_v * db2lin(27.0) * ADC_DIV
    for amp, lab in cases:
        m, cir = run(amp / k_main, late_v / k_late, f"{amp:g}V")
        d = read_raw(cir.with_suffix(".raw"))
        t, v, va = d["time"], d["V(rx_out)"], d["V(adc)"]
        # electronics: when does the amplifier leave saturation (output below 3.9 V of its ~4.2 V swing)?
        sat = [x for x, y in zip(t, v) if 0.1e-3 < x < 1.45e-3 and abs(y) > 3.9]
        unsat = (max(sat) - 0.35e-3) * 1e3 if sat else 0.0
        # physics: when has the transducer's own ringing faded below 50 mV at the amplifier output?
        busy = [x for x, y in zip(t, v) if 0.35e-3 < x < 1.5e-3 and abs(y) > 0.05]
        quiet = (max(busy) - 0.35e-3) * 1e3 if busy else 0.0
        still = busy and max(busy) > 1.44e-3
        if ref_late is None:
            ref_late = m["late_pk"][0]
        checks += [Check(f"{lab}: highest voltage at the ADC input", max(m["adc_pk"][0], -m["adc_min"][0]), "V", hi=ADC_FS,
                         why="inside the ADC's ±1.41 V full scale: the amplifier's ±4.2 V swing × 0.31 divider"),
                   Check(f"{lab}: amplifier back out of saturation, after the echo ends", max(unsat, 0.0), "ms",
                         hi=1.0, why="the electronics recover; 0 = never saturated"),
                   Check(f"{lab}: transducer's own ringing below 50 mV, after the echo ends", quiet, "ms",
                         why="the transducer is a resonator; "
                             + ("still faintly ringing when the next echo arrives" if still else "faded")),
                   Check(f"{lab}: the later weak echo at the ADC input", m["late_pk"][0], "V",
                         0.8 * expect_late, 1.2 * expect_late,
                         why=f"expected about {expect_late:.3f} V (10 mV × 22 × 0.31): no lasting effect")]
        curves.append((t, va, lab, quiet, max(m["adc_pk"][0], -m["adc_min"][0]), unsat, still))
    worst_sat = max(c[5] for c in curves)
    answer = (f"No. Even an echo 50× stronger than a 1 m echo stays inside the ADC's ±1.41 V full scale "
              f"(**{max(c[4] for c in curves):.2f} V** at most: the clamp diodes and the amplifier's ±5 V rails limit "
              f"the swing to ±4.2 V, and the 2k/1k divider scales it by 0.31), and the amplifier comes out of saturation at most **{worst_sat:.2f} ms** after the echo ends. "
              f"What remains is the transducer itself ringing on (it is a resonator, like a struck bell): after the "
              f"strongest echo that ringing is still faintly there when the next echo arrives 1.15 ms later, but the "
              f"weak echo still comes through at {ref_late:.3f} V every time, as expected for a 10 mV echo. A strong "
              f"echo leaves no lasting effect on the electronics.")
    fig, ax = plt.subplots(figsize=(9.5, 5))
    for (t, v, lab, *_), col in zip(curves, ("tab:blue", "tab:orange", RED)):
        ax.plot([x * 1e3 for x in t], v, lw=0.6, label=lab, color=col)
    ax.axvspan(0.1, 0.35, color=GREY, alpha=0.12)
    ax.text(0.12, 1.55, "strong echo\narrives", fontsize=8)
    ax.axvspan(1.5, 1.75, color=GREEN, alpha=0.12)
    ax.text(1.52, 1.55, "weak echo\n(10 mV)", fontsize=8)
    ax.axhline(ADC_FS, color=RED, ls="--", lw=0.8)
    ax.axhline(-ADC_FS, color=RED, ls="--", lw=0.8, label="ADC full scale ±1.41 V")
    ax.set_ylim(-1.8, 1.8)
    finish(ax, "time (ms)", "voltage at the ADC input pin (V)")
    return checks, fig, answer


# ---- transmitter -----------------------------------------------------------------------------
def tx_burst():
    template = (HERE / "tx_burst.cir").read_text()
    burst_start, burst_end = 0.1e-3, 0.1e-3 + 8 / 40e3
    checks, res, raws = [], {}, {}
    for q in (20, 40):
        cir = HERE / f"tx_burst_q{q}.cir"
        cir.write_text(re.sub(r"QM=\d+", f"QM={q}", template, count=1))
        m = parse_meas(run_ltspice(cir))
        res[q] = {k: v[0] for k, v in m.items()}
        raws[q] = read_raw(cir.with_suffix(".raw"))
    v = res[20]
    checks += [
        Check("Voltage on the transducer during the ping", v["xd_pk"], "V", 13, 15.5,
              why="15 V supply minus one diode drop"),
        Check("Peak current from the gate driver", v["i_pk"], "A", hi=1.0, why="UCC27524 is rated 5 A"),
        Check("Amplifier input during the ping, positive peak", v["lim_pk"], "V", hi=0.9,
              why="the clamp diodes keep the 15 V away from the op-amp"),
        Check("Amplifier input during the ping, negative peak", v["lim_min"], "V", lo=-0.9),
        Check("Heat in the 47 Ω drive resistor (during the ping)", v["p_rdrv"] * 1e3, "mW", hi=50, why="rated 100 mW"),
        Check("Heat in the 4.7 kΩ protection resistor (during the ping)", v["p_rlim"] * 1e3, "mW", hi=50,
              why="rated 100 mW"),
        Check("Highest voltage at the ADC input during the ping", v["adc_pk"], "V", hi=ADC_FS, why="ADC full scale ±1.41 V"),
    ]
    ready = {}
    for q in (20, 40):
        t_ready = res[q]["t_100mv"]
        ready[q] = (t_ready - burst_end, SOUND * (t_ready - burst_start) / 2)
        checks.append(Check(f"Time until the element can listen again (Q = {q})", ready[q][0] * 1e3, "ms", hi=2.5,
                            why=f"echoes from closer than {ready[q][1]:.2f} m arrive while it is still ringing"))
    answer = (f"Yes. The transducer gets **{v['xd_pk']:.1f} V** pulses, the driver peaks at {v['i_pk']:.2f} A "
              f"(rated 5 A) and the clamp keeps the amplifier input within **{v['lim_pk']:+.2f} / {v['lim_min']:+.2f} V**. "
              f"After the ping the transducer keeps ringing for a while, and its own receiver hears that: it can "
              f"hear echoes again **{ready[20][0] * 1e3:.1f}–{ready[40][0] * 1e3:.1f} ms** after the ping, so it misses "
              f"objects closer than about **{ready[20][1]:.2f}–{ready[40][1]:.2f} m**. (The two numbers bracket the "
              f"transducer's unknown Q.) In split mode other elements listen, which avoids this blind zone.")
    fig, axs = plt.subplots(3, 1, figsize=(9.5, 8.5), sharex=True)
    d = raws[20]
    t = [x * 1e3 for x in d["time"]]
    axs[0].plot(t, d["V(xd)"], lw=0.6, color="tab:blue", label="voltage on the transducer")
    axs[1].plot(t, d["V(lim)"], lw=0.6, color="tab:orange", label="amplifier input (after the clamp)")
    for q, col in ((20, "tab:green"), (40, "tab:purple")):
        dq = raws[q]
        axs[2].plot([x * 1e3 for x in dq["time"]], dq["V(rx_out)"], lw=0.6, color=col,
                    label=f"amplifier output (RX_OUT), transducer Q = {q}")
        axs[2].axvline((burst_end + ready[q][0]) * 1e3, color=col, ls="--", lw=1)
        axs[2].text((burst_end + ready[q][0]) * 1e3 + 0.03, 3.4 if q == 20 else 2.4,
                    f"ready (Q={q}): objects beyond {ready[q][1]:.2f} m", color=col, fontsize=8)
    axs[2].axhspan(-0.1, 0.1, color=GREEN, alpha=0.25, label="quiet enough to hear echoes (±0.1 V)")
    for a in axs:
        a.axvspan(burst_start * 1e3, burst_end * 1e3, color=GREY, alpha=0.15)
    axs[0].text(0.12, 15.5, "ping: 8 cycles", fontsize=8)
    axs[0].set_ylim(-2, 18)
    axs[1].set_ylim(-1, 1)
    finish(axs[0], None, "V")
    finish(axs[1], None, "V")
    finish(axs[2], "time (ms)", "V")
    return checks, fig, answer


def tx_cw():
    m = parse_meas(run_ltspice(HERE / "tx_cw.cir", timeout=1200))
    v = lambda k: m[k][0]  # noqa: E731
    i_el = v("i_vdrv")
    p_tot = i_el * 15.0
    parts = [("47 Ω drive resistor", v("p_rdrv")), ("4.7 kΩ protection resistor", v("p_rlim")),
             ("33 kΩ bleed resistor", v("p_bleed")), ("sound (transducer)", v("p_elem"))]
    rest = p_tot - sum(p for _, p in parts)
    parts.append(("driver switches, diodes", rest))
    checks = [
        Check("Supply current per transmitting element", i_el * 1e3, "mA", hi=5.0,
              why="C·V²·f alone would be 1.3 mA; see the power split below"),
        Check("Supply current with all 64 elements transmitting", i_el * 64 * 1e3, "mA", hi=900,
              why="the 15 V boost converter can deliver about 0.9 A"),
        Check("Heat in the 47 Ω drive resistor", v("p_rdrv") * 1e3, "mW", hi=50, why="rated 100 mW"),
        Check("Heat in the 4.7 kΩ protection resistor", v("p_rlim") * 1e3, "mW", hi=50, why="rated 100 mW"),
        Check("Heat in the 33 kΩ bleed resistor", v("p_bleed") * 1e3, "mW", hi=50),
        Check("Power turned into sound (model)", v("p_elem") * 1e3, "mW"),
        Check("Current through the clamp diodes (average)", v("i_clamp") * 1e3, "mA", hi=50, why="BAV99 rated 200 mA"),
        Check("Voltage swing on the transducer", v("xd_pp"), "V", 13, 16),
    ]
    answer = (f"Each transmitting element draws **{i_el * 1e3:.1f} mA** ({p_tot * 1e3:.0f} mW) from the 15 V supply; all "
              f"64 together draw **{i_el * 64 * 1e3:.0f} mA**, about a quarter of what the supply can give. No part "
              f"runs hot: the biggest single loss is the receiver's own 4.7 kΩ protection resistor at "
              f"{v('p_rlim') * 1e3:.0f} mW (rated 100 mW). Only about {v('p_elem') * 1e3:.0f} mW becomes sound.")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2), gridspec_kw={"width_ratios": [1.1, 1]})
    names = [n for n, _ in parts]
    vals = [p * 1e3 for _, p in parts]
    a1.barh(names, vals, color=["tab:blue", "tab:orange", "tab:grey", GREEN, "tab:purple"])
    for i, x in enumerate(vals):
        a1.text(x + 0.5, i, f"{x:.1f} mW", va="center", fontsize=8)
    a1.set_title(f"where the {p_tot * 1e3:.0f} mW per element goes")
    finish(a1, "power (mW)", None, legend=False)
    d = read_raw(HERE / "tx_cw.raw")
    t = [x * 1e3 for x in d["time"]]
    a2.plot(t, d["V(xd)"], lw=0.8, label="voltage on the transducer (V)")
    a2.plot(t, [x * 100 for x in d["I(R101)"]], lw=0.8, label="drive current × 100 (A)")
    a2.set_xlim(5.8, 5.9)
    a2.set_title("four cycles in steady state")
    finish(a2, "time (ms)", None)
    return checks, fig, answer


# ---- supplies and clocks -----------------------------------------------------------------------
def mic_supply():
    m = parse_meas(run_ltspice(HERE / "mic_supply.cir"))
    db = lambda k: m[k][0][0]  # noqa: E731
    checks = [
        Check("Noise at the mic clock frequency (3.07 MHz) let through", db("h_3m"), "dB", hi=-40,
              why=f"cut to 1/{db2lin(-db('h_3m')):.0f}"),
        Check("Noise at 100 MHz let through", db("h_100m"), "dB", hi=-40),
        Check("Resonance peak of the ferrite + 10 µF", m["h_pk"][0][0], "dB", hi=3.0,
              why="a big peak would amplify noise in the mics' ultrasonic band"),
        Check("Resonance frequency", m["f_pk"][0] / 1e3, "kHz"),
        Check("Low-frequency noise let through at 40 kHz", db("h_40k"), "dB",
              why="not filtered here; the microphone's own supply rejection handles it"),
    ]
    answer = (f"Yes. Digital noise at the microphones' clock frequency (3 MHz) is cut by **{-db('h_3m'):.0f} dB** "
              f"(to 1/{db2lin(-db('h_3m')):.0f}), and the filter's resonance near {m['f_pk'][0] / 1e3:.0f} kHz peaks by "
              f"only {m['h_pk'][0][0]:.1f} dB, so it does not amplify noise in the ultrasonic band.")
    d = read_raw(HERE / "mic_supply.raw")
    f = [abs(x) for x in d["frequency"]]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.semilogx(f, [20 * math.log10(abs(x)) for x in d["V(out)"]], label="noise on +3V3 → noise at the mics")
    ax.axvline(3.072e6, color=GREY, ls=":")
    ax.annotate(f"mic clock 3 MHz: {db('h_3m'):.0f} dB", xy=(3.072e6, db("h_3m")), xytext=(2e5, -50),
                arrowprops=dict(arrowstyle="->"))
    ax.annotate(f"resonance: +{m['h_pk'][0][0]:.1f} dB only", xy=(m["f_pk"][0], m["h_pk"][0][0]), xytext=(2e3, -20),
                arrowprops=dict(arrowstyle="->"))
    finish(ax, "frequency (Hz)", "fraction let through (dB)")
    return checks, fig, answer


def clock_steps(name, labels, node):
    run_ltspice(HERE / f"{name}.cir")
    d = read_raw(HERE / f"{name}.raw")
    t = d["time"]
    rows = []
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
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
        ax.plot([x * 1e9 for x in t[i0:i1]], d[node][i0:i1], lw=0.9, label=lab)
    ax.axhline(1.65, color=GREY, ls=":", label="switching point (1.65 V)")
    ax.axhspan(3.6, 5, color=RED, alpha=0.1)
    ax.axhspan(-1.5, -0.3, color=RED, alpha=0.1, label="outside the safe range")
    ax.set_ylim(-1.3, 4.6)
    ax.set_xlim(0, 160)
    finish(ax, "time (ns)", "clock voltage (V)")
    return rows, fig


def clock_si():
    rows, fig = clock_steps("clock_si", ["without series resistor", "with 47 Ω series resistor (design)"], "V(l4)")
    _, vmax, vmin, cr = rows[1]
    checks = [Check("Overshoot at the farthest chip, with 47 Ω", vmax, "V", hi=3.6, why="parts rated 3.3 V + 0.5 V"),
              Check("Undershoot at the farthest chip, with 47 Ω", vmin, "V", lo=-0.3, why="parts rated −0.5 V"),
              Check("Edges counted per clock (2 per period, 8 expected)", cr, "", 8, 8,
                    why="more would mean false clocking"),
              Check("Overshoot without the resistor (comparison)", rows[0][1], "V"),
              Check("Undershoot without the resistor (comparison)", rows[0][2], "V")]
    answer = (f"Yes. With the 47 Ω resistor the clock reaches the farthest of the four chips with clean edges "
              f"({vmax:.2f} V / {vmin:+.2f} V, one crossing per edge). Without it, the trace would ring to "
              f"{rows[0][1]:.1f} V / {rows[0][2]:.1f} V, outside the chips' ratings.")
    return checks, fig, answer


def clock_si_drive():
    labels = ["FPGA DRIVE 8 (~40 Ω)", "~60 Ω", "FPGA DRIVE 4 (~80 Ω, design)"]
    rows, fig = clock_steps("clock_si_drive", labels, "V(hin)")
    checks = []
    for lab, vmax, vmin, cr in rows:
        design = "design" in lab
        checks += [Check(f"{lab}: overshoot at our clock buffer", vmax, "V", hi=3.6 if design else None),
                   Check(f"{lab}: undershoot at our clock buffer", vmin, "V", lo=-0.3 if design else None,
                         why="buffer rated −0.5 V" if design else ""),
                   Check(f"{lab}: edges counted (8 expected)", cr, "", 8 if design else None, 8 if design else None)]
    d8, d4 = rows[0], rows[2]
    answer = (f"DRIVE 4. At that setting the FPGA's output roughly matches the ribbon, so clocks arrive clean "
              f"({d4[1]:.2f} V / {d4[2]:+.2f} V). At DRIVE 8 they would overshoot to {d8[1]:.1f} V and dip to "
              f"{d8[2]:.2f} V, close to the buffer's −0.5 V limit.")
    return checks, fig, answer


def mic_clock_si():
    """Mic clock edges at every mic for the old wiring and the one-output-per-pair design."""
    run_ltspice(HERE / "mic_clock_si.cir")
    d = read_raw(HERE / "mic_clock_si.raw")
    t = d["time"]
    labels = ["4 mics on one output, 33 Ω (previous)", "4 mics on one output, 47 Ω",
              "one output per mic pair, 47 Ω (design)"]
    vil, vih = 0.99, 2.31
    fig, axs = plt.subplots(1, 3, figsize=(12, 4.2), sharey=True)
    checks, worst = [], {}
    for mode, ((i0, i1), lab, ax) in enumerate(zip(steps(t), labels, axs)):
        design = mode == 2
        for n in (("V(m1)", "V(m2)") if design else ("V(m1)", "V(m2)", "V(m3)", "V(m4)")):
            ts, vs = t[i0:i1], d[n][i0:i1]
            w = [(x, v) for x, v in zip(ts, vs) if 340e-9 < x < 385e-9]   # one rising edge
            tl = next(x for x, v in w if v > vil)
            th = next(x for x, v in w if v > vih)
            cr, st = 0, None                                               # threshold crossings
            for x, v in zip(ts, vs):
                if x < 50e-9:
                    continue
                st = v > 1.65 if st is None else st
                if (v > vih and not st) or (v < vil and st):
                    st, cr = not st, cr + 1
            mic = f"mic {n[3]}"
            edge = (th - tl) * 1e9
            worst[mode] = max(worst.get(mode, 0), edge)
            checks.append(Check(f"{lab}: {mic}, edge time from 0.3 to 0.7 VDD", edge, "ns",
                                hi=1.5 if design else None,
                                why="datasheet: edges ≤ 3 ns (10–90 %), i.e. ≤ 1.5 ns between the thresholds"
                                if design else ""))
            if design:
                checks += [Check(f"{lab}: {mic}, overshoot", max(vs), "V", hi=3.6, why="mic input rated 3.6 V"),
                           Check(f"{lab}: {mic}, undershoot", min(vs), "V", lo=-0.3, why="mic input rated −0.3 V"),
                           Check(f"{lab}: {mic}, threshold crossings (4 expected)", cr, "", 4, 4,
                                 why="more would mean a false clock")]
            ax.plot([x * 1e9 for x in ts], vs, lw=0.9, label=mic)
        ax.axhspan(vil, vih, color=GREY, alpha=0.15, label="undecided zone (0.3–0.7 VDD)")
        ax.set_title(lab, fontsize=9)
        ax.set_xlim(340, 372)
        finish(ax, "time (ns)", "clock voltage at the mic (V)" if mode == 0 else None, legend=mode == 0)
    answer = (f"Only with one clock output per mic pair. With one output feeding all four mics of a side, the mics "
              f"nearest the buffer see a staircase edge ({worst[0]:.1f} ns between the logic thresholds at 33 Ω, "
              f"{worst[1]:.1f} ns at 47 Ω; the mic allows about 1.5 ns, i.e. 3 ns edges): the reflection from the "
              f"far end arrives late. With one output per pair, 47 Ω, every mic sees a clean edge "
              f"({worst[2]:.1f} ns at worst) with no overshoot.")
    return checks, fig, answer


def clock_si_in():
    labels = ["nothing", "series 47 Ω", "100 Ω + 47 pF to GND (fitted only as a fallback)", "100 Ω + 100 pF to GND"]
    rows, fig = clock_steps("clock_si_in", labels, "V(hin)")
    checks = []
    for lab, vmax, vmin, cr in rows:
        fb = "fallback" in lab
        checks += [Check(f"{lab}: overshoot", vmax, "V", hi=3.6 if fb else None),
                   Check(f"{lab}: undershoot", vmin, "V", lo=-0.3 if fb else None)]
    fb = rows[2]
    answer = (f"If the FPGA ever has to stay at DRIVE 8, the unfitted 100 Ω + 47 pF parts on the header sheet clean "
              f"the clock up ({fb[1]:.2f} V / {fb[2]:+.2f} V). A series resistor at our end does not help much.")
    return checks, fig, answer


# ---- descriptions --------------------------------------------------------------------------------
SIMS = {
    "rx_chain": dict(
        fn=rx_chain, files=["rx_chain.cir"], part="Receiver",
        title="Receiver: how much does an echo get amplified?",
        question="When an echo arrives, how big is the signal that reaches the ADC, and how much room is left "
                 "for stronger echoes?",
        why="The ADC must see the echo well above its own noise but below its ±1.41 V full scale. The transducer "
            "datasheet gives about 0.12 V for an echo from an object 1 m away.",
        setup="One transceiver channel, exported from the schematic: the transducer (electrical model), the "
              "protection resistor and clamp diodes, the ×6 amplifier stage and the 40 kHz band-pass filter, then "
              "the ADC input network from the ADC sheet (2k/1k divider, 100 nF, the ADC's 10 kΩ input). A small sine "
              "wave is swept from 1 kHz to 1 MHz.",
        read="Left to right: frequency. Up: how much bigger the signal is than the echo voltage at the transducer, "
             "in dB (+20 dB = ×10). Blue: the amplifier output. Green: the ADC input pin, 10 dB lower because of "
             "the divider that fits the amplifier's ±4.2 V swing into the ADC's ±1.41 V. Both peak at 40 kHz, the "
             "transducers' frequency, and fall off on both sides, which keeps noise out."),
    "rx_noise": dict(
        fn=rx_noise, files=["rx_noise.cir"], part="Receiver",
        title="Receiver: how much noise does it add?",
        question="How quiet is the receiver compared with the echoes it has to hear, and compared with the ADC?",
        why="Noise sets the weakest echo that can be detected, so it sets the maximum range.",
        setup="The same channel, with LTspice's noise analysis: the thermal noise of every resistor, the op-amp's "
              "noise and the transducer's own internal loss.",
        read="Blue: noise at the ADC input pin. Orange: the same noise expressed as an echo voltage at the transducer "
             "(how big an echo would be just as large as the noise). Red dashed: the ADC's own noise. Only the "
             "shaded 30–50 kHz band matters, because the FPGA filters everything else away."),
    "rx_mc": dict(
        fn=rx_mc, files=["rx_mc.cir (generated)"], part="Receiver",
        title="Receiver: how alike are the 64 channels?",
        question="Real resistors and capacitors are never exactly their value. How different will the 64 receive "
                 "channels be in gain and phase?",
        why="Beamforming adds the 64 channels together with precise delays. If channels differ in phase, the beam "
            "points slightly wrong and gets wider. Fixed differences are removed by a one-time calibration, but "
            "they should be small to start with.",
        setup="200 simulated channels. In each one, every resistor and capacitor gets a random value inside the "
              "tolerance written on its schematic value (1 %, or 5 % for C0G and 10 % for X7R capacitors), plus the ADC "
              "input divider (1 %), its coupling capacitor (10 %) and the ADC's 10 kΩ input (assumed ±20 %).",
        read="Two histograms: how many of the 200 channels have a given gain (left) and phase (right) at 40 kHz. "
             "Narrow is good."),
    "rx_overload": dict(
        fn=rx_overload, files=["rx_overload.cir"], part="Receiver",
        title="Receiver: a strong echo from a close object",
        question="Does a very strong echo (from a close object) damage or confuse the receiver, and how soon can it "
                 "hear the next, weak echo?",
        why="Close objects return echoes many times stronger than distant ones. The receiver must survive them, "
            "and recover fast enough to hear what comes after.",
        setup="Three runs with a strong echo of 0.12 V (an object at 1 m), 1.2 V and 6 V at the transducer, then a "
              "weak 10 mV echo 1.4 ms later. Echo voltages are measured at the transducer terminals, the same way "
              "as in the gain test, so 0.12 V here is the same 1 m echo that gives 0.86 V at the ADC there.",
        read="Voltage at the ADC input pin over time, one colour per strength. The grey band is when the strong echo "
             "arrives; the trace goes on after it because the transducer keeps ringing (it is a resonator). "
             "The strong echoes are flattened where the amplifier saturates (±4.2 V, ±1.3 V after the divider), "
             "inside the dashed ±1.41 V ADC full scale. Each trace then decays back to zero, and the weak echo "
             "(green band) comes out the same size in all three runs."),
    "tx_burst": dict(
        fn=tx_burst, files=["tx_burst.cir"], part="Transmitter",
        title="Transmitter: sending a ping, and listening again",
        question="When an element sends a ping, is the drive safe for every part, and how soon after can the same "
                 "element hear an echo?",
        why="The 15 V drive must never reach the sensitive amplifier input. And the element can only hear echoes "
            "once its own ringing has died down, which sets the closest object it can see.",
        setup="The gate driver (modelled as a 15 V switch) sends 8 cycles at 40 kHz through the channel from the "
              "schematic, then holds low while the element listens. The transducer's sharpness (Q) is not in its "
              "datasheet, so the test runs with Q = 20 and Q = 40, which bracket its 1.8 ms ring-down time.",
        read="Top: the 0–14 V ping on the transducer (grey band). Middle: the amplifier input, held within "
             "±0.6 V by the clamp diodes during the ping. Bottom: the amplifier output. The receiver is overloaded during "
             "the ping and then hears the transducer ringing down; once the trace stays inside the green band, "
             "echoes can be heard. The dashed lines mark that moment and the matching closest distance."),
    "tx_cw": dict(
        fn=tx_cw, files=["tx_cw.cir"], part="Transmitter",
        title="Transmitter: continuous wave, current and heat",
        question="In continuous-wave mode (Doppler, split mode) an element transmits all the time. How much current "
                 "does it draw from the 15 V supply, and does any part get too hot?",
        why="This is the heaviest load on the 15 V supply and the hottest case for the drive and protection "
            "resistors.",
        setup="One element driven at 40 kHz without stopping, from the schematic's channel, until it reaches a "
              "steady state.",
        read="Left: where the power drawn by one element goes. Right: four cycles of the transducer voltage and "
             "drive current."),
    "mic_supply": dict(
        fn=mic_supply, files=["mic_supply.cir"], part="Microphones",
        title="Microphone supply filter",
        question="Do the ferrite bead and capacitors keep digital noise on the 3.3 V rail out of the microphones' "
                 "supply, without creating a resonance?",
        why="The MEMS microphones are sensitive to supply noise, and a ferrite with a large capacitor can resonate "
            "and amplify noise near its resonance.",
        setup="+3V3 → 600 Ω ferrite (GZ1608D601TF) → 10 µF + 8 × 100 nF at the microphones, with a typical ferrite model.",
        read="How much of the noise on +3V3 reaches the microphones, versus frequency. 0 dB = all of it, −60 dB = "
             "a thousandth."),
    "clock_si": dict(
        fn=clock_si, files=["clock_si.cir"], part="Clocks",
        title="Clocks on the board: one buffer feeding four chips",
        question="Does each clock arrive at all four chips clean, with one crossing per edge and no dangerous "
                 "overshoot?",
        why="A ringing clock can be counted twice (corrupting the TX pattern or the ADC data), and overshoot "
            "stresses the chip inputs.",
        setup="A 74LVC2G34 clock buffer drives a 60 Ω board trace with four chip inputs, 3 cm apart, at 20 MHz, "
              "with and without the 47 Ω series resistor.",
        read="The clock voltage at the farthest chip. The dotted line is where the chips decide between 0 and 1; "
             "the red bands are outside their ratings."),
    "clock_si_drive": dict(
        fn=clock_si_drive, files=["clock_si_drive.cir"], part="Clocks",
        title="Clocks from the FPGA: which FPGA drive strength?",
        question="The clocks come from the FPGA over a 15 cm ribbon cable. Which FPGA output strength gives clean "
                 "edges at our end?",
        why="The ribbon reflects the edges back and forth. The FPGA's drive strength (set in its constraints file) "
            "decides how much ringing arrives.",
        setup="FPGA output → 6 cm of FPGA-board trace → 15 cm ribbon → our clock buffer input, at 20 MHz, for "
              "three FPGA output strengths.",
        read="The clock voltage at our buffer's input for each drive strength. Same lines and bands as the board "
             "clock test."),
    "mic_clock_si": dict(
        fn=mic_clock_si, files=["mic_clock_si.cir"], part="Clocks",
        title="Mic clock: does every mic get a clean edge?",
        question="The mic clock runs 200-300 mm down the sides of the array. Does every mic see an edge within its "
                 "3 ns limit, with no false clocks?",
        why="The mics need fast clock edges (≤ 3 ns). A series resistor at the buffer only gives clean edges when "
            "the loads sit together at the far end of the trace; mics spread along the trace can see a staircase.",
        setup="A 74LVC2G34 output drives a 61 Ω (0.25 mm) board trace at 3.07 MHz: either all four mics of one "
              "side, 40 mm apart, 200 mm from the buffer, or one mic pair at the end of a 280 mm trace.",
        read="Each mic's clock voltage during one rising edge. The grey band is where the mic can't tell 0 from 1; "
             "the edge must cross it in about 1.5 ns."),
    "clock_si_in": dict(
        fn=clock_si_in, files=["clock_si_in.cir"], part="Clocks",
        title="Clocks from the FPGA: fallback termination",
        question="If the FPGA had to stay at the stronger DRIVE 8, which resistor/capacitor at our end would clean "
                 "up the edges?",
        why="A fallback in case DRIVE 4 is not possible; the parts are on the board but not fitted.",
        setup="The same path at DRIVE 8 with four options at our buffer input.",
        read="The clock voltage at our buffer's input for each option."),
}

ASSUMPTIONS = [
    "Op-amp: TI's OPA167x model (`sim/models/OPA167x.lib`): gain, bandwidth, noise, output swing, overload.",
    "Diodes (BAV99): 1N4148 junction model, the same die.",
    "Transducer (CUSP-TR80-15-2500-TH): standard electrical equivalent (Butterworth–Van Dyke) with C0 = 2.0 nF and "
    "Rm = 3 kΩ from the datasheet; its sharpness Q (20–40) is an assumption.",
    "Gate driver, clock buffers and the FPGA outputs: simple behavioural models (values in each netlist header).",
    "The channel circuit is exported from `kicad/channel.kicad_sch` at the start of every run.",
]


# ---- reports --------------------------------------------------------------------------------------
def write_report(name, spec, checks, fig, answer, err=None):
    REPORTS.mkdir(exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    fails = [c for c in checks if c.status == "FAIL"]
    status = "ERROR" if err else ("FAIL" if fails else "PASS")
    lines = [f"# {spec['title']}", "", f"**Question:** {spec['question']}", ""]
    if err:
        lines += ["**Answer: the simulation did not run.**", "", "```", str(err)[:2000], "```"]
    else:
        lines += [f"**Answer ({status}):** {answer}", "",
                  "## Why it matters", "", spec["why"], "",
                  "## What is simulated", "", spec["setup"], ""]
        if fig is not None:
            fig.suptitle(spec["title"], fontsize=11)
            fig.tight_layout()
            fig.savefig(REPORTS / f"{name}.png", dpi=100)
            plt.close(fig)
            lines += ["## How to read the plot", "", spec["read"], "", f"![{spec['title']}]({name}.png)", ""]
        lines += ["## The numbers", "",
                  f"{sum(c.status == 'pass' for c in checks)} checks pass, {len(fails)} fail; "
                  f"{sum(c.status == 'info' for c in checks)} rows are for information.", "",
                  "| What | Value | Unit | Limit | OK? | What it means |", "|---|---|---|---|---|---|"]
        for c in checks:
            ok = {"pass": "yes", "FAIL": "**NO**", "info": "—"}[c.status]
            lines.append(f"| {c.label} | {fmt(c.value)} | {c.unit} | {c.limit()} | {ok} | {c.why} |")
    lines += ["", "## Assumptions", "", *[f"- {a}" for a in ASSUMPTIONS], "",
              f"Run {now} with LTspice by `sim/run.py {name}`. Testbench: "
              + ", ".join(f"`sim/{f}`" for f in spec["files"]) + "."]
    (REPORTS / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status


def write_index():
    rows = []
    for name, spec in SIMS.items():
        p = REPORTS / f"{name}.md"
        if not p.exists():
            rows.append(f"| {spec['part']} | {spec['question']} | not run yet | — |")
            continue
        text = p.read_text(encoding="utf-8")
        m = re.search(r"\*\*Answer \((\w+)\):\*\* (.*)", text)
        status, ans = (m.group(1), m.group(2)) if m else ("ERROR", "the simulation did not run")
        sentences = re.split(r"(?<=\.) (?=[A-Z])", ans)
        first = " ".join(sentences[:2]) if len(sentences[0]) < 40 and len(sentences) > 1 else sentences[0]
        rows.append(f"| {spec['part']} | [{spec['question']}]({name}.md) | {first} | **{status}** |")
    index = [
        "# Simulations: what they show", "",
        "The panel sends 40 kHz pings from its 64 transducers and listens for the echoes. Each element has its "
        "own driver (transmit) and its own amplifier and ADC channel (receive). These simulations follow that "
        "path and answer one question each; click a question for the full report with its plot.", "",
        "The transceiver channel is exported from the KiCad schematic before every run, so the results always "
        "match the current design. All of them come from `sim/run.py`.", "",
        "| Part | Question | Answer | Result |", "|---|---|---|---|", *rows, "",
        "## Run them", "",
        "```", "cd C:\\GitHub\\ultrasonic-aesa", "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe sim\\run.py",
        "```", "",
        "Add test names (for example `rx_chain tx_burst`) to run only those.", "",
        "## Look at the waveforms yourself in LTspice", "",
        "1. Run the tests once (above), so `sim\\gen\\channel.cir` exists.",
        "2. Open a testbench: `\"%LOCALAPPDATA%\\Programs\\ADI\\LTspice\\LTspice.exe\" -Run sim\\tx_burst_q20.cir`.",
        "3. LTspice runs it and opens an empty (black) plot window: it does not choose signals for you.",
        "4. Click in the black window, right-click → **Add Traces**, pick for example V(xd), V(lim), V(rx_out), "
        "then OK. Use **Plot Settings → Add Plot Pane** to give each signal its own pane.",
        "",
    ]
    (REPORTS / "README.md").write_text("\n".join(index), encoding="utf-8")


def main(names):
    if not LTSPICE.exists():
        raise SystemExit(f"LTspice not found at {LTSPICE}")
    export_channel()
    print("channel exported from KiCad -> sim/gen/channel.cir")
    for name in names or SIMS:
        spec = SIMS[name]
        try:
            checks, fig, answer = spec["fn"]()
            status = write_report(name, spec, checks, fig, answer)
        except Exception as e:  # report the failure instead of losing it
            import traceback
            status = write_report(name, spec, [], None, "", err=traceback.format_exc())
        print(f"{name}: {status} -> sim/reports/{name}.md")
    write_index()
    print("index: sim/reports/README.md")


if __name__ == "__main__":
    main(sys.argv[1:])
