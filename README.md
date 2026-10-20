# Ultrasonic AESA panel

A 40 kHz in-air phased array, an acoustic "AESA". It has 64 transducers in an 8 × 8 grid, and each one can transmit or listen under FPGA control, element by element and ping by ping. Eight wideband MEMS microphones sit around the edge of the grid. Every element's raw waveform (amplitude and phase) is digitised and streamed to an ALINX AXU3EGB (Zynq UltraScale+ ZU3EG) board, which does the beamforming.

Uses: 3-D sonar imaging, gesture and hand tracking, Doppler motion sensing, and continuous-wave split mode, where some elements transmit while the rest listen.

**Status (2026-10-20):** the schematic is complete and passes ERC (1,701 parts, 0 violations). PCB layout has not started. Full requirements, decisions and part choices are in [DESIGN_BRIEF.md](DESIGN_BRIEF.md).

## Read the schematic

PDFs in [docs/schematics/](docs/schematics/), regenerated from KiCad on every change:

| PDF | Sheet | Used | What it is |
|---|---|---|---|
| [00_aesa](docs/schematics/00_aesa.pdf) | top | 1 | How the blocks connect |
| [01_channel](docs/schematics/01_channel.pdf) | `channel` | 64 | One transceiver: driver resistor, crossed-diode T/R switch, CUSA-TR65 element, input limiter, OPA1678 gain stage and 40 kHz band-pass filter |
| [02_adc](docs/schematics/02_adc.pdf) | `adc` | 8 | AD7606C-16, 8 channels each, serial output to the FPGA |
| [03_tx_group](docs/schematics/03_tx_group.pdf) | `tx_group` | 16 | 74HCT595 shift register and two UCC27524 gate drivers: drives 4 elements |
| [04_en_buf](docs/schematics/04_en_buf.pdf) | `en_buf` | 4 | TX-enable buffer for 8 gate drivers |
| [05_clk_buf](docs/schematics/05_clk_buf.pdf) | `clk_buf` | 7 | Clock fan-out buffer with 33 Ω series termination |
| [06_mic](docs/schematics/06_mic.pdf) | `mic` | 8 | SPH0641LU4H-1 PDM microphone |
| [07_headers](docs/schematics/07_headers.pdf) | `headers` | 1 | The two 40-pin ribbon headers to the AXU3EGB, and idle pull resistors |
| [08_power](docs/schematics/08_power.pdf) | `power` | 1 | 12 V jack to +5 V, ±6.5 V, +5VA/−5VA (low-noise LDOs), +3V3, 3V3_MIC and VDRV (15 V TX supply, switchable) |

[aesa_schematic.pdf](docs/schematics/aesa_schematic.pdf) has all of them in one file. Each block that repeats is drawn once and reused, so a change to `channel` applies to all 64 channels.

## Key numbers

| | |
|---|---|
| Elements | 64 × Same Sky CUSA-TR65 (40 kHz, sealed transceiver) + 8 × SPH0641LU4H-1 MEMS mics |
| TX drive | 0–15 V square wave per element, 16 serial data lines at 10 MHz, per-element per-sample control |
| RX chain | Gain ×22 (+27 dB) from element to ADC, 31–51 kHz pass band, 26 nV/√Hz referred to the element ([simulated](sim/reports/README.md)) |
| ADC | 8 × AD7606C-16, 16-bit, 200 kSPS per channel, ±5 V, 220 kHz input bandwidth |
| Power | One 12 V barrel jack (≥ 1.5 A); every rail has a test point, an isolation jumper and (except the analog LDOs) an LED |
| Board | One 4-layer board, about 200 × 170 mm: transducers on the front, all SMT parts on the back |
| FPGA link | Two 40-pin ribbons, 3.3 V LVCMOS, about 15 cm |

## Repository

| Path | Contents |
|---|---|
| [DESIGN_BRIEF.md](DESIGN_BRIEF.md) | Requirements, decisions with reasons and sources, parts, pinouts, open questions |
| `kicad/` | KiCad 10 project `aesa.kicad_pro`, the source of truth |
| `scripts/` | Generators: `build_cells.py` (the reused cell sheets, ADC, headers), `build_power.py` (power sheet), `gen_top.py` (top sheet and references), `export_pdf.py` (PDFs), plus field and LCSC helpers |
| `skidl/` | Reference spec of each block in SKiDL (values, nets, LCSC numbers) |
| `sim/` | LTspice testbenches and models; `sim/run.py` runs them and writes [sim/reports/](sim/reports/README.md) |
| `docs/schematics/` | Schematic PDFs |
| [CLAUDE.md](CLAUDE.md) | Working rules for the AI assistant on this project |

## Rebuild

Tools: KiCad 10.0 (per-user install) and LTspice 24. Run from the repository root in a Windows terminal:

```
set KPY=%LOCALAPPDATA%\Programs\KiCad\10.0\bin\python.exe
set KCLI=%LOCALAPPDATA%\Programs\KiCad\10.0\bin\kicad-cli.exe
%KPY% scripts\build_cells.py
%KPY% scripts\build_power.py
%KPY% scripts\gen_top.py
%KPY% scripts\sch_lcsc.py kicad\adc.kicad_sch kicad\tx_group.kicad_sch kicad\en_buf.kicad_sch kicad\clk_buf.kicad_sch kicad\mic.kicad_sch kicad\headers.kicad_sch kicad\power.kicad_sch
%KCLI% sch erc kicad\aesa.kicad_sch
%KPY% scripts\export_pdf.py
%KPY% sim\run.py
```

`build_power.py` takes about 10 minutes, because the MCP server rewrites the file once per wire. The `channel` sheet is drawn by hand (with the KiCad MCP server) and is not regenerated.

## Next

1. Power-sheet review.
2. LCSC part numbers for the parts still marked TBD. The AD7606C-16 isn't stocked by LCSC, so it will be bought from DigiKey or Mouser and supplied to JLCPCB.
3. Placement on the 4-layer board, then routing, then fabrication outputs for JLCPCB.
4. Waveguide design and FPGA firmware (XDC constraints, ADC register setup, TX bit streaming).
