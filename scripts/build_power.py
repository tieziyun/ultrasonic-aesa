"""Build kicad/power.kicad_sch from scratch: a wired, testable power sheet.

Uses the KiCad MCP server's own Python command handlers in-process (same code as the
MCP tools, without the 30 s client timeout that truncated sheets). Every regulator block
is drawn with real wires; rails and grounds use power symbols (net names VIN, +5V,
+6V5, -6V5, +5VA, -5VA, +3V3, VDRV, GND). Only a few feedback/control
nets use local labels.

Testability (CLAUDE.md fixed requirement): each regulator output passes a bridged
solder jumper (cut to isolate the rail), has a test point and, except the analog LDO
outputs, an LED. VDRV passes a 2-pin header: remove the shunt to remove TX power.

Run, then re-run scripts/gen_top.py (references) and scripts/sch_lcsc.py:
  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe scripts\\build_power.py
"""

import logging
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCH = REPO / "kicad" / "power.kicad_sch"
MCP_PY = r"C:\GitHub\tools\KiCAD-MCP-Server\python"

sys.path.insert(0, MCP_PY)
_cwd = os.getcwd()
os.chdir(MCP_PY)
logging.disable(logging.WARNING)
from kicad_interface import KiCADInterface  # noqa: E402
os.chdir(_cwd)

K = KiCADInterface()


def mcp(cmd, **params):
    r = K.handle_command(cmd, params)
    if isinstance(r, dict) and r.get("success") is False:
        raise RuntimeError(f"{cmd}: {r}")
    return r


# ---- footprints -------------------------------------------------------------------
R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"
C1210 = "Capacitor_SMD:C_1210_3225Metric"
LED = "LED_SMD:LED_0603_1608Metric"
TPFP = "TestPoint:TestPoint_Pad_D1.5mm"
JPFP = "Jumper:SolderJumper-2_P1.3mm_Bridged_RoundedPad1.0x1.5mm"
IND = "Inductor_SMD:L_Bourns_SRN6045TA"
SMA = "Diode_SMD:D_SMA"

# One ground net (review-full-1 item 1): every former AGND/PGND/DGND reference is GND.
POWER_SYM = {"GND": "power:GND", "PGND": "power:GND", "AGND": "power:GND", "DGND": "power:GND",
             "VIN": "power:+12V", "+5V": "power:+5V", "+6V5": "power:+6V", "-6V5": "power:-6V",
             "+5VA": "power:+5VA", "-5VA": "power:-5VA", "+3V3": "power:+3V3", "VDRV": "power:+15V"}
GROUNDS = ("PGND", "AGND", "DGND")

parts = []      # components for the first batch
syms = []       # power symbols, flags (second batch, positions known in advance)
wires = []      # list of point lists
labels = []     # (net, x, y, angle) local labels on wire ends / pins
pin_labels = []  # (net, ref, pin) local labels snapped to pins
ncs = []        # (ref, pin) no-connects
texts = []      # (text, x, y, size)
_n = {"pwr": 0, "flg": 0}


def part(sym, ref, x, y, value, fp="", rot=0):
    parts.append({"symbol": sym, "reference": ref, "value": value, "footprint": fp,
                  "position": {"x": x, "y": y}, "rotation": rot})


def psym(net, x, y, rot=0):
    net = "GND" if net in GROUNDS else net
    _n["pwr"] += 1
    syms.append({"symbol": POWER_SYM[net], "reference": f"#PWR{_n['pwr']:02d}", "value": net,
                 "position": {"x": x, "y": y}, "rotation": rot})


def flag(x, y):
    _n["flg"] += 1
    syms.append({"symbol": "power:PWR_FLAG", "reference": f"#FLG{_n['flg']:02d}", "value": "PWR_FLAG",
                 "position": {"x": x, "y": y}})


def w(*pts):
    wires.append([list(p) for p in pts])


def rail(y, xs):
    xs = sorted(xs)
    for a, b in zip(xs, xs[1:]):
        w((a, y), (b, y))


def drop(sym, ref, x, ytop, value, fp, gnd=None, rot=0):
    """Upright two-pin part whose top pin sits at (x, ytop); bottom pin to ground symbol."""
    part(sym, ref, x, ytop + 3.81, value, fp, rot)
    if gnd:
        psym(gnd, x, ytop + 7.62)


