# Hybrid 8×8 Ultrasonic Transceiver Panel — Design Brief

An acoustic "AESA": a 40 kHz in-air phased array in which every element can transmit and receive under per-element FPGA control, plus a few wideband MEMS microphones. It feeds an ALINX AXU3EGB (Zynq UltraScale+ ZU3EG) board, which does the beamforming.

Status (2026-10-19): front end and TX drive **approved** (section 4a, with 74AHCT595 and 16 TX data lines); ADC AD7606B (section 4). **The full schematic is drawn in KiCad** (`kicad/aesa.kicad_pro`, section 4b; it is now the source of truth, `skidl/channel.py` is the reference spec): 64 channels, 8 MEMS mics, 8 ADCs, TX, power and AXU3EGB headers, 1,503 parts, kicad-cli ERC 0 violations. Read it in `build/aesa_schematic_summary.pdf`. Review `docs/review-channel-1.md`: B1, B2, F1–F6 applied; N1–N7 open. Full-schematic review done: `docs/review-full-1.md` (power sheet excluded; power redesigned afterwards and still needs its review). Next: apply the review items, LCSC numbers for the TBD parts, then board split and placement.

## 1. Goals (applications the board must support)

| Application | How the hardware supports it |
|---|---|
| Indoor 3D imaging and people tracking, about 0.3–5 m | 8×8 receive beamforming, MIMO (fire one element, all listen) |
| Gesture / hand tracking, cm to 2 m | **Split mode**: a few elements transmit while the rest listen, so the listening elements are not ringing; sub-mm motion from echo phase |
| Doppler: motion, vibration, breathing, fall detection | **Continuous wave** in split mode: some elements transmit continuously, the rest receive continuously |
| Acoustic camera, passive | Transceivers hear about 39–41 kHz (air/gas leaks, electrical arcing). The edge MEMS mics add wideband 20–80 kHz (bats, broadband sources) |

Out of scope: outdoor long range, underwater, anything near people's ears at high drive (40 kHz is audible to pets; keep it indoors).

## 2. Architecture

- **64 transceivers in an 8×8 grid.** Each element has its own TX driver, a per-element T/R switch or RX protection, an RX amplifier, and its own ADC channel.
- **Every element's mode is set by the FPGA per ping**: transmit, listen, or both (where the circuit allows). This mode switching is what enables split mode and continuous wave. It is the single most important requirement of the front end.
- **8 wideband MEMS microphones** around the edge of the grid (PDM, 4 data lines).
- **Front waveguide** (3D-printed tubes) to bring the effective element spacing down to λ/2 = 4.3 mm, so beamforming has no grating lobes. This follows the TU Darmstadt design (reference 1 below).
- Optional: active damping (a brief anti-phase drive after each pulse) to shorten ringing.

### Proposed board split (default; can be changed)
1. **Array panel** (about 130 × 130 mm, 2-layer): 64 transceivers, 8 MEMS mics, waveguide mounting holes, board-to-board connectors.
2. **Front-end / ADC board** behind it (about 150 × 150 mm, 4-layer): 64 channel front ends, ADCs, transmit control, power, header to the AXU3EGB.

## 3. Key numbers

| Quantity | Value |
|---|---|
| Frequency | 40 kHz; λ = 8.6 mm in air; λ/2 = 4.3 mm |
| Range resolution | about 5–8 cm (transducer bandwidth about 2 kHz) |
| Angular resolution, 8 elements at λ/2 | about 14°; MIMO improves this |
| Air absorption | about 1.3 dB/m; realistic range for people 3–5 m, large flat targets up to about 10 m |
| Ringing / blind zone (whole-array pulse-echo) | about 1.8 ms ⇒ about 0.3–0.4 m; split mode avoids most of it |
| RX sampling | about 200 kSPS per channel, 16-bit, simultaneous across channels |
| RX data rate | 64 × 200 kSPS × 16 bit ≈ 205 Mbit/s |
| Memory per ping (5 m range) | about 0.75 MB raw; the 3D volume is about 1 MB |
| Beamforming load | about 0.5 G multiply-adds per second (easy for the ZU3EG fabric) |

## 4. Parts (current choices)

