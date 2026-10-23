# Design check

Run 2026-10-22 22:15 by `scripts/check_design.py` on a fresh netlist of `kicad/aesa.kicad_sch`. **17 pass, 0 warn, 0 fail.**

| # | Check | Result |
|---|---|---|
| 1 | [ERC](#1) | **PASS** |
| 2 | [Part counts and references](#2) | **PASS** |
| 3 | [Footprints exist and match the symbol pins](#3) | **PASS** |
| 4 | [LCSC numbers](#4) | **PASS** |
| 5 | [TX chain, all 64 elements (header -> 595 -> UCC27524 -> DRVn -> channel)](#5) | **PASS** |
| 6 | [RX chain (channel -> RXn -> ADC input) and ADC wiring to J2](#6) | **PASS** |
| 7 | [Clocks: buffered, <= 4 loads per output, traced to the right header pin](#7) | **PASS** |
| 8 | [FPGA header pin maps (scripts/pinmap.py) and GND around clocks](#8) | **PASS** |
| 9 | [No signal above 3.3 V reaches an FPGA pin](#9) | **PASS** |
| 10 | [IC supply rails](#10) | **PASS** |
| 11 | [Decoupling on every IC supply pin](#11) | **PASS** |
| 12 | [Capacitor voltage ratings vs rail voltage](#12) | **PASS** |
| 13 | [Polarity: electrolytics, rectifiers, LEDs](#13) | **PASS** |
| 14 | [Regulator outputs computed from the divider values in the netlist](#14) | **PASS** |
| 15 | [Testability rule: every rail has a test point, isolation and an LED (not the analog LDOs)](#15) | **PASS** |
| 16 | [Single-pin and unconnected nets](#16) | **PASS** |
| 17 | [Power budget (estimates from datasheet currents)](#17) | **PASS** |

<a id="1"></a>
## 1. ERC: PASS

kicad-cli ERC on the full hierarchy: 0 violations.

<a id="2"></a>
## 2. Part counts and references: PASS

1704 parts, 88 distinct values.
| Part | Count |
|---|---|
| OPA1678IDR | 64 |
| CUSA-TR65-065-2200-TH68 | 64 |
| BAV99 | 128 |
| UCC27524DR | 32 |
| 74HCT595D | 16 |
| 74AHCT1G125 | 4 |
| 74LVC2G34 | 7 |
| AD7606C-16BSTZ | 8 |
| SPH0641LU4H-1 | 8 |
| TPS54202DDCR | 1 |
| TPS65131RGER | 1 |
| TPS7A4700RGWT | 1 |
| TPS7A3301RGWR | 1 |
| LM2731XMF | 1 |
| TLV75733PDBVR | 1 |

Duplicate references: 0.

<a id="3"></a>
## 3. Footprints exist and match the symbol pins: PASS

29 distinct footprints, all checked for existence and pad numbers.

<a id="4"></a>
## 4. LCSC numbers: PASS

Parts with an LCSC number: 1623; consigned/hand-solder: 64; **TBD: 0** (0 distinct values); no field: 0.

TBD values (count): 

<a id="5"></a>
## 5. TX chain, all 64 elements (header -> 595 -> UCC27524 -> DRVn -> channel): PASS

Traced 64 elements: header pin, shift-register SER and output, driver input/output, DRVn, channel R_drv. Errors: 0.

<a id="6"></a>
## 6. RX chain (channel -> RXn -> ADC input) and ADC wiring to J2: PASS

Checked: R_out of each channel to its ADC input (ADC k = ceil(n/8), input (n-1) mod 8 + 1), VnGND to GND, DOUTA/B through 33 R to the J2 pins, CS/RESET/SDI, software-mode straps (OS0-2, PAR/SER, STBY, RANGE, WR, VDRIVE, REFSEL on +3V3). Errors: 0.

<a id="7"></a>
## 7. Clocks: buffered, <= 4 loads per output, traced to the right header pin: PASS

| Buffered clock | Loads | |
|---|---|---|
| ADC_CONVST_A | 4 | ok |
| ADC_CONVST_B | 4 | ok |
| ADC_SCLK_A | 4 | ok |
| ADC_SCLK_B | 4 | ok |
| MIC_CLK_A | 4 | ok |
| MIC_CLK_B | 4 | ok |
| TX_LATCH_A | 4 | ok |
| TX_LATCH_B | 4 | ok |
| TX_LATCH_C | 4 | ok |
| TX_LATCH_D | 4 | ok |
| TX_SCK_A | 4 | ok |
| TX_SCK_B | 4 | ok |
| TX_SCK_C | 4 | ok |
| TX_SCK_D | 4 | ok |

Errors: 0.

<a id="8"></a>
## 8. FPGA header pin maps (scripts/pinmap.py) and GND around clocks: PASS

48 signal pins, 26 GND pins, 6 open (+5V, +3.3V from the FPGA board). Errors: 0.

<a id="9"></a>
## 9. No signal above 3.3 V reaches an FPGA pin: PASS

Every board-driven header net and its driver's supply (directly or through the 33 R source resistor). Errors: 0.

<details><summary>25 driven header pins</summary>

- J9901.25 MIC_DATA0 <- MK14101 (SPH0641LU4H-1, 3.3 V)
- J9901.25 MIC_DATA0 <- MK14201 (SPH0641LU4H-1, 3.3 V)
- J9901.27 MIC_DATA2 <- MK14501 (SPH0641LU4H-1, 3.3 V)
- J9901.27 MIC_DATA2 <- MK14601 (SPH0641LU4H-1, 3.3 V)
- J9901.28 MIC_DATA1 <- MK14301 (SPH0641LU4H-1, 3.3 V)
- J9901.28 MIC_DATA1 <- MK14401 (SPH0641LU4H-1, 3.3 V)
- J9901.30 MIC_DATA3 <- MK14701 (SPH0641LU4H-1, 3.3 V)
- J9901.30 MIC_DATA3 <- MK14801 (SPH0641LU4H-1, 3.3 V)
- J9902.10 ADC_DOUTA1 <- U7101 (AD7606C-16BSTZ, 3.3 V)
- J9902.11 ADC_DOUTB2 <- U7201 (AD7606C-16BSTZ, 3.3 V)
- J9902.12 ADC_DOUTA2 <- U7201 (AD7606C-16BSTZ, 3.3 V)
- J9902.13 ADC_DOUTB3 <- U7301 (AD7606C-16BSTZ, 3.3 V)
- J9902.14 ADC_DOUTA3 <- U7301 (AD7606C-16BSTZ, 3.3 V)
- J9902.15 ADC_DOUTB4 <- U7401 (AD7606C-16BSTZ, 3.3 V)
- J9902.16 ADC_DOUTA4 <- U7401 (AD7606C-16BSTZ, 3.3 V)
- J9902.17 ADC_DOUTB5 <- U7501 (AD7606C-16BSTZ, 3.3 V)
- J9902.18 ADC_DOUTA5 <- U7501 (AD7606C-16BSTZ, 3.3 V)
- J9902.19 ADC_DOUTB6 <- U7601 (AD7606C-16BSTZ, 3.3 V)
- J9902.20 ADC_DOUTA6 <- U7601 (AD7606C-16BSTZ, 3.3 V)
- J9902.21 ADC_DOUTB7 <- U7701 (AD7606C-16BSTZ, 3.3 V)
- J9902.22 ADC_DOUTA7 <- U7701 (AD7606C-16BSTZ, 3.3 V)
- J9902.23 ADC_DOUTB8 <- U7801 (AD7606C-16BSTZ, 3.3 V)
- J9902.24 ADC_DOUTA8 <- U7801 (AD7606C-16BSTZ, 3.3 V)
- J9902.8 ADC_BUSY <- U7101 (AD7606C-16BSTZ, 3.3 V)
- J9902.9 ADC_DOUTB1 <- U7101 (AD7606C-16BSTZ, 3.3 V)

</details>

<a id="10"></a>
## 10. IC supply rails: PASS

Every OPA1678, AD7606C, 74HCT595, UCC27524, 74AHCT1G125, 74LVC2G34 and SPH0641 checked. Errors: 0.

<a id="11"></a>
## 11. Decoupling on every IC supply pin: PASS

232 IC supply pins checked. Errors: 0.

<a id="12"></a>
## 12. Capacitor voltage ratings vs rail voltage: PASS

Rule: rating >= 1.2 x voltage (fail), >= 1.5 x for ceramics (warn). Errors: 0, warnings: 0.

<a id="13"></a>
## 13. Polarity: electrolytics, rectifiers, LEDs: PASS

Errors: 0.

- C9501 100u 25V: + on VIN, - on GND: ok
- C9535 47u 35V: + on VDRV, - on GND: ok
- D9501 SS54: anode VIN_FUSED, cathode VIN
- D9502 green: anode LED_VIN, cathode GND
- D9503 green: anode LED_+5V, cathode GND
- D9504 SS14: anode SW_P, cathode 6V5_REG
- D9505 green: anode LED_+6V5, cathode GND
- D9506 SS14: anode -6V5_REG, cathode SW_N
- D9507 green: anode GND, cathode LED_-6V5
- D9508 SS14: anode SW_VDRV, cathode VDRV_REG
- D9509 green: anode LED_VDRV, cathode GND
- D9510 green: anode LED_+3V3, cathode GND
- D9511 green: anode LED_3V3_MIC, cathode GND

<a id="14"></a>
## 14. Regulator outputs computed from the divider values in the netlist: PASS

| Rail | From the netlist | Target | Error |
|---|---|---|---|
| +5V TPS54202 | 5.066 V | 5.0 V | +1.3 % |
| +6V5 TPS65131 boost | 6.457 V | 6.5 V | -0.7 % |
| -6V5 TPS65131 inverter (to VREF) | -6.506 V | -6.5 V | -0.1 % |
| VDRV LM2731X | 15.422 V | 15.0 V | +2.8 % |
| -5VA TPS7A3301 | -4.998 V | -5.0 V | +0.0 % |
| +5VA TPS7A4700 ANY-OUT | 5.000 V | 5.0 V | +0.0 % |

TPS54202 EN UVLO start-up threshold: 7.38 V (needs < 11.5 V at the jack after the diode: ok).

<a id="15"></a>
## 15. Testability rule: every rail has a test point, isolation and an LED (not the analog LDOs): PASS

| Rail | Test point | Isolation | LED |
|---|---|---|---|
| VIN | TP9501 | (input) | R9501 |
| +5V | TP9502 | JP9501 | R9506 |
| +6V5 | TP9503 | JP9505 | R9510 |
| -6V5 | TP9504 | JP9506 | R9513 |
| +5VA | TP9505 | JP9502 | - (by rule) |
| -5VA | TP9506 | JP9503 | - (by rule) |
| VDRV | TP9507 | J9502 | R9519 |
| +3V3 | TP9508 | FB9501, JP9504 | R9520 |
| 3V3_MIC | TP9511 | FB9501 | R9521 |
| GND | TP9509, TP9510 | - | - (by rule) |

<a id="16"></a>
## 16. Single-pin and unconnected nets: PASS

Single-pin nets: 7 (7 expected: BUSY of ADC2-8, unused). Unexpected: []
Intentionally unconnected pins (no-connect flags): 104: QE x16, QF x16, QG x16, QH' x16, QH x16, FRSTDATA x8, NC x3, Pin_2 x2, Pin_39 x2, Pin_40 x2, 6.4V x2, BSW x1, 0.1V x1, 0.2V x1, 0.8V x1

<a id="17"></a>
## 17. Power budget (estimates from datasheet currents): PASS

| Rail | Load | Qty | Typ mA | Max mA |
|---|---|---|---|---|
| +5VA | OPA1678 (dual, 2 x 2.0 mA typ, 2.5 max) | 64 | 256 | 320 |
| +5VA | AD7606C-16 AVCC (about 20 mA at 200 kSPS) | 8 | 160 | 200 |
| -5VA | OPA1678 | 64 | 256 | 320 |
| +3V3 | AD7606C VDRIVE + 74LVC2G34 + mics + LEDs | 1 | 60 | 100 |
| +5V | 74HCT595 (static ~0.1 mA, dynamic at 10 MHz ~2 mA) | 16 | 32 | 64 |
| +5V | TPS65131 input for +-6V5 (0.45 A + 0.3 A loads, 85 %) | 1 | 1150 | 1350 |
| +5V | TLV75733P input (+3V3 load) | 1 | 60 | 100 |
| VDRV | 64 elements CW at 40 kHz, 3.73 mA each (sim/reports/tx_cw.md: C V^2 f, 47 R, 4.7 k limiter, element) | 64 | 239 | 256 |
| VDRV | UCC27524 quiescent | 32 | 32 | 48 |

| Rail | Total typ | Total max | Regulator limit | Margin |
|---|---|---|---|---|
| +5VA | 416 mA | 520 mA | 1000 mA | 48 % |
| -5VA | 256 mA | 320 mA | 1000 mA | 68 % |
| +3V3 | 60 mA | 100 mA | 1000 mA | 90 % |
| +5V | 1242 mA | 1514 mA | 2000 mA | 24 % |
| VDRV | 271 mA | 304 mA | 900 mA | 66 % |

VIN (12 V): +5V buck input about 5 V x 1.6 A / 0.9 / 12 V = 0.75 A, plus VDRV boost 15 V x 0.12 A / 0.85 / 12 V = 0.35 A (all 64 elements in CW): about 1.1 A of the 1.5 A adapter, under the 2 A polyfuse hold current.
