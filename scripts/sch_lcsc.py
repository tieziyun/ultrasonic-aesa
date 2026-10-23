"""Give every placed symbol an LCSC field (CLAUDE.md: every part needs an LCSC number or
a hand-solder/consigned mark).

Missing LCSC fields are filled from scripts/parts.json (written by scripts/bom.py, keyed by
"value|footprint"), else from KNOWN by value, else "TBD". With --force, existing fields are
overwritten from parts.json too (run after bom.py). Power symbols and PWR_FLAGs are skipped.

  python scripts/sch_lcsc.py [--force] kicad/*.kicad_sch
"""

import json
import os
import re
import sys
from common import block_end

_PJ = os.path.join(os.path.dirname(__file__), "parts.json")
PARTS = json.load(open(_PJ)) if os.path.exists(_PJ) else {}
FORCE = "--force" in sys.argv

# Checked part numbers (value -> LCSC). Keep in sync with skidl/channel.py and the brief.
KNOWN = {
    "UCC27524DR": "C465729",
    "OPA1678IDR": "C192421",
    "BAV99": "C2500",
    "AD7606BBSTZ-RL": "C651284",
    "AD7606C-16BSTZ": "C5240947",
    "74HCT595D": "C282339",
    "SPH0641LU4H-1": "C2879853",
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
            j = block_end(lines, i)
            blk = lines[i + 1:j + 1]
            text = "\n".join(blk)
            lib_id = re.search(r'\(lib_id "([^"]+)"\)', text).group(1)
            value = re.search(r'\(property "Value" "([^"]*)"', text).group(1)
            fpm = re.search(r'\(property "Footprint" "([^"]*)"', text)
            code = PARTS.get(f"{value}|{fpm.group(1) if fpm else ''}") or KNOWN.get(value, "TBD")
            if lib_id.startswith("power:"):
                pass
            elif '(property "LCSC"' in text:
                new = re.sub(r'\(property "LCSC" "[^"]*"', f'(property "LCSC" "{code}"', text, count=1)
                if FORCE and new != text and code != "TBD":  # never replace a real number with TBD
                    blk = new.split("\n")
                    added += 1
            else:
                x, y = re.search(r"\(at ([\d.\-]+) ([\d.\-]+)", text).groups()
                prop = [f'\t\t(property "LCSC" "{code}"', f"\t\t\t(at {x} {y} 0)",
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
    for p in [a for a in sys.argv[1:] if not a.startswith("--")]:
        print(p, add_fields(p), "LCSC fields added or changed")
