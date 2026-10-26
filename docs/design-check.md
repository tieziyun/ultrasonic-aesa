# Design check

Run 2026-10-26 09:40 by `scripts/check_design.py` on a fresh netlist of `kicad/aesa.kicad_sch`. **19 pass, 0 warn, 0 fail.**

| # | Check | Result |
|---|---|---|
| 1 | [ERC](#1) | **PASS** |
| 2 | [Part counts and references](#2) | **PASS** |
| 3 | [Footprints exist and match the symbol pins](#3) | **PASS** |
| 4 | [LCSC numbers](#4) | **PASS** |
| 5 | [Datasheets in docs/datasheets/ for every part](#5) | **PASS** |
| 6 | [3D model for every part](#6) | **PASS** |
| 7 | [TX chain, all 64 elements (header -> 595 -> UCC27524 -> DRVn -> channel)](#7) | **PASS** |
| 8 | [RX chain (channel -> RXn -> divider -> ADC input) and ADC wiring to J2](#8) | **PASS** |
| 9 | [Clocks: buffered, <= 4 loads per output, traced to the right header pin](#9) | **PASS** |
| 10 | [FPGA header pin maps (scripts/pinmap.py) and GND around clocks](#10) | **PASS** |
| 11 | [No signal above 3.3 V reaches an FPGA pin](#11) | **PASS** |
| 12 | [IC supply rails](#12) | **PASS** |
| 13 | [Decoupling on every IC supply pin](#13) | **PASS** |
| 14 | [Capacitor voltage ratings vs rail voltage](#14) | **PASS** |
| 15 | [Polarity: electrolytics, rectifiers, LEDs](#15) | **PASS** |
| 16 | [Regulator outputs computed from the divider values in the netlist](#16) | **PASS** |
| 17 | [Testability rule: every rail has a test point, isolation and an LED (not the analog LDOs)](#17) | **PASS** |
| 18 | [Single-pin and unconnected nets](#18) | **PASS** |
| 19 | [Power budget (estimates from datasheet currents)](#19) | **PASS** |

<a id="1"></a>
## 1. ERC: PASS

kicad-cli ERC on the full hierarchy: 0 violations.

<a id="2"></a>
## 2. Part counts and references: PASS

2130 parts, 96 distinct values.
| Part | Count |
|---|---|
| OPA1678IDR | 64 |
| CUSP-TR80-15-2500-TH | 64 |
| BAV99 | 128 |
| UCC27524DR | 32 |
| 74HCT595D | 16 |
| 74AHCT1G125 | 4 |
| 74LVC2G34 | 10 |
| TLV320ADC5140 | 16 |
| SPH0641LU4H-1 | 8 |
| TPS54302DDCR | 1 |
| TPS65131RGER | 1 |
| TPS7A4700RGWT | 2 |
| TPS7A3301RGWR | 1 |
| LM2731XMF | 1 |
| TLV75733PDBVR | 1 |

Duplicate references: 0.

<a id="3"></a>
## 3. Footprints exist and match the symbol pins: PASS

32 distinct footprints, all checked for existence and pad numbers.

<a id="4"></a>
## 4. LCSC numbers: PASS

Parts with an LCSC number: 1976; consigned/hand-solder: 64; **TBD: 0** (0 distinct values); no field: 0.

TBD values (count): 

<a id="5"></a>
## 5. Datasheets in docs/datasheets/ for every part: PASS

78 distinct parts, 78 with a datasheet PDF (scripts/datasheets.py, run by bom.py).

<a id="6"></a>
## 6. 3D model for every part: PASS

2038 footprints with a model that resolves; pad-only footprints (test pads, solder jumpers, fiducials, holes) need none. Substitutes for models missing from KiCad's library: MODEL_SUBST in place.py; own parts: scripts/make_3d.py.

<a id="7"></a>
## 7. TX chain, all 64 elements (header -> 595 -> UCC27524 -> DRVn -> channel): PASS

Traced 64 elements: header pin, shift-register SER and output, driver input/output, DRVn, channel R_drv. Errors: 0.

<a id="8"></a>
## 8. RX chain (channel -> RXn -> divider -> ADC input) and ADC wiring to J2: PASS

Checked: R_out of each channel into its ADC cell (ADC k = ceil(n/4), input 4k + 1 - n: common.adc_port), the 2k/1k divider, 100 nF coupling to INxP, INxM AC-grounded; SDOUT through 33 R to its J2 pin; SHDNZ, SCL, SDA of the group, BCLK/FSYNC of the group, address pins, and that no two ADCs share bus + address. Errors: 0.

<a id="9"></a>
## 9. Clocks: buffered, <= 4 loads per output, traced to the right header pin: PASS

| Buffered clock | Loads | |
|---|---|---|
| ADC_BCLK_A | 0 | ok |
| ADC_BCLK_B | 0 | ok |
| ADC_BCLK_C | 0 | ok |
| ADC_BCLK_D | 0 | ok |
| ADC_FSYNC_A | 0 | ok |
| ADC_FSYNC_B | 0 | ok |
| ADC_FSYNC_C | 0 | ok |
| ADC_FSYNC_D | 0 | ok |
| MIC_CLK_A | 2 | ok |
| MIC_CLK_B | 2 | ok |
| MIC_CLK_C | 2 | ok |
| MIC_CLK_D | 2 | ok |
| TX_LATCH_A | 4 | ok |
| TX_LATCH_B | 4 | ok |
| TX_LATCH_C | 4 | ok |
| TX_LATCH_D | 4 | ok |
| TX_SCK_A | 4 | ok |
| TX_SCK_B | 4 | ok |
| TX_SCK_C | 4 | ok |
| TX_SCK_D | 4 | ok |

Errors: 0.

<a id="10"></a>
## 10. FPGA header pin maps (scripts/pinmap.py) and GND around clocks: PASS

50 signal pins, 24 GND pins, 6 open (+5V, +3.3V from the FPGA board). Errors: 0.

<a id="11"></a>
## 11. No signal above 3.3 V reaches an FPGA pin: PASS

Every board-driven header net and its driver's supply (directly or through the 33 R source resistor). Errors: 0.

<details><summary>40 driven header pins</summary>

- J9901.25 MIC_DATA0 <- MK14101 (SPH0641LU4H-1, 3.3 V)
- J9901.25 MIC_DATA0 <- MK14201 (SPH0641LU4H-1, 3.3 V)
- J9901.27 MIC_DATA2 <- MK14501 (SPH0641LU4H-1, 3.3 V)
- J9901.27 MIC_DATA2 <- MK14601 (SPH0641LU4H-1, 3.3 V)
- J9901.28 MIC_DATA1 <- MK14301 (SPH0641LU4H-1, 3.3 V)
- J9901.28 MIC_DATA1 <- MK14401 (SPH0641LU4H-1, 3.3 V)
- J9901.30 MIC_DATA3 <- MK14701 (SPH0641LU4H-1, 3.3 V)
- J9901.30 MIC_DATA3 <- MK14801 (SPH0641LU4H-1, 3.3 V)
- J9902.10 ADC_SDOUT2 <- U7201 (TLV320ADC5140, 3.3 V)
- J9902.11 ADC_SDOUT3 <- U7301 (TLV320ADC5140, 3.3 V)
- J9902.12 ADC_SDOUT4 <- U7401 (TLV320ADC5140, 3.3 V)
- J9902.13 ADC_SDOUT5 <- U7501 (TLV320ADC5140, 3.3 V)
- J9902.14 ADC_SDOUT6 <- U7601 (TLV320ADC5140, 3.3 V)
- J9902.15 ADC_SDOUT7 <- U7701 (TLV320ADC5140, 3.3 V)
- J9902.16 ADC_SDOUT8 <- U7801 (TLV320ADC5140, 3.3 V)
- J9902.17 ADC_SDOUT9 <- U7901 (TLV320ADC5140, 3.3 V)
- J9902.18 ADC_SDOUT10 <- U8001 (TLV320ADC5140, 3.3 V)
- J9902.19 ADC_SDOUT11 <- U8101 (TLV320ADC5140, 3.3 V)
- J9902.20 ADC_SDOUT12 <- U8201 (TLV320ADC5140, 3.3 V)
- J9902.21 ADC_SDOUT13 <- U8301 (TLV320ADC5140, 3.3 V)
- J9902.22 ADC_SDOUT14 <- U8401 (TLV320ADC5140, 3.3 V)
- J9902.23 ADC_SDOUT15 <- U8501 (TLV320ADC5140, 3.3 V)
- J9902.24 ADC_SDOUT16 <- U8601 (TLV320ADC5140, 3.3 V)
- J9902.25 ADC_SDA2 <- U7501 (TLV320ADC5140, 3.3 V)
- J9902.25 ADC_SDA2 <- U7601 (TLV320ADC5140, 3.3 V)
- J9902.25 ADC_SDA2 <- U7701 (TLV320ADC5140, 3.3 V)
- J9902.25 ADC_SDA2 <- U7801 (TLV320ADC5140, 3.3 V)
- J9902.26 ADC_SDA3 <- U7901 (TLV320ADC5140, 3.3 V)
- J9902.26 ADC_SDA3 <- U8001 (TLV320ADC5140, 3.3 V)
- J9902.26 ADC_SDA3 <- U8101 (TLV320ADC5140, 3.3 V)
- J9902.26 ADC_SDA3 <- U8201 (TLV320ADC5140, 3.3 V)
- J9902.27 ADC_SDA4 <- U8301 (TLV320ADC5140, 3.3 V)
- J9902.27 ADC_SDA4 <- U8401 (TLV320ADC5140, 3.3 V)
- J9902.27 ADC_SDA4 <- U8501 (TLV320ADC5140, 3.3 V)
- J9902.27 ADC_SDA4 <- U8601 (TLV320ADC5140, 3.3 V)
- J9902.8 ADC_SDA1 <- U7101 (TLV320ADC5140, 3.3 V)
- J9902.8 ADC_SDA1 <- U7201 (TLV320ADC5140, 3.3 V)
- J9902.8 ADC_SDA1 <- U7301 (TLV320ADC5140, 3.3 V)
- J9902.8 ADC_SDA1 <- U7401 (TLV320ADC5140, 3.3 V)
- J9902.9 ADC_SDOUT1 <- U7101 (TLV320ADC5140, 3.3 V)

</details>

<a id="12"></a>
## 12. IC supply rails: PASS

Every OPA1678, TLV320ADC5140, 74HCT595, UCC27524, 74AHCT1G125, 74LVC2G34 and SPH0641 checked. Errors: 0.

<a id="13"></a>
## 13. Decoupling on every IC supply pin: PASS

229 IC supply pins checked. Errors: 0.

<a id="14"></a>
## 14. Capacitor voltage ratings vs rail voltage: PASS

Rule: rating >= 1.2 x voltage (fail), >= 1.5 x for ceramics (warn). Errors: 0, warnings: 0.

<a id="15"></a>
## 15. Polarity: electrolytics, rectifiers, LEDs: PASS

Errors: 0.

- C9501 100u 25V: + on VIN, - on GND: ok
- C9535 47u 35V: + on VDRV, - on GND: ok
- C9550 100u 25V: + on VIN, - on GND: ok
- C9551 100u 25V: + on +5V, - on GND: ok
- D9502 green: anode Net-(D9502-A), cathode GND
- D9503 green: anode Net-(D9503-A), cathode GND
- D9508 SS14: anode SW_VDRV, cathode VDRV_REG
- D9509 green: anode Net-(D9509-A), cathode GND
- D9510 green: anode Net-(D9510-A), cathode GND
- D9511 green: anode Net-(D9511-A), cathode GND
- D9504 SS14: anode SW_P, cathode 6V5_REG
- D9505 green: anode Net-(D9505-A), cathode GND
- D9506 SS14: anode -6V5_REG, cathode SW_N
- D9507 green: anode GND, cathode Net-(D9507-K)

<a id="16"></a>
## 16. Regulator outputs computed from the divider values in the netlist: PASS

| Rail | From the netlist | Target | Error |
|---|---|---|---|
| +5V TPS54302 | 5.066 V | 5.0 V | +1.3 % |
| +6V5 TPS65131 boost | 6.457 V | 6.5 V | -0.7 % |
| -6V5 TPS65131 inverter (to VREF) | -6.506 V | -6.5 V | -0.1 % |
| VDRV LM2731X | 15.422 V | 15.0 V | +2.8 % |
| -5VA TPS7A3301 | -4.998 V | -5.0 V | +0.0 % |
| 5VA TPS7A4700 ANY-OUT | 5.000 V | 5.0 V | +0.0 % |
| 3V3A TPS7A4700 ANY-OUT | 3.300 V | 3.3 V | +0.0 % |

TPS54302 EN UVLO start-up threshold: 7.50 V (needs < 11.5 V at the jack after the diode: ok).

<a id="17"></a>
## 17. Testability rule: every rail has a test point, isolation and an LED (not the analog LDOs): PASS

| Rail | Test point | Isolation | LED |
|---|---|---|---|
| VIN | TP9501 | (input) | R9501 |
| +5V | TP9502 | JP9501 | R9506 |
| +6V5 | TP9503 | JP9505 | R9510 |
| -6V5 | TP9504 | JP9506 | R9513 |
| +5VA | TP9505 | JP9502 | - (by rule) |
| -5VA | TP9506 | JP9503 | - (by rule) |
| +3V3A | TP9512 | JP9507 | - (by rule) |
| VDRV | TP9507 | J9502 | R9519 |
| +3V3 | TP9508 | FB9501, JP9504 | R9520 |
| 3V3_MIC | TP9511 | FB9501 | R9521 |
| GND | TP9509, TP9510, TP9516, TP9517, TP9518, TP9519 | - | - (by rule) |

<a id="18"></a>
## 18. Single-pin and unconnected nets: PASS

Single-pin nets: 0. Unexpected: []
Intentionally unconnected pins (no-connect flags): 137: GPIO1 x16, MICBIAS x16, QE x16, QF x16, QG x16, QH' x16, QH x16, NC x5, 6.4V x4, Pin_2 x2, Pin_39 x2, Pin_40 x2, 0.8V x2, BSW x1, 0.1V x1

<a id="19"></a>
## 19. Power budget (estimates from datasheet currents): PASS

| Rail | Load | Qty | Typ mA | Max mA |
|---|---|---|---|---|
| +5VA | OPA1678 (dual, 2 x 2.0 mA typ, 2.5 max) | 64 | 256 | 320 |
| +3V3A | TLV320ADC5140 AVDD (21 mA at 48 kSPS, about 28 mA estimated at 192 kSPS) | 16 | 448 | 560 |
| -5VA | OPA1678 | 64 | 256 | 320 |
| +3V3 | ADC IOVDD + 74LVC2G34 + mics + LEDs + I2C pull-ups | 1 | 60 | 100 |
| +5V | 74HCT595 (static ~0.1 mA, dynamic at 10 MHz ~2 mA) | 16 | 32 | 64 |
| +5V | TPS65131 input for +-6V5 (2 x 0.26 A typ / 0.32 A max at 6.5 V, 85 %) | 1 | 800 | 980 |
| +5V | TPS7A4700 input for +3V3A (= its load) | 1 | 450 | 560 |
| +5V | TLV75733P input (+3V3 load) | 1 | 60 | 100 |
| VDRV | 64 elements CW at 40 kHz, 3.73 mA each (sim/reports/tx_cw.md: C V^2 f, 47 R, 4.7 k limiter, element) | 64 | 239 | 256 |
| VDRV | UCC27524 quiescent | 32 | 32 | 48 |

| Rail | Total typ | Total max | Regulator limit | Margin |
|---|---|---|---|---|
| +5VA | 256 mA | 320 mA | 1000 mA | 68 % |
| +3V3A | 448 mA | 560 mA | 1000 mA | 44 % |
| -5VA | 256 mA | 320 mA | 1000 mA | 68 % |
| +3V3 | 60 mA | 100 mA | 1000 mA | 90 % |
| +5V | 1342 mA | 1704 mA | 3000 mA | 43 % |
| VDRV | 271 mA | 304 mA | 900 mA | 66 % |

VIN (12 V): +5V buck input about 5 V x 1.7 A / 0.9 / 12 V = 0.8 A, plus VDRV boost 15 V x 0.12 A / 0.85 / 12 V = 0.35 A (all 64 elements in CW): about 1.15 A of the 1.5 A adapter, under the 2 A polyfuse hold current.
