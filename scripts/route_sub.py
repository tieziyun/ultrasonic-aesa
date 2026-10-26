"""Sub-board helper for scripts/route.py, run in its own process (pcbnew's Python bindings get confused when
one process edits two boards).

  route_sub.py make  <src.kicad_pcb> <out.kicad_pcb> <spec.json>
      keep only the footprints in spec["keep"] and the tracks in spec["window"] (x0, y0, x1, y1; optional),
      add the keep-out rectangles spec["keepouts"] ([layers, x0, y0, x1, y1, name]), save
  route_sub.py route <board.kicad_pcb> <workdir> <passes> <out.json>
      Freerouting on the board, then write every track/via as a snapshot list to out.json
"""

import json
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
from route_lib import add_keepout, freeroute, snapshot  # noqa: E402

LAYER = {"F.Cu": pcbnew.F_Cu, "B.Cu": pcbnew.B_Cu}


def make(src, out, spec):
    b = pcbnew.LoadBoard(src)
    keep = set(spec["keep"])
    win = spec.get("window")
    blocked = []                       # parts near the block that stay out of the routing: keep-outs instead
    for f in [f for f in b.GetFootprints() if f.GetReference() not in keep]:
        if win:
            c = f.GetCourtyard(pcbnew.B_CrtYd if f.IsFlipped() else pcbnew.F_CrtYd)
            bb = c.BBox() if c.OutlineCount() else f.GetBoundingBox(False)
            x0, y0, x1, y1 = (pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY()), pcbnew.ToMM(bb.GetRight()),
                              pcbnew.ToMM(bb.GetBottom()))
            if x1 > win[0] and x0 < win[2] and y1 > win[1] and y0 < win[3]:
                tht = any(p.GetDrillSize().x > 0 for p in f.Pads())
                side = ["F.Cu", "B.Cu"] if tht else ["B.Cu" if f.IsFlipped() else "F.Cu"]
                blocked.append([side, x0, y0, x1, y1, "route:part"])
        b.Delete(f)
    for z in [z for z in b.Zones() if z.GetIsRuleArea()]:
        b.Delete(z)
    if spec.get("window"):            # only the copper near the block stays (as obstacles)
        x0, y0, x1, y1 = [pcbnew.FromMM(v) for v in spec["window"]]

        def outside(pt):
            return not (x0 <= pt.x <= x1 and y0 <= pt.y <= y1)
        for tr in [tr for tr in b.GetTracks() if outside(tr.GetStart()) and outside(tr.GetEnd())]:
            b.Delete(tr)
    for layers, x0, y0, x1, y1, name in spec["keepouts"] + blocked:
        add_keepout(b, [LAYER[l] for l in layers], x0, y0, x1, y1, name)
    b.Save(out)
    # the project's net classes and rules, so Freerouting gets the right widths and clearances
    import shutil
    kicad = Path(__file__).resolve().parent.parent / "kicad"
    shutil.copy(kicad / "aesa.kicad_pro", Path(out).with_suffix(".kicad_pro"))
    shutil.copy(kicad / "aesa.kicad_dru", Path(out).with_suffix(".kicad_dru"))
    print(f"sub-board: {len(list(b.GetFootprints()))} footprints")


def route(board, work, passes, out):
    log = freeroute(Path(board), Path(work), int(passes))
    b = pcbnew.LoadBoard(board)
    for tr in b.GetTracks():         # Freerouting necks down to 0.1 mm at fine-pitch pins; JLCPCB needs 0.127
        if tr.GetClass() != "PCB_VIA" and tr.GetWidth() < pcbnew.FromMM(0.127):
            tr.SetWidth(pcbnew.FromMM(0.127))
        elif tr.GetClass() == "PCB_VIA":  # it also mixes padstacks (0.6 mm pad, 0.4 mm drill): keep a 0.15 mm ring
            v = tr.Cast()
            d = v.GetWidth(pcbnew.F_Cu) - pcbnew.FromMM(0.3)
            if v.GetDrillValue() > d:
                v.SetDrill(d)
    b.Save(board)
    snaps = [snapshot(t) for t in b.GetTracks()]
    Path(out).write_text(json.dumps({"items": snaps, "log": log[-3000:]}))
    print(f"routed: {len(snaps)} items")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "make":
        make(sys.argv[2], sys.argv[3], json.loads(Path(sys.argv[4]).read_text()))
    else:
        route(*sys.argv[2:6])
