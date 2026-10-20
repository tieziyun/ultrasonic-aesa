"""Plot the simulation results into build/sim_*.png (run after the LTspice runs).

  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe sim\\plots.py
"""

import cmath
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from ltraw import read_raw  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "build"


def steps(t):
    s = [0] + [k for k in range(1, len(t)) if t[k] < t[k - 1]]
    return list(zip(s, s[1:] + [len(t)]))


def rx():
    d = read_raw(HERE / "rx_chain.raw")
    f = [abs(x) for x in d["frequency"]]
    ref = d["V(xdo)"]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    for name, lab in (("V(adc)", "ADC pin"), ("V(adcc)", "after AD7606C-16 filter (220 kHz)"),
                      ("V(adcb)", "after AD7606B filter (22.5 kHz)")):
        h = [v / r for v, r in zip(d[name], ref)]
        a1.semilogx(f, [20 * math.log10(abs(x)) for x in h], label=lab)
        a2.semilogx(f, [math.degrees(cmath.phase(x)) for x in h], label=lab)
    a1.set_ylabel("gain vs open-circuit element voltage (dB)")
    a1.set_ylim(-40, 35)
    a1.axvline(40e3, color="grey", ls=":")
    a1.legend()
    a1.grid(True, which="both", alpha=0.3)
    a2.set_ylabel("phase (deg)")
    a2.set_xlabel("frequency (Hz)")
    a2.axvline(40e3, color="grey", ls=":")
    a2.grid(True, which="both", alpha=0.3)
    a1.set_title("RX chain: CUSA-TR65 -> limiter -> OPA1678 x6 -> 40 kHz band-pass -> ADC")
    fig.tight_layout()
    fig.savefig(OUT / "sim_rx_ac.png", dpi=110)


def tx():
    fig, axs = plt.subplots(3, 1, figsize=(9, 8), sharex=True)
    for q in (20, 40):
        p = HERE / f"tx_burst_q{q}.raw"
        if not p.exists():
            continue
        d = read_raw(p)
        t = [x * 1e3 for x in d["time"]]
        axs[0].plot(t, d["V(xd)"], lw=0.6, label=f"element XD, Q={q}")
        axs[1].plot(t, d["V(lim)"], lw=0.6, label=f"limiter LIM, Q={q}")
        axs[2].plot(t, d["V(adc)"], lw=0.6, label=f"ADC pin, Q={q}")
    for a, yl in zip(axs, ("V", "V", "V")):
        a.set_ylabel(yl)
        a.legend(loc="upper right")
        a.grid(True, alpha=0.3)
    axs[2].axhline(0.1, color="grey", ls=":")
    axs[2].axhline(-0.1, color="grey", ls=":")
    axs[2].set_xlabel("time (ms)")
    axs[0].set_title("TX: 8 cycles at 40 kHz, 15 V, then listen on the same element")
    fig.tight_layout()
    fig.savefig(OUT / "sim_tx_burst.png", dpi=110)


def clock():
    fig, axs = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    d = read_raw(HERE / "clock_si_drive.raw")
    t = d["time"]
    for (a, b), r in zip(steps(t), (40, 60, 80)):
        axs[0].plot([x * 1e9 for x in t[a:b]], d["V(hin)"][a:b], lw=0.8,
                    label=f"FPGA ~{r} ohm (DRIVE {'8' if r == 40 else '6' if r == 60 else '4'})")
    axs[0].set_ylabel("buffer input (V)")
    axs[0].set_title("Ribbon end at the clk_buf input, no termination")
    d = read_raw(HERE / "clock_si.raw")
    t = d["time"]
    for (a, b), rs in zip(steps(t), (0, 33)):
        axs[1].plot([x * 1e9 for x in t[a:b]], d["V(l4)"][a:b], lw=0.8, label=f"last load, Rs = {rs} ohm")
    axs[1].set_ylabel("4th load (V)")
    axs[1].set_title("Buffer output -> 4 loads on a 60-ohm trace")
    axs[1].set_xlabel("time (ns)")
    for a in axs:
        a.legend(loc="upper right")
        a.grid(True, alpha=0.3)
        a.axhline(1.65, color="grey", ls=":")
    fig.tight_layout()
    fig.savefig(OUT / "sim_clock_si.png", dpi=110)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    rx()
    tx()
    clock()
    print("wrote build/sim_rx_ac.png, sim_tx_burst.png, sim_clock_si.png")
