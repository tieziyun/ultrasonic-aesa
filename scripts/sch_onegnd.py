"""Merge AGND, PGND and DGND into one GND net on the given sheets (review-full-1 item 1).

* global labels AGND/PGND/DGND -> GND
* power symbols power:GNDA / GNDPWR / GNDD -> power:GND with value GND (pin positions are
  identical, so connectivity is unchanged); the power:GND definition is embedded in the
  sheet's lib_symbols when missing.

  python scripts/sch_onegnd.py kicad/channel.kicad_sch kicad/adc.kicad_sch ...
"""

import re
import sys

POWER_LIB = r"C:\Users\Tiezi\AppData\Local\Programs\KiCad\10.0\share\kicad\symbols\power.kicad_sym"
OLD_NETS = ("AGND", "PGND", "DGND")
OLD_SYMS = ("power:GNDA", "power:GNDPWR", "power:GNDD")


def gnd_definition():
    lines = open(POWER_LIB, encoding="utf-8").read().split("\n")
    start = lines.index('\t(symbol "GND"')
    depth, j = 0, start
    while True:
        depth += lines[j].count("(") - lines[j].count(")")
        if depth == 0:
            break
        j += 1
    block = ["\t" + l for l in lines[start:j + 1]]
    block[0] = '\t\t(symbol "power:GND"'
    return block


def convert(path):
    t = open(path, encoding="utf-8").read()
    n_lbl = 0
    for net in OLD_NETS:
        t, k = re.subn(r'\(global_label "' + net + '"', '(global_label "GND"', t)
        n_lbl += k
    n_sym = 0
    for old in OLD_SYMS:
        t, k = re.subn(r'\(lib_id "' + re.escape(old) + r'"\)', '(lib_id "power:GND")', t)
        n_sym += k
    if n_sym:
        for net in OLD_NETS:
            t = t.replace(f'(property "Value" "{net}"', '(property "Value" "GND"')
        if '(symbol "power:GND"' not in t:
            lines = t.split("\n")
            k = next(i for i, l in enumerate(lines) if l.strip() == "(lib_symbols")
            lines[k + 1:k + 1] = gnd_definition()
            t = "\n".join(lines)
    open(path, "w", encoding="utf-8").write(t)
    print(f"{path}: {n_lbl} labels, {n_sym} power symbols -> GND")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        convert(p)