def led_drop(rref, lref, x, ytop, rval, gnd, negative=False):
    """Rail indicator: resistor then LED to ground (LED reversed on a negative rail)."""
    drop("Device:R", rref, x, ytop, rval, R0603)
    part("Device:LED", lref, x, ytop + 11.43, "green", LED, 270 if negative else 90)
    psym(gnd, x, ytop + 15.24)


def tp(ref, x, y, net):
    part("Connector:TestPoint", ref, x, y, f"TP {net}", TPFP)


def text(s, x, y, size=2.0):
    texts.append((s, x, y, size))


# =================================================================================
# A. 12 V input: barrel jack -> polyfuse -> reverse-polarity Schottky -> VIN
# =================================================================================
text("12 V INPUT (jack, polyfuse, SS54)", 20.32, 30.48)
part("Connector:Barrel_Jack", "J1", 22.86, 48.26, "12 V in", "Connector_BarrelJack:BarrelJack_CUI_PJ-063AH_Horizontal")
part("Device:Polyfuse", "F1", 40.64, 45.72, "2A hold 1812", "Fuse:Fuse_1812_4532Metric", 90)
part("Device:D_Schottky", "D1", 55.88, 45.72, "SS54", "Diode_SMD:D_SMC", 180)
w((30.48, 45.72), (36.83, 45.72))
w((44.45, 45.72), (52.07, 45.72))
w((30.48, 50.8), (33.02, 50.8), (33.02, 53.34))
psym("PGND", 33.02, 53.34)
rail(45.72, [59.69, 66.04, 73.66, 81.28, 91.44, 99.06, 106.68])
drop("Device:C_Polarized", "C1", 66.04, 45.72, "100u 25V", "Capacitor_SMD:CP_Elec_8x10", "PGND")
drop("Device:C", "C2", 73.66, 45.72, "100n 50V X7R", C0603, "PGND")
led_drop("R1", "D2", 81.28, 45.72, "10k 1%", "PGND")
tp("TP1", 91.44, 45.72, "VIN")
flag(99.06, 45.72)
psym("VIN", 106.68, 45.72)

# =================================================================================
# B. +5V digital: TPS54202 buck (TI SLVSD26 Figure 7-1 reference design)
# =================================================================================
text("+5V  TPS54202 buck (TI ref. design)", 127, 30.48)
U1x, U1y = 175.26, 60.96          # VIN(3) 165.10,58.42  EN(5) 165.10,63.50  GND(1) 175.26,68.58
part("Regulator_Switching:TPS54202DDC", "U1", U1x, U1y, "TPS54202DDCR", "Package_TO_SOT_SMD:SOT-23-6")
psym("VIN", 134.62, 45.72)
rail(45.72, [134.62, 139.7, 147.32, 154.94, 160.02])
drop("Device:C", "C3", 139.7, 45.72, "10u 25V X5R", C1206, "PGND")
drop("Device:C", "C4", 147.32, 45.72, "100n 50V X7R", C0603, "PGND")
drop("Device:R", "R2", 154.94, 45.72, "511k 1%", R0603)          # EN UVLO divider top
part("Device:R", "R3", 154.94, 69.85, "105k 1%", R0603)            # bottom: 66.04..73.66
psym("PGND", 154.94, 73.66)
w((154.94, 53.34), (154.94, 63.5))
w((154.94, 66.04), (154.94, 63.5))
w((154.94, 63.5), (165.1, 63.5))
w((160.02, 45.72), (160.02, 58.42), (165.1, 58.42))
psym("PGND", 175.26, 68.58)
# BOOT cap from BOOT(6) 185.42,58.42 to the SW node; SW(2) 185.42,60.96
part("Device:C", "C5", 195.58, 54.61, "100n 16V X7R", C0603)      # pins 50.80 / 58.42
w((185.42, 58.42), (187.96, 58.42), (187.96, 50.8), (195.58, 50.8))
w((185.42, 60.96), (195.58, 60.96))
w((195.58, 58.42), (195.58, 60.96))
part("Device:L", "L1", 203.2, 60.96, "15u 3A", IND, 90)              # 199.39 / 207.01
w((195.58, 60.96), (199.39, 60.96))
rail(60.96, [207.01, 212.09, 219.71, 227.33, 232.41, 246.38])
drop("Device:C", "C6", 212.09, 60.96, "22u 16V X5R", C1206, "PGND")
drop("Device:C", "C7", 219.71, 60.96, "22u 16V X5R", C1206, "PGND")
drop("Device:R", "R4", 227.33, 60.96, "100k 1%", R0603)            # FB divider top
drop("Device:C", "C8", 232.41, 60.96, "75p 50V C0G", C0603)        # feed-forward across R4
w((227.33, 68.58), (232.41, 68.58))
drop("Device:R", "R5", 227.33, 68.58, "13.3k 1%", R0603, "PGND")
labels.append(("FB_5V", 227.33, 68.58, 180))
pin_labels.append(("FB_5V", "U1", "4"))
labels.append(("5V_REG", 240.03, 60.96, 0))
part("Jumper:SolderJumper_2_Bridged", "JP1", 251.46, 60.96, "cut to isolate +5V", JPFP)
rail(60.96, [256.54, 261.62, 271.78, 276.86, 281.94])
led_drop("R6", "D3", 261.62, 60.96, "2.2k 1%", "PGND")
tp("TP2", 271.78, 60.96, "+5V")
flag(276.86, 60.96)
psym("+5V", 281.94, 60.96)

