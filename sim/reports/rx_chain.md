# Receiver: how much does an echo get amplified?

**Question:** When an echo arrives, how big is the signal that reaches the ADC, and does the ADC's own input filter weaken it?

**Answer (PASS):** A 1 m echo (0.12 V at the transducer) reaches the ADC as about **2.7 V peak**: the receiver amplifies ×22 (27.0 dB) at 40 kHz and passes 31–51 kHz. The chosen ADC (AD7606C-16) loses only 0.00 dB at 40 kHz; the AD7606B would have lost 10 dB.

## Why it matters

The ADC must see the echo well above its own noise but below its ±5 V limit. The transducer datasheet gives about 0.12 V for an echo from an object 1 m away.

## What is simulated

One transceiver channel, exported from the schematic: the transducer (electrical model), the protection resistor and clamp diodes, the ×6 amplifier stage and the 40 kHz band-pass filter, into the ADC input. A small sine wave is swept from 1 kHz to 1 MHz.

## How to read the plot

Left to right: frequency. Up: how much bigger the signal at the ADC is than the echo voltage at the transducer, in dB (+20 dB = ×10, +27 dB = ×22). The response peaks at 40 kHz, the transducers' frequency, and falls off on both sides, which keeps noise out. The green curve (chosen ADC) lies on the blue one; the dashed red curve shows what the old ADC would have let through.

![Receiver: how much does an echo get amplified?](rx_chain.png)

## The numbers

6 checks pass, 0 fail; 1 rows are for information.

| What | Value | Unit | Limit | OK? | What it means |
|---|---|---|---|---|---|
| Gain from the element's echo voltage to the ADC pin, 40 kHz | 27 | dB | 25 … 30 | yes | ×22.3: a 1 m echo (0.12 V) arrives as 2.7 V, inside the ±5 V ADC range |
| Signal lost because the receiver loads the transducer | -0.529 | dB | ≥ -1 | yes | the 2.2 nF transducer is a weak source; the receiver must not load it |
| Pass band, lower edge (−3 dB) | 30.9 | kHz | 25 … 35 | yes | rejects low-frequency noise |
| Pass band, upper edge (−3 dB) | 50.6 | kHz | 45 … 60 | yes | wide enough for short pings (range resolution) |
| Phase change from 39 to 41 kHz | 11.8 | ° | ≤ 20 | yes | fixed per channel, removed by calibration |
| Loss in the AD7606C-16 input filter at 40 kHz | -0.00476 | dB | ≥ -0.1 | yes | the chosen ADC |
| Loss the old AD7606B filter would have caused | -10.4 | dB | — | — | why the ADC was changed |

## Assumptions

- Op-amp: TI's OPA167x model (`sim/models/OPA167x.lib`): gain, bandwidth, noise, output swing, overload.
- Diodes (BAV99): 1N4148 junction model, the same die.
- Transducer (CUSA-TR65): standard electrical equivalent (Butterworth–Van Dyke) with C0 = 2.2 nF and Rm = 3 kΩ from the datasheet; its sharpness Q (20–40) is an assumption.
- Gate driver, clock buffers and the FPGA outputs: simple behavioural models (values in each netlist header).
- The channel circuit is exported from `kicad/channel.kicad_sch` at the start of every run.

Run 2026-10-21 12:05 with LTspice by `sim/run.py rx_chain`. Testbench: `sim/rx_chain.cir`.
