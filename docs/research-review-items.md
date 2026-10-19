# Standard answers for the review-full-1 items

Date: 2026-10-19. For each open item: what the references say, then what it means for this board. Sources are listed per item. "Not found" means the search did not turn up an authoritative source; those claims still need checking against the primary document.

## 1. TX return current through AGND (blocker)

**What the references say**
- Use **one continuous ground plane and separate by placement (partitioning)**. Split planes are a secondary option, and any signal crossing a split makes a large return loop. If planes are split, join them at one point at the converter ([TI SLYT512](https://ti.com/lit/pdf/SLYT512); ADI guidance via [Electronic Design](https://www.electronicdesign.com/technologies/analog/article/21263439/analog-devices-basic-guidelines-for-mixed-signal-pcb-layout-design); [TI E2E FAQ, high-resolution ADC layout](https://e2e.ti.com/support/data-converters/f/data-converters-forum/755516/faq-pcb-layout-guidelines-and-grounding-recommendations-for-high-resolution-adcs)).
- For gate drivers, keep the turn-on/turn-off loop tiny, with the bypass capacitor at the driver and the source and return run side by side. Keep the high-current return separate from the logic-signal return, so ground bounce doesn't false-trigger the inputs ([TI UCC27528 layout guidelines](https://www.ti.com/document-viewer/UCC27528-Q1/datasheet/layout); [TI LMG1020 ground bounce](https://www.ti.com/document-viewer/lit/html/SNOSD45B/handling-ground-bounce-t4793309-3); [Allegro AN296278](https://www.allegromicro.com/-/media/files/application-notes/an296278-pcb-layout-of-motor-gate-drivers.pdf?sc_lang=en)).

**What this means here**
- Merge AGND, PGND and DGND into **one GND net and one solid plane** on the 4-layer board, and remove the two net-ties. Keep the regions apart by placement: drivers and VDRV on one side, analog front end and ADCs on the other, logic next to the header.
- The pulse current returns through the element. Each DRVn/XDn trace should run over unbroken plane straight back to its own driver, with no route through the analog region.
- Gate-driver bypass capacitors go right at each UCC27524.

## 2. LCSC numbers, 74AHCT595 not on LCSC (blocker)

**What the references say**
- 74HCT595D (Nexperia, LCSC C282339): fmax is listed as **57 MHz** in Nexperia's parametric table ([Nexperia 74HC/HCT595](https://nexperia.com/group/74hc595-74hct595)).
- The guaranteed minimum at 4.5 V was not found; check it in the Nexperia datasheet's AC table.

**What this means here**
- At SRCLK = 10 MHz the 74HCT595 has wide margin, and its inputs accept 3.3 V logic. Switch back to 74HCT595D / C282339.
- The other 1,175 TBD parts are a parts-sourcer job, not a design question.

## 3. Panel-to-front-end connector missing (blocker)

**What the references say**
- More ground pins give better performance at the cost of density. Samtec's crosstalk tests use G-A-G-Q-G (ground between every signal) as the best case and G-A-Q-G as the worst ([Samtec MEC6 characterization](https://suddendocs.samtec.com/testreports/characterization-details_mec6-dv_web.pdf)).
- No analog-specific ratio was found.

**What this means here**
- Each XDn carries a 15 V TX edge at one moment and a microvolt echo the next, so neighbouring channels must not couple; about 1 pF already injects mV (review N3). Use **signal-ground alternating (1:1)** for the 64 XD lines: about 128 contacts plus the mic signals and power.
- That suggests two or more 2-row 1.27 mm or 2.54 mm headers, or a mezzanine connector with a ground blade.

## 4. ADC symbol is AD7606, not AD7606B

**What the references say**
- ADI AN-1559 says the AD7606B is a **pin-for-pin replacement**.
- **Pin 10 becomes WR.** Tie it high, low or to CONVST when not using software parallel mode, and use pin 9 (CONVST) for all 8 channels.
- In serial mode, **tie pins 32 and 33 to AGND**.
- OS[2:0] = 111 selects software mode; the AD7606 had no such mode.
- Sources: [AN-1559](https://www.analog.com/en/resources/app-notes/an-1559.html), [ADI EngineerZone FAQ](https://ez.analog.com/data_converters/precision_adcs/f/q-a/111529/faq-ad7606-vs-ad7606b/332905).

**What this means here**
- The current wiring is already correct: pin 10 is on CONVST (allowed), pins 32/33 go to AGND, and OS = 000.
- Rename the symbol pins to the AD7606B names for clarity. No electrical change is needed.

## 5. Clocks over the ribbon cable

**What the references say**
- A series termination resistor at the source, typically **10–75 Ω**, set by looking at the waveform: too low rings, too high steps. A series-terminated line **cannot drive multiple loads** ([CTS termination app note](https://www.CTSCorp.com/Files/Product-Marketing-Documents/Passive-Components/Frequency-Control-Products/CTS-Passive-Components-Frequency-Control-Oscillators-Termination-Techniques-Layout-Considerations-Application-Note.pdf); [Renesas clock termination](https://www.renesas.com/ja/video/how-properly-terminate-clock-signal-identifying-common-signal-integrity-issues)).
- In flat cable, crosstalk becomes severe once the line delay approaches the edge rise time. A ground between signals (G-S-G) drains the coupling, at the cost of more conductors ([EP0083389](https://data.epo.org/publication-server/rest/v1.2/patents/EP0083389NWB1/document.pdf); [US4551576](https://patents.google.com/patent/US4551576); [Roos Instruments interconnect study](https://ftp.roos.com/roos/Documentation.nsf/387b4af8707421a7882573fc0000a7c0/d20d0a6bd5559385882577d6007e8e1d/$FILE/digital_interconnect_revD.pdf)).

**What this means here**
- **At the FPGA:** a 22–33 Ω series resistor on each clock and strobe output, with SLEW=SLOW and modest DRIVE in the constraints. The FPGA is the source of TX_SCK, TX_LATCH, ADC_SCLK and MIC_CLK_IN.
- **On our board, at the header:** one buffer per clock (74LVC/74AHCT), with each buffer output driving one short branch through its own 22–33 Ω resistor. Never drive 16 loads from one series-terminated line.
- **Ribbon:** put a ground next to each clock. Turn spare header I/O into grounds (J1 pins 23/29/31–36, J2 pins 7/25–36) and move the clocks beside them. Keep the ribbon short, about 10–15 cm.

## 6. AXU3EGB header pinout — VERIFIED

**Source:** ALINX *AXU3EGB 开发板用户手册* REV1.0 (supplied by the user), section 三(八) "40 针扩展口", pp. 41–42. Also consistent with the AXU3EGB schematic page "EXTEND IO".

**What the manual shows**
- Two 40-pin 2.54 mm headers, **J15 and J16**. Each carries 1 × 5 V, 2 × 3.3 V, 3 × GND and 34 I/O, all **3.3 V** standard. (The English manual calls them J45/J46.)
- **J15** pins:
  - 1 = GND, 2 = +5V.
  - 3–36 = bank 43/44 pairs, **N on odd pins, P on even pins** (3/4 = B43_L2_N/P on AG11/AF11 … 35/36 = B44_L7_N/P on AB13/AA13).
  - 37, 38 = GND; 39, 40 = +3.3V.
- **J16** pins: the same layout with bank 45/46 (3/4 = B45_L9_N/P on B10/C11 … 35/36 = B46_L1_N/P on A15/B15).
- None of the header I/O is shared with on-board functions: the PL UART (AH11/AH12), user LED/key (AE12/AF12), RS-485, camera and fan (AA11) all sit on other pins.
- The board is powered from **12 V DC**, the same voltage as ours.

**What this means here**
- `headers.kicad_sch` is correct as drawn: J1 = J15 (TX + mics), J2 = J16 (ADC), GND on 1/37/38, and +5V/+3.3V left unconnected.
- Only 3 grounds per header, so item 5 (clock grounds and buffers) stands.
- The FPGA ball for each net comes from these tables when writing the XDC.
- One 12 V adapter could power both boards, but each board keeps its own jack.

## 7. One buffer drives all 64 transmit-enable pins

**What the references say**
- No specific source was searched; this is datasheet arithmetic.
- 64 × 15 V / 200 kΩ ≈ 4.8 mA DC, within the 74AHCT1G125's ±8 mA. The concern is the edge rate into roughly 64 × 5 pF plus a long trace.

**What this means here**
- Use a 74AHCT125 (4 buffers), each driving 8 drivers (16 EN pins) in its own board region.

## 8. ADC noise dominates (oversampling)

**What the references say**
- On-chip oversampling improves SNR and reduces bandwidth. AD7606 at ±10 V: 90 dB with no oversampling, 95.5 dB at OS ×16 (for a 130 Hz input) ([AD7606 datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/ad7606_7606-6_7606-4.pdf)).
- Ideal gain is +3 dB per doubling.
- AD7606B figures for ×2 and ×4 were not found; check the AD7606B datasheet's OS table.

**What this means here**
- The AD7606B converts at up to 800 kSPS. OS ×4 at 200 kSPS output needs 800 kSPS conversions, which gives about +6 dB, minus a little sinc droop at 40 kHz.
- That requires the serial readout to finish within the 1.25 µs conversion window. Check the timing before committing.

## 9. Idle-state pulls on ADC control lines

**What the references say**
- Before configuration, AMD UltraScale+ SelectIO pins are high-impedance when PUDC_B = High, and have weak pull-ups when PUDC_B = Low ([AMD design advisory 000039996](https://docs.amd.com/r/en-US/000039996); [Zynq-7000 UG865 PUDC_B description](https://web.pa.msu.edu/hep/atlas/l1calo/htm/hardware/components/Xilinx_Zynq_xc7z035/xc7z035_pin_names_and_types.pdf)).
- How the AXU3EGB ties PUDC_B was not found. Check the AXU3EGB schematic.

**What this means here**
- Until the FPGA is configured, our inputs may float or be pulled up.
- Add explicit pulls so every external part sits in a safe state on its own:
  - ADC_CS_N: 10 k pull-up.
  - ADC_CONVST and ADC_RESET: 10 k pull-downs.
  - TX lines: already done.
  - MIC_CLK_IN: already done.

## 10. Capacitor losing value at its operating voltage

**What the references say**
- Class 2 MLCCs lose a large part of their capacitance under DC bias.
- Measured 10 µF 0805 X7R parts lost **35–65 % at rated voltage** and 12–32 % at 3.3 V, and the loss differs by vendor ([EPCI MLCC bias paper](https://epci.eu/wp-content/uploads/2019/10/HIGH-CV-MLCC-DCAC-BIAS-AGEING-CAPACITANCE-LOSS-EXPLAINED.pdf); [Rutronik](https://www.rutronik.com/article/multilayer-ceramic-chip-capacitors-capacitance-loss-due-to-dc-bias-in-mlccs); [Starfish Medical](https://starfishmedical.com/?p=8258)).
- Use the manufacturer's bias curve for the exact part (Murata SimSurfing, TDK SEAT).

**What this means here**
- Choose voltage ratings of at least 2–3× the working voltage and larger packages for bulk capacitance:
  - ADC REFCAP (4.4 V): 10 µF 16 V 1206 X7R.
  - VDRV (15 V): 10 µF 50 V 1210 X7R, already chosen.
  - LDO and TPS65131 outputs (6.5 V): 25 V 1206, already chosen.
- Check each part's bias curve when the LCSC numbers are chosen.

## 11. N1 / N2 / N6 from review 1

**What the references say**
- **N1:** with diode T/R limiters, the transmit pulse charges the receiver input, which must discharge before it can receive; this causes a near-field dead zone. Bleed resistors set the recovery time ([US11163046](https://patents.google.com/patent/US11163046); [US9568597](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9568597); [TI E2E TX810 thread](https://e2e.ti.com/support/data-converters-group/data-converters/f/data-converters-forum/394416/tx810---programmable-tr-rx-switch-for-ultrasound)).
- **N2:** op-amp inputs rectify out-of-band RF into a DC offset through their ESD/p-n junctions. Fix with an RC low-pass at the input, corner at least 20–30× the signal bandwidth, using low-inductance chip capacitors. On a non-inverting input the capacitor can go directly from the input to ground ([ADI MT-070](https://www-cce.cldnet.analog.com/media/en/training-seminars/tutorials/MT-070.pdf); [EE Power summary](https://eepower.com/technical-articles/stop-interfering-ways-to-prevent-electromagnetic-interference-in-amplifier-circuits/); [TI, Todd Toporski](https://www.radiolocman.com/review/article.html?di=335613)).
- **N6:** Knowles mic supply and clock-routing notes were not found; they are on the [Knowles SiSonic application notes page](https://www.mouser.com/datasheet/2/218/mic_selection_guide_v121222-3223927.pdf). ADI AN-1003 covers mounting bottom-port mics.

**What this means here**
- **N1:** τ = R_bleed × 2.2 nF. At 100 k that is 220 µs, which costs about 4 cm of range per τ. Use 33 k (τ ≈ 73 µs).
- **N2:** 100 pF from LIM to AGND gives a 340 kHz corner with 4.7 k, about 8× the band edge. 33 pF across R_F1 gives about 480 kHz. Both stay well clear of 40 kHz, and their phase error is calibrated out per element.
- **N6:** a ferrite bead or 10 Ω plus 1 µF on the mic VDD, and a short buffered clock per pair (already done).
