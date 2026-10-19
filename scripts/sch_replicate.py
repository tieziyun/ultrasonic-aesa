"""Copy a finished "cell" of a KiCad schematic sheet to new positions (repetitive sheets).

The KiCad MCP server rewrites the whole file for every pin, so large batches on a big
sheet time out and can leave the file empty. For repeated cells (gate drivers, shift
registers, ADC blocks) it is faster and safer to draw one cell with the MCP server and
copy it here: symbols, global/local labels, wires and no-connect flags inside the cell's
box are duplicated with an offset, new UUIDs, new references and renamed nets.

Usage from Python:
    from sch_replicate import replicate
    replicate("kicad/tx_drivers.kicad_sch",
              refs=["U1", "C1", "C2"], box=(20, 40, 90, 85),
              copies=[{"dx": 71.12, "dy": 0, "refs": {"U1": "U2", ...},
                       "nets": {"TX_BIT1": "TX_BIT3", ...}}, ...])
"""

import re
import uuid


def _top_blocks(lines):
    """Yield (start, end, kind) for each top-level item of the sheet."""
    i = 0
    while i < len(lines):
        m = re.match(r"^\t\((\w+)", lines[i])
        if m:
            depth, j = 0, i
            while True:
                depth += lines[j].count("(") - lines[j].count(")")
                if depth == 0:
                    break
                j += 1
            yield i, j, m.group(1)
            i = j
        i += 1


def _shift(text, dx, dy):
    def at(m):
        return f"(at {float(m.group(1)) + dx:.2f} {float(m.group(2)) + dy:.2f}"
    text = re.sub(r"\(at ([\d.\-]+) ([\d.\-]+)", at, text)

    def xy(m):
        return f"(xy {float(m.group(1)) + dx:.2f} {float(m.group(2)) + dy:.2f})"
    return re.sub(r"\(xy ([\d.\-]+) ([\d.\-]+)\)", xy, text)


def _new_uuids(text):
    return re.sub(r'\(uuid "[^"]+"\)', lambda m: f'(uuid "{uuid.uuid4()}")', text)


def _anchor(text):
    m = re.search(r"\(at ([\d.\-]+) ([\d.\-]+)", text) or re.search(r"\(xy ([\d.\-]+) ([\d.\-]+)\)", text)
    return float(m.group(1)), float(m.group(2))


def replicate(path, refs, box, copies):
    lines = open(path, encoding="utf-8").read().split("\n")
    xmin, ymin, xmax, ymax = box
    cell = []
    for s, e, kind in _top_blocks(lines):
        text = "\n".join(lines[s:e + 1])
        if kind == "symbol":
            ref = re.search(r'\(property "Reference" "([^"]+)"', text).group(1)
            if ref in refs:
                cell.append(("symbol", text))
        elif kind in ("global_label", "label", "wire", "no_connect", "junction"):
            x, y = _anchor(text)
            if xmin <= x <= xmax and ymin <= y <= ymax:
                cell.append((kind, text))
    out = []
    for c in copies:
        for kind, text in cell:
            t = _new_uuids(_shift(text, c["dx"], c["dy"]))
            if kind == "symbol":
                old = re.search(r'\(property "Reference" "([^"]+)"', t).group(1)
                new = c["refs"][old]
                t = t.replace(f'(property "Reference" "{old}"', f'(property "Reference" "{new}"')
                t = t.replace(f'(reference "{old}")', f'(reference "{new}")')
            elif kind in ("global_label", "label"):
                name = re.match(r'\t\(\w+ "([^"]+)"', t).group(1)
                if name in c["nets"]:
                    t = t.replace(f'"{name}"', f'"{c["nets"][name]}"', 1)
            out.append(t)
    end = next(k for k in range(len(lines) - 1, -1, -1) if lines[k].startswith("\t(sheet_instances")
               or lines[k] == ")")
    lines[end:end] = out
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    return len(cell), len(out)
