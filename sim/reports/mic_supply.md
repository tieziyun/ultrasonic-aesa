# 3V3_MIC ferrite filter

Run 2026-10-20 13:57 with LTspice (`sim/run.py mic_supply`). Netlist: [`sim/mic_supply.cir`](../mic_supply.cir).

+3V3 -> 220 R/100 MHz ferrite -> 10 uF + 8 x 100 nF: noise transfer, attenuation at the PDM clock and whether the bead/cap resonance peaks.

Models: OPA1678 = TI OPA167x macro-model (`sim/models/OPA167x.lib`); BAV99 = 1N4148 die model, Cj 1.5 pF; CUSA-TR65 = Butterworth–Van Dyke, C0 2.2 nF and Rm 3 kΩ from the datasheet, Q assumed; drivers, buffers and the FPGA are behavioural (values in each netlist header).

## Result: **PASS** (3 pass, 0 fail, 2 info)

| Quantity | Value | Unit | Limit | Status | Note |
|---|---|---|---|---|---|
| Peaking of the bead + 10 uF resonance | 0.947 | dB | ≤ 3 | pass | no noise gain in the mics' ultrasonic band |
| Resonance frequency | 64.6 | kHz | — | info |  |
| Attenuation at the PDM clock (3.072 MHz) | -60.9 | dB | ≤ -40 | pass |  |
| Attenuation at 100 MHz | -68.2 | dB | ≤ -40 | pass |  |
| Transfer at 40 kHz (in-band; the mic's PSRR handles it) | 0.566 | dB | — | info |  |

![3V3_MIC ferrite filter](mic_supply.png)
