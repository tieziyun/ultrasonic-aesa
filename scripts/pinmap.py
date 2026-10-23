"""AXU3EGB header pin maps, shared by build_cells.py (drawing) and check_design.py (checks).

J1 = our EX_IO1 header to AXU3EGB J15 (banks 43/44), J2 = EX_IO2 to J16 (banks 45/46).
AXU3EGB pins: GND 1, 37, 38; +5V 2; +3.3V 39, 40 (left open: the panel has its own supply).
Odd pins are N, even pins P of a pair. Every clock (32, 34, 36) has GND on both ribbon neighbours.
None = left open.
"""

J1_MAP = {1: "GND", 2: None, 3: "GND", 4: "GND", 5: "TX_SDI1", 6: "TX_SDI0", 7: "TX_SDI3", 8: "TX_SDI2",
          9: "TX_SDI5", 10: "TX_SDI4", 11: "TX_SDI7", 12: "TX_SDI6", 13: "TX_SDI9", 14: "TX_SDI8",
          15: "TX_SDI11", 16: "TX_SDI10", 17: "TX_SDI13", 18: "TX_SDI12", 19: "TX_SDI15", 20: "TX_SDI14",
          21: "TX_CLR_N", 22: "TX_OE_N", 23: "GND", 24: "TX_EN_FPGA", 25: "MIC_DATA0", 26: "GND",
          27: "MIC_DATA2", 28: "MIC_DATA1", 29: "GND", 30: "MIC_DATA3", 31: "GND", 32: "TX_SCK", 33: "GND",
          34: "MIC_CLK_IN", 35: "GND", 36: "TX_LATCH", 37: "GND", 38: "GND", 39: None, 40: None}
J2_MAP = {1: "GND", 2: None, 3: "GND", 4: "GND", 5: "ADC_SHDNZ", 6: "GND", 7: "ADC_SCL", 8: "ADC_SDA1",
          **{9 + k: f"ADC_SDOUT{k + 1}" for k in range(16)},
          25: "ADC_SDA2", 26: "ADC_SDA3", 27: "ADC_SDA4", 28: "GND", 29: "GND", 30: "GND", 31: "GND",
          32: "ADC_BCLK", 33: "GND", 34: "ADC_FSYNC", 35: "GND", 36: "GND", 37: "GND", 38: "GND",
          39: None, 40: None}
