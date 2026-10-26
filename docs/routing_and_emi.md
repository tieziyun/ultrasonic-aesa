# Routing and EMI plan

Research for routing the AESA panel (2026-10-24): which kinds of track the board needs, how long they are, what shape and topology each needs, and everything that keeps emissions (EMI) low. Lengths come from the placed board (`scripts/net_lengths.py`, which writes `build/net_lengths.csv`). Impedances are calculated for the JLC04161H-7628 stackup (outer layer over a GND plane 0.21 mm below, εr 4.4).

## 1. What the board has to carry

| Group | Nets | Pads per net | Shortest route (mm) | Frequency / edge | Source → load |
|---|---|---|---|---|---|
| TX shift and latch clocks, buffered (`TX_SCK_A–D`, `TX_LATCH_A–D`) | 8 | 5 | **70–92** (288–373 before the buffers moved under the TX rows) | 10 MHz / 2.5 MHz, edges about 1 ns (74LVC2G34) | clock buffer under the TX rows → 4 TX groups |
| TX serial data (`TX_SDI0–15`) | 16 | 2 | 246–355 | ≤ 5 Mbit/s, FPGA DRIVE 4 SLOW (about 3 ns edges) | header → one 74HCT595 |
| Mic PDM clock, buffered (`MIC_CLK_A–D`, one per mic pair since 2026-10-24) | 4 | 3 | 128–218 | 3.07 MHz, about 1 ns edges (mics need ≤ 3 ns) | clock buffer → one mic pair, 40 mm apart, at the end of the trace |
| Mic PDM data (`MIC_DATA0–3`) | 4 | 3 | 157–333 | 3.07 Mbit/s | 2 mics → header |
| ADC bit and frame clocks, buffered (`ADC_BCLK/FSYNC_A–D`) | 8 | 5 | 67–136 | 18.432 MHz / 192 kHz, about 1 ns edges | clock buffer → 4 ADCs |
| ADC TDM data (`ADC_SDOUT1–16`) | 16 | 2 | 25–153 | 18.432 Mbit/s, 33 Ω at the ADC | ADC → header |
| Clocks from the headers (unbuffered) | 5 | 3–8 | 79–449 (TX_SCK and TX_LATCH run down to the TX buffers) | as above, FPGA DRIVE 4 SLOW (about 3 ns edges) | header → clock buffers |
| I²C, enables, shutdown | 13 | 6–18 | 109–592 | 100 kHz or static | header → many |
| TX drive (`DRVn`, element side `XD`) | 64 (+64) | 2 | 46–156 | 40 kHz, 0–15 V, 0.3 A peaks, about 7 ns edges | gate driver → 47 Ω → element |
| RX analog (`RXn`) | 64 | 3 | 29–198 | 40 kHz, up to ±4.2 V, 100 Ω source | amplifier → test pad → ADC divider |
| HighCurrent (VIN, +5V and their switch nodes) | 6 | — | VIN about 280 trunk, +5V about 680 tree | DC up to 1.6 A, SW_5V 3 A peak | jack → buck; buck → LDOs and logic |
| Power rails (±6V5, ±5VA, +3V3A, +3V3, VDRV, 3V3_MIC) | 8 | up to 130 | ±5VA about 1,450 tree each | DC; VDRV 18 A instantaneous if all 64 elements switch at once (from local caps) | regulator → loads |
| Everything else (local, inside blocks) | about 950 | 2–3 | mostly < 10 | | |

**When a track is a transmission line.** On this stackup a signal travels about 6 ps/mm. A track behaves as a transmission line (reflections, ringing, radiation) once its one-way delay is more than about 1/6 of the edge time. For 1 ns edges that is about 28 mm; for 3 ns edges about 80 mm. So every buffered clock is electrically long, and the clocks are the main signal-integrity and EMI risk after the switching regulators. Before the changes in section 4, the buffered TX clocks ran about 300 mm (2.2 ns of delay against 1 ns edges). Now the longest fast-edged clock track is a mic clock at about 220 mm (1.3 ns); the long TX runs carry the FPGA's slow edges.

**No length matching is needed anywhere.** For a phased array, element timing only has to match to a small fraction of the 25 µs period. Track delays differ by at most about 1 ns, which is 0.015° of phase. For the digital links:
- **ADC TDM:** a skew of 1 ns or less against a 54 ns bit period.
- **TX frames:** 100 ns per bit.
- **PDM:** 326 ns per bit.

