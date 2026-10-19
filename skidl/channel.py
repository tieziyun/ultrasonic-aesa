"""Circuit blocks of the hybrid ultrasonic AESA: transceiver channel, MEMS mics, shared parts.

Transceiver channel (front-end board; DESIGN_BRIEF.md section 4a):

  595 Qx --> UCC27524 half --R_DRV--> BAV99 crossed diodes --+-- XD --> transducer (array panel)
                                                             |
                                                          R_BLEED
                                                             |
  XD --R_LIM--+-- LIM --> OPA1678 A (x6, AC-coupled gain) --> OPA1678 B (MFB band-pass,
              |                                               40 kHz, Q=2, gain -4)
           BAV99 clamp to AGND                                      --R_OUT--> RX_OUT --> ADC

* TX: the FPGA streams a 1-bit frame on 16 data lines to 16 x 74AHCT595 (4 elements
  each). A '1' drives the element to VDRV and a '0' pulls it to ground. The crossed diodes isolate the driver from the
  element while the driver sits low, so the element listens whenever its bit is 0.
  This gives per-element, per-sample transmit/listen control.
* RX: the raw waveform is kept (amplitude and phase). There is no envelope detector.

MEMS mic pair (array panel edge): two SPH0641LU4H-1 PDM mics share one DATA line,
one with SELECT = VDD (data valid on the rising clock edge), one with SELECT = GND
(falling edge). 4 pairs = 8 mics on 4 DATA lines and 1 clock, fanned out by a buffer.

Grounds: AGND (analog: amplifiers, clamps, element returns, ADC), PGND (gate drivers and
VDRV decoupling) and DGND (logic, mics). They are separate nets joined at one point by
net-ties (ground_star), so layout can keep the drive edge currents out of the analog area.

Every part carries an "LCSC" field: a C-number, "TBD" (to be filled by parts-sourcer),
"consigned" or "hand-solder".

Reference spec only: the design's source of truth is the KiCad hierarchy in kicad/
(aesa.kicad_pro). Keep the two in step when a block changes.

This module only defines blocks. hybrid_test.py builds one channel plus all 8 mics.
"""

import os

os.environ.setdefault(
    "KICAD10_SYMBOL_DIR",
    r"C:\Users\Tiezi\AppData\Local\Programs\KiCad\10.0\share\kicad\symbols",
)

import builtins  # noqa: E402

from skidl import (  # noqa: E402
    KICAD10, Net, Part, TEMPLATE, set_default_tool, subcircuit,
)

set_default_tool(KICAD10)
NC = builtins.NC  # SKiDL installs its no-connect net as a builtin

FP_R = "Resistor_SMD:R_0603_1608Metric"
FP_C = "Capacitor_SMD:C_0603_1608Metric"
FP_C0805 = "Capacitor_SMD:C_0805_2012Metric"
FP_SOIC8 = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
FP_SOIC16 = "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm"
FP_SOT23_5 = "Package_TO_SOT_SMD:SOT-23-5"
FP_SC70_6 = "Package_TO_SOT_SMD:SOT-363_SC-70-6"
FP_TRANSDUCER = "aesa:CUSA-TR65"  # kicad/aesa.pretty, drawn from the datasheet

# LCSC numbers: only the ones checked so far. "TBD" = to be filled by parts-sourcer.
LCSC = {
    "UCC27524DR": "C465729",
    "OPA1678IDR": "C192421",
    "74AHCT595D": "TBD",  # no LCSC listing found (74AHC595D C5520 is not TTL-input)
    "BAV99": "C68983",  # Extended part; check stock before ordering
}


def lcsc(part, number):
    """Set the LCSC field. SKiDL writes only part.fields to the netlist, not attributes."""
    part.fields["LCSC"] = number
    return part


# Templates (one library lookup each, then copied; copies keep their fields).
_r = Part("Device", "R", dest=TEMPLATE, footprint=FP_R)
_c = Part("Device", "C", dest=TEMPLATE, footprint=FP_C)
_bav99 = lcsc(Part("Diode", "BAV99", dest=TEMPLATE), LCSC["BAV99"])
_opamp = lcsc(Part("Amplifier_Operational", "OPA1678", dest=TEMPLATE,
                   footprint=FP_SOIC8, value="OPA1678IDR"), LCSC["OPA1678IDR"])
