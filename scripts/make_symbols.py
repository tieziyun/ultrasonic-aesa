"""Write the project symbol library kicad/aesa.kicad_sym (symbols KiCad does not ship).

  TLV320ADC5140   TI 4-channel 768 kHz audio ADC, WQFN-24 (RTW) + thermal pad 25.
                  Pin numbers and names from datasheet SBAS892A, Pin Functions table. Pin types
                  are for I2C control (ADDR1_MISO is only an address input there).

  python scripts/make_symbols.py
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "kicad" / "aesa.kicad_sym"
FONT = "(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t)"


def prop(name, value, x, y, hide=False):
    h = "\n\t\t\t(hide yes)" if hide else ""
    return (f'\t\t(property "{name}" "{value}"\n\t\t\t(at {x} {y} 0){h}\n\t\t\t(effects\n\t\t\t\t(font\n'
            f'\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left)\n\t\t\t)\n\t\t)\n')


def pin(kind, x, y, ang, name, num, hidden=False):
    h = "\n\t\t\t\t(hide yes)" if hidden else ""
    return (f'\t\t\t(pin {kind} line\n\t\t\t\t(at {x} {y} {ang})\n\t\t\t\t(length 5.08){h}\n'
            f'\t\t\t\t(name "{name}"\n\t\t\t\t\t{FONT}\n\t\t\t\t)\n'
            f'\t\t\t\t(number "{num}"\n\t\t\t\t\t{FONT}\n\t\t\t\t)\n\t\t\t)\n')


def tlv320adc5140():
    name = "TLV320ADC5140"
    w, h = 15.24, 25.4  # half width, half height of the body
    left = [("6", "IN1P_GPI1", "input"), ("7", "IN1M_GPO1", "input"), ("8", "IN2P_GPI2", "input"),
            ("9", "IN2M_GPO2", "input"), ("10", "IN3P_GPI3", "input"), ("11", "IN3M_GPO3", "input"),
            ("12", "IN4P_GPI4", "input"), ("13", "IN4M_GPO4", "input"), None,
            ("14", "SHDNZ", "input"), ("16", "ADDR0_SCLK", "input"), ("15", "ADDR1_MISO", "input"),
            ("17", "SCL_MOSI", "input"), ("18", "SDA_SSZ", "bidirectional")]
    right = [("23", "FSYNC", "bidirectional"), ("22", "BCLK", "bidirectional"), ("21", "SDOUT", "tri_state"),
             ("20", "GPIO1", "bidirectional"), None, ("5", "MICBIAS", "power_out"), ("3", "VREF", "passive"),
             ("2", "AREG", "passive"), ("24", "DREG", "passive")]
    top = [("1", "AVDD", "power_in", -5.08), ("19", "IOVDD", "power_in", 5.08)]
    bottom = [("4", "AVSS", "power_in", -2.54), ("25", "VSS", "power_in", 2.54)]
    out = [f'\t(symbol "{name}"\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n',
           prop("Reference", "U", -w, h + 6.35), prop("Value", name, -w, -h - 7.62),
           prop("Footprint", "Package_DFN_QFN:Texas_RTW_WQFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.7mm_ThermalVias",
                0, -h - 6.35, True),
           prop("Datasheet", "https://www.ti.com/lit/ds/symlink/tlv320adc5140.pdf", 0, -h - 8.89, True),
           prop("Description", "4-channel 768-kHz audio ADC, TDM/I2S, I2C/SPI, WQFN-24", 0, -h - 11.43, True),
           f'\t\t(symbol "{name}_0_1"\n\t\t\t(rectangle\n\t\t\t\t(start {-w} {h})\n\t\t\t\t(end {w} {-h})\n'
           f'\t\t\t\t(stroke\n\t\t\t\t\t(width 0.254)\n\t\t\t\t\t(type default)\n\t\t\t\t)\n'
           f'\t\t\t\t(fill\n\t\t\t\t\t(type background)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n',
           f'\t\t(symbol "{name}_1_1"\n']
    for i, p in enumerate(left):
        if p:
            out.append(pin(p[2], -w - 5.08, h - 3.81 - 2.54 * i, 0, p[1], p[0]))
    for i, p in enumerate(right):
        if p:
            out.append(pin(p[2], w + 5.08, h - 3.81 - 2.54 * i, 180, p[1], p[0]))
    for num, nm, kind, x in top:
        out.append(pin(kind, x, h + 5.08, 270, nm, num))
    for num, nm, kind, x in bottom:
        out.append(pin(kind, x, -h - 5.08, 90, nm, num))
    out.append("\t\t)\n\t)\n")
    return "".join(out)


def main():
    text = ('(kicad_symbol_lib\n\t(version 20241209)\n\t(generator "make_symbols.py")\n'
            + tlv320adc5140() + ")\n")
    OUT.write_text(text, encoding="utf-8")
    table = OUT.parent / "sym-lib-table"
    table.write_text('(sym_lib_table\n  (version 7)\n  (lib (name "aesa")(type "KiCad")'
                     '(uri "${KIPRJMOD}/aesa.kicad_sym")(options "")(descr "Project symbols"))\n)\n',
                     encoding="utf-8")
    print(f"wrote {OUT.name} and {table.name}")


if __name__ == "__main__":
    main()
