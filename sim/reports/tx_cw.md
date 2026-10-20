# TX continuous wave: current and dissipation

Run 2026-10-20 13:57 with LTspice (`sim/run.py tx_cw`). Netlist: [`sim/tx_cw.cir`](../tx_cw.cir).

One element driven continuously at 40 kHz from 15 V (split mode / Doppler), steady state: supply current per element, power in each resistor and the clamp diodes.

Models: OPA1678 = TI OPA167x macro-model (`sim/models/OPA167x.lib`); BAV99 = 1N4148 die model, Cj 1.5 pF; CUSA-TR65 = Butterworth–Van Dyke, C0 2.2 nF and Rm 3 kΩ from the datasheet, Q assumed; drivers, buffers and the FPGA are behavioural (values in each netlist header).

## Result: **PASS** (7 pass, 0 fail, 1 info)

| Quantity | Value | Unit | Limit | Status | Note |
|---|---|---|---|---|---|
| VDRV current per element (CW, 40 kHz) | 3.73 | mA | ≤ 5 | pass | C V^2 f alone is 1.3 mA; the RX limiter's 4.7 k takes 19 mW of its own element's drive |
| VDRV current, all 64 elements in CW | 239 | mA | ≤ 900 | pass | LM2731X boost limit about 0.9 A at 15 V from 12 V |
| Power in the 47 R drive resistor | 15.9 | mW | ≤ 50 | pass | 0603: 100 mW |
| Power in the 4.7 k limiter resistor | 19 | mW | ≤ 50 | pass | 0603: 100 mW |
| Power in the 33 k bleed resistor | 2.98 | mW | ≤ 50 | pass |  |
| Acoustic power into the element (Rm) | 11.9 | mW | — | info | what actually radiates (model) |
| Average limiter/clamp current | 1.45 | mA | ≤ 50 | pass | BAV99 200 mA continuous |
| Element voltage, peak to peak | 13.6 | V | 13 … 16 | pass |  |

![TX continuous wave: current and dissipation](tx_cw.png)
