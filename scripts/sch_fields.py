"""Place Reference/Value fields of a KiCad schematic so they don't overlap (cosmetic only).

KiCad's and the MCP server's auto-placement stack both fields on top of each other for
rotated two-pin parts. This applies simple rules per part type instead. Connectivity is
never touched: only the (at ...) of the Reference and Value properties and their
justification change.

  python scripts/sch_fields.py kicad/channel.kicad_sch [--vertical] [REF=dx,dy,dx2,dy2 ...]

--vertical writes the fields of upright two-pin parts as vertical text beside the body
(reference left, value right), for dense sheets where parts sit about 10 mm apart.

Optional overrides give the Reference offset (dx,dy) and Value offset (dx2,dy2) from the
symbol origin, left-justified, for parts the rules don't suit.
"""

import re
import sys

VERTICAL = False
TWO_PIN = ("R", "C", "C_Polarized", "L", "Polyfuse")


def blocks(lines):
    """Yield (start, end) line indexes of top-level placed symbols."""
    i = 0
    while i < len(lines):
        if lines[i] == "\t(symbol":
            depth, j = 0, i
            while True:
                depth += lines[j].count("(") - lines[j].count(")")
                if depth == 0:
                    break
                j += 1
            yield i, j
            i = j
        i += 1


def body_boxes(text):
    """{lib_id: (xmin, ymin, xmax, ymax)} of each embedded symbol's rectangle (library
    coordinates, y up). Symbols drawn without a rectangle are left out."""
    boxes = {}
    lib = text[text.index("(lib_symbols"):]
    for m in re.finditer(r'\n\t\t\(symbol "([^"]+)"', lib):
        nxt = lib.find('\n\t\t(symbol "', m.end())
        part = lib[m.end(): nxt if nxt > 0 else len(lib)]
        r = re.search(r"\(rectangle\s+\(start ([\d.\-]+) ([\d.\-]+)\)\s+\(end ([\d.\-]+) ([\d.\-]+)\)", part)
        if r:
            a, b, c, d = (float(v) for v in r.groups())
            boxes[m.group(1)] = (min(a, c), min(b, d), max(a, c), max(b, d))
    return boxes


BOXES = {}


def rule(lib_id, rot, unit):
    """Return ((dx, dy, justify[, angle]), ...) for Reference and Value.

    angle, when given, is the field angle relative to the symbol; otherwise text is
    kept horizontal."""
    name = lib_id.split(":")[1]
    if lib_id.startswith("power:"):
        down = name.startswith("GND") or name.startswith("-")
        if rot == 180:
            down = not down
        dy = 5.08 if down else -3.81
        return (None, (0, dy, "center"))
    if name in TWO_PIN:
        if rot in (90, 270):  # horizontal body: ref above, value below
            return ((0, -2.54, "center"), (0, 2.54, "center"))
        if VERTICAL:
            return ((-2.54, 0, "center", 90), (2.54, 0, "center", 90))
        return ((2.54, -1.27, "left"), (2.54, 1.27, "left"))
    if name.startswith("BAV99"):
        if rot == 180:
            return ((0, 5.08, "center"), (0, 7.62, "center"))
        return ((3.81, 3.81, "left"), (3.81, 6.35, "left"))
    if name.startswith("Speaker"):
        return ((5.08, -5.08, "left"), (-5.08, 12.7, "left"))
    if lib_id.startswith("Amplifier_Operational"):
        if unit == 3:  # power unit: text to the left of the pins
            return ((-3.81, -1.27, "right"), (-3.81, 1.27, "right"))
        return ((5.08, -6.35, "left"), (5.08, 7.62, "left"))
    if lib_id in BOXES and rot == 0:  # IC: outside the top-right corner of the body
        xmin, ymin, xmax, ymax = BOXES[lib_id]
        return ((xmax + 1.27, -ymax - 3.81, "left"), (xmax + 1.27, -ymax - 1.27, "left"))
    return ((2.54, -1.27, "left"), (2.54, 1.27, "left"))


def set_field(block, prop, x, y, justify, angle=0):
    """Rewrite the (at ...) and justification of one property inside a symbol block."""
    start = next(k for k, l in enumerate(block) if l.strip().startswith(f'(property "{prop}"'))
    depth, end = 0, start
    while True:
        depth += block[end].count("(") - block[end].count(")")
        if depth == 0:
            break
        end += 1
    for k in range(start, end + 1):
        if block[k].strip().startswith("(at "):
            block[k] = re.sub(r"\(at [^)]*\)", f"(at {x:.2f} {y:.2f} {angle})", block[k])
        if block[k].strip().startswith("(justify"):
            block[k] = ""
    if justify != "center":
        for k in range(start, end + 1):
            if block[k].strip() == "(effects":
                indent = block[k][: len(block[k]) - len(block[k].lstrip())]
                block.insert(k + 1, f"{indent}\t(justify {justify})")
                break


def main(path, overrides):
    text = open(path, encoding="utf-8").read()
    BOXES.update(body_boxes(text))
    lines = text.split("\n")
    out, last = [], 0
    for s, e in blocks(lines):
        block = lines[s:e + 1]
        text = "\n".join(block)
        lib_id = re.search(r'\(lib_id "([^"]+)"\)', text).group(1)
        x, y, rot = (float(v) for v in re.search(r"\n\t\t\(at ([\d.\-]+) ([\d.\-]+) ([\d.\-]+)\)", text).groups())
        unit = int(re.search(r"\(unit (\d+)\)", text).group(1))
        ref = re.search(r'\(property "Reference" "([^"]+)"', text).group(1)
        ref_rule, val_rule = rule(lib_id, int(rot), unit)
        # Field angles are stored relative to the symbol: undo its rotation so text reads horizontally.
        angle = int(rot) % 180
        if ref in overrides:
            a, b, c, d = overrides[ref]
            ref_rule, val_rule = (a, b, "left"), (c, d, "left")
        if ref_rule:
            a = ref_rule[3] if len(ref_rule) > 3 else angle
            set_field(block, "Reference", x + ref_rule[0], y + ref_rule[1], ref_rule[2], a)
        a = val_rule[3] if len(val_rule) > 3 else angle
        set_field(block, "Value", x + val_rule[0], y + val_rule[1], val_rule[2], a)
        out += lines[last:s] + [l for l in block if l != ""]
        last = e + 1
    out += lines[last:]
    open(path, "w", encoding="utf-8").write("\n".join(out))


if __name__ == "__main__":
    ov = {}
    if "--vertical" in sys.argv:
        sys.argv.remove("--vertical")
        VERTICAL = True
    for arg in sys.argv[2:]:
        r, v = arg.split("=")
        ov[r] = tuple(float(t) for t in v.split(","))
    main(sys.argv[1], ov)
