# Simulation 1 (2026-10-20)

LTspice 24.0.9 in batch mode (`LTspice.exe -b <file>.cir`). Netlists are in `sim/`, plots in
`build/sim_*.png` (made by `sim/plots.py`), raw-file reader `sim/ltraw.py`.

Models:
- **OPA1678:** TI's OPA167x macro-model (`sim/models/OPA167x.lib`, from ti.com/lit/zip/sbomac5, unencrypted). It models gain, bandwidth, noise, slew, output swing and overload recovery.
- **BAV99:** 1N4148 die model with Cj 1.5 pF.
- **CUSA-TR65:** a Butterworth–Van Dyke model. C0 = 2.2 nF and Rm = 3 kΩ come from the datasheet; Q = 20–40 is assumed, consistent with the 2 kHz bandwidth and 1.8 ms response time.
- **Drivers, buffers and the FPGA:** behavioural models, with values in each file header.

## 1. RX chain: transducer to ADC (`sim/rx_chain.cir`, `sim/rx_noise.cir`, `build/sim_rx_ac.png`)

| Quantity | Result |
|---|---|
| Loading of the element by the front end at 40 kHz | −0.53 dB |
| Gain, open-circuit element voltage → ADC pin, 40 kHz | **+27.0 dB (×22.3)** |
| Amplifier pass band (−3 dB) | 30.9–50.6 kHz, peak 27.6 dB |
| Phase change 39 → 41 kHz (amplifier) | 11.7° (calibrated per element) |
| AD7606C-16 high-bandwidth filter at 40 kHz | **−0.005 dB**, −15° (same on every channel) |
| AD7606B fixed filter at 40 kHz (for comparison) | **−10.4 dB**, −130° |
| Output noise density at 40 kHz | 275 nV/√Hz (26 nV/√Hz referred to the element) |
| Output noise, 30–50 kHz | 33 µV rms |
| 1 m echo (120 mV, datasheet) at the ADC pin | about 2.7 V peak |

- **ADC choice:** this confirms the switch to the AD7606C-16. The AD7606B's fixed filter would have cost 10.4 dB at 40 kHz, on top of the 6 dB from its ±10 V range.
- **Noise:** the input-referred noise is dominated by the element's own resistance and the 4.7 kΩ limiter resistor; the op-amp contributes less. The ADC's own noise is estimated (not simulated) at about 50 µV rms in a 20 kHz band on the ±5 V range, so the front end and the ADC are comparable. Further front-end gain would buy little.
- **Close echoes:** anything closer than about 0.5 m drives the output to the ±4.2 V swing limit. That is inside the ADC range, so it is harmless.

## 2. TX burst and listen-after-transmit (`sim/tx_burst*.cir`, `build/sim_tx_burst.png`)

8 cycles at 40 kHz, VDRV = 15 V, UCC27524 modelled as 5 Ω pull-up / 0.4 Ω pull-down.

| Quantity | Result |
|---|---|
| Element voltage | 0 to 14.2 V (unipolar square, as designed) |
| Peak driver current (edges into 2.2 nF through 47 Ω) | 0.29 A (UCC27524: 5 A) |
| Limiter node during TX | +0.63 / −0.45 V (OPA1678 input safe) |
| Average power, 47 Ω / 4.7 kΩ during the burst | 16 mW / 19 mW (0603: 100 mW; continuous wave is similar) |
| ADC pin during TX | ±4.2 V (amplifier saturates, ADC not over-ranged) |
| Recovery to < 100 mV after the burst ends, Q = 20 / Q = 40 | 1.0 ms / 1.9 ms |
| Recovery to < 10 mV, Q = 20 / Q = 40 | 1.35 ms / 2.6 ms |

The blind time comes from the element's mechanical ringing (it scales with Q), not from the electronics: the limiter releases immediately and the 33 k bleed discharges C0 in about 73 µs. 1.9 ms is about 0.33 m of range, which agrees with the brief's 0.3–0.4 m blind zone. Split mode avoids it.

## 3. Clock lines (`sim/clock_si.cir`, `sim/clock_si_in.cir`, `sim/clock_si_drive.cir`, `build/sim_clock_si.png`)

Path: FPGA (3.3 V LVCMOS, 3–4 ns edges) → 6 cm board trace → 15 cm ribbon (100 Ω, ground conductor on each side) → clk_buf (74LVC2G34) → 33 Ω → 60 Ω trace with 4 loads of 5 pF, every 30 mm. Clock at 20 MHz.

| Case | Overshoot / undershoot | Notes |
|---|---|---|
| Buffer output to 4 loads, no series R | 4.41 / −1.11 V | rings |
| Buffer output to 4 loads, **33 Ω** (design) | 3.31 / −0.01 V | clean, monotonic |
| Ribbon end at the buffer input, FPGA DRIVE 8 (~40 Ω) | 3.7–4.0 / −0.4…−0.74 V | switches cleanly, but undershoot exceeds the 74LVC −0.5 V input rating |
| Same, RC end termination 100 Ω + 47 pF | 3.28 / +0.02 V | clean; 10 mW per clock at 20 MHz |
| Same, **FPGA DRIVE 4 (~80 Ω), no termination** | **3.32 / −0.02 V** | clean, no parts |

- **Decision:** all FPGA ribbon outputs (clocks and data) use DRIVE 4, SLEW SLOW, which nearly source-matches the ribbon.
- **Fallback:** RC terminations for the five clock lines are on the header sheet as DNP parts. Fit them only if the FPGA has to stay at DRIVE 8: together with DRIVE 4 they halve the initial step to about 1.8 V, close to the 1.65 V threshold.
- **Threshold crossings:** every case gives exactly 2 per clock period (no double clocking).

## 4. Not simulated yet

- **Power regulators:** only the feedback dividers are checked (see section 5). Loop stability and start-up need the vendor models (TI PSpice for the TPS54202 and TPS65131).
- **Crosstalk:** coupling between neighbouring channels, and from the TX drive into RX inputs, depends on the layout. Rerun with PCB parasitics after placement and routing (kicad-happy `extract_parasitics.py`).
- **AD7606C-16 noise:** taken from an estimate. Check it against the datasheet SNR at ±5 V when the ADI site is reachable.
