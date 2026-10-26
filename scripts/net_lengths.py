"""Estimate the routed length of every net from the placed board, for routing planning.

Length = minimum spanning tree over the net's pads with Manhattan distances (a lower bound on the
real track length; real routes run 10-30 % longer). Writes build/net_lengths.csv and prints a summary
per net class and per signal group, plus the electrical-length check: a line longer than about
1/6 of the edge's rise length behaves as a transmission line and needs a terminated, continuous-
reference route.

  python scripts/net_lengths.py
"""

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import pcbnew

REPO = Path(__file__).resolve().parent.parent
PCB = REPO / "kicad" / "aesa.kicad_pcb"
PRO = REPO / "kicad" / "aesa.kicad_pro"
V_PS_PER_MM = 6.0  # outer-layer microstrip on JLC04161H-7628: er_eff about 3.3 -> about 6 ps/mm

GROUPS = [  # (group, net-name regex) in order; first match wins
    ("TX shift/latch clocks (buffered)", r"TX_(SCK|LATCH)_[A-D]$"),
    ("ADC BCLK/FSYNC (buffered)", r"ADC_(BCLK|FSYNC)_[A-D]$"),
    ("Mic PDM clock (buffered)", r"MIC_CLK_[A-D]$"),
    ("Clocks from the headers (FPGA side)", r"^(TX_SCK|TX_LATCH|ADC_BCLK|ADC_FSYNC|MIC_CLK_IN)$"),
    ("ADC TDM data", r"ADC_SDOUT\d+$"),
    ("TX serial data", r"TX_SDI\d+$"),
    ("Mic PDM data", r"MIC_DATA\d$"),
    ("I2C / control", r"ADC_(SCL|SDA\d|SHDNZ)$|TX_(EN|OE|CLR).*"),
    ("TX drive to elements", r"^DRV\d+$"),
    ("RX analog to ADCs", r"^RX\d+$"),
]


def mst_manhattan(pts):
    if len(pts) < 2:
        return 0.0
    left, tree, total = set(range(1, len(pts))), {0}, 0.0
    best = {i: abs(pts[i][0] - pts[0][0]) + abs(pts[i][1] - pts[0][1]) for i in left}
    while left:
        j = min(left, key=best.get)
        total += best[j]
        left.remove(j)
        for i in left:
            d = abs(pts[i][0] - pts[j][0]) + abs(pts[i][1] - pts[j][1])
            if d < best[i]:
                best[i] = d
    return total


def main():
    board = pcbnew.LoadBoard(str(PCB))
    pads = defaultdict(list)
    for fp in board.GetFootprints():
        for p in fp.Pads():
            n = p.GetNetname()
            if n and not n.startswith("unconnected"):
                pos = p.GetPosition()
                pads[n].append((pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y)))
    pats = [(pp["netclass"], pp["pattern"]) for pp in json.loads(PRO.read_text())["net_settings"]["netclass_patterns"]]

    def cls(net):
        for c, pat in pats:
            if re.fullmatch(re.escape(pat).replace(r"\*", ".*"), net):
                return c
        return "Default"

    rows = []
    for net, pts in pads.items():
        if net == "GND":
            continue
        rows.append((net, cls(net), len(pts), round(mst_manhattan(pts), 1)))
    out = REPO / "build" / "net_lengths.csv"
    with open(out, "w", newline="") as f:
        csv.writer(f).writerows([("net", "class", "pads", "mst_mm")] + sorted(rows, key=lambda r: -r[3]))
    by_cls = defaultdict(list)
    for r in rows:
        by_cls[r[1]].append(r)
    print(f"{len(rows)} nets (GND excluded) -> {out.relative_to(REPO)}")
    print(f"{'class':12s} {'nets':>5s} {'total m':>8s} {'longest mm':>10s}  longest net")
    for c, rs in sorted(by_cls.items(), key=lambda kv: -sum(r[3] for r in kv[1])):
        lo = max(rs, key=lambda r: r[3])
        print(f"{c:12s} {len(rs):5d} {sum(r[3] for r in rs) / 1000:8.2f} {lo[3]:10.1f}  {lo[0]}")
    print()
    print(f"{'group':38s} {'nets':>4s} {'pads/net':>8s} {'min..max mm':>14s} {'delay ns':>8s}")
    for g, rx in GROUPS:
        rs = [r for r in rows if re.search(rx, r[0].split("/")[-1])]
        if not rs:
            continue
        mn, mx = min(r[3] for r in rs), max(r[3] for r in rs)
        pads_n = sorted({r[2] for r in rs})
        print(f"{g:38s} {len(rs):4d} {'/'.join(map(str, pads_n)):>8s} {mn:6.0f}..{mx:<6.0f} {mx * V_PS_PER_MM / 1000:8.2f}")


if __name__ == "__main__":
    main()
