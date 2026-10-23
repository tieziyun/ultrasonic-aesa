"""Keep docs/datasheets/ in step with the BOM: one PDF per part on the board, plus an index.

Parts come from scripts/parts.json (written by bom.py: every value|footprint -> LCSC number). For
each distinct LCSC number the datasheet is downloaded once:
  * TI and Nexperia parts from the maker's own "latest revision" link (MAKER_URL), so a refresh
    picks up new revisions;
  * everything else through LCSC (the kicad-happy lcsc skill's fetcher).
Identical PDFs (e.g. one series datasheet for many resistor values) are stored once.
docs/datasheets/README.md lists every part, its file, source and download date.

bom.py runs this at the end, so the folder follows every BOM change. Files older than
--refresh-days (default 90) are downloaded again; parts that left the BOM are deleted.

  python scripts/datasheets.py [--refresh-days N] [--force]
"""

import datetime
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "datasheets"
INDEX = OUT / "index.json"
FETCH = Path.home() / ".claude/plugins/cache/kicad-happy/kicad-happy/2.3.1/skills/lcsc/scripts/fetch_datasheet_lcsc.py"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Maker links that always serve the latest revision
TI = "https://www.ti.com/lit/ds/symlink/{}.pdf"
NXP = "https://assets.nexperia.com/documents/data-sheet/{}.pdf"
MAKER_URL = {  # LCSC number -> URL (or (URL, maker)); tried before LCSC's own copy
    "C2879853": ("https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/930/SPH0641LU4H-1.PDF", "Knowles"),
    "C1002": ("https://static.chipdip.ru/lib/674/DOC012674675.pdf", "Sunlord", "Sunlord_GZ_series"),  # LCSC has no link
    "C78804": ("https://e2e.ti.com/cfs-file/__key/communityserver-discussions-components-files/196/SWPA6045-Series.pdf",
               "Sunlord"),
    "C79272": ("https://e2e.ti.com/cfs-file/__key/communityserver-discussions-components-files/196/SWPA6045-Series.pdf",
               "Sunlord"),
    "C2876368": TI.format("tlv320adc5140"), "C311983": TI.format("tps54302"), "C192421": TI.format("opa1678"),
    "C465729": TI.format("ucc27524"), "C87663": TI.format("tps65131"), "C28286": TI.format("tps7a47"),
    "C90082": TI.format("tps7a33"), "C485804": TI.format("lm2731"), "C485517": TI.format("tlv757p"),
    "C7484": TI.format("sn74ahct1g125"), "C282339": NXP.format("74HC_HCT595"),
    "C52140488": NXP.format("74LVC2G34"), "C2500": NXP.format("BAV99_SER"),
}
# Parts not bought through LCSC: value -> (file name, URL)
EXTRA = {"CUSA-TR65-065-2200-TH68": ("CUSA-TR65-065-2200-TH68.pdf",
                                    "https://www.cuidevices.com/product/resource/cusa-tr65-065-2200-th68.pdf")}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        data = r.read()
    if not data.startswith(b"%PDF"):
        raise ValueError(f"not a PDF: {url}")
    return data


def via_lcsc(code):
    """(pdf bytes, MPN, maker, url) through the lcsc skill's lookup; the PDF is downloaded here."""
    sys.path.insert(0, str(FETCH.parent))
    import fetch_datasheet_lcsc as f  # noqa: E402  (kicad-happy lcsc skill)
    comp = f.search_lcsc_direct(code) or {}
    url = f.normalize_url(f._get_datasheet_url(comp) or "")
    if not url:
        raise ValueError(f"no datasheet link on LCSC for {code}")
    return get(url), f._get_mpn(comp) or code, f._get_manufacturer(comp), url


