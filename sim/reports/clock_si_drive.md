# Ribbon end: FPGA drive strength

Run 2026-10-20 13:57 with LTspice (`sim/run.py clock_si_drive`). Netlist: [`sim/clock_si_drive.cir`](../clock_si_drive.cir).

FPGA output through 6 cm of board trace and a 15 cm, 100 Ω ribbon to the clk_buf input, no termination, for FPGA output impedances 40/60/80 Ω (DRIVE 8 … 4).

Models: OPA1678 = TI OPA167x macro-model (`sim/models/OPA167x.lib`); BAV99 = 1N4148 die model, Cj 1.5 pF; CUSA-TR65 = Butterworth–Van Dyke, C0 2.2 nF and Rm 3 kΩ from the datasheet, Q assumed; drivers, buffers and the FPGA are behavioural (values in each netlist header).

## Result: **PASS** (3 pass, 0 fail, 6 info)

| Quantity | Value | Unit | Limit | Status | Note |
|---|---|---|---|---|---|
| DRIVE 8 (~40 Ω): overshoot at buffer input | 3.72 | V | — | info |  |
| DRIVE 8 (~40 Ω): undershoot at buffer input | -0.422 | V | — | info |  |
| DRIVE 8 (~40 Ω): threshold crossings (8 expected) | 8 |  | — | info |  |
| ~60 Ω: overshoot at buffer input | 3.45 | V | — | info |  |
| ~60 Ω: undershoot at buffer input | -0.154 | V | — | info |  |
| ~60 Ω: threshold crossings (8 expected) | 8 |  | — | info |  |
| DRIVE 4 (~80 Ω, design): overshoot at buffer input | 3.32 | V | ≤ 3.6 | pass |  |
| DRIVE 4 (~80 Ω, design): undershoot at buffer input | -0.021 | V | ≥ -0.3 | pass | 74LVC2G34 input rating −0.5 V |
| DRIVE 4 (~80 Ω, design): threshold crossings (8 expected) | 8 |  | 8 … 8 | pass |  |

![Ribbon end: FPGA drive strength](clock_si_drive.png)