Timing is set in the FPGA, not in copper. Two FPGA-side items follow from that:
- Launch `TX_SDI` on the opposite clock edge to the one the 595 samples on. The buffered SCK arrives 3–4 ns after the data, so a same-edge launch would eat into hold time.
- Constrain the ADC read-back with the full round-trip delay in the XDC: ribbon twice, plus buffer, plus the ADC's data delay.

## 2. Track types: width, spacing, layer, shape

Impedance on the outer layers (microstrip over GND, my calculation using the IPC-2141 formula; check against KiCad's calculator or JLCPCB's before ordering):

| Width (mm) | Z0 (Ω) | Use |
|---|---|---|
| 0.20 | about 67 | general signals |
| 0.25 | about 61 | clocks (matches the 74LVC2G34's about 20 Ω plus the 47 Ω series resistor) |
| 0.35 | about 50 (0.30–0.34 if a GND pour runs alongside at 0.2 mm) | not needed |

| Track type | Class | Width / clearance | Layer | Shape and topology | Termination | Notes |
|---|---|---|---|---|---|---|
| Buffered clocks (TX, ADC, mic) | Clock | **0.25 mm** (change from 0.2) / **≥ 0.65 mm gap to any other track** (1 mm pitch) | One layer from buffer to loads, over continuous GND | **Daisy chain (fly-by)** to the load cluster: one trunk, then stubs ≤ 5 mm to each load; no T or star branches. Keep the loads within about 40 mm of each other at the far end (true for every clock since the mic clocks were split per pair) | 47 Ω source series resistor right at the buffer pin (fitted) | ≥ 5 mm from the board edge; no layer change if possible, otherwise a GND via next to each signal via |
| Long FPGA data (TX_SDI, mic data, TDM data) | Default | 0.2 mm / 0.4 mm gap (0.6 mm pitch) | outer, over GND | point to point, no stubs | FPGA DRIVE 4 SLOW (about 80 Ω, near-matched); 33 Ω at the ADC and mic data outputs | route as a bundle; keep clocks 1 mm away, or put a GND pour between |
| TX drive DRVn / XD | Drive | 0.3 mm (lanes 0.2 mm) / 0.2 mm, ≥ 1 mm to receive nets (rule) | **F.Cu DRV lane**, left of each column (section 6) | straight vertical lane, one per element; 45° corners | 47 Ω at the element (fitted) | return current flows in In1 directly below; keep the lane unbroken |
| RX analog RXn | Analog | 0.25 mm (lanes 0.2 mm) / 0.2 mm, ≥ 0.5 mm to digital tracks (rule) | **F.Cu RX lane**, right of each column (section 6) | straight vertical lane; test pad in the cell | 100 Ω at the amplifier (fitted) | never parallel to a clock; cross digital tracks only at 90° on the other layer |
| VIN, +5V trunks | HighCurrent | **2 mm** for the long trunks, 1 mm elsewhere; neck down at IC pins | outer, by hand | short and wide, no thin bottlenecks | — | VIN trunk down the left margin, +5V back up the right |
| Rails to many loads (±5VA, +3V3A, +3V3, VDRV) | Power | 0.5 mm (0.8 mm for the VDRV feed to each TX row) | outer | tree: trunk along each row or column, short branches; decoupling capacitor between the branch and the IC pin | — | ±5VA: one trunk per column on F.Cu, between the transducer pads (section 6) |
| Switch nodes (SW_5V, SW_P, SW_N, SW_VDRV) | Power | as short and small as the current allows | B.Cu, never through vias | the hot loop (input cap → switch → GND) as small as possible; already placed | — | no signal within 2 mm (rule); nothing on F.Cu above the switchers |
| Feedback, enable, soft-start, NR | Default | 0.2 mm | B.Cu | short, away from SW nodes and inductors; feedback taken from the output capacitor | — | |
| Local block wiring | Default | 0.2 mm | B.Cu | shortest | — | |

Shapes, everywhere:
- 45° corners.
- No acute angles.
- Teardrops on every pad and via after routing (KiCad 10: Edit → Add teardrops).
- No unused stubs.
- No tracks over plane gaps (the GND planes are solid; keep them that way).

## 3. EMI: what keeps emissions low

In order of impact for this board:

1. **Solid return planes.** Both inner layers are unbroken GND (already set, and typed as planes so the autorouter cannot cut them). Every signal on F.Cu returns in In1, every signal on B.Cu in In2.
2. **Return vias at layer changes.** A signal that changes from F.Cu to B.Cu switches its reference from In1 to In2. Both are GND, so one GND via within about 1 mm of the signal via carries the return current between the planes; without it the return current detours and the loop radiates. Rule for routing: **a GND via next to every signal via** on clock and fast data nets, and as close as practical on everything else.
3. **Switching regulators** (TPS54302 at 400 kHz, TPS65131 at about 1.25 MHz, LM2731X at 1.6 MHz). The hot loop sets the radiated EMI, and the placement already keeps it small (checked: input caps ≤ 4 mm, SW to inductor ≤ 3.6 mm). For routing:
   - Keep each SW node to a short, wide stub.
   - Give each input-capacitor GND pad its own vias straight into the planes.
   - Route nothing under or beside the inductors.
   - Take feedback from the output capacitor, away from the SW node.
   - Leave F.Cu above the switchers free of signals.
4. **Clocks and long fast lines.** Fast edges on long tracks are the main radiators after the switchers:
   - Route the clocks at 0.25 mm (matched to their source) with a daisy-chain topology.
   - Keep them ≥ 1 mm pitch from other tracks. Two tracks 0.6 mm apart over a ground plane 0.21 mm below couple about 11 % of an edge into each other (estimate k ≈ 1/(1 + (pitch/height)²)); at 1 mm pitch that drops to about 4 %, at 1.5 mm to about 2 %.
   - Keep them ≥ 5 mm from the board edge.
   - Never route a clock along the edge or across the ribbon headers' pin field.
5. **Board-edge stitching.** Fringing fields at the plane edges radiate. Ring the board with GND vias tying In1, In2 and the outer GND pours, at **≤ 6 mm pitch** (well under λ/20 for the harmonics that matter here). Also put a grid of plane-stitching vias about every 10–15 mm across the board, and around every mounting hole.
6. **Outer GND pours.** After routing, fill the free areas of F.Cu and B.Cu with GND. Stitch each pour to the planes (no unconnected islands; remove slivers under 0.5 mm). This shields the long lanes and gives the transducers' GND pins a short path to the planes.
7. **Decoupling vias.** Every decoupling capacitor's GND pad gets its own via to the plane, 0.5 mm or less from the pad, and the capacitor sits between the via and the IC pin. Already true by placement (all caps ≤ 2.4 mm from their pins).
8. **TX drive.** 64 × 15 V edges is the largest switched energy apart from the regulators. The 47 Ω series resistors and the 2 nF elements already slow each edge to about 90 ns at the element. Each driver has its own 1 µF + 100 nF. Keep each DRV lane over an unbroken plane: its loop area is then only lane length × 0.21 mm.
9. **The ribbon cables** are the largest antennas in the system: 2 × 40 conductors, about 15 cm, carrying 10–18 MHz. Already done: every clock conductor has GND on both sides, every spare conductor is GND, and the FPGA outputs run at DRIVE 4 SLOW. In use:
   - Keep the ribbons as short as possible and lay them flat against the FPGA board or enclosure.
   - If emissions are a problem, a flat clamp-on ferrite (for example a Laird 28x flat core) suppresses common-mode current without touching the signals.
10. **The 12 V cable** would carry the buck's input ripple out of the board: the C45–L5–C1 filter after the polyfuse stops it (decision 3).
11. **Mounting holes** are plated and tied to GND (done). Ground the board to the FPGA board and to any metal enclosure at those holes.

## 4. EMI changes made (user, 2026-10-24)

1. **TX clock buffers moved next to the TX groups.** The four TX_SCK/TX_LATCH buffers now sit in a band under the TX rows, centred under the 8 groups each one drives:
   - Only the two FPGA lines (DRIVE 4, slow edges) make the 300 mm run down the board, instead of 8 buffered 1 ns clocks.
   - The fast edges stay within about 60 mm of their loads.

   Placement only, no new parts.
2. **One mic clock per mic pair, 47 Ω terminators.** I first proposed slower mic-clock edges, but the SPH0641 datasheet requires clock edges ≤ 3 ns, so that would have broken the mics. The simulation ([sim/reports/mic_clock_si.md](../sim/reports/mic_clock_si.md)) showed that with one output feeding all four mics of a side, the mics nearest the buffer see a staircase edge (2.5 ns between the logic thresholds, about 5 ns 10–90 %, outside the spec), with 33 Ω or 47 Ω alike. Fix:
   - A 10th 74LVC2G34 (CLKB10), so each mic pair has its own clock output: MIC_CLK_A mics 1–2, B 3–4, C 5–6, D 7–8. Every mic now sees a clean edge, 1.0 ns at worst.
   - Every clock buffer's series resistor goes from 33 Ω to 47 Ω. With the buffer's about 20 Ω this matches the 61 Ω clock tracks: no overshoot (33 Ω gave 3.57 V against the mic's 3.6 V limit). The board-clock test still passes at 47 Ω ([sim/reports/clock_si.md](../sim/reports/clock_si.md)).
3. **12 V input filter:** C45 10 µF 50 V on the cable side, L5 1 µH (Sunlord SWPA4030S1R0NT, 4 A), then the existing 100 µF electrolytic C1, whose ESR damps the filter. That gives about 57 dB of attenuation at the buck's 400 kHz. I chose an inductor over a ferrite bead because the 1206 beads at LCSC rated ≥ 3 A have only about 60 Ω at 100 MHz and do little at 400 kHz.
4. **Unfitted RC snubbers** (4.7 Ω + 470 pF, both DNP) on all four switch nodes: R28/C46 on SW_5V, R29/C47 on SW_P, R30/C48 on SW_N, R31/C49 on SW_VDRV. Fit one only if a measurement shows ringing.
5. **Clock net class 0.25 mm, clock tracks ≥ 0.65 mm from other tracks** (`kicad/aesa.kicad_dru`).

## 5. Routing order

1. Power trunks by hand: VIN (2 mm), +5V (2 mm), VDRV feed to each TX row, ±5VA column trunks, +3V3A and +3V3 to the ADC rows.
2. The 64 DRV lanes and 64 RX lanes on F.Cu, by script (they are straight).
3. Clocks by hand, daisy-chained, with return vias.
4. Freerouting for the rest, with DRC after every pass. Freerouting ignores `kicad/aesa.kicad_dru`.
5. GND pours on F.Cu and B.Cu, the edge via fence, plane stitching, return vias, then teardrops.
6. DRC 0, a layout review (layout-reviewer), then fabrication outputs.

## 6. Channel block (2026-10-25)

The 64 channel cells are one hand-made layout: placement `CHANNEL` in `scripts/place.py`, routing `CHANNEL_ROUTE` in `scripts/route.py`. `scripts/cell_design.py` checks them together. It puts both on a 2 × 2 block of real cells with the busiest column copper around them, runs KiCad's DRC and plots one cell to `build/cell_design.png`.

**Front copper of a column** (x from the element centre):

| Item | Where | Why |
|---|---|---|
| 8 drive lanes | −9.3 … −6.5, 0.2 mm tracks at 0.4 mm pitch | Narrow lanes leave a 2 mm via column on each side of the op-amp. 0.2 mm carries the 0.3 A peak easily (0.4 Ω over 160 mm). A 0.2 mm gap at 15 V is above IPC-2221's 0.13 mm. |
| 8 receive lanes | +6.5 … +9.3, same size | |
| GND guard | on every column boundary, with a via at each cell corner | One column's outer drive lane runs beside the next column's outer receive lane for 160 mm. They are now 1.2 mm apart (rule ≥ 1 mm) with grounded copper between. Before, they were 0.95 mm apart. |
| +5VA / −5VA rails | −0.6 / +0.6 | Between the transducer pads. |
| Cell's own vias | \|x\| ≤ 6.3 | The lanes take the rest. Each lane via sits at its cell's edge of the bundle and jogs to its row's slot. The innermost slot belongs to the row that ends or starts there, so the jog crosses no other lane. |

**Back of the cell** (parts and the op-amp pinout):
- **Op-amp.** U1 (OPA1678, SOIC-8) sits across the top. Stage 1 uses half B, so the input (pin 5) faces the element and S1 and FB1 sit above it. Stage 2 uses half A, so its output (pin 1) is in the corner nearest the RX lanes. This unit swap is a schematic change with the same parts. With the first assignment the input pin was wedged between the −5 V capacitor and the feedback network.
- **Decoupling.** Each supply capacitor sits at its pin: rail via under the op-amp, then the pin, then the capacitor. Each capacitor has its own GND via about 1 mm away.
- **Stage 1.** R4 ∥ C7 sit across pins 7/6. The gain leg R5 → C3 goes to the top-left. The 100 pF input filter C6 sits at pin 5. The clamp D2 and the 4.7 k R3 sit in the pocket beside the element, and C6 and D2 share one GND via (the input reference).
- **Stage 2.** R8 sits across pins 1/2, with C4/C5 beside it (INV2 3.8 mm, N2 4.8 mm). Pin 3 has its own GND via. S1 reaches R6 under the op-amp, out between pins 3 (GND) and 4 (−5 V).
- **Output.** OUT2 crosses to R9 and the RX test pad in the right pocket on a short front-copper jumper. The RX lane via is next to them.
- **TX.** R1, D1 and the bleed R2 sit at the bottom-left, more than 9 mm from the op-amp, between the drive-lane via and the element's XD pad.
- **Soldering keep-out.** No part comes within 2.5 mm of a transducer pad edge.
- **Isolation.** The drive and receive lanes run on the other side of both GND planes from every amplifier node.

## Sources

- JLC04161H-7628 50 Ω coplanar width (0.30–0.34 mm): [LibreCAL pull request](https://github.com/jankae/LibreCAL/pull/8), [EEVblog: JLC stackup coupons](https://www.eevblog.com/forum/manufacture/jlc-pcb-stackup-layer-spacing), [JLCPCB impedance calculator guide](https://jlcpcb.com/help/article/user-guide-to-the-jlcpcb-impedance-calculator)
- Return vias at layer changes: [RF Essentials: transitioning between layers](https://rfessentials.com/rf-knowledge-base/what-is-the-proper-technique-for-transitioning-a-signal-between-layers-on-a-mult/), [Cadence: return paths](https://resources.pcb.cadence.com/blog/grounding-and-return-paths-power-plane-no-nos), [JLCPCB: via stitching](https://jlcpcb.com/blog/via-stitching-in-pcb-design), [Flux: via stitching guide](https://www.flux.ai/p/blog/via-stitching-pcb-guide)
- Hot loops and switch nodes: [Analog Devices: Layout for Power Designs #1, hot loops](https://www.analog.com/en/resources/technical-articles/2017/09/14/04/06/layout-for-power-designs-1-hot-loops.html), [TI LMR38020 layout (SNVAA93)](https://ti.com/document-viewer/lit/html/SNVAA93/GUID-B4AD942C-CE4C-44AD-B59B-68B10ADED91B), [TI LM5168E layout](https://www.ti.com/document-viewer/lit/html/SLVSIY7/GUID-D9AC7261-8013-48F4-9A7F-960E6D11C156), [TI LM21305 layout](https://www.ti.com/document-viewer/ja-jp/LM21305/datasheet/layout), [TI LMS3655 layout](https://www.ti.com/document-viewer/LMS3655/datasheet/layout-guidelines-snvsaa71175)
- Termination and multiple loads: [ICD: multiple loads](https://www.icd.com.au/articles/Multiple_Loads_PCBD-Feb2014.pdf), [SiTime AN10002](https://www.sitime.com/support/resource-library/application-notes/an10002-termination-recommendations-single-ended), [time-nuts: terminators](https://www.febo.com/pipermail/time-nuts/2012-November/072112.html)
- Edge stitching: [IEEE SCV EMC chapter (Federico)](https://r6.ieee.org/scv-emcs/wp-content/uploads/sites/111/032019Federico.pdf), [Atlas PCB: ground stitching](https://atlaspcb.com/blog/pcb-ground-plane-stitching-via)
- Cable common mode and ferrites: [TDK: common-mode noise](https://www.tdk.com/en/tech-mag/noise/09), [Laird 28x flat cores](https://www.mouser.in/new/laird-technologies/laird-28x-ferrite-cable-cores)
- PDM clock layout: [KiCad forum: microphone arrays](https://forum.kicad.info/t/microphone-array-project/61406), [INMP621 datasheet](https://www.alldatasheet.com/html-pdf/1140867/TDK/INMP621ACEZ-R7/1057/14/INMP621ACEZ-R7.html), [TI PCMD3140 PDM timing](https://www.ti.com/document-viewer/lit/html/SBASA64A/GUID-XXXXXXXX-SF0T-XXXX-XXXX-000000196890)
- Stackup and EMC: [Altium: choosing a stackup for EMC](https://resources.altium.com/p/how-to-choose-stackup-for-emc-design)
- Parts: 74LVC2G34 output drive (VOH at 24 mA) and LM2731 limits from the datasheets in `docs/datasheets/`.
