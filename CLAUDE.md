# Ultrasonic AESA panel — instructions for Claude

Read DESIGN_BRIEF.md first. It holds the requirements, decisions, parts and open questions. Update it whenever a decision changes.

## Fixed requirements (the user decided these; do not drop or change them without asking)
- **Hybrid panel**: 64 transceivers in an 8×8 grid **plus 8 wideband MEMS microphones** (SPH0641LU4H-1, PDM) around the grid edge. Every schematic, layout, pin budget and BOM must include both parts.
- **Raw waveform capture on every transceiver channel** (amplitude and phase); envelope-only receivers are not acceptable.
- **Per-element, FPGA-controlled transmit/listen mode** on all 64 channels (needed for split mode and continuous wave).
- **Easy to power, easy to test every part** (user, 2026-10-19). Power from one 12 V barrel jack (5.5×2.1 mm, ≥1.5 A); no other supply needed. Every rail has a test point and a way to isolate it (bridged solder jumper; VDRV through a removable shunt header so TX can be unpowered); every rail except the analog LDO outputs has an LED. Each block must be testable on its own: add test points on its key signals, and prefer standard, well-documented, in-production parts with downloadable datasheets (no end-of-life parts).

## Decision criteria (user, 2026-10-23): judge every design and layout decision on all of these
1. **Low interference and noise** between blocks (TX drive, digital, receivers, mics, power).
2. **Hard to burn, safe to use** (protection, current limits, no exposed hazards, margins on ratings).
3. **Good thermal behaviour** (spread the hot parts, copper and vias under them, keep heat away from sensitive parts).
4. **Easy to manufacture** (JLCPCB standard capabilities, no exotic processes).
5. **Cheap to make** (board area, layer count, extended-part fees, special processes).
6. **Hard to damage during soldering** (room around hand-soldered parts, no fragile parts next to iron work).
7. **Easy to test** (accessible test points, isolation, probe clearance).
State which criteria drove a choice and what it traded off.

## Toolchain (Windows, installed 2026-10-19)
- KiCad 10.0.6, per-user: `C:\Users\Tiezi\AppData\Local\Programs\KiCad\10.0`
  - CLI: `...\10.0\bin\kicad-cli.exe` (ERC, DRC, renders, Gerbers, BOM/positions)
  - Python with `pcbnew` and SKiDL 3.0.0: `...\10.0\bin\python.exe`. Use this interpreter for design scripts, not the system Python.
- SKiDL needs `KICAD10_SYMBOL_DIR=C:\Users\Tiezi\AppData\Local\Programs\KiCad\10.0\share\kicad\symbols` (set it inside the script). The footprint library table exists only after KiCad's GUI has been opened once.
- Freerouting 2.5.0 autorouter (installed 2026-10-24): run the jar `C:\GitHub\tools\freerouting\jar\fr-2.5.0.jar` on the portable Java 25 `C:\GitHub\tools\jre25\jdk-25.0.4.1+1-jre\bin\java.exe` (the jar needs Java 25, the installed Temurin is 21; the native `cli\freerouting-cli.exe` crashes on start-up). `scripts/route_lib.py` runs it headless; the same command without arguments opens its window. Its settings file `%APPDATA%\freerouting\freerouting.json` has `router.fanout.enabled = false` (2026-10-25): its fanout stage put a via on every SMD pin and took over an hour per pass; our scripts place the GND vias. Progress: `%LOCALAPPDATA%\freerouting\logs\freerouting.log`. Export the board with `pcbnew.ExportSpecctraDSN(board, path)`; import the session with `pcbnew.ImportSpecctraSES`. Freerouting ignores `kicad/aesa.kicad_dru`: run DRC after every autoroute.
- MCP server `kicad` (mixelpixx/KiCAD-MCP-Server v2.8.2, from `C:\GitHub\tools\KiCAD-MCP-Server`), configured in `.mcp.json`.
- Plugin `kicad-happy` (project scope): skills kicad, bom, jlcpcb, lcsc, digikey, mouser, element14, pcbway, spice, emc, datasheets.
- Project subagents in `.claude/agents/`: schematic-reviewer, layout-reviewer, parts-sourcer.
- The user's shell is often cmd.exe: never give them commands with trailing `#` comments.

## Token budget and visible progress
- Run the schematic-reviewer / layout-reviewer subagents **only at milestones**: one channel done, full schematic done, placement done, routing done, before fab output. Each spawn starts cold and costs a lot; review small edits inline instead.
- Every milestone must leave something the user can **see**: the schematic PDF `docs/schematics/aesa_schematic.pdf` (one linked file, one page per sheet; `gen_top.py` regenerates it after every schematic change, and the pre-commit hook in `.githooks/` refuses sheet changes committed without it) or a rendered board PNG. Tell the user the file path.

