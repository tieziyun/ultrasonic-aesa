# Clock fan-out: buffer to 4 loads

Run 2026-10-20 13:57 with LTspice (`sim/run.py clock_si`). Netlist: [`sim/clock_si.cir`](../clock_si.cir).

74LVC2G34 output through the series resistor into a 60 Ω trace with four 5 pF loads every 30 mm, 20 MHz. Compares 0 Ω and 33 Ω.

Models: OPA1678 = TI OPA167x macro-model (`sim/models/OPA167x.lib`); BAV99 = 1N4148 die model, Cj 1.5 pF; CUSA-TR65 = Butterworth–Van Dyke, C0 2.2 nF and Rm 3 kΩ from the datasheet, Q assumed; drivers, buffers and the FPGA are behavioural (values in each netlist header).

## Result: **PASS** (3 pass, 0 fail, 2 info)

| Quantity | Value | Unit | Limit | Status | Note |
|---|---|---|---|---|---|
| 4th load overshoot, 33 Ω | 3.31 | V | ≤ 3.6 | pass | 74LVC/74HCT abs. max VCC+0.5 V |
| 4th load undershoot, 33 Ω | -0.0139 | V | ≥ -0.3 | pass |  |
| Threshold crossings 100–300 ns (20 MHz → 8) | 8 |  | 8 … 8 | pass | no double clocking |
| 4th load overshoot without the resistor (comparison) | 4.41 | V | — | info |  |
| 4th load undershoot without the resistor (comparison) | -1.11 | V | — | info |  |

![Clock fan-out: buffer to 4 loads](clock_si.png)
