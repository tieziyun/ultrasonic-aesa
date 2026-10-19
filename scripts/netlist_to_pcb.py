"""Build a KiCad board from a SKiDL/KiCad netlist (replaces kinet2pcb, which can't find
a per-user KiCad 10 install).

Loads every footprint, copies reference, value and fields (LCSC etc.), assigns pad nets
and lays the parts out on a simple grid inside a board outline, ready for scripts/place.py
or manual placement. Run with KiCad's Python:

  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe scripts\\netlist_to_pcb.py ^
      build\\hybrid_test.net kicad\\aesa_test.kicad_pcb
"""

import os
import sys
from pathlib import Path

import pcbnew

KICAD_FP_DIR = Path(r"C:\Users\Tiezi\AppData\Local\Programs\KiCad\10.0\share\kicad\footprints")
PROJECT_FP_DIR = Path(__file__).resolve().parent.parent / "kicad"  # holds aesa.pretty


def parse_sexp(text):
    """Minimal s-expression parser: lists of strings."""
    stack, cur, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "(":
            stack.append(cur)
            cur = []
            i += 1
        elif c == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
            i += 1
        elif c == '"':
            j, buf = i + 1, []
            while text[j] != '"':
                if text[j] == "\\":
                    j += 1
                buf.append(text[j])
                j += 1
            cur.append("".join(buf))
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            cur.append(text[i:j])
            i = j
    return cur[0]


def children(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def child_value(node, key, default=""):
    found = children(node, key)
    return found[0][1] if found and len(found[0]) > 1 else default


def fp_library_path(lib):
    for base in (PROJECT_FP_DIR, KICAD_FP_DIR):
        p = base / f"{lib}.pretty"
        if p.is_dir():
            return str(p)
    raise FileNotFoundError(f"footprint library {lib}.pretty not found")


def main(net_file, pcb_file):
    tree = parse_sexp(Path(net_file).read_text(encoding="utf-8"))
    comps = children(children(tree, "components")[0], "comp")
    nets = children(children(tree, "nets")[0], "net")

    board = pcbnew.BOARD()
    pad_net = {}
    for net in nets:
        name = child_value(net, "name")
        if name.startswith("__NOCONNECT"):
            continue
        item = pcbnew.NETINFO_ITEM(board, name)
        board.Add(item)
        for node in children(net, "node"):
            pad_net[(child_value(node, "ref"), child_value(node, "pin"))] = item

    pitch = pcbnew.FromMM(10)
    cols = max(1, int(len(comps) ** 0.5) + 1)
    missing = []
    for k, comp in enumerate(comps):
        ref = child_value(comp, "ref")
        lib, _, name = child_value(comp, "footprint").partition(":")
        try:
            fp = pcbnew.FootprintLoad(fp_library_path(lib), name)
        except FileNotFoundError:
            fp = None
        if fp is None:
            missing.append(f"{ref} ({lib}:{name})")
            continue
        fp.SetReference(ref)
        fp.SetValue(child_value(comp, "value"))
        for field in children(children(comp, "fields")[0], "field") if children(comp, "fields") else []:
            fname = child_value(field, "name")
            if fname and len(field) > 2:
                fp.SetField(fname, field[2])
                f = fp.GetField(fname)  # new fields default to visible silkscreen text
                f.SetVisible(False)
                f.SetLayer(pcbnew.F_Fab)
        fp.SetPosition(pcbnew.VECTOR2I(pitch * (k % cols + 1), pitch * (k // cols + 1)))
        board.Add(fp)
        for pad in fp.Pads():
            item = pad_net.get((ref, pad.GetNumber()))
            if item:
                pad.SetNet(item)

    # Board outline around the grid.
    rows = (len(comps) + cols - 1) // cols
    w, h = pitch * (cols + 1), pitch * (rows + 1)
    pts = [(0, 0), (w, 0), (w, h), (0, h)]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(pcbnew.VECTOR2I(x0, y0))
        seg.SetEnd(pcbnew.VECTOR2I(x1, y1))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(pcbnew.FromMM(0.1))
        board.Add(seg)

    os.makedirs(os.path.dirname(os.path.abspath(pcb_file)), exist_ok=True)
    board.Save(pcb_file)
    print(f"{len(comps) - len(missing)} footprints, {len(nets)} nets -> {pcb_file}")
    if missing:
        print("MISSING footprints:", ", ".join(missing))
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
