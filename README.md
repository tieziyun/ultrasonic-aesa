# Ultrasonic AESA panel

A 40 kHz in-air phased array, an acoustic "AESA". It has 64 transducers in an 8 × 8 grid, and each one can transmit or listen under FPGA control, element by element and ping by ping. Eight wideband MEMS microphones sit around the edge of the grid. Every element's raw waveform (amplitude and phase) is digitised and streamed to an ALINX AXU3EGB (Zynq UltraScale+ ZU3EG) board, which does the beamforming.

Uses: 3-D sonar imaging, gesture and hand tracking, Doppler motion sensing, and continuous-wave split mode, where some elements transmit while the rest listen.

**Status (2026-10-20):** the schematic is complete and passes ERC (1,704 parts, 0 violations) and has been reviewed. PCB layout has not started. Full requirements, decisions and part choices are in [DESIGN_BRIEF.md](DESIGN_BRIEF.md).

## Read the schematic

PDFs in [docs/schematics/](docs/schematics/), regenerated from KiCad on every change. Start with page 00: a block diagram of how the signal flows. Each block opens the sheet below it.

| PDF | Sheet | What it is |
|---|---|---|
| [00_aesa](docs/schematics/00_aesa.pdf) | top | **Block diagram**: FPGA → TX drive → transceivers → receive amplifiers → ADCs → FPGA, plus mics and power |
| [01_headers](docs/schematics/01_headers.pdf) | `headers` | The two 40-pin ribbon headers to the AXU3EGB, idle pull resistors |
| [02_clocks](docs/schematics/02_clocks.pdf) | group | The 7 clock buffers and which clock each one feeds |
| [03_clk_buf](docs/schematics/03_clk_buf.pdf) | `clk_buf` ×7 | One clock fan-out buffer with 33 Ω series termination |
| [04_tx](docs/schematics/04_tx.pdf) | group | TX drive: 16 TX groups and 4 enable buffers |
| [05_tx_group](docs/schematics/05_tx_group.pdf) | `tx_group` ×16 | 74HCT595 shift register and two UCC27524 gate drivers: drives 4 elements |
| [06_en_buf](docs/schematics/06_en_buf.pdf) | `en_buf` ×4 | TX-enable buffer for 8 gate drivers |
| [07_array](docs/schematics/07_array.pdf) | group | The 64 transceiver channels as an 8 × 8 grid |
| [08_channel](docs/schematics/08_channel.pdf) | `channel` ×64 | One transceiver: crossed-diode T/R switch, CUSA-TR65 element, limiter, OPA1678 gain stage, 40 kHz band-pass |
| [09_adcs](docs/schematics/09_adcs.pdf) | group | The 8 ADCs and which channels each one digitises |
| [10_adc](docs/schematics/10_adc.pdf) | `adc` ×8 | AD7606C-16, 8 channels, serial output to the FPGA |
| [11_mics](docs/schematics/11_mics.pdf) | group | The 8 microphones, paired on 4 data lines |
| [12_mic](docs/schematics/12_mic.pdf) | `mic` ×8 | SPH0641LU4H-1 PDM microphone |
| [13_power](docs/schematics/13_power.pdf) | `power` | 12 V jack → +5 V, ±6.5 V, ±5VA (low-noise), VDRV 15 V (switchable), +3V3, 3V3_MIC |

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
| `scripts/` | Generators: `build_cells.py` (every cell sheet except `channel`: ADC, TX, buffers, mic, headers, power), `gen_top.py` (block diagram, group sheets, references), `sch_annotate.py` (channel title and notes), `export_pdf.py` (PDFs), plus field and LCSC helpers |
| `skidl/` | Reference spec of each block in SKiDL (values, nets, LCSC numbers) |
| `sim/` | LTspice testbenches and models. `sim/run.py` exports the channel circuit from the KiCad schematic, runs every testbench and writes [sim/reports/](sim/reports/README.md) |
| `docs/schematics/` | Schematic PDFs |
| [CLAUDE.md](CLAUDE.md) | Working rules for the AI assistant on this project |

## Rebuild

Tools: KiCad 10.0 (per-user install) and LTspice 24. Run from the repository root in a Windows terminal:

```
set KPY=%LOCALAPPDATA%\Programs\KiCad\10.0\bin\python.exe
set KCLI=%LOCALAPPDATA%\Programs\KiCad\10.0\bin\kicad-cli.exe
%KPY% scripts\build_cells.py
%KPY% scripts\sch_annotate.py
%KPY% scripts\gen_top.py
%KPY% scripts\sch_lcsc.py kicad\adc.kicad_sch kicad\tx_group.kicad_sch kicad\en_buf.kicad_sch kicad\clk_buf.kicad_sch kicad\mic.kicad_sch kicad\headers.kicad_sch kicad\power.kicad_sch
%KCLI% sch erc kicad\aesa.kicad_sch
%KPY% scripts\export_pdf.py
%KPY% sim\run.py
```

The `channel` sheet is drawn by hand (with the KiCad MCP server) and is not regenerated; everything else is.

BOM (needs internet: it queries JLCPCB for parts, stock and prices, and takes a few minutes):

```
%KPY% scripts\bom.py
%KPY% scripts\sch_lcsc.py --force kicad\channel.kicad_sch kicad\adc.kicad_sch kicad\tx_group.kicad_sch kicad\en_buf.kicad_sch kicad\clk_buf.kicad_sch kicad\mic.kicad_sch kicad\headers.kicad_sch kicad\power.kicad_sch
%KPY% scripts\gen_top.py
```

`bom.py` writes the costed BOM `docs/bom.md`, the JLCPCB upload file `fab/bom_jlcpcb.csv` and `scripts/parts.json`, from which `sch_lcsc.py` fills every part's LCSC field.

## Next

1. Placement on the 4-layer board, then routing, then fabrication outputs for JLCPCB.
2. Waveguide design and FPGA firmware (XDC constraints, ADC register setup, TX bit streaming).
