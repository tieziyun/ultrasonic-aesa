# Hybrid 8×8 Ultrasonic Transceiver Panel — Design Brief

An acoustic "AESA": a 40 kHz in-air phased array in which every element can transmit and receive under per-element FPGA control, plus a few wideband MEMS microphones. It feeds an ALINX AXU3EGB (Zynq UltraScale+ ZU3EG) board, which does the beamforming.

Status (2026-10-20): the full schematic is drawn in KiCad (`kicad/aesa.kicad_pro`, the source of truth; PDFs in `docs/schematics/`): 64 transceiver channels, 8 MEMS mics, 8 AD7606C-16 ADCs, TX drive, power and the AXU3EGB headers, 1,701 parts, ERC 0 violations. Decided: discrete front end and 74HCT595 TX drive (section 4a), AD7606C-16 in software mode (section 4), one 4-layer board (section 2), one reused sheet per repeated block (section 4b). Simulations: [sim/reports/](sim/reports/README.md) (all pass). BOM done 2026-10-22 (every part has an LCSC number, `docs/bom.md`). Next: placement.

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

### Board split (decided 2026-10-20): one board
**One 4-layer board, about 200 × 170 mm.**
- **Front:** the 64 transducers (hand-soldered through-hole) and the waveguide.
- **Back:** every SMT part, so assembly is single-sided. Each channel's front end sits directly behind its element.
- **Mics:** the 8 bottom-port MEMS mics mount on the back, with port holes through the board.
- **Clean side:** ADCs, logic and the AXU3EGB ribbon headers go along one edge; power and the 15 V drivers go along the opposite edge, all over one continuous GND plane.

