"""Design loop for the channel block: the hand-made placement (place.py CHANNEL) and routing (route.py
CHANNEL_ROUTE) put on a 2 x 2 block of real cells (CH01, CH02, CH09, CH10) of the placed board, with the
worst case of the front-copper column plan around them (every lane slot occupied, route.py), checked with
KiCad's DRC and plotted. The board itself is not changed.

Checks on top of the DRC: no courtyard within the soldering keep-out (SOLDER_R from a transducer pad
centre), every part inside its cell, and the length of each net (shortest Manhattan tree over its pads).

  python scripts/cell_design.py
writes build/cell/cell.kicad_pcb, build/cell/drc.json and build/cell_design.png
"""

import json
import math
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pcbnew  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import place  # noqa: E402
import route  # noqa: E402
from route_lib import local_ref, xy  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "build" / "cell"
CELLS = (1, 2, 9, 10)                      # CH01 and its neighbours below, right, diagonal
SOLDER_R = 1.0 + place.SOLDER_KEEPOUT      # keep-out radius around each transducer pad centre
HALF = place.P / 2 - 0.2
SENSITIVE = ("LIM", "FB1", "INV2", "N2", "N103")


def box_dist(b, x, y):
    return math.hypot(max(b[0] - x, 0, x - b[2]), max(b[1] - y, 0, y - b[3]))


def mst(pts):
    if len(pts) < 2:
        return 0.0
    left, tree, tot = set(range(1, len(pts))), [0], 0.0
    while left:
        d, j = min((abs(pts[j][0] - pts[i][0]) + abs(pts[j][1] - pts[i][1]), j) for j in left for i in tree)
        tot += d
        left.remove(j)
        tree.append(j)
    return tot


def build():
    board = pcbnew.LoadBoard(str(REPO / "kicad" / "aesa.kicad_pcb"))
    keep = {}
    for f in list(board.GetFootprints()):
        sid = place.sid_of(f.GetReference())
        if sid in CELLS:
            keep.setdefault(sid, []).append(f)
        else:
            board.Delete(f)
    for t in list(board.GetTracks()):
        board.Delete(t)
    for z in list(board.Zones()):
        if z.GetIsRuleArea() or z.GetLayer() not in (pcbnew.In1_Cu, pcbnew.In2_Cu):
            board.Delete(z)
    centres = {}
    for n, fps in keep.items():
        ls = next(f for f in fps if f.GetReference().startswith("LS"))
        cx, cy = xy(ls.GetPosition())
        centres[n] = (cx, cy)
        rest = place.place_template([f for f in fps if f is not ls], cx, cy, place.CHANNEL)
        if rest:
            raise SystemExit(f"not in the template: {[f.GetReference() for f in rest]}")
        route.draw_cell(board, fps, cx, cy, route.CHANNEL_ROUTE, drv_row=7, rx_row=0)  # busiest rows
    cols = sorted({c for c, _ in centres.values()})
    y0 = min(c for _, c in centres.values()) - place.P / 2
    y1 = max(c for _, c in centres.values()) + place.P / 2
    for cx in cols:
        route.draw_column_worst_case(board, cx, y0, y1, guards=(-1, 1) if cx == cols[-1] else (-1,))
    OUT.mkdir(parents=True, exist_ok=True)
    pcb = OUT / "cell.kicad_pcb"
    board.Save(str(pcb))
    for ext in ("kicad_pro", "kicad_dru"):
        shutil.copy(REPO / "kicad" / f"aesa.{ext}", pcb.with_suffix("." + ext))
    return board, keep, centres, pcb


def drc(pcb, centres):
    rpt = OUT / "drc.json"
    subprocess.run([str(place.KICAD_CLI), "pcb", "drc", "--format", "json", "--refill-zones", "--severity-all",
                    "-o", str(rpt), str(pcb)], capture_output=True)
    data = json.loads(rpt.read_text(encoding="utf-8"))
    cx, cy = centres[1]
    x0 = min(c for c, _ in centres.values()) - 10.5
    x1 = max(c for c, _ in centres.values()) + 10.5
    y0 = min(c for _, c in centres.values()) - 10.5
    y1 = max(c for _, c in centres.values()) + 10.5
    out = defaultdict(list)
    for v in data.get("violations", []) + data.get("unconnected_items", []):
        items = v.get("items", [])
        pos = [(i["pos"]["x"], i["pos"]["y"]) for i in items if "pos" in i]
        if v["type"] in ("lib_footprint_issues", "lib_footprint_mismatch"):
            continue
        if pos and not any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in pos):
            continue                               # the board edge, other blocks' leftovers
        descs = " ".join(i.get("description", "") for i in items)
        if v["type"] == "track_dangling" and re.search(r"\[(DRV|RX)\d+\]", descs):
            continue                               # the lanes stop at the block and cell edges here
        if v["type"] == "unconnected_items" and "Pad" not in descs and "Via" not in descs:
            continue                               # lanes and rails of the two columns meet off this block
        rel = [f"({x - cx:+.2f},{y - cy:+.2f})" for x, y in pos]
        out[v["type"]].append(f"{v['description']}: " + " / ".join(i.get("description", "") for i in items)
                              + " @ " + " ".join(rel))
    return out


