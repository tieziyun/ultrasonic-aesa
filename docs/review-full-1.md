# Full-schematic review 1 (power sheet excluded)

Date: 2026-10-19. Reviewer: schematic-reviewer subagent, against `kicad/aesa.kicad_pro` and `build/aesa.net`. `power.kicad_sch` was skipped because it is being redesigned. Status of each item is tracked at the end.

## Passed
- 1,459 references, all unique.
- All 64 channel sheets are identical to CH01 and match `skidl/channel.py` `tx_rx_channel`, including fixes B1, B2 and F1–F6 from review 1.
- DRVn has two endpoints: UCC27524 OUTx and CHn R_DRV.
- TX_BITn connects a 595 Q pin to the IN pin of the same driver half that produces DRVn. TX_SDIk feeds QA–QD, which are TX_BIT 4k+1 … 4k+4.
- RXn goes to ADC ⌈n/8⌉ input V((n−1) mod 8 + 1).
- Every driver has EN A/B on TX_EN, GND on PGND and VDD on VDRV.
- The only single-node nets are the intended `/ADC2–8/BUSY` and no-connect pins.
- AD7606B strapping and decoupling match the datasheet.
- The MEMS sheet is correct: SEL alternates VDD/GND, 33 Ω per mic on shared DATA, 74LVC2G34 pinout, 100 k pull-down, C0G bypass, port-hole footprint.
- Both header pinouts match brief section 4b.

## Blockers
1. **TX return current flows through AGND.** The element's return, the bleed resistor and the clamp are on AGND, while the drivers are on PGND. The AGND/PGND net-tie is at the power entry, so up to about 19 A peak of edge current crosses the analog area. The AGND/DGND tie is also far from the ADCs.
   - Fix: use one continuous ground plane on the 4-layer board and separate by placement. If the net-ties stay, put them at the driver bank and at the ADCs/header. Route each DRVn/XD with its own return.
2. **1,175 parts have LCSC "TBD".** This includes the 74AHCT595D, which has no LCSC listing; the 74HCT595D (C282339) is an acceptable fallback at 10 MHz. Also check that the 100 nF 16 V C0G 0805 exists.
3. **The board split is not in the schematic.** Add the array-panel connector (64 XD lines alternating with ground, mic DATA/CLK, power) before placement.

## Fix before order
4. **The ADC symbol is the AD7606, not the AD7606B.** Pin 10 may be WR on the AD7606B, not CONVST B (unverified). Re-pin to the AD7606B table.
5. **Clocks over the ribbon have no buffers or termination.** TX_SCK and TX_LATCH have 16 loads each; ADC_SCLK has 8. There are only 3 grounds per header (pins 1, 37, 38).
   - Buffer the clocks at the header, 22–33 Ω per output.
   - Add source resistors on DOUTA/B and BUSY.
   - Turn spare pins next to the clocks into grounds.
   - Set SLEW=SLOW and DRIVE=8 on the FPGA side.
6. **The AXU3EGB pinout is unverified,** including whether J16 = J15. Check before ordering.
7. **One TX_EN buffer drives 64 EN pins** (about 4.8 mA, within spec, but against the ≤16-driver note). Use 2–4 buffers or a 74AHCT125.

## Nice to have
8. **The ADC noise dominates:** about 32 µV rms referred to the input, against about 2 µV from the front end. Keep ±10 V, and consider ×2–4 oversampling or a stage-1 gain of 12.
9. **Pull-downs on ADC_RESET and ADC_CONVST, pull-up on ADC_CS_N,** so the ADCs sit idle while the FPGA configures.
10. **REFCAP 10 µF 10 V X5R 0805 derates to about 5 µF at 4.4 V.** Use 16 V in 1206, or X7R.
11. **Still open from review 1:** N1, N2, N6.
12. **Power-up order:** fine as is, because TX_EN's pull-down keeps the drivers off.

## Status
- Power sheet: being redesigned (2026-10-19), review separately.
- All other items: open.
