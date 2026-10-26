"""Shared constants and helpers for the design scripts (one place instead of a copy in each).

  from common import KICAD_CLI, KICAD_FOOTPRINTS, KICAD_SYMBOLS, DATE, REV, block_end
"""

import os
from pathlib import Path

KICAD_BIN = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Tiezi\AppData\Local")) / "Programs" / "KiCad" / "10.0"
KICAD_CLI = str(KICAD_BIN / "bin" / "kicad-cli.exe")
KICAD_FOOTPRINTS = KICAD_BIN / "share" / "kicad" / "footprints"
KICAD_SYMBOLS = KICAD_BIN / "share" / "kicad" / "symbols"
KICAD_3DMODELS = KICAD_BIN / "share" / "kicad" / "3dmodels"

# Title-block date and revision of every generated sheet
DATE, REV = "2026-10-24", "1"  # every title block and the board silkscreen


def block_end(lines, start):
    """Index of the line that closes the s-expression opened on lines[start].
    Parentheses inside quoted strings (values, notes) are ignored."""
    depth = 0
    for j in range(start, len(lines)):
        quoted = escaped = False
        for ch in lines[j]:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = not quoted
            elif not quoted:
                depth += (ch == "(") - (ch == ")")
        if depth == 0:
            return j
    raise ValueError(f"unbalanced s-expression from line {start + 1}")


# Which block port serves element n (1..64; column (n-1)//8, row (n-1)%8). Chosen for the board (2026-10-25):
# each column's lanes leave their bundle innermost first without crossing (docs/routing_and_emi.md section 6).
TXG_PORT_OFFSET = {1: 0, 2: 1, 3: 2, 4: 3}     # TX group output DRVi -> row offset within its four rows
# 74HCT595 output driving TX group output DRVi (build_cells.tx_group): DRV1 = U2 OUTB <- INB <- QA, DRV2 = U2 OUTA
# <- INA <- QB, DRV3 = U3 OUTB <- QC, DRV4 = U3 OUTA <- QD. The firmware's bit order: QA..QD = elements in order.
TXG_Q_FOR_PORT = {1: "A", 2: "B", 3: "C", 4: "D"}


def tx_port(n):
    """Element n -> (TX group k, output i): TX group k drives elements 4(k-1)+1 .. 4k in order. Of the two
    groups of a column the odd one (rows 0-3) sits in the lower TX row, the even one (rows 4-7, the rows
    nearest the TX zone) next to the array."""
    c, r = divmod(n - 1, 8)
    k = 2 * c + (2 if r >= 4 else 1)
    i = next(i for i, o in TXG_PORT_OFFSET.items() if o == r % 4)
    return k, i


def tx_element(k, i):
    """TX group k, output i -> element n."""
    return 8 * ((k - 1) // 2) + (0 if k % 2 else 4) + TXG_PORT_OFFSET[i] + 1


def adc_port(n):
    """Element n -> (ADC k, ADC channel j): ADC k takes elements 4(k-1)+1 .. 4k on its sheet ports IN1-IN4 in
    order; inside the adc sheet port INm feeds channel 5-m (the order the lanes reach the inputs on the board),
    so the first element is on channel 4."""
    k = (n - 1) // 4 + 1
    return k, 4 * (k - 1) + 5 - n
