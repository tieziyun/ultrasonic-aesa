"""Give every placed symbol an LCSC field (CLAUDE.md: every part needs an LCSC number or
a hand-solder/consigned mark).

Existing LCSC fields are kept. Missing ones are filled from KNOWN by value, else "TBD"
(to be filled by the parts-sourcer). Power symbols and PWR_FLAGs are skipped.

  python scripts/sch_lcsc.py kicad/*.kicad_sch
"""

import re
import sys

# Checked part numbers (value -> LCSC). Keep in sync with skidl/channel.py and the brief.
KNOWN = {
    "UCC27524DR": "C465729",
    "OPA1678IDR": "C192421",
    "BAV99": "C68983",
    "AD7606BBSTZ-RL": "C651284",
    "AD7606C-16BSTZ": "consigned (DigiKey/Mouser, not on LCSC)",
    "74HCT595D": "C282339",
    "SPH0641LU4H-1": "consigned",
    "CUSA-TR65-065-2200-TH68": "hand-solder",
    "NetTie AGND-PGND": "none (copper)",
    "NetTie AGND-DGND": "none (copper)",
}


def add_fields(path):
    lines = open(path, encoding="utf-8").read().split("\n")
    out, i, added = [], 0, 0
    while i < len(lines):
        out.append(lines[i])
        if lines[i] == "\t(symbol":
            depth, j = 0, i
            while True:
                depth += lines[j].count("(") - lines[j].count(")")
                if depth == 0:
                    break
                j += 1
            blk = lines[i + 1:j + 1]
            text = "\n".join(blk)
            lib_id = re.search(r'\(lib_id "([^"]+)"\)', text).group(1)
            if not lib_id.startswith("power:") and '(property "LCSC"' not in text:
                value = re.search(r'\(property "Value" "([^"]*)"', text).group(1)
                x, y = re.search(r"\(at ([\d.\-]+) ([\d.\-]+)", text).groups()
                prop = [f'\t\t(property "LCSC" "{KNOWN.get(value, "TBD")}"', f"\t\t\t(at {x} {y} 0)",
                        "\t\t\t(effects", "\t\t\t\t(font", "\t\t\t\t\t(size 1.27 1.27)", "\t\t\t\t)",
                        "\t\t\t\t(hide yes)", "\t\t\t)", "\t\t)"]
                k = next(n for n, l in enumerate(blk) if l.startswith("\t\t(pin ") or l.startswith("\t\t(instances"))
                blk = blk[:k] + prop + blk[k:]
                added += 1
            out += blk
            i = j + 1
            continue
        i += 1
    open(path, "w", encoding="utf-8").write("\n".join(out))
    return added


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(p, add_fields(p), "LCSC fields added")