# =================================================================================
# C. +-6.5 V pre-regulator: TPS65131 (boost + inverting), from +5V
# =================================================================================
text("+/-6.5 V PRE-REGULATOR  TPS65131 (feeds the LDOs)", 20.32, 111.76)
U2x, U2y = 96.52, 154.94
part("Regulator_Switching:TPS65130RGE", "U2", U2x, U2y, "TPS65131RGER",
     "Package_DFN_QFN:VQFN-24-1EP_4x4mm_P0.5mm_EP2.45x2.45mm_ThermalVias")
psym("+5V", 30.48, 124.46)
rail(124.46, [30.48, 35.56, 43.18, 50.8, 60.96, 66.04])
drop("Device:C", "C11", 35.56, 124.46, "4.7u 16V X7R", C0805, "PGND")
drop("Device:C", "C12", 43.18, 124.46, "4.7u 16V X7R", C0805, "PGND")
drop("Device:R", "R7", 50.8, 124.46, "100 1%", R0603)              # VIN control-supply filter
w((50.8, 132.08), (50.8, 152.4))
rail(152.4, [50.8, 55.88, 86.36])
drop("Device:C", "C13", 55.88, 152.4, "100n 16V X7R", C0603, "PGND")
flag(55.88, 152.4)                                                 # VIN pin is fed through R7
w((60.96, 124.46), (60.96, 149.86), (86.36, 149.86))            # INN supply
part("Device:L", "L2", 73.66, 139.7, "4.7u 2.5A", IND, 90)          # boost inductor 69.85 / 77.47
w((66.04, 124.46), (66.04, 139.7), (69.85, 139.7))
w((77.47, 139.7), (81.28, 139.7))
w((81.28, 139.7), (86.36, 139.7))
w((81.28, 139.7), (81.28, 132.08), (92.71, 132.08))
part("Device:D_Schottky", "D4", 96.52, 132.08, "SS14", SMA, 180)    # A 92.71, K 100.33
rail(132.08, [100.33, 109.22, 121.92, 127.0, 134.62, 142.24, 149.86, 160.02, 165.1, 172.72])
w((106.68, 139.7), (109.22, 139.7), (109.22, 132.08))            # VPOS sense
drop("Device:R", "R8", 121.92, 132.08, "562k 1%", R0603)           # FBP divider top
drop("Device:C", "C14", 127.0, 132.08, "6.8p 50V C0G", C0603)
w((121.92, 139.7), (127.0, 139.7))
drop("Device:R", "R9", 121.92, 139.7, "130k 1%", R0603, "PGND")
labels.append(("FBP", 121.92, 139.7, 180))
pin_labels.append(("FBP", "U2", "22"))
drop("Device:C", "C15", 134.62, 132.08, "10u 25V X7R", C1206, "PGND")
drop("Device:C", "C16", 142.24, 132.08, "10u 25V X7R", C1206, "PGND")
led_drop("R10", "D5", 149.86, 132.08, "3.3k 1%", "PGND")
tp("TP3", 160.02, 132.08, "+6V5")
flag(165.1, 132.08)
psym("+6V5", 172.72, 132.08)
# inverting side: OUTN(13/14) 106.68,160.02 -> L3 to ground; D5 K at OUTN, A to -6V5
w((106.68, 160.02), (114.3, 160.02))
drop("Device:L", "L3", 114.3, 160.02, "4.7u 2.5A", IND, "PGND")
w((114.3, 160.02), (120.65, 160.02))
part("Device:D_Schottky", "D6", 124.46, 160.02, "SS14", SMA, 0)     # K 120.65, A 128.27
rail(160.02, [128.27, 132.08, 139.7, 144.78, 152.4, 160.02, 167.64, 172.72, 176.53, 180.34])
w((106.68, 154.94), (132.08, 154.94), (132.08, 160.02))          # VNEG sense
drop("Device:R", "R11", 139.7, 160.02, "649k 1%", R0603)           # FBN divider (VNEG -> FBN)
drop("Device:C", "C17", 144.78, 160.02, "7.5p 50V C0G", C0603)
w((139.7, 167.64), (144.78, 167.64))
drop("Device:R", "R12", 139.7, 167.64, "121k 1%", R0603)           # FBN -> VREF
labels.append(("FBN", 139.7, 167.64, 180))
pin_labels.append(("FBN", "U2", "16"))
labels.append(("VREF65", 139.7, 175.26, 180))
pin_labels.append(("VREF65", "U2", "17"))
drop("Device:C", "C18", 50.8, 177.8, "220n 16V X7R", C0603, "PGND")   # VREF cap
labels.append(("VREF65", 50.8, 177.8, 0))
drop("Device:C", "C19", 58.42, 177.8, "10n 16V X7R", C0603, "PGND")   # CP compensation
labels.append(("CP65", 58.42, 177.8, 0))
pin_labels.append(("CP65", "U2", "21"))
drop("Device:C", "C20", 66.04, 177.8, "4.7n 50V C0G", C0603, "PGND")  # CN compensation
labels.append(("CN65", 66.04, 177.8, 0))
pin_labels.append(("CN65", "U2", "18"))
drop("Device:C", "C21", 152.4, 160.02, "10u 25V X7R", C1206, "PGND")
drop("Device:C", "C22", 160.02, 160.02, "10u 25V X7R", C1206, "PGND")
led_drop("R13", "D7", 167.64, 160.02, "3.3k 1%", "PGND", negative=True)
tp("TP4", 172.72, 160.02, "-6V5")
flag(176.53, 160.02)
psym("-6V5", 180.34, 160.02)
pin_labels += [("+5V", "U2", "8"), ("+5V", "U2", "10")]          # ENP, ENN: always on
pin_labels += [("PGND", "U2", "9"), ("PGND", "U2", "11")]        # PSP, PSN low: fixed-frequency PWM
# PGND(2/3), AGND(19) and the exposed pad all point down: ground symbols on the pins.
psym("PGND", 99.06, 175.26)
psym("PGND", 93.98, 175.26)
psym("PGND", 96.52, 175.26)
ncs += [("U2", "7"), ("U2", "12"), ("U2", "20")]