## Workflow
1. **The KiCad hierarchical schematic in `kicad/` is the source of truth** (project `kicad/aesa.kicad_pro`). `skidl/channel.py` stays as the reference spec of each block (values, nets, LCSC numbers); when a block changes, change the sheet and the spec together.
   - **Every block that repeats is its own sheet, instantiated N times** (user rule, 2026-10-20), so there is one sheet to change: `channel` ×64, `adc` ×16 (TLV320ADC5140, 4 channels each), `tx_group` ×16 (595 + 2 × UCC27524), `en_buf` ×4, `clk_buf` ×10, `mic` ×8; one-off sheets `power`, `power_analog` and `headers`. Inter-sheet nets are global labels on the top sheet (`DRVn`, `RXn`, `TX_*`, `ADC_*`, `MIC_*`, rails); there is **one GND net** (no AGND/PGND/DGND split, no net-ties).
   - Hierarchy (2026-10-20): top sheet = block diagram of the signal flow (user request); group sheets `tx`, `array`, `adcs`, `mics`, `clocks` hold the reused cells; `power`, `power_analog` and `headers` sit directly under the top. `scripts/gen_top.py` writes the top and group sheets; `scripts/build_cells.py` generates every cell sheet except `channel` (adc, tx_group, en_buf, clk_buf, mic, headers, power, power_analog); `scripts/sch_annotate.py` sets the channel sheet's title and notes. Edit the scripts rather than the sheets, then re-run gen_top.py, sch_lcsc.py and export_pdf.py. Only `channel.kicad_sch` is MCP-drawn.
   - Draw and edit sub-sheets with the KiCad MCP server (`batch_add_and_connect`, `batch_connect`, `add_schematic_wire`). **It rewrites the whole file per pin and times out at 30 s; a timed-out write has left a sheet at 0 bytes twice.** Copy the sheet to the scratchpad before every MCP write, keep batches small (about 4 ICs or 10 no-connects per call), and check the file size afterwards. For repeated cells, draw one with the MCP server and copy it with `scripts/sch_replicate.py`. For whole sheets, call the MCP server's Python handlers in-process (no timeout) as `scripts/build_cells.py` does (`KiCADInterface().handle_command(...)`), so the sheet is rebuilt from a script.
   - The MCP server writes its own `(instances ...)` blocks (often on the same line as a `)`); `gen_top.py` strips every instances block and writes one, so always run it after MCP edits.
   - MCP symbol-loader bug: symbols that `extends` another (e.g. `TPS65131RGE`) produce an unbalanced file. Use the base symbol and set the value (e.g. `TPS65130RGE` with value TPS65131RGER).
   - `scripts/gen_top.py` writes the top and group sheets and the per-instance references (R101..R6409 in the channels, U7101.. in the ADCs, 101xx-116xx TX groups, 121xx TX enable, 131xx-140xx clock buffers, 141xx-148xx mics, 95xx both power sheets (their local references are unique across the two), 99xx headers). Re-run it after adding or removing parts in any sheet; never draw on the top or group sheets by hand. It tolerates a `kicad-cli sch upgrade` re-save (which stores the first instance's references in the sheet).
   - **Every sheet is A3 or prints at A3** (user, 2026-10-24: one page size, readable text); `export_pdf.py` scales the A4 cells and the A2 array sheet onto A3 pages. The power supply is two A3 sheets, `power` (input, +5V, VDRV, +3V3, 3V3_MIC) and `power_analog` (±6V5, ±5VA, +3V3A), drawn in boxed sections whose positions (and sheet) live in `scripts/power_sections.py`; `place.py` groups the power parts on the board by the box they sit in, so move a section there, not in `build_cells.py` alone.
   - Every footprint needs a 3D model (`check_design.py` checks): stock models from KiCad's library, `MODEL_SUBST` in `place.py` where KiCad has none, and `scripts/make_3d.py` for the project's own footprints (`kicad/aesa.3dshapes`).
   - `scripts/common.py` holds what the scripts share: KiCad paths, the sheet DATE/REV, and `block_end()` (finds the end of an s-expression, ignoring parentheses inside strings). Use it rather than copying.
   - `scripts/sch_fields.py <sheet> [--vertical] [--only] [REF=dx,dy,dx2,dy2 ...]` fixes field placement (`--only` touches just the named parts: use it on the hand-drawn channel sheet; on rotated parts KiCad anchors the text at its right end); `scripts/sch_lcsc.py <sheets>` gives every part an LCSC field.
   - Check: `kicad-cli sch erc kicad/aesa.kicad_sch` (target: 0 violations), export the netlist and spot-check nets, look at `docs/schematics/aesa_schematic.pdf` (written by `gen_top.py`; `scripts/export_pdf.py` alone re-exports it).
   - Review with schematic-reviewer at milestones only, against the KiCad sheets and netlist.
2. Board: `scripts/place.py` builds `kicad/aesa.kicad_pcb` from the schematic (netlist via `scripts/netlist_to_pcb.py`) and places every part. **Lay the PCB out in blocks, like the schematic (user rule, 2026-10-23):** each schematic block (channel ×64, adc ×16, tx_group ×16, clk_buf ×10, en_buf ×4, mic ×8, power (both sheets), headers) gets one layout that is copied to every instance and grouped (KiCad group per instance, e.g. CH01, ADC03). The channel block is a hand-made template: placement `CHANNEL` in `place.py`, routing `CHANNEL_ROUTE` in `route.py`. Change the templates, not individual instances. Check them with `scripts/cell_design.py`, which runs DRC on a 2 × 2 block of cells with the worst-case column copper and plots `build/cell_design.png`. Then re-run `place.py` and `route.py`.
3. Route: Freerouting for the bulk of the signals; power, TX drive and analog inputs by hand or reviewed carefully.
4. Check every iteration with kicad-cli ERC/DRC, and render PNGs and look at them (layout-reviewer).
5. Fabrication outputs to `fab/` (Gerbers, drill, BOM and positions with LCSC numbers) for JLCPCB.

## Simulation
- **Run every simulation through `sim/run.py`** (user rule, 2026-10-20): it runs LTspice, checks the results against limits and writes `sim/reports/<name>.md` + plot and the index `sim/reports/README.md`. Never report a simulation result that has no report. New testbench: add `sim/<name>.cir` and an entry in `SIMS` in `sim/run.py` with its checks.
- **The simulated channel comes from the schematic**: `scripts/sch_sim.py kicad/channel.kicad_sch` attaches SPICE models (KiCad Sim.* fields; models in `sim/models/aesa_parts.lib`), and `sim/run.py` exports the sheet with `kicad-cli sch export netlist --format spice` into `sim/gen/channel.cir` before every run. Testbenches hold only stimulus, loads and measurements. Re-run `sch_sim.py` after changing the channel sheet. The same fields let the channel be probed in KiCad's own simulator (ngspice).
- Solver: LTspice 24 in batch mode (`LTspice.exe -b`). Models: TI OPA167x macro-model; BAV99 = 1N4148 die; transducer = Butterworth-Van Dyke (C0 2.2 nF, Rm 3 kΩ, Q 20-40 via `.param QM`).

## Design rules (JLCPCB defaults unless the brief says otherwise)
- One 4-layer board, JLC04161H-7628 stackup, 1.6 mm, **ENIG** (user, 2026-10-24). Inner layers are GND planes typed "power" (no routing on them). Custom DRC rules in `kicad/aesa.kicad_dru`.
- Minimum track/space 0.127 mm (5 mil), aim for 0.2 mm or more on general signals. Vias 0.3 mm drill / 0.6 mm pad; 0.2 / 0.45 mm only for the thermal vias of QFN footprints (JLCPCB standard multilayer capability).
- Board edge: no component within 3 mm (the 12 V jack is flush on purpose), copper ≥ 0.5 mm; 3 mm 45° bevel on every corner.
- Prefer LCSC Basic parts. Every part must have an LCSC number or be marked hand-solder/consigned.
- Keep the TX high-voltage rail and drive traces away from RX inputs; continuous ground under the analog sections.

## Datasheets (user rule, 2026-10-23)
- `docs/datasheets/` holds a PDF of every part on the board (index: `docs/datasheets/README.md`). `scripts/datasheets.py` keeps it in step with `scripts/parts.json` and re-downloads files older than 90 days; `scripts/bom.py` runs it at the end, and `check_design.py` warns about any part without a datasheet. After adding or changing a part, run `bom.py` (or `datasheets.py`). If LCSC has no link for a part, add the maker's URL to `MAKER_URL` in `datasheets.py`.
- Read the part's datasheet from this folder before designing it in.

## Folder layout (create as needed)
- `sim/` LTspice testbenches and models, `sim/reports/` the generated reports · `kicad/` KiCad project and schematic (source of truth) · `skidl/` reference spec of the blocks (`channel.py`) and the SKiDL test build · `scripts/` placement and helper scripts · `build/` generated renders and reports (git-ignored) · `fab/` outputs for ordering · `docs/` datasheets and notes