Why:
- **Cost:** one PCB instead of two, one SMT setup ($25 instead of 2 × $25 at [JLCPCB](https://jlcpcb.com/help/article/pcb-assembly-price)), and no board-to-board connectors carrying 64 analog lines plus grounds.
- **Noise:** the high-impedance element node (XD, 3 kΩ source) stays a few mm long instead of crossing a connector, so there's less pickup and no connector crosstalk between neighbouring elements.
- **Industry practice:** flat ultrasonic arrays commonly put transducers and drivers on one PCB ([FPGA levitation array, arXiv 1901.07317](https://arxiv.org/pdf/1901.07317); [SonicSurface build](https://z.jpralves.net/post/2021/08/05/sonicsurface-phased-array-for-levitation-mid-air-tactile-feedback-and-target-directional-speakers.html)), and transducer-front, electronics-back is a standard arrangement ([US10315224](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10315224)).

Rejected:
- **Two-board stack** (2-layer panel plus 4-layer front end): most expensive, and puts the sensitive XD lines through a connector.
- **Hybrid** (front ends on the panel, ADC/digital on a second board): middle cost, still two boards and two assemblies.

So there is no board-to-board connector. The only cable is the ribbon to the AXU3EGB.

### Clock and data lines to the AXU3EGB (decided 2026-10-20)
Single-ended 3.3 V LVCMOS. At 10–20 MHz over a short ribbon that is the standard choice, and the AXU3EGB's 3.3 V HD banks can't drive LVDS anyway ([Xilinx XAPP230](https://www.hep.ucl.ac.uk/~mp/FPGA/Xilinx-Appl-Note-230_LVDS-Standards.pdf); [CTS termination note](https://www.CTSCorp.com/Files/Product-Marketing-Documents/Passive-Components/Frequency-Control-Products/CTS-Passive-Components-Frequency-Control-Oscillators-Termination-Techniques-Layout-Considerations-Application-Note.pdf)).
- **FPGA outputs** (all ribbon outputs, clocks and data): **DRIVE = 4 mA**, SLEW = SLOW in the XDC (changed from 8 mA after simulation, [sim/reports/clock_si_drive.md](sim/reports/clock_si_drive.md)): about 80 Ω output impedance nearly source-matches the ~100 Ω ribbon, so edges at our buffer inputs reach 3.32 V / −0.02 V instead of 3.7 V / −0.4 V at DRIVE 8. Fallback: DNP RC end terminations (100 Ω + 47 pF) on the five clock lines at the header, to be fitted only with DRIVE 8 (with DRIVE 4 they halve the initial step to ~1.8 V, too close to the 1.65 V threshold).
- **Ground conductors:** a GND pin on each side of every clock in the ribbon. Spare header pins become GND, and the FPGA leaves those pins as inputs.
- **Clock fan-out on our board:** a 74LVC2G34 buffer at the header. Each output drives at most 4 loads through its own 33 Ω series resistor, because a series-terminated line can't drive multiple loads.
- **Data back to the FPGA:** a 33 Ω series resistor at each ADC's DOUTA/DOUTB/BUSY output.
- **Cable:** keep the ribbon short, about 15 cm.

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
| ADCs | **AD7606C-16** (AD7606C-16BSTZ, decided 2026-10-20). Pin-compatible with the AD7606B; per-channel analog bandwidth 25 kHz or 220 kHz, selectable only in software mode (BANDWIDTH register 0x07). With 220 kHz and the ±5 V range (RANGE_CHx code 0x1) it recovers the ~10 dB filter loss and ~6 dB range loss of the AD7606B (fixed 22.5 kHz Butterworth, ±10 V). Software mode: OS0–OS2 high; registers written over DB11/SDI (16-bit frame: bit 14 = read, bits 13:8 address, 7:0 data; CONFIG 0x02 DOUT_FORMAT bits 4:3, RANGE 0x03–0x06, OVERSAMPLING 0x08), all 8 ADCs share SDI/CS so one write configures all. **Stocked by JLCPCB as AD7606C-16BSTZ-RL, C5240947 ($58.81 each, 2026-10-22), so it is assembled, not consigned.** It is 83 % of the assembled-parts cost (`docs/bom.md`). Fallback: AD7606B (LCSC C651284) in the same footprint. Exact BANDWIDTH bit polarity to be confirmed in the datasheet before writing the FPGA code (ADI site unreachable on 2026-10-20; register addresses from ADI's no-OS driver and the Linux ad7606 driver) | Serial, 2 DOUT lines per ADC, shared SCLK/CS/CONVST/RESET/SDI, one BUSY | [ADI product page](https://www.analog.com/en/products/ad7606c-16.html), [datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/ad7606c-16.pdf), [no-OS driver](https://github.com/analogdevicesinc/no-OS/blob/main/drivers/adc/ad7606/ad7606.h) |
| Per-channel TX + RX front end | **Discrete (approved 2026-10-19)**, see section 4a. TUSS4470 rejected: its VOUT is a log-amp envelope (no phase) | Must allow per-element mode switching (see section 2) | — |

### 4a. Front end and TX drive (approved 2026-10-19)

Reference design: Darmstadt (reference 1) uses, per 8 elements, one HV7355 pulser (20 Vpp unipolar), two TX810 T/R switches, two AD8335 VGAs and one AD7761 ADC. We keep its signal chain (unipolar square-wave drive, raw-waveform capture at 200 kSPS) but use cheaper parts that are in stock at LCSC, because our drive voltage is low (≤ 18 V).

**Per channel** (`skidl/channel.py`):
- TX: half of a **UCC27524** dual gate driver (LCSC C465729), VDRV = 15 V nominal (18 V max) → about 14 Vpp at the element. 47 Ω series resistor, then a **BAV99 crossed-diode pair**. While the driver output is at GND the diodes are off, so the element is isolated from the driver and listens. Ringing above about 0.6 V is damped through the diodes.
- Element: CUSA-TR65, 2200 pF, 3 kΩ at resonance. 33 kΩ bleed to GND (73 µs recovery after transmit). Footprint `aesa:CUSA-TR65` (`kicad/aesa.pretty`): 5 mm pitch, 1.0 mm drills; pin 2 is the ground/can lead. The datasheet doesn't say which lead is the can, so check it with a meter.
- RX protection: 4.7 kΩ series + BAV99 clamp to GND (±0.77 V, 3 mA during transmit; 1 kΩ would dissipate 94 mW in CW mode), then 100 pF to GND (RF filter, 340 kHz). Stage 1 has 33 pF across its 10 k feedback resistor (roll-off near 480 kHz).
- Filter parts: 1 % resistors, 1 nF C0G 1 % capacitors (5 % parts would give up to ±11° phase spread between channels).
- Ground: **one GND net and one continuous plane** (TI SLYT512, ADI layout guidance). Drive, analog and logic sections are separated by placement; each DRVn/XDn returns over unbroken plane to its own driver. No net-ties.
- RX gain: **OPA1678** dual (LCSC C192421) on ±5 V. Stage 1 non-inverting ×6, AC-coupled at 800 Hz. Stage 2 multiple-feedback band-pass, f0 = 39.8 kHz, Q = 2, gain −4. Total −24 (27.6 dB), output single-ended through 100 Ω to the ADC. Digital TGC in the FPGA instead of Darmstadt's analog VGA.
- About 16 parts per channel.

**MEMS mics** (fixed requirement, hybrid panel): 4 pairs (`mems_mic_pair`) along the grid edge, 4 DATA lines + 1 clock. Each mic has a 33 Ω series resistor on DATA and a 100 nF C0G bypass (1206: no 100 nF C0G 0805 is stocked at JLCPCB). The clock is fanned out on the panel by 2× 74LVC2G34, one output per pair through 33 Ω, with a 100 k pull-down on the input so the mics see no clock while the FPGA configures.

**Transmit lines** (shift-register frame streaming):
- **16 data lines**, one **74HCT595D** (Nexperia, LCSC C282339, 5 V, TTL inputs, fmax 57 MHz) per 4 elements on QA–QD (QE–QH unused), plus shared SRCLK, RCLK (latch), /OE and /SRCLR. The user chose 16 lines × 4 bits over 8 × 8 to halve the shift clock; it costs 8 more 595s (16 in total) and 8 more FPGA pins.
- The FPGA shifts a 64-bit frame (4 bits per line) and latches it at 2.5 MHz (**SRCLK 10 MHz**). All 64 outputs update on the same RCLK edge, so each element's waveform is any 1-bit pattern with 0.4 µs resolution (1/62 of a 40 kHz period, about 6°).
- Per-element mode falls out of the bit stream: transmit = toggle the bit, listen = hold 0. This supports split mode and continuous wave with no extra mode lines.
- Safety (`tx_control`, once per board): the UCC27524 EN pins have 200 kΩ internal pull-ups to VDRV (datasheet), so they must never connect to an FPGA pin. Four 74AHCT1G125 on +5 V drive them, one per row of 8 drivers (TX_EN1–4), all fed from TX_EN_FPGA with one 10 k pull-down to GND. /OE has a 10 k pull-up to 3.3 V (595 outputs high-Z, driver inputs pulled low) and /SRCLR a 10 k pull-down. The drivers stay off until the FPGA enables them.
- VDRV: 47 µF bulk capacitance; size the supply for ≥ 0.25 A.
- Upgrade path if more range is needed: HV7355 (LCSC C633698, up to 150 V, needs ±5 V and VPP) in the same position; it also has its own per-channel serial enable latch.

**FPGA pin budget** (single-ended 3.3 V LVCMOS, about 68 pins on the two headers):

| Function | Pins |
|---|---|
| TX: 16 serial data + SRCLK + RCLK + /OE + /SRCLR + TX_EN | 21 |
| ADC: 8 × AD7606C-16, 2 DOUT each (16) + SCLK + CS + CONVST + RESET + BUSY + SDI (shared register-write line, software mode). 64 bits per line per sample at 200 kSPS = 12.8 Mbit/s, so SCLK about 20 MHz. | 22 |
| MEMS mics: 4 PDM data + 1 clock | 5 |
| I²C (config, power monitor) | 2 |
| **Total** | **about 50** (plus 27 header pins tied to GND as clock returns) |

### 4b. KiCad schematic (updated 2026-10-20)

Project `kicad/aesa.kicad_pro`. Three levels, all generated by `scripts/gen_top.py` except the cells:
- **Top sheet** `aesa.kicad_sch`: a block diagram of the signal flow (FPGA → headers → clock buffers / TX drive → transceivers → ADCs → headers → FPGA, plus mics and power). Its blocks are sheet symbols: click one to open it.
- **Group sheets**: `tx.kicad_sch` (16 × tx_group + 4 × en_buf), `array.kicad_sch` (64 × channel), `adcs.kicad_sch` (8 × adc), `mics.kicad_sch` (8 × mic), `clocks.kicad_sch` (7 × clk_buf). They place the cells and join their ports to global labels.
- **Cell sheets** (below), plus `power` and `headers` directly under the top sheet.

| Sheet | Instances | Contents |
|---|---|---|
| `channel.kicad_sch` | 64 (CH01–CH64) | One transceiver channel, wired: 47 Ω + BAV99 crossed diodes, CUSA-TR65 element, 33 k bleed, 4.7 k + BAV99 clamp, OPA1678 ×6 stage and 40 kHz MFB band-pass, 100 Ω out. Ports DRV_OUT (from the driver) and RX_OUT (to the ADC). The transducer stays in this sheet (one board). |
| `adc.kicad_sch` | 8 (ADC1–ADC8) | AD7606C-16, **software mode** (OS0–OS2 high), serial, 2 DOUT, internal reference; pin 10 (WR) held high; DB11/SDI (pin 29) on global ADC_SDI; 33 Ω source resistors on DOUTA/DOUTB/BUSY; decoupling per datasheet. Ports IN1–8, SCLK, CONVST, DOUTA, DOUTB, BUSY. ADC k takes RX 8(k−1)+1 … 8k; ADC1–4 on ADC_SCLK_A/ADC_CONVST_A, ADC5–8 on _B. BUSY used from ADC1 only. |
| `tx_group.kicad_sch` | 16 (TXG01–TXG16) | One 74HCT595D (QA–QD) + two UCC27524 (1 µF + 100 nF each): drives DRV 4(k−1)+1 … 4k. Ports SDI (TX_SDIk−1), SCK, LATCH (TX_SCK_A–D / TX_LATCH_A–D, 4 groups per buffered clock), EN (TX_EN1–4, 4 groups each), DRV1–4. TX_OE_N, TX_CLR_N global. |
| `en_buf.kicad_sch` | 4 (TXEN1–4) | 74AHCT1G125, TX_EN_FPGA → TX_ENj (8 drivers, 16 EN pins each). |
| `clk_buf.kicad_sch` | 7 (CLKB1–7) | 74LVC2G34 with both gates on IN, 33 Ω per output, 100 k pull-down on IN. TX_SCK → A/B, C/D; TX_LATCH → A/B, C/D; ADC_SCLK → A/B; ADC_CONVST → A/B; MIC_CLK_IN → MIC_CLK_A (mics 1–4) / B (5–8). Each output ≤ 4 loads. |
| `mic.kicad_sch` | 8 (MIC1–8) | SPH0641LU4H-1, 100 nF C0G 1206, 33 Ω on DATA. SEL from the top sheet: odd mics 3V3_MIC, even mics GND; mics 2k−1 and 2k share MIC_DATAk−1. |
| `power.kicad_sch` | 1 | See below. |
| `headers.kicad_sch` | 1 | J1 = AXU3EGB EX_IO1 (J15), J2 = EX_IO2 (J16), 2×20 IDC box headers for ribbon cables; idle pulls R1–R5. |

**Rule (user, 2026-10-20): every block that repeats has its own sheet, instantiated N times, so there is one sheet to change.** `channel` is drawn with the MCP server; `adc`, `tx_group`, `en_buf`, `clk_buf`, `mic`, `headers` and `power` are generated by `scripts/build_cells.py`; the top, group sheets and references by `scripts/gen_top.py`. Reference numbering: CH n → R n01…, ADC k → U7k01…, TXG k → U1(00+k)01…, TXEN j → U12j01, CLKB j → U13j01, MIC m → MK14m01, power 95xx, headers 99xx.

**Power tree (reviewed and redrawn 2026-10-20, generated by `scripts/build_cells.py`; all TI parts with downloadable datasheets, in production):** one regulated 12 V adapter on a 5.5×2.1 mm barrel jack (≥1.5 A; LM2731 VIN max 14 V) powers everything. Each rail: regulator node `*_REG` → bridged solder jumper (cut to isolate) → rail with test point, LED (not on the analog LDO outputs) and PWR_FLAG.
- Input: jack → SMD1812P200TF16 polyfuse (2 A hold, **16 V**) → SS54 (SMA) reverse-polarity Schottky → VIN (C 100 µF + 100 nF, LED, TP).
- +5V: TPS54202 buck, TI reference design (L 15 µH Bourns SRN8040TA-150M, Isat 4 A; 75k/10k + 100 pF → 5.07 V, EN UVLO 510k/100k ≈ 7.3 V; JLCPCB Basic resistor values), input 10 µF 50 V X7R 1210, 2 × 22 µF out; JP1. Load ≈ 1.4 A of 2 A.
- ±6V5 pre-regulator: **TPS65131RGER** (drawn with the pin-identical TPS65130RGE symbol; the TPS65130 has only 800 mA switches, so order the TPS65131) from +5V: boost to +6.46 V (562k/130k + 6.8 pF), inverter to −6.51 V (649k/121k to VREF + 7.5 pF), 4.7 µH (Isat ≥ 2 A) + SS14 each side, PSP/PSN low (fixed-frequency PWM), CP 10 nF, CN 4.7 nF, VREF 220 nF, VIN pin through 100 Ω + 100 nF, input 2 × 10 µF 16 V X7R 1206. **New JP5/JP6** isolate +6V5/−6V5 from the LDOs.
- +5VA: TPS7A4700RGWT low-noise LDO (the TPS7A4701RGWR is out of stock at JLCPCB; same pinout, ANY-OUT identical) from +6V5, ANY-OUT 1.4 + 3.2 + 0.4 = 5.0 V, NR 1 µF, 2 × 10 µF out; JP2. Load ≈ 0.45 A (64 × OPA1678 at ~4 mA per dual + 8 × AD7606C AVCC); 0.7 W in the LDO, thermal vias under the pad.
- −5VA: TPS7A3301 low-noise LDO from −6V5, 715k/220k → −5.00 V, CFF 10 nF, NR/SS 1 µF, 22 µF out; JP3. Load ≈ 0.3 A.
- VDRV: LM2731X boost from VIN to 15.4 V (150k/13k, Cf 180 pF), 10 µH (Isat ≥ 2 A), SS14, 2 × 10 µF 50 V out, then **J2, a 2-pin shunt header: remove the shunt to unpower all TX drivers**, 47 µF bulk. Worst-case load, all 64 elements in CW (simulated, `sim/reports/tx_cw.md`): 3.7 mA per element, ≈ 0.24 A: C·V²·f is only 1.3 mA; the rest goes into the 47 Ω, the acoustic load and the RX limiter (4.7 kΩ dissipates 19 mW whenever its own element transmits).
- +3V3: **TLV75733P** LDO from +5V (replaces the TLV1117-33, whose output needs ≥ 10 µF with an ESR window; the TLV757P is stable with ceramics). Load < 0.15 A; JP4.
- 3V3_MIC: +3V3 → 600 Ω@100 MHz ferrite GZ1608D601TF (FB1, remove to isolate) → 10 µF, LED, TP.
- Ground: one GND net and plane; TP9/TP10 for scope grounds.
- Inductors: L1 Bourns SRN8040TA-150M (Isat 4 A); L2/L3 Sunlord SWPA6045S4R7MT (Isat 5.5 A); L4 Sunlord SWPA6045S100MT (Isat 3.5 A); footprints match.
- **BOM (2026-10-22):** every part has an LCSC number, chosen by `scripts/bom.py` (Basic parts first); costed BOM in `docs/bom.md`, upload file `fab/bom_jlcpcb.csv`.

**ADC and mic additions (group 1, 2026-10-19; updated 2026-10-20):** ADC_CS_N 10 k pull-up to +3V3, ADC_RESET and ADC_SDI 10 k pull-downs (headers sheet); ADC_CONVST and all other clocks are held low by the 100 k in their clk_buf. ADC REFCAP is 10 µF 16 V X7R 1206 (DC-bias derating). The 8 mics run from 3V3_MIC: +3V3 → 220 Ω@100 MHz ferrite → 10 µF, with LED, test point and flag (power sheet section H; remove FB1 to isolate).

**AXU3EGB header pinout** (verified 2026-10-19 against the ALINX AXU3EGB user manual REV1.0, pp. 41–42, and the schematic page "EXTEND IO"; J15 = banks 43/44, J16 = banks 45/46, same layout): pin 1, 37, 38 GND; pin 2 +5V; pins 39, 40 +3.3V; pins 3–36 = 17 P/N pairs (N odd, P even). Our board connects GND to DGND and leaves the +5V/+3.3V pins unconnected (own supplies).
- J1 (EX_IO1, updated 2026-10-20): TX_SDI0–15 on 5–20 (P = even SDI), TX_CLR_N 21, TX_OE_N 22, TX_EN_FPGA 24, MIC_DATA0 25, MIC_DATA2 27, MIC_DATA1 28, MIC_DATA3 30, **TX_SCK 32, MIC_CLK_IN 34, TX_LATCH 36**; GND 1, 3, 4, 23, 26, 29, 31, 33, 35, 37, 38; open 2, 39, 40.
- J2 (EX_IO2, updated 2026-10-20): ADC_RESET 5, ADC_SDI 7, ADC_BUSY 8, ADC_DOUTBk on 9, 11 … 23, ADC_DOUTAk on 10, 12 … 24, **ADC_SCLK 32, ADC_CONVST 34, ADC_CS_N 36**; GND 1, 3, 4, 6, 25–31, 33, 35, 37, 38; open 2, 39, 40.
- On a flat ribbon each clock conductor (32, 34, 36) has GND on both neighbours (31/33, 33/35, 35/37). The FPGA pins wired to GND must be left unused (inputs) in the XDC.

**Local deviations from the KiCad libraries:** `adc.kicad_sch` uses the AD7606 symbol (same LQFP-64 pinout as the AD7606C-16; pin names are the AD7606's: pin 10 "CONVST_B" is WR, pin 29 "DB11" is DB11/SDI). DB0–DB6, DB9, DB10, DB12–DB15 set to passive (tied to GND), DB11 set to input (SDI). 74HCT595D and 74AHCT1G125 are placed from their base symbols 74HC595 / 74LVC1G125 (identical pinout) because the MCP loader breaks on `extends`.

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
3. ADC: **decided 2026-10-20 (user chose option B)**: 8 × AD7606C-16 in software mode, serial, 2 DOUT lines each, ±5 V range and high (220 kHz) bandwidth set by register writes at boot. Recovers about 16 dB of SNR at 40 kHz against the AD7606B (fixed 22 kHz filter, ±10 V). Pin-compatible, so the AD7606B (LCSC C651284) remains a drop-in fallback in hardware mode only if OS pins are re-strapped. JLCPCB stocks it (C5240947), see section 4.
4. How to drive 64 transmit lines: **decided**, 16 × 74HCT595D frame streaming at SRCLK 10 MHz (section 4a).
5. Waveguide geometry (tube length and outlet pitch). Take it from reference 1.
6. Board split: **decided 2026-10-20**, one 4-layer board (section 2).

## 7. References

1. Allevato, Kupnik et al. (TU Darmstadt), *Real-Time 3-D Imaging Using an Air-Coupled Ultrasonic Phased-Array*, IEEE TUFFC — 8×8 transceivers at 40 kHz, waveguide to λ/2, Zynq-7010, ±80° field of view, 0.5–3 m, 29 frames/s. [PubMed](https://pubmed.ncbi.nlm.nih.gov/32746193/), [IEEE](https://ieeexplore.ieee.org/iel7/58/9363464/09126862.pdf). Companion imaging-method paper: [d-nb.info PDF](https://d-nb.info/1214848524/34). **Closest published match to this design; read it before choosing the front end and waveguide.**
2. eRTIS (University of Antwerp): 1 transmitter + 32 SPH0641LU4H-1 MEMS mics, broadband FM sweep, 3D in-air sonar. [arXiv](https://arxiv.org/pdf/2308.05504)
3. HiRIS: 1024-channel MEMS microphone array for in-air imaging. [arXiv](https://arxiv.org/html/2402.13110v1)
4. dylanmsu/Ultrasonic_Phased_Array: open-source Zynq transmit phased array (PS sends a 7-bit phase per transducer to the fabric). Reusable TX logic. [GitHub](https://github.com/dylanmsu/Ultrasonic_Phased_Array)
5. SonicSurface (16×16 TX array, FPGA, about $200) and Ultraino (64 channels, Arduino Mega): open transmit-only levitation arrays. [SonicSurface](https://www.instructables.com/SonicSurface-Phased-array-for-Levitation-Mid-air-T/), [Ultraino paper](https://doi.org/10.1109/tuffc.2017.2769399)