# DigiKey stock; no LCSC listing checked yet, so consigned for now.
_mic = lcsc(Part("Sensor_Audio", "SPH0641LU4H-1", dest=TEMPLATE,
                 value="SPH0641LU4H-1"), "consigned")


def R(value, tag, spec="1%", number="TBD"):
    """0603 resistor. The value string carries the spec for the BOM."""
    return lcsc(_r(value=f"{value} {spec}".strip(), tag=tag), number)


def C(value, tag, spec, footprint=FP_C, number="TBD"):
    """Capacitor. spec gives voltage, dielectric and tolerance, e.g. "50V C0G 1%"."""
    return lcsc(_c(value=f"{value} {spec}", tag=tag, footprint=footprint), number)


@subcircuit
def tx_rx_channel(drv_in, drv_out, tx_bit, xd, rx_out, v_pos, v_neg, agnd):
    """One transceiver channel.

    drv_in, drv_out : input and output pins of one half of a UCC27524 (shared by 2 channels)
    tx_bit          : net from the 74AHCT595 output for this element (5 V logic)
    xd              : net to the transducer's signal terminal (other terminal = agnd)
    rx_out          : single-ended analog output to the ADC input
    v_pos, v_neg    : analog rails, +5 V and -5 V
    agnd            : analog ground
    """
    # ---- Transmit -------------------------------------------------------------
    drv_in += tx_bit

    # Series R limits the edge current into the 2.2 nF element (tau ~ 100 ns).
    r_drv = R("47", "r_drv")
    drv_node = Net("DRV")
    drv_out & r_drv & drv_node

    # Crossed diodes. BAV99 pin 1 = anode D1, pin 3 = cathode D1 / anode D2,
    # pin 2 = cathode D2. Pins 1+2 on one side and pin 3 on the other make an
    # anti-parallel pair. Echoes below ~0.3 V see an open circuit.
    d_iso = _bav99(tag="d_iso")
    drv_node += d_iso[1], d_iso[2]
    xd += d_iso[3]

    # DC reference for the element while both diodes are off.
    r_bleed = R("100k", "r_bleed")
    xd & r_bleed & agnd

    # ---- Receive protection ---------------------------------------------------
    # 4.7 k + diode clamp keeps the amplifier input within about +-0.77 V during
    # transmit: about 3 mA and 43 mW peak at a 15 V drive (1 k would burn 94 mW
    # in CW mode, the full 0603 rating). Adds about 9 nV/rtHz, still negligible.
    r_lim = R("4.7k", "r_lim")
    lim = Net("LIM")
    xd & r_lim & lim
    d_clamp = _bav99(tag="d_clamp")
    lim += d_clamp[3]
    agnd += d_clamp[1], d_clamp[2]

    # ---- Stage 1: non-inverting, gain 1 + 10k/2k = 6, DC gain 1 ----------------
    u = _opamp(tag="u_amp")
    ua, ub, upwr = u.uA, u.uB, u.uC
    upwr["V+"] += v_pos
    upwr["V-"] += v_neg
    v_pos & C("100n", "c_vp", "16V X7R") & agnd
    v_neg & C("100n", "c_vn", "16V X7R") & agnd

    s1 = Net("S1")
    fb1 = Net("FB1")
    ua["+"] += lim
    ua["-"] += fb1
    ua[1] += s1  # unit A output pin
    s1 & R("10k", "r_f1") & fb1
    # 2 k + 100 nF to AGND: high-pass corner at about 800 Hz, removes offset and drift.
    fb1 & R("2k", "r_g1") & C("100n", "c_g1", "16V X7R") & agnd

    # ---- Stage 2: multiple-feedback band-pass ----------------------------------
    # C1 = C2 = 1 nF C0G, R1 = 2k, R2 = 16k, R3 = 2k:
    # f0 = 39.8 kHz, Q = 2.0 (bandwidth about 20 kHz), centre gain -R2/(2 R1) = -4.
    # Needs 1 % resistors and 1 % caps: with 5 % caps f0 moves up to 5 % and the
    # 40 kHz phase shifts by up to about 11 degrees between channels; with 1 % parts
    # it is a few degrees, which per-element calibration removes.
    n2 = Net("N2")
    inv2 = Net("INV2")
    out2 = Net("OUT2")
    s1 & R("2k", "r1") & n2                           # R1
    n2 & R("2k", "r3") & agnd                         # R3
    n2 & C("1n", "c1", "50V C0G 1%") & inv2           # C1
    n2 & C("1n", "c2", "50V C0G 1%") & out2           # C2
    inv2 & R("16k", "r2") & out2                      # R2
    ub["-"] += inv2
    ub["+"] += agnd
    ub[7] += out2  # unit B output pin

    # Total mid-band gain about -24 (27.6 dB). A 1 m echo of about 120 mV gives
    # about 2.9 V peak at the ADC. Change the 2k in stage 1 to adjust it.
    out2 & R("100", "r_out") & rx_out


