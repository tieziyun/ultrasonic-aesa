"""Layout of the two power sheets: one framed box per section, in power-flow order. Both sheets are A3,
like every other page of the schematic PDF. Shared by build_cells.power() (draws the boxes and the parts
inside them) and place.py (groups the power parts on the board by the box they sit in on the sheet).

  power.kicad_sch (Power 1: input and the supplies for logic and TX)
    A 12 V input/eFuse -> VIN           G ground test points
    B +5V buck                          F +3V3 LDO
    E VDRV boost (TX)                   H 3V3_MIC
  power_analog.kicad_sch (Power 2: the analog supply chain)
    C +-6V5 boost/inverter              N power tree and bring-up notes
    D +-5VA LDOs (receivers)            I +3V3A LDO (ADCs)

Both sheets use sheet id 95 for their references (U9501 ...): the local references are unique across
the two, and the power symbols of Power 2 are numbered from 500.
"""

# key: (sheet, x, y, width, height) in mm; multiples of 2.54 so pins stay on the grid. The A3 title
# block covers x > 300, y > 255.
BOXES = {
    "A": ("power", 17.78, 20.32, 243.84, 78.74), "G": ("power", 266.7, 20.32, 137.16, 78.74),
    "B": ("power", 17.78, 104.14, 228.6, 78.74), "F": ("power", 251.46, 104.14, 152.4, 78.74),
    "E": ("power", 17.78, 187.96, 203.2, 81.28), "H": ("power", 251.46, 187.96, 152.4, 60.96),
    "C": ("power_analog", 17.78, 20.32, 289.56, 109.22), "N": ("power_analog", 312.42, 20.32, 91.44, 109.22),
    "D": ("power_analog", 17.78, 134.62, 160.02, 109.22), "I": ("power_analog", 182.88, 134.62, 142.24, 81.28),
}
SECTIONS = [k for k in BOXES if k != "N"]  # the boxes that hold parts
SHEET_FILES = ("power", "power_analog")


def origin(key):
    """Top-left of a section's drawing area: the heading's baseline."""
    _, x, y, _, _ = BOXES[key]
    return round(x + 2.54, 2), round(y + 5.08, 2)


# Section D holds both analog LDOs: +5VA (U3) above this line, -5VA (U4) below it
D_SPLIT_Y = origin("D")[1] + 66.04


def section_of(sheet, x, y):
    """Box key holding point (x, y) of the given sheet (file stem), or None."""
    for k in SECTIONS:
        s, bx, by, w, h = BOXES[k]
        if s == sheet and bx <= x <= bx + w and by <= y <= by + h:
            return k
    return None
