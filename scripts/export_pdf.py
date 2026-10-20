"""Export the schematic PDFs into build/:

  build/aesa_schematic.pdf          every sheet instance (top + 64 channels + ...)
  build/aesa_schematic_summary.pdf  the top sheet plus one copy of each distinct sheet file

  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe scripts\\export_pdf.py
"""

import subprocess
from pathlib import Path

import fitz  # PyMuPDF, bundled with KiCad's Python

REPO = Path(__file__).resolve().parent.parent
CLI = r"C:\Users\Tiezi\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe"
BUILD = REPO / "build"


def main():
    BUILD.mkdir(exist_ok=True)
    full = BUILD / "aesa_schematic.pdf"
    subprocess.run([CLI, "sch", "export", "pdf", "-o", str(full), str(REPO / "kicad" / "aesa.kicad_sch")],
                   check=True, capture_output=True)
    doc = fitz.open(full)
    keep, seen = [0], set()
    for i in range(1, doc.page_count):
        text = doc[i].get_text()
        name = next((l.split("File:")[1].strip() for l in text.splitlines() if "File:" in l), f"page{i}")
        if name not in seen:
            seen.add(name)
            keep.append(i)
    out = fitz.open()
    for i in keep:
        out.insert_pdf(doc, from_page=i, to_page=i)
    out.save(BUILD / "aesa_schematic_summary.pdf")
    print(f"{full.name}: {doc.page_count} pages; summary: {len(keep)} pages ({', '.join(sorted(seen))})")


if __name__ == "__main__":
    main()
