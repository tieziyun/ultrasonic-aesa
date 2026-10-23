# Clocks from the FPGA: fallback termination

**Question:** If the FPGA had to stay at the stronger DRIVE 8, which resistor/capacitor at our end would clean up the edges?

**Answer (PASS):** If the FPGA ever has to stay at DRIVE 8, the unfitted 100 Ω + 47 pF parts on the header sheet clean the clock up (3.28 V / +0.02 V). A series resistor at our end does not help much.

## Why it matters

A fallback in case DRIVE 4 is not possible; the parts are on the board but not fitted.

## What is simulated

The same path at DRIVE 8 with four options at our buffer input.

## How to read the plot

The clock voltage at our buffer's input for each option.

![Clocks from the FPGA: fallback termination](clock_si_in.png)

## The numbers

2 checks pass, 0 fail; 6 rows are for information.

| What | Value | Unit | Limit | OK? | What it means |
|---|---|---|---|---|---|
| nothing: overshoot | 4.04 | V | — | — |  |
| nothing: undershoot | -0.738 | V | — | — |  |
| series 47 Ω: overshoot | 3.87 | V | — | — |  |
| series 47 Ω: undershoot | -0.569 | V | — | — |  |
| 100 Ω + 47 pF to GND (fitted only as a fallback): overshoot | 3.28 | V | ≤ 3.6 | yes |  |
| 100 Ω + 47 pF to GND (fitted only as a fallback): undershoot | 0.0158 | V | ≥ -0.3 | yes |  |
| 100 Ω + 100 pF to GND: overshoot | 3.16 | V | — | — |  |
| 100 Ω + 100 pF to GND: undershoot | 0.133 | V | — | — |  |

## Assumptions

- Op-amp: TI's OPA167x model (`sim/models/OPA167x.lib`): gain, bandwidth, noise, output swing, overload.
- Diodes (BAV99): 1N4148 junction model, the same die.
- Transducer (CUSP-TR80-15-2500-TH): standard electrical equivalent (Butterworth–Van Dyke) with C0 = 2.0 nF and Rm = 3 kΩ from the datasheet; its sharpness Q (20–40) is an assumption.
- Gate driver, clock buffers and the FPGA outputs: simple behavioural models (values in each netlist header).
- The channel circuit is exported from `kicad/channel.kicad_sch` at the start of every run.

Run 2026-10-23 22:25 with LTspice by `sim/run.py clock_si_in`. Testbench: `sim/clock_si_in.cir`.
