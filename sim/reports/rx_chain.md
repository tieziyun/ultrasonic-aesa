# Receiver: how much does an echo get amplified?

**Question:** When an echo arrives, how big is the signal that reaches the ADC, and how much room is left for stronger echoes?

**Answer (PASS):** A 1 m echo (0.12 V at the transducer) reaches the ADC as **0.80 V peak**, 4.9 dB below its ±1.41 V full scale: the receiver amplifies ×6.7 (16.5 dB) at 40 kHz after the 2k/1k divider and passes 31–51 kHz. The ADC's own filter is digital and flat to about 86 kHz, so nothing is lost at 40 kHz.

## Why it matters

The ADC must see the echo well above its own noise but below its ±1.41 V full scale. The transducer datasheet gives about 0.12 V for an echo from an object 1 m away.

## What is simulated

One transceiver channel, exported from the schematic: the transducer (electrical model), the protection resistor and clamp diodes, the ×6 amplifier stage and the 40 kHz band-pass filter, then the ADC input network from the ADC sheet (2k/1k divider, 100 nF, the ADC's 10 kΩ input). A small sine wave is swept from 1 kHz to 1 MHz.

## How to read the plot

Left to right: frequency. Up: how much bigger the signal is than the echo voltage at the transducer, in dB (+20 dB = ×10). Blue: the amplifier output. Green: the ADC input pin, 10 dB lower because of the divider that fits the amplifier's ±4.2 V swing into the ADC's ±1.41 V. Both peak at 40 kHz, the transducers' frequency, and fall off on both sides, which keeps noise out.

![Receiver: how much does an echo get amplified?](rx_chain.png)

## The numbers

9 checks pass, 0 fail; 0 rows are for information.

| What | Value | Unit | Limit | OK? | What it means |
|---|---|---|---|---|---|
| Gain from the element's echo voltage to the ADC input pin, 40 kHz | 16.5 | dB | 14 … 19 | yes | ×6.7: a 1 m echo (0.12 V) arrives as 0.80 V peak |
| 1 m echo relative to the ADC full scale (±1.41 V) | -4.9 | dBFS | -12 … -3 | yes | room above it for closer, stronger echoes; the receiver itself clips at ±4.2 V = ±1.27 V here |
| Gain of the amplifier (element side of the divider), 40 kHz | 26.6 | dB | 25 … 30 | yes |  |
| ADC input divider at 40 kHz (2k/1k, 100 nF, 10 k input) | -10.1 | dB | -11.5 … -9.5 | yes | ×0.31 |
| Phase shift of the 100 nF coupling at 40 kHz | 0.124 | ° | ≤ 1 | yes | its 150 Hz corner is far below the band; any spread between channels is calibrated out |
| Signal lost because the receiver loads the transducer | -0.573 | dB | ≥ -1 | yes | the 2.2 nF transducer is a weak source; the receiver must not load it |
| Pass band, lower edge (−3 dB) | 30.9 | kHz | 25 … 35 | yes | rejects low-frequency noise |
| Pass band, upper edge (−3 dB) | 50.6 | kHz | 45 … 60 | yes | wide enough for short pings (range resolution) |
| Phase change from 39 to 41 kHz | 11.8 | ° | ≤ 20 | yes | fixed per channel, removed by calibration |

## Assumptions

- Op-amp: TI's OPA167x model (`sim/models/OPA167x.lib`): gain, bandwidth, noise, output swing, overload.
- Diodes (BAV99): 1N4148 junction model, the same die.
- Transducer (CUSP-TR80-15-2500-TH): standard electrical equivalent (Butterworth–Van Dyke) with C0 = 2.0 nF and Rm = 3 kΩ from the datasheet; its sharpness Q (20–40) is an assumption.
- Gate driver, clock buffers and the FPGA outputs: simple behavioural models (values in each netlist header).
- The channel circuit is exported from `kicad/channel.kicad_sch` at the start of every run.

Run 2026-10-25 01:31 with LTspice by `sim/run.py rx_chain`. Testbench: `sim/rx_chain.cir`.
