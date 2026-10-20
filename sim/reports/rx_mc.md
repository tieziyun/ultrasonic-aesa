# RX chain: channel-to-channel spread (Monte Carlo)

Run 2026-10-20 13:56 with LTspice (`sim/run.py rx_mc`). Netlist: [`sim/rx_mc.cir`](../rx_mc.cir).

200 copies of the amplifier with 1 % resistors, 1 % C0G band-pass caps, 5 % Cf1 and 10 % X7R coupling cap, uniform distribution: gain and phase at 40 kHz. Tells how much per-element calibration has to remove.

Models: OPA1678 = TI OPA167x macro-model (`sim/models/OPA167x.lib`); BAV99 = 1N4148 die model, Cj 1.5 pF; CUSA-TR65 = Butterworth–Van Dyke, C0 2.2 nF and Rm 3 kΩ from the datasheet, Q assumed; drivers, buffers and the FPGA are behavioural (values in each netlist header).

## Result: **PASS** (5 pass, 0 fail, 0 info)

| Quantity | Value | Unit | Limit | Status | Note |
|---|---|---|---|---|---|
| Mean gain at 40 kHz | 27 | dB | 26 … 29 | pass |  |
| Gain spread, standard deviation | 0.103 | dB | ≤ 0.5 | pass |  |
| Gain spread, max - min | 0.581 | dB | ≤ 2 | pass | removed by per-element calibration; small keeps the dynamic range equal |
| Phase spread, standard deviation | 1.31 | deg | ≤ 3 | pass | beam pointing error before calibration; 3 deg is about lambda/120 |
| Phase spread, max - min | 6.35 | deg | ≤ 12 | pass | per-element calibration removes it; the brief expected a few degrees with 1 % parts |

![RX chain: channel-to-channel spread (Monte Carlo)](rx_mc.png)
