"""Attach SPICE models to the parts of a sheet (KiCad Sim.* fields), so the sheet itself can be
simulated: in KiCad's simulator, or exported with
  kicad-cli sch export netlist --format spice
and run in LTspice by sim/run.py. Models live in sim/models/aesa_parts.lib.

Passives get their numeric value (the Value field carries ratings, e.g. "1n 50V C0G 1%").

  python scripts/sch_sim.py kicad/channel.kicad_sch
"""

import re
import sys
from pathlib import Path
from common import block_end

LIB = "${KIPRJMOD}/../sim/models/aesa_parts.lib"
SUBCKT = {  # lib_id -> (model name, symbol pin -> model pin)
    "Amplifier_Operational:OPA1678": ("OPA1678_DUAL", "1=1 2=2 3=3 4=4 5=5 6=6 7=7 8=8"),
    "Diode:BAV99": ("BAV99", "1=1 2=2 3=3"),
    "Device:Speaker_Ultrasound": ("CUSP_TR80", "1=1 2=2"),
}


def number(value):
    m = re.match(r"\s*([\d.]+)\s*([pnumkKM]?)", value)
    if not m:
        return None
    return m.group(1) + {"": "", "p": "p", "n": "n", "u": "u", "m": "m", "k": "k", "K": "k", "M": "Meg"}[m.group(2)]


def prop(name, val, x, y):
    return [f'\t\t(property "{name}" "{val}"', f"\t\t\t(at {x} {y} 0)", "\t\t\t(effects",
            "\t\t\t\t(font", "\t\t\t\t\t(size 1.27 1.27)", "\t\t\t\t)", "\t\t\t\t(hide yes)", "\t\t\t)", "\t\t)"]


def main(path):
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    out, i, n = [], 0, 0
    while i < len(lines):
        if lines[i] == "\t(symbol" and lines[i + 1].startswith("\t\t(lib_id "):
            j = block_end(lines, i)
            blk = lines[i:j + 1]
            text = "\n".join(blk)
            lib = re.search(r'\(lib_id "([^"]+)"\)', text).group(1)
            val = re.search(r'\(property "Value" "([^"]*)"', text).group(1)
            x, y = re.search(r"\n\t\t\(at ([\d.\-]+) ([\d.\-]+)", text).groups()
            # drop earlier Sim.* fields
            keep, k = [], 0
            while k < len(blk):
                if blk[k].startswith('\t\t(property "Sim.'):
                    k = block_end(blk, k) + 1
                    continue
                keep.append(blk[k])
                k += 1
            blk = keep
            new = []
            if lib in ("Device:R", "Device:C", "Device:L") and number(val):
                kind = lib.split(":")[1]
                new = prop("Sim.Device", kind, x, y) + prop("Sim.Params", f"{kind.lower()}={number(val)}", x, y)
            elif lib in SUBCKT:
                name, pins = SUBCKT[lib]
                new = (prop("Sim.Library", LIB, x, y) + prop("Sim.Name", name, x, y)
                       + prop("Sim.Device", "SUBCKT", x, y) + prop("Sim.Pins", pins, x, y))
            elif not lib.startswith("power:"):
                new = prop("Sim.Enable", "0", x, y)  # no model: leave it out of the simulation
            if new:
                at = next(m for m, l in enumerate(blk) if l.startswith("\t\t(pin ") or l.startswith("\t\t(instances"))
                blk = blk[:at] + new + blk[at:]
                n += 1
            out += blk
            i = j + 1
            continue
        out.append(lines[i])
        i += 1
    Path(path).write_text("\n".join(out), encoding="utf-8")
    print(f"{path}: simulation fields on {n} parts")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
