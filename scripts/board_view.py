"""Plot the board (or a window of it) seen from the top: courtyards coloured by block, tracks (red F.Cu,
blue B.Cu), vias, and block names. For layout work and reviews.

  python scripts/board_view.py [out.png] [x0 y0 x1 y1] [--pour F|B] [--board file.kicad_pcb]
"""

import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pcbnew  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
KIND = [(r"^CH\d", "tab:green"), (r"^ADC", "tab:purple"), (r"^TXG", "tab:orange"), (r"^CLKB", "tab:cyan"),
        (r"^TXEN", "tab:brown"), (r"^MIC", "tab:pink"), (r"^Power", "tab:red"), (r"^Headers", "tab:olive")]


def colour(name):
    return next((c for p, c in KIND if re.match(p, name)), "0.5")


def draw_pours(ax, board, layer):
    """Filled zone copper of one layer (outlines filled, holes cut out in white)."""
    from matplotlib.path import Path as MPath
    from matplotlib.patches import PathPatch
    for z in board.Zones():
        if z.GetIsRuleArea() or not z.IsOnLayer(layer):
            continue
        polys = z.GetFilledPolysList(layer)
        for i in range(polys.OutlineCount()):
            rings = [polys.Outline(i)] + [polys.Hole(i, h) for h in range(polys.HoleCount(i))]
            verts, codes = [], []
            for r in rings:
                pts = [(pcbnew.ToMM(r.CPoint(k).x), pcbnew.ToMM(r.CPoint(k).y)) for k in range(r.PointCount())]
                if len(pts) < 3:
                    continue
                verts += pts + [pts[0]]
                codes += [MPath.MOVETO] + [MPath.LINETO] * (len(pts) - 1) + [MPath.CLOSEPOLY]
            if verts:
                ax.add_patch(PathPatch(MPath(verts, codes), fc="tab:red" if layer == pcbnew.F_Cu else "tab:blue",
                                       alpha=0.15, lw=0, zorder=1))


def main(out, win=None, board_path=None, pour=None):
    board = pcbnew.LoadBoard(str(board_path or REPO / "kicad" / "aesa.kicad_pcb"))
    bb = board.GetBoardEdgesBoundingBox()
    x0, y0, x1, y1 = win or (pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY()), pcbnew.ToMM(bb.GetRight()),
                             pcbnew.ToMM(bb.GetBottom()))
    scale = 22 / max(x1 - x0, y1 - y0)
    fig, ax = plt.subplots(figsize=((x1 - x0) * scale + 1, (y1 - y0) * scale + 1))
    lw = max(0.3, 60 / max(x1 - x0, y1 - y0))

    def inside(x, y, m=0):
        return x0 - m <= x <= x1 + m and y0 - m <= y <= y1 + m

    if pour:
        draw_pours(ax, board, pcbnew.F_Cu if pour == "F" else pcbnew.B_Cu)
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts:
            s, e = d.GetStart(), d.GetEnd()
            ax.plot([pcbnew.ToMM(s.x), pcbnew.ToMM(e.x)], [pcbnew.ToMM(s.y), pcbnew.ToMM(e.y)], "k", lw=1)
    owner = {}
    for g in board.Groups():
        for i in g.GetItems():
            owner[i.m_Uuid.AsString()] = g.GetName()
    for f in board.GetFootprints():
        x, y = pcbnew.ToMM(f.GetPosition().x), pcbnew.ToMM(f.GetPosition().y)
        if not inside(x, y, 10):
            continue
        g = owner.get(f.m_Uuid.AsString(), "")
        c = f.GetCourtyard(pcbnew.B_CrtYd if f.IsFlipped() else pcbnew.F_CrtYd)
        if c.OutlineCount():
            b = c.BBox()
            ax.add_patch(plt.Rectangle((pcbnew.ToMM(b.GetX()), pcbnew.ToMM(b.GetY())), pcbnew.ToMM(b.GetWidth()),
                                       pcbnew.ToMM(b.GetHeight()), fc=colour(g), alpha=0.18, ec=colour(g), lw=0.4,
                                       ls="-" if f.IsFlipped() else ":"))
        for p in f.Pads():
            px, py = pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)
            pb = p.GetBoundingBox()
            w, h = pcbnew.ToMM(pb.GetWidth()), pcbnew.ToMM(pb.GetHeight())
            ax.add_patch(plt.Rectangle((px - w / 2, py - h / 2), w, h, color="0.35", alpha=0.6, lw=0))
    for t in board.GetTracks():
        (xa, ya), (xb, yb) = [(pcbnew.ToMM(v.x), pcbnew.ToMM(v.y)) for v in (t.GetStart(), t.GetEnd())]
        if not (inside(xa, ya, 1) or inside(xb, yb, 1)):
            continue
        if t.GetClass() == "PCB_VIA":
            ax.add_patch(plt.Circle((xa, ya), pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2, color="k", lw=0, zorder=5))
        else:
            front = t.GetLayer() == pcbnew.F_Cu
            ax.plot([xa, xb], [ya, yb], color="tab:red" if front else "tab:blue",
                    lw=max(0.2, pcbnew.ToMM(t.GetWidth()) * lw * 3), alpha=0.5 if front else 0.8,
                    solid_capstyle="round", zorder=3)
    for g in board.Groups():
        b = g.GetBoundingBox()
        gx, gy = pcbnew.ToMM(b.GetCenter().x), pcbnew.ToMM(b.GetCenter().y)
        if inside(gx, gy):
            ax.text(gx, gy, g.GetName(), fontsize=max(4, lw * 2.2), ha="center", va="center", zorder=9,
                    color=colour(g.GetName()))
    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)
    ax.set_aspect("equal")
    ax.set_title("top view: red F.Cu, blue B.Cu, black vias; courtyards by block (solid back, dotted front)")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(out)


if __name__ == "__main__":
    a = sys.argv[1:]
    opt = {}
    for k in ("--pour", "--board"):
        if k in a:
            i = a.index(k)
            opt[k] = a[i + 1]
            a = a[:i] + a[i + 2:]
    main(a[0] if a else str(REPO / "build" / "board.png"), tuple(map(float, a[1:5])) if len(a) >= 5 else None,
         board_path=opt.get("--board"), pour=opt.get("--pour"))
