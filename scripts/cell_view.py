"""Plot one block of the board for layout work: courtyards, pads coloured by net (labelled), the airwires
still to route (minimum spanning tree per net), and the routed tracks/vias. Board coordinates seen from
the top; the block's centre or origin is at (0, 0).

  python scripts/cell_view.py CH01 [out.png]          (channel, centred on its transducer)
  python scripts/cell_view.py ADC01|TXG01|CLKB1 [out.png]
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pcbnew  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def main(name, out):
    board = pcbnew.LoadBoard(str(REPO / "kicad" / "aesa.kicad_pcb"))
    grp = next(g for g in board.Groups() if g.GetName() == name)
    fps = [i for i in grp.GetItems() if isinstance(i, pcbnew.FOOTPRINT)]
    if name.startswith("CH"):
        n = int(name[2:])
        ls = next(f for f in board.GetFootprints() if f.GetReference() == f"LS{n}01")
        fps.append(ls)
        ox, oy = pcbnew.ToMM(ls.GetPosition().x), pcbnew.ToMM(ls.GetPosition().y)
    else:
        bb = grp.GetBoundingBox()
        ox, oy = pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY())
    fig, ax = plt.subplots(figsize=(10, 10))
    pads = defaultdict(list)
    colors = plt.cm.tab20.colors
    nets = sorted({p.GetNetname() for f in fps for p in f.Pads()})
    col = {n: colors[k % len(colors)] for k, n in enumerate(nets)}
    for f in fps:
        side = "B" if f.IsFlipped() else "F"
        for layer in (pcbnew.B_CrtYd, pcbnew.F_CrtYd):
            c = f.GetCourtyard(layer)
            if c.OutlineCount():
                b = c.BBox()
                x0, y0 = pcbnew.ToMM(b.GetX()) - ox, pcbnew.ToMM(b.GetY()) - oy
                ax.add_patch(plt.Rectangle((x0, y0), pcbnew.ToMM(b.GetWidth()), pcbnew.ToMM(b.GetHeight()),
                                           fill=False, ec="0.6", lw=0.6, ls="-" if side == "B" else ":"))
        r = f.GetReference()
        p = f.GetPosition()
        ax.text(pcbnew.ToMM(p.x) - ox, pcbnew.ToMM(p.y) - oy, re.sub(r"\d\d$", "", r[:-2]) + r[-2:].lstrip("0"),
                fontsize=7, ha="center", va="center", color="0.3")
        for pd in f.Pads():
            net = pd.GetNetname()
            x, y = pcbnew.ToMM(pd.GetPosition().x) - ox, pcbnew.ToMM(pd.GetPosition().y) - oy
            w, h = pcbnew.ToMM(pd.GetBoundingBox().GetWidth()), pcbnew.ToMM(pd.GetBoundingBox().GetHeight())
            ax.add_patch(plt.Rectangle((x - w / 2, y - h / 2), w, h, color=col.get(net, "k"), alpha=0.75))
            pads[net].append((x, y))
            short = net.split("/")[-1]
            ax.text(x, y + h / 2 + 0.25, short, fontsize=5.5, ha="center", va="top")
    for net, pts in pads.items():  # airwires: MST
        if len(pts) < 2 or net == "GND":
            continue
        left, tree = set(range(1, len(pts))), [0]
        while left:
            j, i = min(((j, i) for j in left for i in tree),
                       key=lambda ji: abs(pts[ji[0]][0] - pts[ji[1]][0]) + abs(pts[ji[0]][1] - pts[ji[1]][1]))
            ax.plot([pts[i][0], pts[j][0]], [pts[i][1], pts[j][1]], color=col[net], lw=0.8, ls="--")
            left.remove(j)
            tree.append(j)
    for t in board.GetTracks():
        a, b = t.GetStart(), t.GetEnd()
        xa, ya, xb, yb = (pcbnew.ToMM(a.x) - ox, pcbnew.ToMM(a.y) - oy, pcbnew.ToMM(b.x) - ox, pcbnew.ToMM(b.y) - oy)
        if max(abs(xa), abs(ya)) > 14:
            continue
        if t.GetClass() == "PCB_VIA":
            ax.add_patch(plt.Circle((xa, ya), pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2, color="k"))
        else:
            ax.plot([xa, xb], [ya, yb], lw=pcbnew.ToMM(t.GetWidth()) * 7,
                    color="tab:red" if t.GetLayer() == pcbnew.F_Cu else "tab:blue", alpha=0.6)
    lim = 11 if name.startswith("CH") else 14
    ax.set_xlim(-lim, lim if name.startswith("CH") else 2 * lim)
    ax.set_ylim(lim, -lim)
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    ax.set_title(f"{name}: courtyards (solid back, dotted front), pads by net, airwires dashed")
    fig.savefig(out, dpi=130, bbox_inches="tight")
    print(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else str(REPO / "build" / f"cell_{sys.argv[1]}.png"))
