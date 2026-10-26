"""Summarise a KiCad DRC report (JSON): violations by type with a few examples, and the unrouted nets.

  python scripts/drc_summary.py build/drc.json [type ...]     (types: list every item of those types)
"""

import json
import re
import sys
from collections import Counter


def main(path, show=()):
    d = json.load(open(path, encoding="utf-8"))
    v = d.get("violations", [])
    un = d.get("unconnected_items", [])
    print(f"{len(v)} violations, {len(un)} unconnected")
    for k, n in Counter(x["type"] for x in v).most_common():
        ex = [x for x in v if x["type"] == k]
        print(f"  {k}: {n}")
        for x in ex if k in show else ex[:3]:
            pos = " ".join(f"({i['pos']['x']:.2f},{i['pos']['y']:.2f})" for i in x["items"] if "pos" in i)
            print("    " + " / ".join(i.get("description", "")[:80] for i in x["items"]) + " @ " + pos)
    nets = Counter()
    for x in un:
        for i in x["items"][:1]:
            m = re.search(r"\[([^\]]+)\]", i.get("description", ""))
            if m:
                nets[m.group(1)] += 1
    if nets:
        print("  unconnected by net (top 25):", ", ".join(f"{k} {n}" for k, n in nets.most_common(25)))


if __name__ == "__main__":
    main(sys.argv[1], set(sys.argv[2:]))
