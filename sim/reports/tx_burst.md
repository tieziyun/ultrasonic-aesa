# TX burst and listen-after-transmit

Run 2026-10-20 13:56 with LTspice (`sim/run.py tx_burst`). Netlist: [`sim/tx_burst.cir`](../tx_burst.cir).

8 cycles at 40 kHz from a 15 V UCC27524 half through 47 Ω and the crossed diodes into the element, then the same element listens. Run for element Q = 20 and 40 (the datasheet's 1.8 ms response time lies between).

Models: OPA1678 = TI OPA167x macro-model (`sim/models/OPA167x.lib`); BAV99 = 1N4148 die model, Cj 1.5 pF; CUSA-TR65 = Butterworth–Van Dyke, C0 2.2 nF and Rm 3 kΩ from the datasheet, Q assumed; drivers, buffers and the FPGA are behavioural (values in each netlist header).

## Result: **PASS** (9 pass, 0 fail, 0 info)

| Quantity | Value | Unit | Limit | Status | Note |
|---|---|---|---|---|---|
| Element peak voltage | 14.2 | V | 13 … 15.5 | pass | VDRV 15 V minus one diode |
| Driver peak current | 0.287 | A | ≤ 1 | pass | UCC27524 rated 5 A peak |
| Limiter node, positive peak | 0.634 | V | ≤ 0.9 | pass | OPA1678 input protection |
| Limiter node, negative peak | -0.451 | V | ≥ -0.9 | pass |  |
| Average power in the 47 Ω drive resistor | 15.7 | mW | ≤ 50 | pass | 0603 rating 100 mW, keep 2× margin; CW is similar |
| Average power in the 4.7 kΩ limiter resistor | 19.2 | mW | ≤ 50 | pass |  |
| ADC pin peak during TX | 4.2 | V | ≤ 5 | pass | inside the ±5 V ADC range |
| Recovery to < 100 mV after the burst, element Q = 20 | 1 | ms | ≤ 2.5 | pass | element ringing; 2 ms ≈ 0.34 m blind range |
| Recovery to < 100 mV after the burst, element Q = 40 | 1.88 | ms | ≤ 2.5 | pass | element ringing; 2 ms ≈ 0.34 m blind range |

![TX burst and listen-after-transmit](tx_burst.png)
