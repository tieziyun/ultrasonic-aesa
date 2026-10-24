"""Export the schematic as ONE PDF with working links: docs/schematics/aesa_schematic.pdf.

KiCad prints every sheet instance (115 pages: 64 identical channel pages, 16 tx_group pages, ...).
This keeps one page per sheet file (the first instance) and fixes it up:
  * every clickable block (sheet symbol) opens the page of its sheet; KiCad's own links point
    at instance pages that are not in this file, so each one is remapped to the sheet's page
  * an "up" link in the top margin returns to the parent sheet
  * bookmarks: one per sheet, nested as in the hierarchy, with the sheet title
  * the page ids in the title blocks are renumbered "n/14"

gen_top.py runs this at the end, so the PDF follows every schematic change. By hand:
  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe scripts\\export_pdf.py
The 115-page KiCad export stays in build/aesa_schematic_full.pdf (not committed).
"""

import re
import subprocess
import sys
from pathlib import Path

import pymupdf as fitz  # bundled with KiCad's Python

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import KICAD_CLI  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FULL = REPO / "build" / "aesa_schematic_full.pdf"
OUT = REPO / "docs" / "schematics" / "aesa_schematic.pdf"
ORDER = ["aesa", "headers", "clocks", "clk_buf", "tx", "tx_group", "en_buf", "array", "channel", "adcs", "adc",
         "mics", "mic", "power", "power_analog"]


def field(text, key):
    m = re.search(rf"^{key}: (.*)$", text, re.M)
    return m.group(1).strip() if m else None


def same_size(doc):
    """Every page on an A3 landscape canvas, so the pages read at one scale (the cell sheets are A4, a
    few group sheets A2; ISO A-sizes share one aspect ratio, so each page just scales). Links follow."""
    a3 = fitz.paper_rect("a3-l")
    out = fitz.open()
    for k in range(len(doc)):
        page = out.new_page(width=a3.width, height=a3.height)
        page.show_pdf_page(page.rect, doc, k)
    for k, src in enumerate(doc):  # links once every page exists (they point forward too)
        s, page = a3.width / src.rect.width, out[k]
        for ln in src.get_links():
            new = {"kind": ln["kind"], "from": ln["from"] * fitz.Matrix(s, s)}
            if ln["kind"] == fitz.LINK_GOTO:
                new["page"] = ln["page"]
            elif ln["kind"] == fitz.LINK_URI:
                new["uri"] = ln["uri"]
            else:
                continue
            page.insert_link(new)
    return out


def main():
    FULL.parent.mkdir(exist_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([KICAD_CLI, "sch", "export", "pdf", "-o", str(FULL), str(REPO / "kicad" / "aesa.kicad_sch")],
                   check=True, capture_output=True)
    full = fitz.open(FULL)
    sheet_of, first, count, title = {}, {}, {}, {}
    for i, page in enumerate(full):
        t = page.get_text()
        name = (field(t, "File") or "").replace(".kicad_sch", "")
        sheet_of[i] = name
        first.setdefault(name, i)
        count[name] = count.get(name, 0) + 1
        title.setdefault(name, field(t, "Title") or name)
    missing, extra = [n for n in ORDER if n not in first], [n for n in first if n not in ORDER]
    if missing or extra:
        raise SystemExit(f"sheet list out of date: missing {missing}, new {extra}; update ORDER")
    new_page = {name: k for k, name in enumerate(ORDER)}

    out = fitz.open()
    parent = {}
    for name in ORDER:
        out.insert_pdf(full, from_page=first[name], to_page=first[name], links=False, annots=False)
    for k, name in enumerate(ORDER):
        src, dst = full[first[name]], out[k]
        for ln in src.get_links():
            if ln["kind"] == fitz.LINK_GOTO:
                target = sheet_of.get(ln["page"])
                if target not in new_page or target == name:
                    continue
                parent.setdefault(target, name)
                dst.insert_link({"kind": fitz.LINK_GOTO, "from": ln["from"], "page": new_page[target]})
            elif ln["kind"] == fitz.LINK_URI:
                dst.insert_link({"kind": fitz.LINK_URI, "from": ln["from"], "uri": ln["uri"]})
        # page id in the title block: "Id: 10/115" -> "Id: 5/14"
        old_id = field(src.get_text(), "Id")
        for r in dst.search_for(f"Id: {old_id}"):
            dst.add_redact_annot(r, fill=False)  # no box: the page is not white
        dst.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        for r in src.search_for(f"Id: {old_id}"):
            dst.insert_text((r.x0, r.y1 - 0.25 * r.height), f"Id: {k + 1}/{len(ORDER)}", fontsize=r.height * 0.85,
                            color=(0.52, 0, 0))  # KiCad title-block red
    # "up" links in the top margin, and a note on reused sheets
    for k, name in enumerate(ORDER):
        dst = out[k]
        x, y = 30, 22
        if name in parent:
            label = f"<< up to: {title[parent[name]]}"  # (Helvetica has no arrow glyph)
            dst.insert_text((x, y), label, fontsize=9, color=(0, 0.3, 0.8))
            w = fitz.get_text_length(label, fontsize=9)
            dst.insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(x, y - 9, x + w, y + 2),
                             "page": new_page[parent[name]]})
            x += w + 20
        if count[name] > 1:
            dst.insert_text((x, y), f"This sheet is used {count[name]} times; the page shows the first instance.",
                            fontsize=9, color=(0.4, 0.4, 0.4))
    # bookmarks, nested like the hierarchy
    def depth(name):
        return 1 if name == "aesa" else depth(parent[name]) + 1
    toc = [[depth(n), title[n] + (f" (x{count[n]})" if count[n] > 1 and "(x" not in title[n] else ""),
            new_page[n] + 1] for n in ORDER]
    dst = None  # release the last page, so its new links are in the document before it is copied
    out = same_size(out)
    out.set_toc(toc)
    out.set_metadata({"title": title["aesa"], "subject": "Schematic, one page per sheet"})
    try:
        out.save(OUT, garbage=4, deflate=True)
    except Exception:  # a PDF viewer holds the file open: keep the result, say what to do
        alt = REPO / "build" / OUT.name
        out.save(alt, garbage=4, deflate=True)
        raise SystemExit(f"{OUT.relative_to(REPO)} is open in another program (close it and run "
                         f"scripts/export_pdf.py); this export is in {alt.relative_to(REPO)}")
    unlinked = [n for n in ORDER if n != "aesa" and n not in parent]
    if unlinked:
        raise SystemExit(f"no block links to: {unlinked}")
    print(f"{OUT.relative_to(REPO)}: {len(ORDER)} pages, {sum(len(p.get_links()) for p in out)} links, "
          f"{OUT.stat().st_size // 1024} KB (all {full.page_count} instance pages: {FULL.relative_to(REPO)})")


if __name__ == "__main__":
    main()
