# Receiver: how much noise does it add?

**Question:** How quiet is the receiver compared with the echoes it has to hear, and compared with the ADC?

**Answer (PASS):** In the band that matters (30–50 kHz) the receiver adds **10 µV rms** of noise at the ADC input, about the same as the ADC itself (~9 µV). A 1 m echo is **93 dB** above the total. An echo is still clearly detectable (10 dB above noise) down to about 9 µV at the transducer, 13418× weaker than a 1 m echo.

## Why it matters

Noise sets the weakest echo that can be detected, so it sets the maximum range.

## What is simulated

The same channel, with LTspice's noise analysis: the thermal noise of every resistor, the op-amp's noise and the transducer's own internal loss.

## How to read the plot

Blue: noise at the ADC input pin. Orange: the same noise expressed as an echo voltage at the transducer (how big an echo would be just as large as the noise). Red dashed: the ADC's own noise. Only the shaded 30–50 kHz band matters, because the FPGA filters everything else away.

![Receiver: how much noise does it add?](rx_noise.png)

## The numbers

4 checks pass, 0 fail; 2 rows are for information.

| What | Value | Unit | Limit | OK? | What it means |
|---|---|---|---|---|---|
| Receiver noise at the ADC input pin, 30–50 kHz | 10.1 | µV rms | ≤ 20 | yes | the FPGA's digital filter keeps only this band |
| ADC's own noise in the same band (estimate, not simulated) | 8.91 | µV rms | — | — | TLV320ADC5140 SNR 107 dB differential, taken 6 dB worse single-ended |
| Noise density at 40 kHz, at the ADC input pin | 83 | nV/√Hz | ≤ 150 | yes |  |
| Same, referred back to the transducer | 12.2 | nV/√Hz | ≤ 50 | yes | mostly the transducer's own loss resistance and the 4.7 kΩ protection resistor |
| 1 m echo above the total noise (receiver + ADC) | 92.6 | dB | ≥ 60 | yes |  |
| Weakest echo that is still 10 dB above the noise | 8.94 | µV | — | — | 13418× weaker than a 1 m echo |

## Assumptions

- Op-amp: TI's OPA167x model (`sim/models/OPA167x.lib`): gain, bandwidth, noise, output swing, overload.
- Diodes (BAV99): 1N4148 junction model, the same die.
- Transducer (CUSA-TR65): standard electrical equivalent (Butterworth–Van Dyke) with C0 = 2.2 nF and Rm = 3 kΩ from the datasheet; its sharpness Q (20–40) is an assumption.
- Gate driver, clock buffers and the FPGA outputs: simple behavioural models (values in each netlist header).
- The channel circuit is exported from `kicad/channel.kicad_sch` at the start of every run.

Run 2026-10-23 13:40 with LTspice by `sim/run.py rx_noise`. Testbench: `sim/rx_noise.cir`.