@subcircuit
def transducer(xd, agnd):
    """CUSA-TR65 element on the array panel.

    The datasheet does not say which lead is connected to the aluminium can. Pin 2
    is the ground lead in the footprint: check the can lead with a meter on the
    first parts and fit them so the can goes to pin 2 (shielding).
    """
    x = lcsc(Part("Device", "Speaker_Ultrasound", value="CUSA-TR65-065-2200-TH68",
                  footprint=FP_TRANSDUCER, tag="ls_xd"), "hand-solder")
    x[1] += xd
    x[2] += agnd


@subcircuit
def gate_driver(en, vdrv, pgnd):
    """One UCC27524 (2 channels) with its local decoupling. Returns the part.

    en must come from tx_control's 5 V buffer, never straight from an FPGA pin:
    ENA/ENB have 200 kohm internal pull-ups to VDRV.
    """
    drv = lcsc(Part("Driver_FET", "UCC27524D", footprint=FP_SOIC8, value="UCC27524DR",
                    tag="u_drv"), LCSC["UCC27524DR"])
    drv["V_{DD}"] += vdrv
    drv["GND"] += pgnd
    drv["ENA"] += en
    drv["ENB"] += en
    # 1 uF 50 V X7R in 0805 keeps most of its capacitance at 15 V DC bias.
    vdrv & C("1u", "c_drv_bulk", "50V X7R", footprint=FP_C0805) & pgnd
    vdrv & C("100n", "c_drv", "50V X7R") & pgnd
    return drv


# 74x595 output pins QA..QH (by number: the "QH" name also matches "QH'").
_Q_PINS = (15, 1, 2, 3, 4, 5, 6, 7)


@subcircuit
def tx_shift_register(sdi, sck, latch, oe_n, clr_n, bits, v5, dgnd):
    """One 74AHCT595 on its own FPGA data line, driving len(bits) elements (4).

    16 data lines x 4 bits: each frame is 4 SRCLK cycles, so a 2.5 MHz frame rate
    needs SRCLK = 10 MHz (the 74AHCT595 is rated far above that). QA..QD drive the
    elements; the remaining outputs and QH' are left unconnected.
    """
    sr = lcsc(Part("74xx", "74AHCT595", footprint=FP_SOIC16, value="74AHCT595D",
                   tag="u_sr"), LCSC["74AHCT595D"])
    sr["VCC"] += v5
    sr["GND"] += dgnd
    v5 & C("100n", "c_sr", "16V X7R") & dgnd
    sr["SER"] += sdi
    sr["SRCLK"] += sck
    sr["RCLK"] += latch
    sr["~{OE}"] += oe_n
    sr["~{SRCLR}"] += clr_n
    sr[9] += NC  # QH', no cascade
    for i, pin in enumerate(_Q_PINS):
        sr[pin] += bits[i] if i < len(bits) else NC