| Function | Part | Notes | Links |
|---|---|---|---|
| Transceiver (default) | **Same Sky CUSA-TR65-065-2200-TH68** | Combined TX/RX, 40 kHz, Ø14 × 9 mm, beam 120° × 65° (−6 dB), 100 dB SPL at 10 V/30 cm, 120 mV echo at 1 m, up to 150 Vpp, IP68, response time 1.8 ms, about $4.72 (DigiKey, 370 in stock on 2026-10-19) | [DigiKey](https://www.digikey.com/en/products/detail/same-sky-formerly-cui-devices/CUSA-TR65-065-2200-TH68/16579204), [datasheet](https://www.cuidevices.com/product/resource/cusa-tr65-065-2200-th68.pdf) |
| Transceiver (alternative) | Generic open-type T40-16 / R40-16 (HC-SR04 type) or 10 mm levitation type (Manorshi MSO-P1040H07T) | Cheaper and more sensitive, not sealed, large unit-to-unit phase spread: buy about 30% spares and calibrate per element | — |
| Edge MEMS mics | **Knowles/Syntiant SPH0641LU4H-1** | PDM, 100 Hz–80 kHz, about €2.7 (DigiKey). Same mic as the eRTIS research sonar. Datasheet checked 2026-10-19: ultrasonic mode needs a 3.072–4.8 MHz clock, and the mic must not power up or wake from sleep in that mode (start at 1.024–2.475 MHz, then raise it). VDD 1.62–3.6 V; we use 3.3 V to match the FPGA I/O. **Bottom port, Ø0.325 mm**: mics go on the rear of the panel with a hole through the PCB. Pairs share a DATA line (SELECT = VDD and SELECT = GND). Bypass capacitors must be C0G (no Class 2 dielectric). No LCSC number checked yet (marked consigned) | [DigiKey](https://www.digikey.ie/en/products/detail/syntiant/SPH0641LU4H-1/5332438) |
| Analog alternative mic | Knowles SPU0410LR5H-QB | Analog, 100 Hz–80 kHz. Must be the "-QB" suffix | — |
| ADCs | **AD7606B** (AD7606BBSTZ-RL, LCSC C651284, 1,635 in stock, about $22.49 each / $180 for 8, JLCPCB Extended; LQFP-64, AVCC 5 V, VDRIVE 3.3 V). Chosen over the AD7606 (C398827, $17.69) for its faster serial port: 60 MHz SCLK, 1/2/4 DOUT lines, 0.85 µs conversion, so 200 kSPS is easy (the AD7606 is marginal at 200 kSPS in serial mode). **Caveat:** both parts have a fixed 2nd-order Butterworth input filter with no bypass; on the AD7606B it is −3 dB at 22.5 kHz (±10 V), 13.5 kHz (±5 V), 11.5 kHz (±2.5 V). At 40 kHz on ±10 V that is about −10 dB and about 130° lag, with 240 ns (about 3.5°) channel-to-channel phase-delay matching that per-element calibration must remove. The ±10 V range also wastes about 6 dB of range with the ±5 V OPA1678 output. Net about 16 dB of SNR lost against an ADC without the filter. Alternative if that proves too much: AD7761 (sigma-delta, Darmstadt's choice). Source: parts-sourcer, 2026-10-19 | Serial mode, 2 DOUT lines per ADC (hardware mode), shared SCLK/CS/CONVST/RESET, one BUSY | [LCSC](https://www.lcsc.com/product-detail/C651284.html), [datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/ad7606b.pdf) |
| Per-channel TX + RX front end | **Discrete (approved 2026-10-19)**, see section 4a. TUSS4470 rejected: its VOUT is a log-amp envelope (no phase) | Must allow per-element mode switching (see section 2) | — |

### 4a. Front end and TX drive (approved 2026-10-19)

Reference design: Darmstadt (reference 1) uses, per 8 elements, one HV7355 pulser (20 Vpp unipolar), two TX810 T/R switches, two AD8335 VGAs and one AD7761 ADC. We keep its signal chain (unipolar square-wave drive, raw-waveform capture at 200 kSPS) but use cheaper parts that are in stock at LCSC, because our drive voltage is low (≤ 18 V).

**Per channel** (`skidl/channel.py`):
- TX: half of a **UCC27524** dual gate driver (LCSC C465729), VDRV = 15 V nominal (18 V max) → about 14 Vpp at the element. 47 Ω series resistor, then a **BAV99 crossed-diode pair**. While the driver output is at GND the diodes are off, so the element is isolated from the driver and listens. Ringing above about 0.6 V is damped through the diodes.
- Element: CUSA-TR65, 2200 pF, 3 kΩ at resonance. 100 kΩ bleed to AGND. Footprint `aesa:CUSA-TR65` (`kicad/aesa.pretty`): 5 mm pitch, 1.0 mm drills; pin 2 is the ground/can lead. The datasheet doesn't say which lead is the can, so check it with a meter.
- RX protection: 4.7 kΩ series + BAV99 clamp to AGND (±0.77 V, 3 mA during transmit). 1 kΩ would dissipate 94 mW in CW mode.
- Filter parts: 1 % resistors, 1 nF C0G 1 % capacitors (5 % parts would give up to ±11° phase spread between channels).
- Grounds: AGND (analog, element returns, ADC), PGND (drivers, VDRV decoupling), DGND (logic, mics), joined at one point by net-ties (2 mm tie for PGND).
- RX gain: **OPA1678** dual (LCSC C192421) on ±5 V. Stage 1 non-inverting ×6, AC-coupled at 800 Hz. Stage 2 multiple-feedback band-pass, f0 = 39.8 kHz, Q = 2, gain −4. Total −24 (27.6 dB), output single-ended through 100 Ω to the ADC. Digital TGC in the FPGA instead of Darmstadt's analog VGA.
- About 16 parts per channel.

**MEMS mics** (fixed requirement, hybrid panel): 4 pairs (`mems_mic_pair`) along the grid edge, 4 DATA lines + 1 clock. Each mic has a 33 Ω series resistor on DATA and a 100 nF C0G bypass. The clock is fanned out on the panel by 2× 74LVC2G34, one output per pair through 33 Ω, with a 100 k pull-down on the input so the mics see no clock while the FPGA configures.

**Transmit lines** (shift-register frame streaming):
- **16 data lines**, one **74AHCT595** (5 V, TTL inputs, fmax about 170 MHz; no LCSC listing found yet) per 4 elements on QA–QD (QE–QH unused), plus shared SRCLK, RCLK (latch), /OE and /SRCLR. The user chose 16 lines × 4 bits over 8 × 8 to halve the shift clock; it costs 8 more 595s (16 in total) and 8 more FPGA pins.
- The FPGA shifts a 64-bit frame (4 bits per line) and latches it at 2.5 MHz (**SRCLK 10 MHz**). All 64 outputs update on the same RCLK edge, so each element's waveform is any 1-bit pattern with 0.4 µs resolution (1/62 of a 40 kHz period, about 6°).
- Per-element mode falls out of the bit stream: transmit = toggle the bit, listen = hold 0. This supports split mode and continuous wave with no extra mode lines.
- Safety (`tx_control`, once per board): the UCC27524 EN pins have 200 kΩ internal pull-ups to VDRV (datasheet), so they must never connect to an FPGA pin. A 74AHCT1G125 on +5 V drives them; its input has a 10 k pull-down to DGND. /OE has a 10 k pull-up to 3.3 V (595 outputs high-Z, driver inputs pulled low) and /SRCLR a 10 k pull-down. The drivers stay off until the FPGA enables them.
- VDRV: 47 µF bulk capacitance; size the supply for ≥ 0.25 A.
- Upgrade path if more range is needed: HV7355 (LCSC C633698, up to 150 V, needs ±5 V and VPP) in the same position; it also has its own per-channel serial enable latch.

**FPGA pin budget** (single-ended 3.3 V LVCMOS, about 68 pins on the two headers):

| Function | Pins |
|---|---|
| TX: 16 serial data + SRCLK + RCLK + /OE + /SRCLR + TX_EN | 21 |
| ADC: 8 × AD7606B, 2 DOUT each (16) + SCLK + CS + CONVST + RESET + BUSY. 64 bits per line per sample at 200 kSPS = 12.8 Mbit/s, so SCLK about 20 MHz. (1 DOUT per ADC would save 8 pins but needs software mode and SCLK ≥ 26 MHz over the headers.) | 21 |
| MEMS mics: 4 PDM data + 1 clock | 5 |
| I²C (config, power monitor) | 2 |
| **Total** | **about 49** |

### 4b. KiCad schematic (2026-10-19)

Project `kicad/aesa.kicad_pro`, top sheet `aesa.kicad_sch` (generated by `scripts/gen_top.py`).

| Sheet | Instances | Contents |
|---|---|---|
| `channel.kicad_sch` | 64 (CH01–CH64) | One transceiver channel, wired: 47 Ω + BAV99 crossed diodes, CUSA-TR65 element, 100 k bleed, 4.7 k + BAV99 clamp, OPA1678 ×6 stage and 40 kHz MFB band-pass, 100 Ω out. Ports DRV_OUT (from the driver) and RX_OUT (to the ADC). The transducer is in this sheet for now; it moves to the array panel when the boards are split. |
| `adc.kicad_sch` | 8 (ADC1–ADC8) | AD7606B, hardware mode, serial 2 DOUT, ±10 V (RANGE high), internal reference, no oversampling; decoupling per datasheet. ADC k takes RX 8(k−1)+1 … 8k. BUSY used from ADC1 only. |
| `tx_shift.kicad_sch` | 1 | `tx_control` (74AHCT1G125 enable buffer, /OE pull-up, /SRCLR pull-down) and 16 × 74AHCT595 (QA–QD → TX_BIT1–64). |
| `tx_drivers.kicad_sch` | 1 | 32 × UCC27524 (TX_BIT → DRV1–64), 1 µF + 100 nF each, VDRV/PGND. |
| `mems.kicad_sch` | 1 | 2 × 74LVC2G34 clock fan-out (100 k pull-down, 33 Ω per pair), 8 × SPH0641LU4H-1 in 4 pairs (SEL to VDD/GND, 33 Ω on DATA, 100 nF C0G). |
| `power.kicad_sch` | 1 | See below. |
| `headers.kicad_sch` | 1 | J1 = AXU3EGB EX_IO1 (J15), J2 = EX_IO2 (J16), 2×20 IDC box headers for ribbon cables. |

**Power tree (redesigned 2026-10-19, `scripts/build_power.py`; all TI parts with downloadable datasheets, in production):** one 12 V barrel jack (5.5×2.1 mm, ≥1.5 A) powers everything.
- Input: jack → 2 A polyfuse → SS54 reverse-polarity Schottky → VIN (LED, test point).
- +5V: TPS54202 buck, TI reference design (15 µH, 100k/13.3k + 75 pF, EN UVLO 511k/105k), about 2 A available.
- ±6.5 V pre-regulator: TPS65131 (symbol TPS65130RGE, same pinout) from +5V: boost to +6.46 V (562k/130k), inverting to −6.51 V (649k/121k to VREF), 4.7 µH + SS14 each side, PSP/PSN low (fixed-frequency PWM), CP 10 nF, CN 4.7 nF, VREF 220 nF, VIN pin through 100 Ω + 100 nF.
- +5VA: TPS7A4701 low-noise LDO from +6V5, ANY-OUT 1.4 + 3.2 + 0.4 = 5.0 V (no divider), NR 1 µF.
- −5VA: TPS7A3301 low-noise LDO from −6V5, 715k/220k (VREF −1.175 V → −4.99 V), CFF 10 nF, NR/SS 1 µF.
- VDRV: LM2731X boost from VIN to 15.1 V (150k/13.3k, Cf 180 pF), 10 µH, SS14, then **J2, a 2-pin shunt header in series: remove the shunt to unpower all TX drivers** (a boost cannot switch its output off by itself), 47 µF bulk.
- +3V3: TLV1117-33 from +5V.
- Testability: every regulator output passes a bridged solder jumper (JP1 +5V, JP2 +5VA, JP3 −5VA, JP4 +3V3; cut to isolate a rail), has a test point (also on +6V5, −6V5, VIN, PGND, AGND, DGND), and an LED on VIN, +5V, ±6V5, VDRV, +3V3 (none on the analog LDO outputs).
- Grounds: switchers on PGND, analog LDOs on AGND; AGND–PGND (2 mm) and AGND–DGND (0.5 mm) net-ties. See review-full-1 finding 1 on where the ties go in layout.
- Replaced: MT3608 (no datasheet available), second TPS54202 and the paralleled LM2776 charge pumps. kicad-cli ERC on the full hierarchy: 0 violations.

**AXU3EGB header pinout** (verified 2026-10-19 against the ALINX AXU3EGB user manual REV1.0, pp. 41–42, and the schematic page "EXTEND IO"; J15 = banks 43/44, J16 = banks 45/46, same layout): pin 1, 37, 38 GND; pin 2 +5V; pins 39, 40 +3.3V; pins 3–36 = 17 P/N pairs (N odd, P even). Our board connects GND to DGND and leaves the +5V/+3.3V pins unconnected (own supplies).
- J1 (EX_IO1): TX_SCK 4, TX_LATCH 3, TX_SDI0–15 on 5–20 (P = even SDI), TX_OE_N 22, TX_CLR_N 21, TX_EN_FPGA 24, MIC_CLK_IN 26, MIC_DATA0 25, MIC_DATA1 28, MIC_DATA2 27, MIC_DATA3 30; spare 23, 29, 31–36.
- J2 (EX_IO2): ADC_SCLK 4, ADC_CS_N 3, ADC_CONVST 6, ADC_RESET 5, ADC_BUSY 8, ADC_DOUTAk on 10, 12 … 24 and ADC_DOUTBk on 9, 11 … 23; spare 7, 25–36.
- Only 4 ground pins per header: the fast clocks (TX_SCK 10 MHz, ADC_SCLK ~20 MHz, MIC clock 4.8 MHz) need a ribbon with good return paths; consider shortening the cable or adding series terminations at the FPGA.

**Local deviations from the KiCad libraries:** AD7606 pins DB0–DB6, DB9–DB15 set to passive in `adc.kicad_sch` (tied to AGND in serial mode per datasheet; the library marks them as outputs).

Rejected: Murata MA40H1S-R (end of life), Murata MA40S4S/R (not recommended for new designs), HC-SR04 modules as modules (onboard MCU adds microseconds of jitter and only outputs an echo pulse).

## 5. FPGA interface: ALINX AXU3EGB

- Two 2×20-pin 2.54 mm headers, EX_IO1 (banks 43/44) and EX_IO2 (banks 45/46). Both are **HD banks at 3.3 V**. About 17 P/N pairs per header, plus +3.3 V and GND. Source: [AXU3EGB schematic](https://e2e.ti.com/cfs-file/__key/communityserver-discussions-components-files/138/AXU3EGB_5F00_schematics.pdf), page 21 "EXTEND IO".
- HD banks: 3.3 V LVCMOS is fine. No LVDS needed for this board.
- **Pin budget is the open problem.** 64 TX lines plus 64 mode controls is far more than the headers provide. Options:
  - Shift-register latches load each ping's per-element mode and delay over a fast serial bus, with TX fired through grouped lines.
  - A small on-board FPGA/CPLD generates the 64 delayed TX waveforms locally.
- Signals that do fit: about 16 ADC serial data lines plus clocks, 4 PDM data lines plus a clock, TX control, I²C/SPI for configuration.

## 6. Open decisions

1. Transducer: CUSA-TR65 (default) or open-type T40/R40-16.
2. Per-channel front-end topology: **decided**, discrete (section 4a).
3. ADC: **decided**, 8 × AD7606B, serial, 2 DOUT lines each (section 4, pin budget in 4a). Watch the input-filter loss at 40 kHz.
4. How to drive 64 transmit lines: **decided**, 16 × 74AHCT595 frame streaming at SRCLK 10 MHz (section 4a).
5. Waveguide geometry (tube length and outlet pitch). Take it from reference 1.
6. Board split and the board-to-board connector.

## 7. References

1. Allevato, Kupnik et al. (TU Darmstadt), *Real-Time 3-D Imaging Using an Air-Coupled Ultrasonic Phased-Array*, IEEE TUFFC — 8×8 transceivers at 40 kHz, waveguide to λ/2, Zynq-7010, ±80° field of view, 0.5–3 m, 29 frames/s. [PubMed](https://pubmed.ncbi.nlm.nih.gov/32746193/), [IEEE](https://ieeexplore.ieee.org/iel7/58/9363464/09126862.pdf). Companion imaging-method paper: [d-nb.info PDF](https://d-nb.info/1214848524/34). **Closest published match to this design; read it before choosing the front end and waveguide.**
2. eRTIS (University of Antwerp): 1 transmitter + 32 SPH0641LU4H-1 MEMS mics, broadband FM sweep, 3D in-air sonar. [arXiv](https://arxiv.org/pdf/2308.05504)
3. HiRIS: 1024-channel MEMS microphone array for in-air imaging. [arXiv](https://arxiv.org/html/2402.13110v1)
4. dylanmsu/Ultrasonic_Phased_Array: open-source Zynq transmit phased array (PS sends a 7-bit phase per transducer to the fabric). Reusable TX logic. [GitHub](https://github.com/dylanmsu/Ultrasonic_Phased_Array)
5. SonicSurface (16×16 TX array, FPGA, about $200) and Ultraino (64 channels, Arduino Mega): open transmit-only levitation arrays. [SonicSurface](https://www.instructables.com/SonicSurface-Phased-array-for-Levitation-Mid-air-T/), [Ultraino paper](https://doi.org/10.1109/tuffc.2017.2769399)
