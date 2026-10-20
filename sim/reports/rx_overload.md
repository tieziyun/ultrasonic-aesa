# RX overload: strong near echo, recovery

Run 2026-10-20 13:57 with LTspice (`sim/run.py rx_overload`). Netlist: [`sim/rx_overload.cir`](../rx_overload.cir).

A 10-cycle echo of 0.12 V (1 m), 1 V and 5 V open-circuit at the element, then a 10 mV echo 1.4 ms later: clamp, saturation, recovery and whether the later echo is clean.

Models: OPA1678 = TI OPA167x macro-model (`sim/models/OPA167x.lib`); BAV99 = 1N4148 die model, Cj 1.5 pF; CUSA-TR65 = Butterworth–Van Dyke, C0 2.2 nF and Rm 3 kΩ from the datasheet, Q assumed; drivers, buffers and the FPGA are behavioural (values in each netlist header).

## Result: **PASS** (8 pass, 0 fail, 3 info)

| Quantity | Value | Unit | Limit | Status | Note |
|---|---|---|---|---|---|
| echo 0.12 V (open-circuit at the element): ADC pin peak | 0.767 | V | ≤ 5 | pass | inside the ADC's +-5 V range |
| echo 0.12 V (open-circuit at the element): recovery to < 50 mV after the echo ends | 0.394 | ms | ≤ 1.1 | pass | the next echo (1.5 ms) must be clean |
| echo 0.12 V (open-circuit at the element): later 10 mV echo at the ADC | 0.0643 | V | — | info |  |
| echo 1 V (open-circuit at the element): ADC pin peak | 4.2 | V | ≤ 5 | pass | inside the ADC's +-5 V range |
| echo 1 V (open-circuit at the element): recovery to < 50 mV after the echo ends | 0.715 | ms | ≤ 1.1 | pass | the next echo (1.5 ms) must be clean |
| echo 1 V (open-circuit at the element): later 10 mV echo at the ADC | 0.0644 | V | — | info |  |
| echo 1 V (open-circuit at the element): later echo vs after a weak echo | 0.143 | % | -5 … 5 | pass | the strong echo leaves no lasting distortion or gain change |
| echo 5 V (open-circuit at the element): ADC pin peak | 4.2 | V | ≤ 5 | pass | inside the ADC's +-5 V range |
| echo 5 V (open-circuit at the element): recovery to < 50 mV after the echo ends | 0.938 | ms | ≤ 1.1 | pass | the next echo (1.5 ms) must be clean |
| echo 5 V (open-circuit at the element): later 10 mV echo at the ADC | 0.0636 | V | — | info |  |
| echo 5 V (open-circuit at the element): later echo vs after a weak echo | -1.06 | % | -5 … 5 | pass | the strong echo leaves no lasting distortion or gain change |

![RX overload: strong near echo, recovery](rx_overload.png)
