"""Set the title block and the design notes of a hand-drawn sheet (channel.kicad_sch).

Generated sheets get theirs from build_cells.py; this does the same for the MCP-drawn channel
sheet. Re-running replaces the notes it wrote before (they carry a fixed uuid prefix).

  python scripts/sch_annotate.py
"""

import re
import uuid
from pathlib import Path
from common import DATE, REV

KDIR = Path(__file__).resolve().parent.parent / "kicad"
NS = uuid.UUID("6f1e3a52-0000-4000-8000-0000000000d4")

SHEETS = {
    "channel": dict(
        title="Transceiver channel (x64): TX, T/R switch, RX amplifier",
        notes=[
            ("Transceiver channel (one of 64). TX: UCC27524 half -> 47 R -> BAV99 crossed diodes -> element "
             "(0-15 V square).", 25.4, 22.86, 2.0),
            ("33k bleed resets the element after TX. RX: 4.7k + BAV99 clamp (+-0.7 V) + 100 pF (340 kHz RF filter).",
             25.4, 27.43, 1.8),
            ("OPA1678 stage 1: gain 6, AC-coupled at 800 Hz. Stage 2: MFB band-pass f0 39.8 kHz, Q 2, gain -4. "
             "Then 100 R to the ADC.", 25.4, 31.5, 1.8),
            ("Element -> amplifier out +27 dB (x22), -> ADC pin +16.5 dB after the ADC cell's 2k/1k divider; "
             "pass band 31-51 kHz (sim/reports/rx_chain.md). Band-pass parts 1 % C0G.", 25.4, 35.56, 1.8),
        ]),
}


def text(s, x, y, size, k, name):
    u = uuid.uuid5(NS, f"{name}/{k}")
    return (f'\t(text "{s}"\n\t\t(exclude_from_sim no)\n\t\t(at {x} {y} 0)\n\t\t(effects\n\t\t\t(font\n'
            f'\t\t\t\t(size {size} {size})\n\t\t\t\t(bold yes)\n\t\t\t)\n\t\t\t(justify left bottom)\n\t\t)\n'
            f'\t\t(uuid "{u}")\n\t)\n')


def annotate(name, spec):
    path = KDIR / f"{name}.kicad_sch"
    t = path.read_text(encoding="utf-8")
    t = re.sub(r"\n\t\(title_block\n.*?\n\t\)", "", t, flags=re.S)
    tb = (f'\n\t(title_block\n\t\t(title "{spec["title"]}")\n\t\t(date "{DATE}")\n\t\t(rev "{REV}")\n'
          f'\t\t(company "Hybrid 8x8 ultrasonic AESA")\n\t)')
    t = re.sub(r'(\n\t\(paper "[^"]+"\))', lambda m: m.group(1) + tb, t, count=1)
    ids = {str(uuid.uuid5(NS, f"{name}/{k}")) for k in range(len(spec["notes"]))}
    for i in ids:  # drop earlier copies of our notes
        t = re.sub(r'\t\(text "[^"]*"\n(?:\t\t[^\n]*\n)*?\t\t\(uuid "' + i + r'"\)\n\t\)\n', "", t)
    new = "".join(text(s, x, y, size, k, name) for k, (s, x, y, size) in enumerate(spec["notes"]))
    k = t.rindex("\t(sheet_instances")
    t = t[:k] + new + t[k:]
    path.write_text(t, encoding="utf-8")
    print(f"{name}: title block and {len(spec['notes'])} notes")


if __name__ == "__main__":
    for n, s in SHEETS.items():
        annotate(n, s)
