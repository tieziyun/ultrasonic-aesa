# Schematic review: channel 1 (tx_rx_channel + mems_mic_pair)

Recovered on 2026-10-19 from the schematic-reviewer subagent's final report in the earlier session (the report was delivered 5 times by a Claude Code bug; this is the final version, kept once). **Status 2026-10-19:** B1, B2 and F1–F6 applied in `skidl/channel.py` and `skidl/hybrid_test.py`; B1's 200 kΩ EN pull-up confirmed in the TI datasheet. N1–N7 left for later.

---

## Schematic review: hybrid panel test build (channel.py, hybrid_test.py, build/hybrid_test.net)

The pin mappings you asked about are all correct (BAV99, UCC27524, OPA1678, SPH0641). The schematic is not ready to order: there are 2 blockers, the TX_EN line and the missing LCSC numbers, plus several fixes. I did not edit any project files.

**ERC.** kicad-cli ERC was not run because there is no `.kicad_sch` anywhere in the repo yet; it needs a generated schematic first. The SKiDL ERC passes with 0 errors, but 14 nets are excluded from it with `do_erc=False` (TX_BIT1–7, the TX and mic control lines, RX0, TX_EN, MIC_CLK_IN). I regenerated `C:\GitHub\ultrasonic-aesa\build\hybrid_test.net`; it matches the old one apart from sheet order, and the old copy is in the scratchpad.

### Checked and correct
- **BAV99 (Q1):** the KiCad symbol's pin *names* are wrong (K, A, K), but its drawing and numbers match the Nexperia pinout: pin 1 = anode D1, pin 3 = common, pin 2 = cathode D2.
  - `d_iso` (DRV on pins 1+2, XD on pin 3) is a true anti-parallel pair.
  - `d_clamp` (LIM on pin 3, GND on pins 1+2) is two opposite diodes to GND, as intended.
- **UCC27524D pins (Q2):** 1 ENA, 2 INA, 3 GND, 4 INB, 5 OUTB, 6 VDD, 7 OUTA, 8 ENB. Matches TI.
  - 74HCT595 5 V logic into the inputs at VDD 15 V is fine; the input thresholds don't depend on VDD.
  - Unused half (INB to GND, OUTB open) is fine.
  - Peak current is about 14.3 V / 47 Ω ≈ 0.30 A, decaying with τ ≈ 100 ns. The driver is rated 5 A; the BAV99 is rated 500 mA repetitive and 4 A at 1 µs. Average diode current at 40 kHz is about 1.3 mA. All fine.
  - The enable wiring is a blocker; see B1.
- **OPA1678 (Q3):** inherits the LM2904 pinout. The netlist confirms unit A = pins 1/2/3 (S1, FB1, LIM), unit B = 7/6/5 (OUT2, INV2, GND), power = pins 4 and 8.
  - ±5 V is within the supply range, and LIM (±0.85 V) stays inside the input range.
  - Stage 1 (gain 6, unity-gain-stable amp) is stable.
  - The MFB values work out: f0 = 39.8 kHz, Q = 2.0, gain −4. Open-loop gain at 40 kHz is about 400, far above 2Q² = 8, so stage 2 is fine.
  - Noise referred to the input is about 8 nV/√Hz from the amplifier and resistors (OPA1678 4.5, R_LIM 4.1, 10k‖2k 5.2). That is about 1.4 µVrms in band, negligible against a 120 mV echo.
- **Q4, RX loading:** the crossed diodes add about 3 pF and roughly 17 MΩ (small-signal) across XD, negligible next to the 2200 pF element. The idle driver output (held low) does not load RX for echoes below about 0.3 V. Recovery after transmit is slower than needed; see N1.
- **SPH0641LU4H-1 (Q5):** symbol and footprint agree (1 DATA, 2 SEL, 3 GND ring, 4 CLOCK, 5 VDD). The footprint is drawn from the SPH0641 datasheet and has a 0.5 mm non-plated port hole, which JLCPCB can drill.
  - SEL tied directly to VDD or GND is fine. The 33 Ω series resistor at each mic DATA output is right for a shared tri-stated line.
  - The clock fan-out is not fine; see F4.
- **Q6, 64× replication:** each channel is electrically independent and the parts copy correctly (tested with 3 instances in the scratchpad). The problems are the single shared ground (F3) and net naming (N5).

### Blockers