def own_checks(keep, centres):
    problems, lengths = [], {}
    for n, fps in keep.items():
        cx, cy = centres[n]
        for f in fps:
            if f.GetReference().startswith("LS"):
                continue
            x, y, w, h = place.bbox_mm(f)
            b = (x - cx, y - cy, x + w - cx, y + h - cy)
            for px in (-2.5, 2.5):
                d = box_dist(b, px, 0)
                if d < SOLDER_R - 1e-6:
                    problems.append(f"{f.GetReference()} {d:.2f} mm from the transducer pad at x={px:+} "
                                    f"(keep-out {SOLDER_R})")
            if min(b) < -HALF - 1e-6 or max(b) > HALF + 1e-6:
                problems.append(f"{f.GetReference()} outside its cell: {tuple(round(v, 2) for v in b)}")
        if n == 1:
            nets = defaultdict(list)
            for f in fps:
                for p in f.Pads():
                    nets[p.GetNetname().split("/")[-1]].append(xy(p.GetPosition()))
            lengths = {k: mst(v) for k, v in nets.items() if len(v) > 1 and k != "GND"}
    return problems, lengths


def plot(board, fps, centre, out):
    ox, oy = centre
    fig, ax = plt.subplots(figsize=(11, 11))
    for a, b in route.lane_band(-1):
        ax.axvspan(a, b, color="tab:red", alpha=0.07)
    for a, b in route.lane_band(1):
        ax.axvspan(a, b, color="tab:green", alpha=0.07)
    for px in (-2.5, 2.5):
        ax.add_patch(plt.Circle((px, 0), SOLDER_R, fill=False, ls="--", ec="tab:orange", lw=0.8))
    nets = sorted({p.GetNetname() for f in fps for p in f.Pads()})
    col = {n: plt.cm.tab20.colors[k % 20] for k, n in enumerate(nets)}
    col.update({n: "0.55" for n in nets if n.endswith("GND")})
    for t in board.GetTracks():
        (xa, ya), (xb, yb) = xy(t.GetStart()), xy(t.GetEnd())
        xa, ya, xb, yb = xa - ox, ya - oy, xb - ox, yb - oy
        if max(abs(xa), abs(ya), abs(xb), abs(yb)) > 12.5:
            if not (abs(xa) < 12.5 and abs(xb) < 12.5):
                continue
        if t.GetClass() == "PCB_VIA":
            ax.add_patch(plt.Circle((xa, ya), pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2, color="k", zorder=5))
            ax.add_patch(plt.Circle((xa, ya), pcbnew.ToMM(t.GetDrillValue()) / 2, color="w", zorder=6))
        else:
            front = t.GetLayer() == pcbnew.F_Cu
            ax.plot([xa, xb], [ya, yb], lw=pcbnew.ToMM(t.GetWidth()) * 13, solid_capstyle="round",
                    color="tab:red" if front else "tab:blue", alpha=0.35 if front else 0.75, zorder=3)
    for f in fps:
        c = f.GetCourtyard(pcbnew.B_CrtYd if f.IsFlipped() else pcbnew.F_CrtYd)
        if c.OutlineCount() and not f.GetReference().startswith("LS"):
            bb = c.BBox()
            ax.add_patch(plt.Rectangle((pcbnew.ToMM(bb.GetX()) - ox, pcbnew.ToMM(bb.GetY()) - oy),
                                       pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight()),
                                       fill=False, ec="0.5", lw=0.6))
        x, y = xy(f.GetPosition())
        r = f.GetReference()
        ax.text(x - ox, y - oy, local_ref(r), fontsize=7, ha="center",
                va="center", color="0.25", zorder=8)
        for p in f.Pads():
            net = p.GetNetname()
            px, py = xy(p.GetPosition())
            bb = p.GetBoundingBox()
            w, h = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())
            ax.add_patch(plt.Rectangle((px - ox - w / 2, py - oy - h / 2), w, h, color=col.get(net, "k"),
                                       alpha=0.8, zorder=4))
            ax.text(px - ox, py - oy + h / 2 + 0.05, net.split("/")[-1], fontsize=5, ha="center", va="top",
                    zorder=9)
    ax.add_patch(plt.Rectangle((-10, -10), 20, 20, fill=False, ec="k", lw=0.5, ls=":"))
    ax.set_xlim(-11, 11)
    ax.set_ylim(11, -11)
    ax.set_aspect("equal")
    ax.grid(alpha=0.25)
    ax.set_title("CH01 seen from the top (parts on the back): blue B.Cu, red F.Cu, black vias;\n"
                 "shaded: DRV (red) and RX (green) lane bands, dashed: soldering keep-out")
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)


def main():
    board, keep, centres, pcb = build()
    problems, lengths = own_checks(keep, centres)
    viol = drc(pcb, centres)
    print("placement:", "ok" if not problems else "")
    for p in problems:
        print("  " + p)
    print("DRC (2 x 2 block):", "clean" if not viol else "")
    for k, vs in sorted(viol.items()):
        print(f"  {k}: {len(vs)}")
        for v in vs[:12]:
            print("    " + v)
    print("net lengths (mm, pad tree):", ", ".join(f"{n} {lengths[n]:.1f}" for n in SENSITIVE if n in lengths),
          "|", ", ".join(f"{n} {v:.1f}" for n, v in sorted(lengths.items()) if n not in SENSITIVE))
    out = REPO / "build" / "cell_design.png"
    plot(board, keep[1], centres[1], out)
    print(out)


if __name__ == "__main__":
    main()
