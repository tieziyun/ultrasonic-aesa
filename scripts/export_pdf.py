"""Export the schematic PDFs.

  docs/schematics/<sheet>.pdf          one PDF per sheet file (first instance, e.g. CH01 for channel)
  docs/schematics/aesa_schematic.pdf   top sheet + one copy of each sheet, in one file (committed)
  build/aesa_schematic_full.pdf        every instance, 110 pages (not committed)

Run after every schematic change:
  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe scripts\\export_pdf.py
"""

import subprocess
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import fitz  # noqa: E402  PyMuPDF, bundled with KiCad's Python

REPO = Path(__file__).resolve().parent.parent
CLI = r"C:\Users\Tiezi\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe"
BUILD = REPO / "build"
DOCS = REPO / "docs" / "schematics"
ORDER = ["aesa", "channel", "adc", "tx_group", "en_buf", "clk_buf", "mic", "headers", "power"]


def main():
    BUILD.mkdir(exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    full = BUILD / "aesa_schematic_full.pdf"
    subprocess.run([CLI, "sch", "export", "pdf", "-o", str(full), str(REPO / "kicad" / "aesa.kicad_sch")],
                   check=True, capture_output=True)
    doc = fitz.open(full)
    first = {}
    for i in range(doc.page_count):
        text = doc[i].get_text()
        name = next((l.split("File:")[1].strip() for l in text.splitlines() if "File:" in l), None)
        if name:
            first.setdefault(name.replace(".kicad_sch", ""), i)
    missing = [n for n in ORDER if n not in first]
    extra = [n for n in first if n not in ORDER]
    if missing or extra:
        raise SystemExit(f"sheet list out of date: missing {missing}, new {extra}; update ORDER")
    for old in DOCS.glob("*.pdf"):
        old.unlink()
    combined = fitz.open()
    for n, name in enumerate(ORDER):
        one = fitz.open()
        one.insert_pdf(doc, from_page=first[name], to_page=first[name])
        one.save(DOCS / f"{n:02d}_{name}.pdf")
        combined.insert_pdf(doc, from_page=first[name], to_page=first[name])
    combined.save(DOCS / "aesa_schematic.pdf")
    print(f"{full.relative_to(REPO)}: {doc.page_count} pages; docs/schematics: {len(ORDER)} sheets + combined")


if __name__ == "__main__":
    main()
