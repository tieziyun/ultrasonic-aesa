r"""Placement metrics for kicad/aesa.kicad_pcb (run after place.py): distances that decide loop area and
routability. Prints one line per check with the worst case.

  C:\Users\Tiezi\AppData\Local\Programs\KiCad\10.0\bin\python.exe scripts\placement_check.py
"""

import math
import re
from pathlib import Path

import pcbnew

PCB = Path(__file__).resolve().parent.parent / "kicad" / "aesa.kicad_pcb"
b = pcbnew.LoadBoard(str(PCB))
fps = {f.GetReference(): f for f in b.GetFootprints()}


def pad(ref, num):
    p = next(p for p in fps[ref].Pads() if p.GetNumber() == str(num))
    return pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)


def near(ref_a, num_a, ref_b, net=None):
    """Distance from pad num_a of ref_a to the closest pad of ref_b (on `net` if given)."""
    ax, ay = pad(ref_a, num_a)
    pts = [(pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)) for p in fps[ref_b].Pads()
           if net is None or p.GetNetname().split("/")[-1] == net]
    return min(math.hypot(ax - x, ay - y) for x, y in pts)


def report(name, values, limit):
    worst = max(values)
    print(f"{'OK  ' if worst <= limit else 'HIGH'} {name}: worst {worst:.1f} mm (limit {limit} mm)")


# decoupling: closest cap pad on the rail to the IC supply pin
report("ADC01 100 nF caps to AVDD/AREG/DREG/IOVDD pins", [near("U7101", 1, "C7110"), near("U7101", 2, "C7112"),
                                                         near("U7101", 24, "C7114"), near("U7101", 19, "C7116")], 3.0)
report("ADC01 VREF 1 uF to pin 3", [near("U7101", 3, "C7117")], 3.0)
report("ADC01 bulk 1-10 uF caps to their pins", [near("U7101", 1, "C7109"), near("U7101", 2, "C7111"),
                                                  near("U7101", 24, "C7113"), near("U7101", 19, "C7115")], 8.0)
report("ADC01 coupling caps to INxP pins", [near("U7101", 6 + 2 * i, f"C71{i + 1:02d}") for i in range(4)], 7.0)
report("ADC01 divider R to coupling cap", [near(f"C71{i + 1:02d}", 1, f"R71{i + 1:02d}") for i in range(4)], 3.0)
report("TXG01 UCC VDD caps to pin 6", [near("U10102", 6, "C10103"), near("U10103", 6, "C10105")], 3.0)
report("TXG01 595 VCC cap to pin 16", [near("U10101", 16, "C10101")], 3.0)
report("CH01 OPA supply caps to pins 8/4", [near("U101", 8, "C101"), near("U101", 4, "C102")], 3.0)
# switchers: SW pin to inductor and diode; input caps to VIN pin
report("TPS54302 SW to L1, VIN to input caps", [near("U9501", 2, "L9501"), near("U9501", 3, "C9503"),
                                                  near("U9501", 3, "C9504")], 4.0)
report("LM2731 SW to L4 and D8", [near("U9505", 1, "L9504"), near("U9505", 1, "D9508")], 4.0)
report("LM2731 D8 cathode to the nearest output cap", [min(near("D9508", 1, "C9533"), near("D9508", 1, "C9534"))], 4.0)
# columns: TX group k and ADC k must sit under the column of CH 4k-3..4k
dx = []
for k in range(1, 17):
    ch = 4 * k - 3
    cx = pcbnew.ToMM(fps[f"LS{ch}01"].GetPosition().x)
    dx += [abs(pcbnew.ToMM(fps[f"U{100 + k}01"].GetPosition().x) - cx),
           abs(pcbnew.ToMM(fps[f"U{70 + k}01"].GetPosition().x) - cx)]
report("TX group / ADC under their channel column", dx, 10.0)
