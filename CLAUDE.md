# Ultrasonic AESA panel — instructions for Claude

Read DESIGN_BRIEF.md first. It holds the requirements, decisions, parts and open questions. Update it whenever a decision changes.

## Fixed requirements (the user decided these; do not drop or change them without asking)
- **Hybrid panel**: 64 transceivers in an 8×8 grid **plus 8 wideband MEMS microphones** (SPH0641LU4H-1, PDM) around the grid edge. Every schematic, layout, pin budget and BOM must include both parts.
- **Raw waveform capture on every transceiver channel** (amplitude and phase); envelope-only receivers are not acceptable.
- **Per-element, FPGA-controlled transmit/listen mode** on all 64 channels (needed for split mode and continuous wave).
- **Easy to power, easy to test every part** (user, 2026-10-19). Power from one 12 V barrel jack (5.5×2.1 mm, ≥1.5 A); no other supply needed. Every rail has a test point and a way to isolate it (bridged solder jumper; VDRV through a removable shunt header so TX can be unpowered); every rail except the analog LDO outputs has an LED. Each block must be testable on its own: add test points on its key signals, and prefer standard, well-documented, in-production parts with downloadable datasheets (no end-of-life parts).

## Toolchain (Windows, installed 2026-10-19)
- KiCad 10.0.6, per-user: `C:\Users\Tiezi\AppData\Local\Programs\KiCad\10.0`
  - CLI: `...\10.0\bin\kicad-cli.exe` (ERC, DRC, renders, Gerbers, BOM/positions)
  - Python with `pcbnew` and SKiDL 3.0.0: `...\10.0\bin\python.exe`. Use this interpreter for design scripts, not the system Python.
- SKiDL needs `KICAD10_SYMBOL_DIR=C:\Users\Tiezi\AppData\Local\Programs\KiCad\10.0\share\kicad\symbols` (set it inside the script). The footprint library table exists only after KiCad's GUI has been opened once.
- Java 21 (Temurin) is installed, for the Freerouting autorouter. Freerouting itself is not installed yet.
- MCP server `kicad` (mixelpixx/KiCAD-MCP-Server v2.8.2, from `C:\GitHub\tools\KiCAD-MCP-Server`), configured in `.mcp.json`.
- Plugin `kicad-happy` (project scope): skills kicad, bom, jlcpcb, lcsc, digikey, mouser, element14, pcbway, spice, emc, datasheets.
- Project subagents in `.claude/agents/`: schematic-reviewer, layout-reviewer, parts-sourcer.
- The user's shell is often cmd.exe: never give them commands with trailing `#` comments.

## Token budget and visible progress
- Run the schematic-reviewer / layout-reviewer subagents **only at milestones**: one channel done, full schematic done, placement done, routing done, before fab output. Each spawn starts cold and costs a lot; review small edits inline instead.
- Every milestone must leave something the user can **see**: the schematic PDFs in `docs/schematics/` (one per sheet plus `aesa_schematic.pdf`; regenerate with `scripts/export_pdf.py` after every schematic change and commit them) or a rendered board PNG. Tell the user the file path.