# =================================================================================
# D. Low-noise analog LDOs: TPS7A4701 (+5VA) and TPS7A3301 (-5VA)
# =================================================================================
text("ANALOG RAILS  TPS7A4701 +5VA, TPS7A3301 -5VA", 215.9, 111.76)
part("Regulator_Linear:TPS7A4701xRGW", "U3", 284.48, 139.7, "TPS7A4701RGWR",
     "Package_DFN_QFN:Texas_RGW0020A_VQFN-20-1EP_5x5mm_P0.65mm_EP3.15x3.15mm_ThermalVias")
psym("+6V5", 238.76, 124.46)
rail(124.46, [238.76, 246.38, 266.7, 274.32])
drop("Device:C", "C23", 246.38, 124.46, "10u 25V X7R", C1206, "AGND")
w((266.7, 124.46), (266.7, 127.0), (274.32, 127.0))              # EN to IN
for pin_, y_ in (("10", 137.16), ("6", 144.78)):                   # 0.4 V + 3.2 V to GND
    w((274.32, y_), (271.78, y_), (271.78, y_ + 2.54))
    psym("AGND", 271.78, y_ + 2.54)
ncs += [("U3", n) for n in ("4", "5", "8", "9", "11", "12")]
psym("AGND", 287.02, 157.48)
psym("AGND", 281.94, 157.48)
rail(124.46, [294.64, 297.18, 302.26, 309.88, 314.96])
w((294.64, 129.54), (297.18, 129.54), (297.18, 124.46))          # SENSE to OUT
drop("Device:C", "C24", 302.26, 124.46, "10u 16V X7R", C1206, "AGND")
drop("Device:C", "C25", 309.88, 124.46, "10u 16V X7R", C1206, "AGND")
w((294.64, 149.86), (299.72, 149.86))
drop("Device:C", "C26", 299.72, 149.86, "1u 16V X7R", C0603, "AGND")   # NR
labels.append(("5VA_REG", 312.42, 124.46, 0))
part("Jumper:SolderJumper_2_Bridged", "JP2", 320.04, 124.46, "cut to isolate +5VA", JPFP)
rail(124.46, [325.12, 330.2, 335.28, 340.36])
tp("TP5", 330.2, 124.46, "+5VA")
flag(335.28, 124.46)
psym("+5VA", 340.36, 124.46)