**B1. TX_EN, U2 pins 1/8, R1: the line is pulled toward 15 V and lands on an FPGA pin.**
- The UCC27524 ENA/ENB pins have internal pull-ups to VDD (15 V), about 200 kΩ each from memory (please confirm in the datasheet).
- Test build: 100 kΩ to 15 V against the 10 k pull-down puts TX_EN at about 1.4 V, inside the enable threshold band, so "off" is not guaranteed.
- Full board: 32 drivers make 64 pull-ups, about 3.1 kΩ to 15 V. TX_EN then floats to about 11 V while the FPGA is unconfigured or high-Z, so the drivers turn on. That 11 V also lands on a 3.3 V HD-bank pin (absolute max is VCCO + 0.55 V). When the FPGA drives high, about 4 mA flows back into its 3.3 V supply.
- **Fix:** buffer TX_EN with a 5 V gate, e.g. one section of a 74HCT125 or 74AHCT1G125 on +5V. Put a 10 k pull-down on the buffer *input* on the FPGA side, and let the buffer output drive all EN pins (one buffer per 8–16 drivers). Never route EN to a header pin directly.

**B2. LCSC numbers never reach the netlist (every part).**
- `LCSC=...` in `Part(...)` and template calls only sets a Python attribute. SKiDL 3.0's KiCad 10 netlist writer emits only `part.fields` (`skidl/tools/kicad10/gen_netlist.py` lines 191–208).
- `grep -c LCSC build/hybrid_test.net` returns 0. The "consigned" and "hand-solder" marks are lost too.
- **Fix:** in `R()`, `C()` and for every `Part`, set `p.fields["LCSC"] = lcsc` after creating it. Template copies carry `fields` over (part.py line 705).

### Fix before order

**F1. U1 (74HCT595) has no defined state at power-up (TX_OE_N, TX_CLR_N).**
- The register powers up with random contents and /OE floats, so random elements can be driven to 14 V before the FPGA configures. B1 makes this worse.
- **Fix:** 10 k pull-up on TX_OE_N, and optionally a pull-down on TX_CLR_N. Pull up to **+3V3, not +5V**: the nets go to FPGA pins, and HCT inputs (VIH 2.0 V) work fine at 3.3 V. The brief puts this pull-up at group level, but it should be in the test build too.
- The UCC27524 inputs have internal pull-downs, so with /OE high the drivers sit low.

**F2. R_LIM (1 k, 0603, R4) runs hot and dominates the VDRV budget.**
- While the driver is high, 13.7 mA flows from XD through R_LIM into the clamp: 0.19 W peak. In CW mode (50 % duty) that averages 94 mW, the full rating of an 0603. A bit stuck at 1 gives 0.19 W continuous.
- Across 64 channels in CW, about 0.44 A of VDRV (6 W) is burned in R_LIM, against about 80 mA for actually charging the elements.
- **Fix:** raise R_LIM to 4.7 kΩ: 3 mA, 43 mW peak, clamp at about 0.77 V. Noise rises to about 11 nV/√Hz, still negligible against the echo levels. Alternatively keep 1 k but use a 1206.
- Size the VDRV supply for at least 0.25 A after this change (0.55 A with 1 k), and put 47–100 µF bulk on the VDRV rail.

**F3. One GND for everything (net GND).**
- TX return edges (64 × 0.3 A at once, about 19 A peak for 100 ns), the analog reference (U3 pin 5, clamp, R3, bleed, element return), digital and the mics all share one net.
- **Fix:** split into PGND (UCC27524 GND, VDRV decoupling), AGND (op-amps, clamp, MFB, element returns, ADC) and DGND, joined at one point with KiCad net-ties. That lets layout keep the edge currents out from under the analog sections, as CLAUDE.md requires.

**F4. Mic clock fan-out (R11, MIC_CLK).**
- One 33 Ω resistor "at the panel connector" is not at the source; the source is the FPGA, two connectors away.
- It drives a multi-drop line of roughly 400 mm around the panel edge with 8 loads. A 1–2 ns LVCMOS edge on that line will ring, and the mics can double-clock.
- **Fix:** add a buffer on the panel, e.g. 74LVC2G34 or 74LVC1G125 × 2–4 on +3V3. Each output drives at most 2–4 mics on a short trace through its own 22–33 Ω series resistor at the buffer output.
- Add a 100 k pull-down on MIC_CLK so the mics see no clock while the FPGA configures, which keeps them out of ultrasonic mode at power-up.