## Workflow
1. **The KiCad hierarchical schematic in `kicad/` is the source of truth** (project `kicad/aesa.kicad_pro`). `skidl/channel.py` stays as the reference spec of each block (values, nets, LCSC numbers); when a block changes, change the sheet and the spec together.
   - **Every block that repeats is its own sheet, instantiated N times** (user rule, 2026-10-20), so there is one sheet to change: `channel` ×64, `adc` ×8 (AD7606C-16), `tx_group` ×16 (595 + 2 × UCC27524), `en_buf` ×4, `clk_buf` ×7, `mic` ×8; one-off sheets `power` and `headers`. Inter-sheet nets are global labels on the top sheet (`DRVn`, `RXn`, `TX_*`, `ADC_*`, `MIC_*`, rails); there is **one GND net** (no AGND/PGND/DGND split, no net-ties).
   - Hierarchy (2026-10-20): top sheet = block diagram of the signal flow (user request); group sheets `tx`, `array`, `adcs`, `mics`, `clocks` hold the reused cells; `power` and `headers` sit directly under the top. `scripts/gen_top.py` writes the top and group sheets; `scripts/build_cells.py` generates every cell sheet except `channel` (adc, tx_group, en_buf, clk_buf, mic, headers, power); `scripts/sch_annotate.py` sets the channel sheet's title and notes. Edit the scripts rather than the sheets, then re-run gen_top.py, sch_lcsc.py and export_pdf.py. Only `channel.kicad_sch` is MCP-drawn.
   - Draw and edit sub-sheets with the KiCad MCP server (`batch_add_and_connect`, `batch_connect`, `add_schematic_wire`). **It rewrites the whole file per pin and times out at 30 s; a timed-out write has left a sheet at 0 bytes twice.** Copy the sheet to the scratchpad before every MCP write, keep batches small (about 4 ICs or 10 no-connects per call), and check the file size afterwards. For repeated cells, draw one with the MCP server and copy it with `scripts/sch_replicate.py`. For whole sheets, call the MCP server's Python handlers in-process (no timeout) as `scripts/build_cells.py` does (`KiCADInterface().handle_command(...)`), so the sheet is rebuilt from a script.
   - The MCP server writes its own `(instances ...)` blocks (often on the same line as a `)`); `gen_top.py` strips every instances block and writes one, so always run it after MCP edits.
   - MCP symbol-loader bug: symbols that `extends` another (e.g. `TPS65131RGE`) produce an unbalanced file. Use the base symbol and set the value (e.g. `TPS65130RGE` with value TPS65131RGER).
   - `scripts/gen_top.py` writes the top and group sheets and the per-instance references (R101..R6409 in the channels, U7101.. in the ADCs, 101xx-116xx TX groups, 121xx TX enable, 131xx-137xx clock buffers, 141xx-148xx mics, 95xx power, 99xx headers). Re-run it after adding or removing parts in any sheet; never draw on the top or group sheets by hand. It tolerates a `kicad-cli sch upgrade` re-save (which stores the first instance's references in the sheet).
   - `scripts/sch_fields.py <sheet> [--vertical] [--only] [REF=dx,dy,dx2,dy2 ...]` fixes field placement (`--only` touches just the named parts: use it on the hand-drawn channel sheet; on rotated parts KiCad anchors the text at its right end); `scripts/sch_lcsc.py <sheets>` gives every part an LCSC field.
   - Check: `kicad-cli sch erc kicad/aesa.kicad_sch` (target: 0 violations), export the netlist and spot-check nets, run `scripts/export_pdf.py` and look at `docs/schematics/`.
   - Review with schematic-reviewer at milestones only, against the KiCad sheets and netlist.
2. Board: `scripts/netlist_to_pcb.py build/aesa.net kicad/aesa.kicad_pcb` (or KiCad's Update PCB from Schematic), then place components with a pcbnew Python script (`scripts/place.py`) at the exact array pitch in the brief.
3. Route: Freerouting for the bulk of the signals; power, TX drive and analog inputs by hand or reviewed carefully.
4. Check every iteration with kicad-cli ERC/DRC, and render PNGs and look at them (layout-reviewer).
5. Fabrication outputs to `fab/` (Gerbers, drill, BOM and positions with LCSC numbers) for JLCPCB.

## Simulation
- **Run every simulation through `sim/run.py`** (user rule, 2026-10-20): it runs LTspice, checks the results against limits and writes `sim/reports/<name>.md` + plot and the index `sim/reports/README.md`. Never report a simulation result that has no report. New testbench: add `sim/<name>.cir` and an entry in `SIMS` in `sim/run.py` with its checks.
- **The simulated channel comes from the schematic**: `scripts/sch_sim.py kicad/channel.kicad_sch` attaches SPICE models (KiCad Sim.* fields; models in `sim/models/aesa_parts.lib`), and `sim/run.py` exports the sheet with `kicad-cli sch export netlist --format spice` into `sim/gen/channel.cir` before every run. Testbenches hold only stimulus, loads and measurements. Re-run `sch_sim.py` after changing the channel sheet. The same fields let the channel be probed in KiCad's own simulator (ngspice).
- Solver: LTspice 24 in batch mode (`LTspice.exe -b`). Models: TI OPA167x macro-model; BAV99 = 1N4148 die; transducer = Butterworth-Van Dyke (C0 2.2 nF, Rm 3 kΩ, Q 20-40 via `.param QM`).

## Design rules (JLCPCB defaults unless the brief says otherwise)
- 2-layer array panel, 4-layer front-end board, 1.6 mm, HASL lead-free or ENIG.
- Minimum track/space 0.127 mm (5 mil), aim for 0.2 mm or more on general signals. Minimum via 0.3 mm drill / 0.6 mm pad.
- Prefer LCSC Basic parts. Every part must have an LCSC number or be marked hand-solder/consigned.
- Keep the TX high-voltage rail and drive traces away from RX inputs; continuous ground under the analog sections.

## Folder layout (create as needed)
- `sim/` LTspice testbenches and models, `sim/reports/` the generated reports · `kicad/` KiCad project and schematic (source of truth) · `skidl/` reference spec of the blocks (`channel.py`) and the SKiDL test build · `scripts/` placement and helper scripts · `build/` generated renders and reports (git-ignored) · `fab/` outputs for ordering · `docs/` datasheets and notes