@subcircuit
def tx_control(en_fpga, en_drv, oe_n, clr_n, v5, v3, dgnd):
    """Once per board: TX safe state while the FPGA is unconfigured or high-Z.

    * en_fpga -> 74AHCT1G125 (5 V) -> en_drv. The 10 k pull-down on the FPGA side holds
      the drivers disabled. The buffer sinks the drivers' 200 kohm EN pull-ups (about
      2.4 mA for 16 drivers) and keeps their 15 V away from the 3.3 V FPGA pin.
      Use one buffer per 16 drivers at most.
    * /OE pulled up to 3.3 V: 595 outputs high-Z, and the drivers' internal input
      pull-downs hold them low. /SRCLR pulled down: registers held cleared.
      Both lines go to FPGA pins, so the pull-up goes to 3.3 V, not 5 V
      (74HCT inputs see 3.3 V as high).
    """
    buf = lcsc(Part("74xGxx", "74AHCT1G125", footprint=FP_SOT23_5, value="74AHCT1G125",
                    tag="u_en"), "TBD")
    buf["VCC"] += v5
    buf["GND"] += dgnd
    buf[1] += dgnd     # /OE: always enabled
    buf[2] += en_fpga  # A
    buf[4] += en_drv   # Y
    v5 & C("100n", "c_en", "16V X7R") & dgnd
    en_fpga & R("10k", "r_en_pd") & dgnd
    oe_n & R("10k", "r_oe_pu") & v3
    clr_n & R("10k", "r_clr_pd") & dgnd


@subcircuit
def mems_mic_pair(data, clk, vdd, dgnd):
    """Two SPH0641LU4H-1 mics sharing one PDM DATA line.

    data : PDM data net to the FPGA (both mics, time-multiplexed on the clock edges)
    clk  : this pair's buffered clock from mic_clock_fanout, 3.072-4.8 MHz for
           ultrasonic mode. The FPGA must start the clock in standard mode
           (1.024-2.475 MHz) and then raise it: the datasheet says not to power up
           or wake from sleep into ultrasonic mode.
    vdd  : 3.3 V. The mic's logic thresholds track VDD, so it must match the FPGA I/O.

    Layout: bottom-port mics (port 0.325 mm). They sit on the rear of the array panel
    with a hole through the PCB under each port.
    """
    for side, sel in (("hi", vdd), ("lo", dgnd)):
        m = _mic(tag=f"mic_{side}")
        m["VDD"] += vdd
        m["GND"] += dgnd
        m["SEL"] += sel
        m["CLOCK"] += clk
        # Series resistor at each mic output damps the shared, tri-stated line.
        m["DATA"] & R("33", f"r_data_{side}") & data
        # Datasheet: no Class 2 dielectrics near the mic, so C0G (0805 for 100 nF).
        vdd & C("100n", f"c_mic_{side}", "16V C0G", footprint=FP_C0805) & dgnd


@subcircuit
def mic_clock_fanout(clk_in, clk_outs, vdd, dgnd):
    """PDM clock buffer on the array panel: 2x 74LVC2G34, one output per mic pair.

    clk_in comes from the FPGA through the board connectors. Each output drives one
    pair (2 mics) on a short trace through its own 33 ohm source resistor. The 100 k
    pull-down keeps the clock low while the FPGA configures, so the mics never see
    an ultrasonic-mode clock at power-up.
    """
    clk_in & R("100k", "r_clk_pd") & dgnd
    for i in range(2):
        b = lcsc(Part("74xGxx", "74LVC2G34", footprint=FP_SC70_6, value="74LVC2G34",
                      tag=f"u_clk{i}"), "TBD")
        b["VCC"] += vdd
        b["GND"] += dgnd
        vdd & C("100n", f"c_clk{i}", "16V X7R") & dgnd
        for j, (a_pin, y_pin) in enumerate(((1, 6), (3, 4))):  # 1A->1Y, 2A->2Y
            b[a_pin] += clk_in
            b[y_pin] & R("33", f"r_clk{i}{j}") & clk_outs[2 * i + j]


@subcircuit
def ground_star(agnd, pgnd, dgnd):
    """Single-point joins between the ground nets (KiCad net-ties).

    PGND uses a 2 mm tie: the drive edges return through it (up to about 19 A for
    ~100 ns when all 64 elements switch together). Place both ties at the same spot,
    next to the power entry.
    """
    t_p = lcsc(Part("Device", "NetTie_2", footprint="NetTie:NetTie-2_SMD_Pad2.0mm",
                    value="NetTie AGND-PGND", tag="nt_pgnd"), "none (copper)")
    t_d = lcsc(Part("Device", "NetTie_2", footprint="NetTie:NetTie-2_SMD_Pad0.5mm",
                    value="NetTie AGND-DGND", tag="nt_dgnd"), "none (copper)")
    t_p[1] += agnd
    t_p[2] += pgnd
    t_d[1] += agnd
    t_d[2] += dgnd
