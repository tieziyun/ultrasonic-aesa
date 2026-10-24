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