part("Regulator_Linear:TPS7A3301RGW", "U4", 284.48, 185.42, "TPS7A3301RGWR",
     "Package_DFN_QFN:Texas_RGW0020A_VQFN-20-1EP_5x5mm_P0.65mm_EP3.15x3.15mm_ThermalVias")
psym("-6V5", 238.76, 180.34)
rail(180.34, [238.76, 246.38, 266.7, 274.32])
drop("Device:C", "C27", 246.38, 180.34, "10u 25V X7R", C1206, "AGND")
w((266.7, 180.34), (266.7, 182.88), (274.32, 182.88))            # EN to IN
w((274.32, 187.96), (259.08, 187.96))
drop("Device:C", "C28", 259.08, 187.96, "1u 16V X7R", C0603, "AGND")   # NR/SS
psym("AGND", 287.02, 195.58)
psym("AGND", 281.94, 195.58)
rail(180.34, [294.64, 302.26, 307.34, 314.96, 320.04])
drop("Device:R", "R14", 302.26, 180.34, "715k 1%", R0603)          # OUT -> FB
drop("Device:C", "C29", 307.34, 180.34, "10n 50V C0G", C0603)      # CFF
w((302.26, 187.96), (307.34, 187.96))
w((302.26, 187.96), (294.64, 187.96))
drop("Device:R", "R15", 302.26, 187.96, "220k 1%", R0603, "AGND")
drop("Device:C", "C30", 314.96, 180.34, "22u 16V X7R", C1210, "AGND")
labels.append(("-5VA_REG", 317.5, 180.34, 0))
part("Jumper:SolderJumper_2_Bridged", "JP3", 325.12, 180.34, "cut to isolate -5VA", JPFP)
rail(180.34, [330.2, 335.28, 340.36, 345.44])
tp("TP6", 335.28, 180.34, "-5VA")
flag(340.36, 180.34)
psym("-5VA", 345.44, 180.34)

