"""List the nets that are not fully connected yet: for each, how many separate copper islands its pads
form (1 = done), with the pads' spread. Uses KiCad's own connectivity (tracks, vias and zone fills).

  python scripts/open_nets.py [board.kicad_pcb]
"""

import sys
from collections import defaultdict
from pathlib import Path

import pcbnew

REPO = Path(__file__).resolve().parent.parent


def islands(board):
    board.BuildConnectivity()
    conn = board.GetConnectivity()
    pads = defaultdict(list)
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetNetname():
                pads[p.GetNetname()].append(p)
    out = {}
    for net, ps in pads.items():
        if len(ps) < 2:
            continue
        seen, groups = set(), []
        for p in ps:
            if p.m_Uuid.AsString() in seen:
                continue
            grp, todo = set(), [p]               # flood fill through directly connected items
            while todo:
                it = todo.pop()
                u = it.m_Uuid.AsString()
                if u in grp:
                    continue
                grp.add(u)
                todo += [i for i in conn.GetConnectedItems(it) if i.m_Uuid.AsString() not in grp]
            seen |= grp
            groups.append([q for q in ps if q.m_Uuid.AsString() in grp])
        if len(groups) > 1:
            out[net] = groups
    return out


def main(path):
    board = pcbnew.LoadBoard(str(path))
    res = islands(board)
    for net, groups in sorted(res.items(), key=lambda kv: -len(kv[1])):
        xs = [pcbnew.ToMM(p.GetPosition().x) for g in groups for p in g]
        ys = [pcbnew.ToMM(p.GetPosition().y) for g in groups for p in g]
        print(f"{net:34s} {len(groups):3d} islands, {sum(map(len, groups)):3d} pads, "
              f"x {min(xs):5.1f}..{max(xs):5.1f} y {min(ys):5.1f}..{max(ys):5.1f}")
    print(f"{len(res)} nets open, {sum(len(g) - 1 for g in res.values())} connections to make; KiCad ratsnest: "
          f"{board.GetConnectivity().GetUnconnectedCount(False)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else REPO / "kicad" / "aesa.kicad_pcb")
