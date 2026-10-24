"""Assembly drawing for building, repairing and debugging the board: docs/assembly/aesa_assembly.pdf.

Page 1 = front (transducers, viewed from the front), page 2 = back (all SMT parts, mirrored so it reads
as seen when the board is turned over). Each page shows the outline, every part's body outline and pads
(fab layer) with its reference, and crosses out the DNP parts (the hand-soldered transducers).

  python scripts/assembly_pdf.py
"""

import subprocess
from pathlib import Path

import pymupdf as fitz  # bundled with KiCad's Python

from common import KICAD_CLI

REPO = Path(__file__).resolve().parent.parent
PCB = REPO / "kicad" / "aesa.kicad_pcb"
OUT = REPO / "docs" / "assembly" / "aesa_assembly.pdf"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pages = []
    for side, layers, extra in (("front", "F.Fab,Edge.Cuts", []), ("back", "B.Fab,Edge.Cuts", ["--mirror"])):
        pdf = REPO / "build" / f"assembly_{side}.pdf"
        subprocess.run([KICAD_CLI, "pcb", "export", "pdf", "--mode-single", "-o", str(pdf), "-l", layers,
                        "--include-border-title", "--scale", "0", "--sketch-pads-on-fab-layers",
                        "--crossout-DNP-footprints-on-fab-layers", "--exclude-value", "--black-and-white",
                        *extra, str(PCB)], check=True, capture_output=True)
        pages.append(fitz.open(pdf))
    doc = fitz.open()
    for title, src in zip(("Assembly, FRONT (transducers; DNP = hand-soldered)",
                           "Assembly, BACK (all SMT parts; mirrored: as seen with the board turned over)"), pages):
        doc.insert_pdf(src)
        doc[-1].insert_text((40, 40), title, fontsize=14)
        doc.set_toc(doc.get_toc() + [[1, title, len(doc)]])
        src.close()
    doc.save(OUT, garbage=3, deflate=True)
    print(f"{OUT.relative_to(REPO)}: {len(doc)} pages")


if __name__ == "__main__":
    main()