def safe(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def main():
    refresh = int(sys.argv[sys.argv.index("--refresh-days") + 1]) if "--refresh-days" in sys.argv else 90
    force = "--force" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    parts = json.load(open(REPO / "scripts" / "parts.json"))
    uses = {}  # LCSC code -> set of values
    for key, code in parts.items():
        code = code.replace(" (DNP)", "")
        if re.fullmatch(r"C\d+", code):
            uses.setdefault(code, set()).add(key.split("|")[0])
    index = json.loads(INDEX.read_text()) if INDEX.exists() else {}
    today = datetime.date.today()
    by_hash = {e["sha1"]: e["file"] for e in index.values() if "sha1" in e}
    jobs = [(code, None) for code in sorted(uses)] + [(v, spec) for v, spec in EXTRA.items()]
    for key, extra in jobs:
        e = index.get(key)
        fresh = e and e.get("file") and (OUT / e["file"]).exists() and \
            (today - datetime.date.fromisoformat(e["date"])).days < refresh
        if fresh and not force:
            continue
        try:
            if extra:
                data, mpn, maker, url = get(extra[1]), key, "", extra[1]
                fname = extra[0]
            else:
                try:
                    if key not in MAKER_URL:
                        raise KeyError(key)
                    spec = MAKER_URL[key] if isinstance(MAKER_URL[key], tuple) else \
                        (MAKER_URL[key], "TI" if "ti.com/lit" in MAKER_URL[key] else "Nexperia")
                    url, maker = spec[0], spec[1]
                    name = spec[2] if len(spec) > 2 else Path(url).stem
                    data, mpn = get(url), name.upper()
                    fname = f"{safe(name)}.pdf"
                except Exception:  # no maker link, or the maker site refused: LCSC's copy
                    data, mpn, maker, url = via_lcsc(key)
                    fname = f"{safe(mpn)}_{key}.pdf"
        except Exception as ex:  # keep the old copy if there is one; report the failure in the index
            print(f"  {key}: {ex}")
            index.setdefault(key, {})["error"] = str(ex)[:200]
            continue
        sha = hashlib.sha1(data).hexdigest()
        if sha in by_hash and (OUT / by_hash[sha]).exists():
            fname = by_hash[sha]  # same document as another part: link to it
        else:
            (OUT / fname).write_bytes(data)
            by_hash[sha] = fname
        index[key] = dict(file=fname, mpn=mpn, maker=maker, url=url, sha1=sha, date=today.isoformat(),
                          used_as=sorted(uses.get(key, {key})))
        print(f"  {key:10s} {mpn[:28]:28s} -> {fname}")
    # drop parts that left the BOM, and files nothing points to
    for key in [k for k in index if k not in uses and k not in EXTRA]:
        del index[key]
    keep = {e["file"] for e in index.values() if "file" in e}
    for f in OUT.glob("*.pdf"):
        if f.name not in keep:
            f.unlink()
            print(f"  removed {f.name} (no longer in the BOM)")
    for k in index:
        if k in uses:
            index[k]["used_as"] = sorted(uses[k])
    INDEX.write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")
    rows = sorted(index.items(), key=lambda kv: (kv[1].get("file", ""), kv[0]))
    md = ["# Datasheets", "",
          "One PDF per part on the board, kept in step with the BOM by `scripts/datasheets.py` (run by "
          "`scripts/bom.py`; files older than 90 days are downloaded again). TI and Nexperia parts come from the "
          "maker's latest-revision link, the rest through LCSC. Parts that share a series datasheet share a file.", "",
          "| Part | LCSC | Used as | Datasheet | Downloaded |", "|---|---|---|---|---|"]
    for key, e in rows:
        f = e.get("file")
        link = f"[{f}]({f})" if f else f"**missing** ({e.get('error', '')})"
        md.append(f"| {e.get('mpn', key)} | {key if key.startswith('C') else '-'} | "
                  f"{', '.join(e.get('used_as', []))[:80]} | {link} | {e.get('date', '-')} |")
    (OUT / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    n_files = len(list(OUT.glob("*.pdf")))
    size = sum(f.stat().st_size for f in OUT.glob("*.pdf")) / 1e6
    missing = [k for k, e in index.items() if "file" not in e]
    print(f"datasheets: {len(index)} parts, {n_files} PDFs, {size:.1f} MB; missing: {missing}")


if __name__ == "__main__":
    main()
