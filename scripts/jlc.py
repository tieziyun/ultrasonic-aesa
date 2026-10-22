"""Small client for the JLCPCB parts library (same C-numbers as LCSC) and the jlcsearch
parametric index. Used by scripts/bom.py.

  python scripts/jlc.py TPS54202DDCR 74LVC2G34GW      (print the matches)
"""

import json
import sys
import time
import urllib.parse
import urllib.request

JLC = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120", "Content-Type": "application/json",
     "Accept": "application/json", "Origin": "https://jlcpcb.com", "Referer": "https://jlcpcb.com/parts"}
_cache = {}


def jlc_search(keyword, n=25):
    """Parts matching a keyword (MPN, C-number or description): list of dicts with
    code, mpn, maker, package, basic, preferred, stock, prices [(qty, usd)], description."""
    if keyword in _cache:
        return _cache[keyword]
    body = json.dumps({"keyword": keyword, "currentPage": 1, "pageSize": n, "searchSource": "search"}).encode()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(JLC, data=body, headers=H), timeout=40) as r:
                data = json.load(r)
            break
        except Exception:
            time.sleep(2 + 3 * attempt)
    else:
        return []
    out = []
    for x in ((data.get("data") or {}).get("componentPageInfo") or {}).get("list") or []:
        out.append(dict(code=x.get("componentCode"), mpn=x.get("componentModelEn"),
                        maker=x.get("componentBrandEn"), package=x.get("componentSpecificationEn"),
                        basic=x.get("componentLibraryType") == "base",
                        preferred=x.get("preferredComponentFlag") in (True, 1, "true"),
                        stock=x.get("stockCount") or 0,
                        prices=[(p.get("startNumber"), p.get("productPrice")) for p in x.get("componentPrices") or []],
                        description=x.get("describe") or ""))
    _cache[keyword] = out
    time.sleep(0.4)
    return out


def price_at(part, qty):
    best = None
    for start, p in sorted(part["prices"], key=lambda t: t[0] or 0):
        if start is None or qty >= start:
            best = p
    return best if best is not None else (part["prices"][0][1] if part["prices"] else None)


def jlcsearch(path, **q):
    url = "https://jlcsearch.tscircuit.com" + path + "?" + urllib.parse.urlencode(q)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                                        timeout=40) as r:
                return json.load(r)
        except Exception:
            time.sleep(2 + 3 * attempt)
    return {}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for kw in sys.argv[1:]:
        print(f"== {kw}")
        for p in jlc_search(kw)[:8]:
            print(f"  {p['code']:9s} {p['mpn'][:28]:28s} {p['package'][:16]:16s} {'BASIC' if p['basic'] else 'ext  '} "
                  f"stock {p['stock']:>8} ${price_at(p, 1)} | {p['description'][:90]}")