**F5. Missing passive specs: voltage, tolerance and dielectric.**
- C2 and C3 sit on 15 V VDRV: specify 25 V or higher, with the 1 µF in 0805 X7R so DC bias doesn't strip its capacitance.
- MFB R1/R2/R3: specify 1 %.
- C7/C8 (1 nF C0G): use 1–2 % parts. With ±5 % caps, f0 can move ±5 %, which at Q = 2 shifts the 40 kHz phase by up to about ±11° per channel. The "a few degrees" comment in channel.py only holds for 1 % caps.

**F6. LS1 footprint is `TBD:CUSA-TR65`.**
- Make the footprint before ordering the panel.
- Check which lead connects to the metal can and tie that lead to GND (currently pin 2), for shielding.

### Nice to have

**N1. RX recovery after transmit (R3 bleed, 100 k).**
- After a falling edge, XD stops at the D2 knee, about +0.4–0.6 V, then decays with τ = 100 k × 2.2 nF = 220 µs. For the first about 0.5 ms, the positive half of the ringing and early echoes is clipped and damped through D2.
- **Fix:** a 22–33 k bleed brings τ to about 50–70 µs at a cost of about 0.6 mA of TX load.
- Ringing above 0.6 V is damped well through the 47 Ω; below that it decays only at the element's natural Q, about 1 ms to the noise floor. Active damping through the diodes also can't go below about 0.6 V.

**N2. RF and anti-alias filtering.**
- Stage 1 is flat at gain 6 up to about 2.7 MHz, so SRCLK, latch and PDM-clock pickup is amplified. Add about 33 pF across R_F1 (pole near 480 kHz) and about 100 pF from LIM to GND (1.6 MHz with 1 k).
- The MFB alone gives only about −12.7 dB at 100 kHz and −17.6 dB at 160 kHz, which aliases onto 40 kHz at 200 kSPS. That is fine with a sigma-delta ADC such as the AD7761 (digital decimation filter); a SAR ADC needs an extra filter stage.
- The single-ended ±3 V output will need a differential driver for the AD7761. This depends on the ADC choice.

**N3. Gain and clipping.**
- In split mode, a listening element's crossed diodes conduct once crosstalk on XD exceeds about 0.5 V. Keep connector and trace coupling between XD lines low: alternate XD and GND pins on the board-to-board connector. 1 pF of coupling already puts about 6 mV of in-band 40 kHz into a neighbour's XD.
- With total gain 24, echoes inside about 0.4 m clip at the ADC, which is acceptable.

**N4. 74HCT595 at SRCLK 20 MHz.** The HCT595's specified minimum fmax is about 20 MHz over −40 to 85 °C (from memory, so check the datasheet), which leaves no margin. Use 74AHCT595 or lower SRCLK.

**N5. Net names when instantiated 64×.**
- In the 3-instance test, SKiDL renames duplicates inconsistently: `DRV`, `DRV1`, `DRV2` and `LIM1`, `LIM2`, but `S1_1`, `FB1_1`, `N2_2`, `OUT2_1`. The suffix doesn't match the channel tag (DRV1 is ch1, S1_1 is also ch1).
- Net-class rules (HV drive clearance, analog inputs) and debugging will suffer.
- **Fix:** pass the channel index and name nets `f"DRV{i}"`, `f"LIM{i}"` and so on. Also chase the "Missing tag on (blank)" warning so annotation stays stable.

**N6. Mics.**
- Give the mics their own filtered supply (ferrite or 10 Ω plus a C0G cap) rather than the digital +3V3 net.
- Check the edge claim in the docstring (SEL = VDD gives data valid on the rising edge) against the datasheet's timing figure before writing the FPGA PDM capture.
- Optionally add a weak pull-down or bus-keeper on each MIC_DATA line.

**N7. Not designed yet, so nothing to check:** power section (separate rails, reverse-polarity and inrush protection on VDRV), ADC, and the FPGA headers. With B1 fixed, the only signals that will reach FPGA pins are 3.3 V ones.

### Files
- C:\GitHub\ultrasonic-aesa\skidl\channel.py
- C:\GitHub\ultrasonic-aesa\skidl\hybrid_test.py
- C:\GitHub\ultrasonic-aesa\build\hybrid_test.net (regenerated)
- C:\Users\Tiezi\AppData\Local\Temp\claude\C--GitHub-ultrasonic-aesa\147ee4bb-ff4f-4730-9ae6-a91deae08ed7\scratchpad\ (old.net, multi.py and multi.net from the 3-channel test, nets2.py netlist parser)