# =================================================================================
# E. VDRV 15 V: LM2731X boost from VIN; header J2 in series = TX power on/off
# =================================================================================
text("VDRV 15 V  LM2731X boost; J2 shunt = TX power", 20.32, 213.36)
part("Regulator_Switching:LM2731XMF", "U5", 76.2, 246.38, "LM2731XMF", "Package_TO_SOT_SMD:SOT-23-5")
psym("VIN", 25.4, 228.6)
rail(228.6, [25.4, 33.02, 48.26, 58.42, 88.9])
drop("Device:C", "C31", 33.02, 228.6, "10u 25V X5R", C1206, "PGND")
drop("Device:R", "R16", 48.26, 228.6, "51k 1%", R0603)            # SHDN pull-up (enabled)
w((48.26, 236.22), (48.26, 248.92), (68.58, 248.92))
w((58.42, 228.6), (58.42, 243.84), (68.58, 243.84))
psym("PGND", 76.2, 254.0)
part("Device:L", "L4", 92.71, 228.6, "10u 1.5A", IND, 90)          # 88.90 / 96.52
w((96.52, 228.6), (99.06, 228.6), (99.06, 236.22))
w((99.06, 236.22), (99.06, 243.84), (83.82, 243.84))            # SW
w((99.06, 236.22), (105.41, 236.22))
part("Device:D_Schottky", "D8", 109.22, 236.22, "SS14", SMA, 180)   # A 105.41, K 113.03
rail(236.22, [113.03, 120.65, 125.73, 133.35, 140.97, 152.4])
drop("Device:R", "R17", 120.65, 236.22, "150k 1%", R0603)
drop("Device:C", "C32", 125.73, 236.22, "180p 50V C0G", C0603)
w((120.65, 243.84), (125.73, 243.84))
drop("Device:R", "R18", 120.65, 243.84, "13.3k 1%", R0603, "PGND")
labels.append(("FB_VDRV", 120.65, 243.84, 180))
pin_labels.append(("FB_VDRV", "U5", "3"))
drop("Device:C", "C33", 133.35, 236.22, "10u 50V X7R", C1210, "PGND")
drop("Device:C", "C34", 140.97, 236.22, "10u 50V X7R", C1210, "PGND")
labels.append(("VDRV_REG", 146.05, 236.22, 0))
part("Connector_Generic:Conn_01x02", "J2", 157.48, 236.22, "TX power (shunt)",
     "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
w((152.4, 238.76), (149.86, 238.76), (149.86, 251.46))
rail(251.46, [149.86, 160.02, 167.64, 175.26, 180.34, 185.42])
drop("Device:C_Polarized", "C35", 160.02, 251.46, "47u 35V", "Capacitor_SMD:CP_Elec_6.3x7.7", "PGND")
led_drop("R19", "D9", 167.64, 251.46, "15k 1%", "PGND")
tp("TP7", 175.26, 251.46, "VDRV")
flag(180.34, 251.46)
psym("VDRV", 185.42, 251.46)

# =================================================================================
# F. +3V3: TLV1117-33 from +5V
# =================================================================================
text("+3V3  TLV1117-33", 193.04, 213.36)
part("Regulator_Linear:TLV1117-33", "U6", 215.9, 238.76, "TLV1117-33", "Package_TO_SOT_SMD:SOT-223-3_TabPin2")
psym("+5V", 193.04, 238.76)
rail(238.76, [193.04, 200.66, 208.28])
drop("Device:C", "C36", 200.66, 238.76, "10u 16V X5R", C0805, "PGND")
psym("PGND", 215.9, 246.38)
rail(238.76, [223.52, 228.6, 238.76])
drop("Device:C", "C37", 228.6, 238.76, "10u 16V X5R", C0805, "PGND")
labels.append(("3V3_REG", 233.68, 238.76, 0))
part("Jumper:SolderJumper_2_Bridged", "JP4", 243.84, 238.76, "cut to isolate +3V3", JPFP)
rail(238.76, [248.92, 254.0, 261.62, 266.7, 271.78])
led_drop("R20", "D10", 254.0, 238.76, "1k 1%", "PGND")
tp("TP8", 261.62, 238.76, "+3V3")
flag(266.7, 238.76)
psym("+3V3", 271.78, 238.76)

# =================================================================================
# G. Ground: one plane (no net-ties); test points and flag
# =================================================================================
text("GROUND  single plane, test points", 297.18, 30.48)
for k, x_ in enumerate((360.68, 373.38)):
    tp(f"TP{9 + k}", x_, 50.8, "GND")
    psym("GND", x_, 50.8)
flag(386.08, 50.8)
psym("GND", 386.08, 50.8)


def build():
    if SCH.exists():
        shutil.copy(SCH, SCH.with_suffix(".kicad_sch.bak"))
        SCH.unlink()
    mcp("create_schematic", name="power", path=str(SCH.parent))
    t = SCH.read_text(encoding="utf-8").replace('(paper "A4")', '(paper "A3")')
    SCH.write_text(t, encoding="utf-8")
    mcp("batch_add_components", schematicPath=str(SCH), components=parts, auto_position_fields=True)
    mcp("batch_add_components", schematicPath=str(SCH), components=syms, auto_position_fields=False)
    floating = []
    for pts in wires:
        r = mcp("add_schematic_wire", schematicPath=str(SCH), waypoints=pts)
        for side in ("start", "end"):
            e = r.get("endpoints", {}).get(side, {})
            if not e.get("snapped") and e.get("nearestPinDistance", 0) and e["nearestPinDistance"] < 1.0:
                floating.append((pts, side, e))
    for net, x, y, a in labels:
        mcp("add_schematic_net_label", schematicPath=str(SCH), netName=net, position=[x, y], orientation=a)
    for net, ref, pn in pin_labels:
        net = "GND" if net in GROUNDS else net
        lt = "global_label" if net in POWER_SYM else "label"
        mcp("add_schematic_net_label", schematicPath=str(SCH), netName=net, componentRef=ref, pinNumber=pn,
            labelType=lt)
    mcp("batch_add_no_connects", schematicPath=str(SCH),
        pins=[{"componentRef": r, "pinName": p} for r, p in ncs])
    for s, x, y, size in texts:
        mcp("add_schematic_text", schematicPath=str(SCH), text=s, position=[x, y], fontSize=size, bold=True)
    print(f"{len(parts)} parts, {len(syms)} power symbols/flags, {len(wires)} wires; "
          f"near-miss wire ends: {len(floating)}")
    for f in floating:
        print("  near miss:", f)


if __name__ == "__main__":
    build()
