"""Test build of the hybrid panel: one transceiver channel plus all 8 MEMS mics.

Run with KiCad's Python from the repo root:
  C:\\Users\\Tiezi\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\python.exe skidl\\hybrid_test.py
It runs ERC, writes build/hybrid_test.net and draws build/svg/hybrid_test.svg
(SKiDL makes a netlist and an SVG drawing, not a KiCad .kicad_sch file).
"""

import builtins
from pathlib import Path

from channel import (
    C, gate_driver, lcsc, mems_mic_pair, clock_buffer, mic_supply_filter, transducer,
    tx_control, tx_rx_channel, tx_shift_register,
)
from skidl import ERC, Net, Part, generate_netlist, generate_svg
from skidl.pin import pin_drives

NC = builtins.NC  # SKiDL installs its no-connect net as a builtin

gnd = Net("GND")
v5a_p = Net("+5VA")
v5a_n = Net("-5VA")
v5 = Net("+5V")
v3 = Net("+3V3")  # logic and mic supply, matches the FPGA's 3.3 V I/O
vdrv = Net("VDRV")  # gate-driver rail, 15 V nominal (UCC27524 max 18 V)
for n in (gnd, v5a_p, v5a_n, v5, v3, vdrv):
    n.drive = pin_drives.POWER  # from regulators on the full board


# ---- Transceiver: one channel and the shared parts it needs ---------------------

tx_en_fpga = Net("TX_EN_FPGA")
tx_en = [Net(f"TX_EN{k + 1}") for k in range(4)]  # 5 V, buffered, one per driver row
tx_oe_n = Net("TX_OE_N")
tx_clr_n = Net("TX_CLR_N")
tx_control(tx_en_fpga, tx_en, tx_oe_n, tx_clr_n, v5, v3, gnd, tag="txctl")

# VDRV bulk, once per board (size the supply for >= 0.25 A).
vdrv & lcsc(Part("Device", "C_Polarized", value="47u 35V",
                 footprint="Capacitor_SMD:CP_Elec_6.3x7.7", tag="c_vdrv"), "TBD") & gnd

tx_bits = [Net(f"TX_BIT{i}") for i in range(4)]
tx_shift_register(Net("TX_SDI0"), Net("TX_SCK"), Net("TX_LATCH"), tx_oe_n, tx_clr_n,
                  tx_bits, v5, gnd, tag="sr0")

drv = gate_driver(tx_en[0], vdrv, gnd, tag="drv0")
for net in tx_en[1:]:  # test build: only one driver row present
    net.do_erc = False

xd0 = Net("XD0")
rx0 = Net("RX0")
tx_rx_channel(drv["INA"], drv["OUTA"], tx_bits[0], xd0, rx0, v5a_p, v5a_n, gnd,
              tag="ch0")
transducer(xd0, gnd, tag="xd0")

# Test build only: the second driver half is unused, and the other bits have no load.
drv["INB"] += gnd
drv["OUTB"] += NC
for net in tx_bits[1:]:
    net.do_erc = False

# ---- MEMS mics: all 8, as 4 pairs on 4 DATA lines and 4 buffered clocks -----------

mic_clk_in = Net("MIC_CLK_IN")
mic_clks = [Net("MIC_CLK_A"), Net("MIC_CLK_B")]  # A: mics 1-4, B: mics 5-8
clock_buffer(mic_clk_in, mic_clks[0], mic_clks[1], v3, gnd, tag="micclk")
v3mic = Net("3V3_MIC")
v3mic.drive = pin_drives.POWER  # fed through the ferrite from +3V3
mic_supply_filter(v3, v3mic, gnd, tag="micpwr")
mic_data = [Net(f"MIC_DATA{i}") for i in range(4)]
for i in range(4):
    mems_mic_pair(mic_data[i], mic_clks[i // 2], v3mic, gnd, tag=f"micpair{i}")

# Signals that leave the board are single-pin nets in this test build.
for net in [rx0, Net.get("TX_SDI0"), Net.get("TX_SCK"), Net.get("TX_LATCH")]:
    net.do_erc = False

out = Path(__file__).resolve().parent.parent / "build"
out.mkdir(exist_ok=True)
ERC()
generate_netlist(file_=str(out / "hybrid_test.net"))
generate_svg(filepath=str(out / "svg"), top_name="hybrid_test")  # one page per block
generate_svg(filepath=str(out / "svg"), top_name="hybrid_test_flat", flatness=1.0)
