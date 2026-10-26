"""Check every block instance on the board (the gate before the blocks are connected to each other):
KiCad DRC violations inside the instance's area, and nets that live entirely inside one instance but are not
fully connected. Prints one line per block kind and every failing instance; exit code 1 if any fails.

  python scripts/block_check.py [board.kicad_pcb]      (default kicad/aesa.kicad_pcb)

A failing block is fixed in its template (place.py placement, block_routes.py / route.py routing) and the
board rebuilt; individual instances are never patched. Dangling tracks are not counted: a block's ports are
stubs that end at its edge until the blocks are connected.
"""

import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import KICAD_CLI  # noqa: E402
from open_nets import islands  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
KINDS = [(r"^CH\d+$", "channel"), (r"^ADC\d+$", "adc"), (r"^TXG\d+$", "tx_group"), (r"^CLKB\d+$", "clk_buf"),
         (r"^TXEN\d+$", "en_buf"), (r"^MIC\d+$", "mic"), (r"^Power$", "power"), (r"^Headers$", "headers")]
IGNORE = {"track_dangling", "via_dangling", "lib_footprint_issues", "lib_footprint_mismatch", "silk_overlap",
          "silk_over_copper", "silk_edge_clearance", "text_height", "text_thickness"}


def drc(board_path):
    """Run kicad-cli DRC next to the project's rules; returns the report dict."""
    board_path = Path(board_path)
    for ext in (".kicad_pro", ".kicad_dru"):          # the project's current rules beside a copy of the board
        side = board_path.with_suffix(ext)
        src = REPO / "kicad" / f"aesa{ext}"
        if side.resolve() != src.resolve():
            shutil.copy(src, side)
    out = REPO / "build" / f"drc_{board_path.stem}.json"
    subprocess.run([str(KICAD_CLI), "pcb", "drc", "--format", "json", "--output", str(out), str(board_path)],
                   capture_output=True, text=True)
    return json.loads(out.read_text(encoding="utf-8"))


ONE_OFF = {"power", "headers"}     # spread over the board: judged by the area of each of their parts


def areas(board):
    """Block instance name -> (kind, [boxes]): the courtyard bounding box of a repeated block's parts, or for a
    one-off block (spread over the board) each part's courtyard grown by 1 mm."""
    out = {}
    for g in board.Groups():
        name = g.GetName()
        kind = next((k for p, k in KINDS if re.match(p, name)), None)
        if kind is None:
            continue
        xs0, ys0, xs1, ys1 = [], [], [], []
        for it in g.GetItems():
            if it.GetClass() != "FOOTPRINT":
                continue
            f = it.Cast()
            c = f.GetCourtyard(pcbnew.B_CrtYd if f.IsFlipped() else pcbnew.F_CrtYd)
            bb = c.BBox() if c.OutlineCount() else f.GetBoundingBox(False)
            xs0.append(pcbnew.ToMM(bb.GetX()))
            ys0.append(pcbnew.ToMM(bb.GetY()))
            xs1.append(pcbnew.ToMM(bb.GetRight()))
            ys1.append(pcbnew.ToMM(bb.GetBottom()))
        if xs0:
            out[name] = (kind, [(a - 1, b - 1, c + 1, d + 1) for a, b, c, d in zip(xs0, ys0, xs1, ys1)]
                         if kind in ONE_OFF else [(min(xs0), min(ys0), max(xs1), max(ys1))])
    return out


def owner(board):
    """Footprint reference -> block instance name."""
    out = {}
    for g in board.Groups():
        for it in g.GetItems():
            if it.GetClass() == "FOOTPRINT":
                out[it.Cast().GetReference()] = g.GetName()
    return out


def main(path):
    import place
    place.netclasses()              # the project's rules from place.py (a KiCad window can save stale ones)
    board = pcbnew.LoadBoard(str(path))
    inst = areas(board)
    fails = defaultdict(list)
    report = drc(path)
    for v in report.get("violations", []):
        if v["type"] in IGNORE:
            continue
        pts = [(i["pos"]["x"], i["pos"]["y"]) for i in v["items"] if "pos" in i]
        hit = {n for n, (_, boxes) in inst.items() for x, y in pts for x0, y0, x1, y1 in boxes
               if x0 <= x <= x1 and y0 <= y <= y1}
        for n in hit:
            fails[n].append(f"{v['type']} at " + " ".join(f"({x:.2f},{y:.2f})" for x, y in pts))
    own = owner(board)
    from route import internal_nets         # the power group counts each of its sections as a block
    inside = set(internal_nets(board, {g.GetName() for g in board.Groups()}))
    for net, groups in islands(board).items():
        if net not in inside:
            continue
        refs = {p.GetParentFootprint().GetReference() for g in groups for p in g}
        homes = {own.get(r) for r in refs}
        if len(homes) == 1 and None not in homes:
            fails[homes.pop()].append(f"internal net {net} in {len(groups)} pieces")
    by_kind = defaultdict(list)
    for n, (k, _) in inst.items():
        by_kind[k].append(n)
    bad = 0
    for _, k in KINDS:
        names = sorted(by_kind.get(k, []))
        if not names:
            continue
        f = [n for n in names if fails.get(n)]
        bad += len(f)
        print(f"{k:9s} {len(names):3d} instances: " + ("all pass" if not f else f"{len(f)} FAIL"))
        for n in f:
            print(f"    {n}: " + "; ".join(fails[n][:6]) + (f" (+{len(fails[n]) - 6} more)" if len(fails[n]) > 6 else ""))
    print(f"{bad} failing instance(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else REPO / "kicad" / "aesa.kicad_pcb"))
