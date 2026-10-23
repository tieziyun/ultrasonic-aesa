"""Optimized, costed BOM for JLCPCB assembly.

Reads the schematic BOM (kicad-cli, grouped by value + footprint), picks an LCSC part for every
line and prices the build:

  * resistors and ceramic capacitors: chosen from the jlcsearch parametric index. A part must meet
    the value, package, voltage (>=), dielectric (same or better: C0G > X7R > X5R) and tolerance
    (<=). Preference: JLCPCB Basic part (no $3 loading fee) > an extended part already used on
    another line (no new fee) > cheapest extended part. Stock is checked on JLCPCB.
  * everything else: the hand-checked choices in PICKS below (reasons in the comments).

Outputs
  fab/bom_jlcpcb.csv     JLCPCB upload format (Comment, Designator, Footprint, LCSC Part #)
  docs/bom.md            costed BOM, totals, parts bought elsewhere, optimization notes
  scripts/parts.json     value|footprint -> LCSC, read by scripts/sch_lcsc.py --force

  python scripts/bom.py [--boards N] [--no-datasheets]   (N = 2 by default, the JLCPCB assembly minimum)
Then it runs scripts/datasheets.py, which keeps docs/datasheets/ in step with the BOM.
"""

import csv
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import KICAD_CLI  # noqa: E402
from jlc import jlc_search, jlcsearch, price_at  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEE = 3.0  # JLCPCB loading fee per extended part type, per order

# Hand-checked picks for non-passives: value -> (LCSC, note). Prices come from JLCPCB at run time.
PICKS = {
    "OPA1678IDR": ("C192421", "TI dual audio op-amp, RX front end; no cheaper part with the same noise/GBW"),
    "UCC27524DR": ("C465729", "TI dual 5 A gate driver; kept over the MX27524S clone ($0.12) for the datasheet and second sources"),
    "74HCT595D": ("C282339", "Nexperia. The $0.14 C52140404 'HCT' clone lists 2.7-5.5 V supply, i.e. HC-type inputs that "
                             "may not accept 3.3 V logic at 5 V; not worth $2 per board"),
    "BAV99": ("C2500", "Nexperia BAV99,215, JLCPCB Basic (was C68983, extended)"),
    "TLV320ADC5140": ("C2876368", "TI 4-channel 768 kHz audio ADC, 16 per board (replaces 8 x AD7606C-16 at $58.81)"),
    "SPH0641LU4H-1": ("C2879853", "Knowles PDM mic, stocked by JLCPCB, so it is assembled, not consigned"),
    "TPS54302DDCR": ("C311983", "TI 3 A buck, +5V: pin-compatible with the TPS54202, 3 A for the ADC rail"),
    "TPS65131RGER": ("C87663", "TI dual +/- boost, +/-6V5"),
    "TPS7A4700RGWT": ("C28286", "TI low-noise LDO, +5VA. Replaces TPS7A4701RGWR (C90084: stock 1, $13.16); "
                                "same VQFN-20 pinout, ANY-OUT works the same"),
    "TPS7A3301RGWR": ("C90082", "TI low-noise negative LDO, -5VA"),
    "LM2731XMF": ("C485804", "TI boost for VDRV; the LM2731XMFX/NOPB reel (2,830 in stock) of the same part"),
    "TLV75733PDBVR": ("C485517", "TI 1 A LDO, +3V3"),
    "74AHCT1G125": ("C7484", "TI SN74AHCT1G125DBVR"),
    "74LVC2G34": ("C52140488", "Nexperia 74LVC2G34GW, SOT-363"),
    "SS54": ("C22452", "40 V 5 A Schottky in SMA, JLCPCB Basic (footprint changed from SMC)"),
    "SS14": ("C2480", "40 V 1 A Schottky, JLCPCB Basic"),
    "SMD1812P200TF16": ("C20812", "2 A hold / 16 V 1812 polyfuse, half the price of the Littelfuse 1812L200/16DR"),
    "green": ("C12624", "KT-0603G. No green 0603 LED is a Basic part; the Basic red KT-0603R (C2286) would save "
                        "the $3 fee if the colour does not matter"),
    "600R@100MHz 0603": ("C1002", "Sunlord GZ1608D601TF, JLCPCB Basic; filters the 3V3_MIC rail (mics draw ~10 mA)"),
    "10u Isat>=5A": ("C2044906", "Bourns SRN8040TA-100M, 10 uH (TI reference value), Isat 5 A = the TPS54302's "
                                 "typical current limit; same footprint as before"),
    "4.7u Isat>=2A": ("C78804", "Sunlord SWPA6045S4R7MT, 4.7 uH, Isat 5.5 A"),
    "10u Isat>=2A": ("C79272", "Sunlord SWPA6045S100MT, 10 uH, Isat 3.5 A"),
    "12 V in (5.5x2.1)": ("C22434582", "CUI PJ-063AH (8 A); kept for the documented footprint"),
    "TX power (shunt)": ("C492401", "1x2 2.54 mm pin header (add a 2.54 mm jumper shunt)"),
    "EX_IO1 (AXU3EGB J15)": ("C19193815", "2x20 2.54 mm shrouded box header, 826 in stock"),
    "EX_IO2 (AXU3EGB J16)": ("C19193815", "2x20 2.54 mm shrouded box header, 826 in stock"),
}
# Bought elsewhere and soldered by hand: value -> (source, unit USD, note)
OFFBOARD = {
    "CUSA-TR65-065-2200-TH68": ("DigiKey 102-CUSA-TR65-065-2200-TH68-ND", 3.5536,
                                "40 kHz transceiver, DNP for JLCPCB (hand-soldered). DigiKey 100+ price "
                                "(1: $4.72, 50: $3.69, 500: $3.28; 350 in stock 2026-10-23); not stocked by LCSC"),
}
NO_PART = ("TestPoint:", "Jumper:SolderJumper")  # copper only

# Schematic changes made for cost or availability (kept here so the report explains them)
CHANGES = [
    "ADC: 8 x AD7606C-16 ($58.81 each, $470 per board) → 16 x TI TLV320ADC5140 4-channel audio ADC ($4.01 each, "
    "about $64 per board). Same simultaneous sampling of all 64 channels, 192 kSPS, more dynamic range; needs a "
    "+3V3A LDO (second TPS7A4700) and the 3 A TPS54302 buck.",
    "SPH0641LU4H-1 is stocked by JLCPCB (C2879853), so it is assembled instead of consigned.",
    "+5VA LDO TPS7A4701RGWR (JLCPCB stock 1, $13.16) → TPS7A4700RGWT (646 in stock, $6.11): same chip "
    "family, pinout and ANY-OUT network.",
    "BAV99 → Nexperia BAV99,215 C2500, a Basic part (no $3 fee).",
    "SS54 moved from SMC to SMA (C22452, Basic, still 5 A).",
    "Polyfuse 1812L200/16DR → SMD1812P200TF16 (same 2 A hold / 16 V, $0.12 instead of $0.21).",
    "Ferrite 220 Ω → 600 Ω GZ1608D601TF (Basic); more filtering on 3V3_MIC, which draws only ~10 mA.",
    "+5V buck divider 100k/13.3k + 75 pF → 75k/10k + 100 pF (5.07 V, same feed-forward zero) and EN divider "
    "511k/105k → 510k/100k (UVLO ≈ 7.3 V): all Basic parts, 3 fewer extended types.",
    "VDRV divider 150k/13.3k → 150k/13k (Basic): VDRV 15.4 V instead of 15.1 V, within the UCC27524 range.",
    "Inductor footprints follow the chosen parts: Sunlord SWPA6045S for L2–L4; L1 stays SRN8040 (Bourns "
    "SRN8040TA-100M, Isat 5 A, the TPS54302's typical current limit).",
    "One 0805 10 µF X7R moved to 1206 to share the part already used on 11 other lines.",
    "Mic bypass 100 nF C0G moved from 0805 to 1206: JLCPCB stocks no 100 nF C0G in 0805.",
    "Ceramic capacitors that only need X5R/X7R share one Basic part where the voltage allows "
    "(e.g. all 303 × 100 nF are CC0603KRX7R9BB104, 50 V).",
]

DIELECTRIC_RANK = {"C0G": 3, "NP0": 3, "X7R": 2, "X5R": 1}
MULT = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "k": 1e3, "M": 1e6, "": 1.0}


def schematic_bom():
    out = os.path.join(ROOT, "build", "bom_raw.csv")
    subprocess.run([KICAD_CLI, "sch", "export", "bom", os.path.join(ROOT, "kicad", "aesa.kicad_sch"), "-o", out,
                    "--fields", "Reference,Value,Footprint,QUANTITY,DNP", "--group-by", "Value,Footprint",
                    "--ref-range-delimiter", ""], check=True, capture_output=True)
    rows = list(csv.DictReader(open(out, encoding="utf-8")))
    return [dict(refs=r["Reference"].split(","), value=r["Value"], fp=r["Footprint"],
                 qty=int(r["QUANTITY"]), dnp=bool(r["DNP"].strip()) or " DNP" in r["Value"]) for r in rows]


def package_of(fp):
    m = re.search(r"_(\d{4})_\d{4}Metric", fp)
    return m.group(1) if m else None


def parse_res(value):
    m = re.match(r"^([\d.]+)([kM]?)\s+([\d.]+)%", value)
    return (float(m.group(1)) * MULT[m.group(2)], float(m.group(3)) / 100) if m else None


def parse_cap(value):
    m = re.match(r"^([\d.]+)([pnu])\s+(\d+)V(?:\s+(C0G|NP0|X7R|X5R))?(?:\s+([\d.]+)%)?", value)
    if not m:
        return None
    return dict(c=float(m.group(1)) * MULT[m.group(2)], v=int(m.group(3)), diel=m.group(4),
                tol=float(m.group(5)) / 100 if m.group(5) else None)


class Picker:
    def __init__(self, boards):
        self.boards = boards
        self.used_ext = set()

    def verify(self, code, need):
        """JLCPCB record for an LCSC code if it is in stock for this build, else None."""
        for p in jlc_search(code, 5):
            if p["code"] == code:
                return p if p["stock"] >= need else None
        return None

    def choose(self, rows, need, ok):
        rows = [r for r in rows if ok(r)]
        rows.sort(key=lambda r: (not r["is_basic"], f"C{r['lcsc']}" not in self.used_ext,
                                 not r.get("is_preferred"), r.get("price1") or 9))
        for floor in (max(need, 500), need):  # prefer parts with a stock margin
            for r in rows[:15]:
                p = self.verify(f"C{r['lcsc']}", floor)
                if p:
                    return p
        return None

    def resistor(self, value, pkg, need):
        r_ohm, tol = parse_res(value)
        rows = jlcsearch("/resistors/list.json", package=pkg, resistance=f"{r_ohm:g}").get("resistors", [])
        return self.choose(rows, need, lambda r: abs(r["resistance"] - r_ohm) < 1e-6 * r_ohm
                           and (r["tolerance_fraction"] or 1) <= tol + 1e-9)

    def capacitor(self, spec, pkg, need, fp):
        rows = jlcsearch("/capacitors/list.json", capacitance=f"{spec['c']:g}",
                         **({"package": pkg} if pkg else {})).get("capacitors", [])

        def ok(r):
            if abs((r["capacitance_farads"] or 0) - spec["c"]) > 1e-3 * spec["c"] or (r["voltage_rating"] or 0) < spec["v"]:
                return False
            if spec["tol"] and (r["tolerance_fraction"] or 1) > spec["tol"] + 1e-9:
                return False
            if "CP_Elec" in fp:  # aluminium electrolytic: match the can size of the footprint
                d, l = re.search(r"CP_Elec_([\d.]+)x([\d.]+)", fp).groups()
                return r["capacitor_type"] != "ceramic" and r["package"].startswith(f"SMD,D{d}xL{l}")
            if r["capacitor_type"] != "ceramic":
                return False
            return DIELECTRIC_RANK.get(r["temperature_coefficient"], 0) >= DIELECTRIC_RANK.get(spec["diel"], 1)
        return self.choose(rows, need, ok)


def main():
    boards = int(sys.argv[sys.argv.index("--boards") + 1]) if "--boards" in sys.argv else 2
    sys.stdout.reconfigure(encoding="utf-8")
    pk = Picker(boards)
    lines, offboard, nopart, dnp, missing = [], [], [], [], []
    bom = schematic_bom()
    # specials first so their extended parts count as "already used" for the passives
    bom.sort(key=lambda b: (parse_res(b["value"]) is not None or parse_cap(b["value"]) is not None, b["value"]))
    for b in bom:
        if b["fp"].startswith(NO_PART):
            nopart.append(b)
            continue
        if b["value"] in OFFBOARD:
            offboard.append((b, *OFFBOARD[b["value"]]))
            continue
        if b["dnp"]:  # not assembled, but give it a part so it can be fitted later
            v = b["value"].replace(" DNP", "")
            b["part"] = (pk.resistor(v, package_of(b["fp"]), 0) if parse_res(v)
                         else pk.capacitor(parse_cap(v), package_of(b["fp"]), 0, b["fp"]) if parse_cap(v)
                         else pk.verify(PICKS[v][0], 0) if v in PICKS else None)
            dnp.append(b)
            continue
        need = b["qty"] * boards
        note = ""
        if b["value"] in PICKS:
            code, note = PICKS[b["value"]]
            part = pk.verify(code, 0)
            if part and part["stock"] < need:
                note += f" **LOW STOCK ({part['stock']})**"
        elif parse_res(b["value"]):
            part = pk.resistor(b["value"], package_of(b["fp"]), need)
        elif parse_cap(b["value"]):
            part = pk.capacitor(parse_cap(b["value"]), package_of(b["fp"]), need, b["fp"])
        else:
            part = None
        if not part:
            missing.append(b)
            continue
        if not part["basic"]:
            pk.used_ext.add(part["code"])
        lines.append(dict(b, part=part, note=note, unit=price_at(part, need)))
        print(f"{b['qty']:4d} {b['value'][:28]:28s} -> {part['code']:10s} {part['mpn'][:26]:26s} "
              f"{'BASIC' if part['basic'] else 'ext  '} ${price_at(part, need)}")

    # merge lines that ended up on the same LCSC part and footprint
    merged = {}
    for ln in lines:
        k = (ln["part"]["code"], ln["fp"])
        if k in merged:
            merged[k]["refs"] += ln["refs"]
            merged[k]["qty"] += ln["qty"]
            merged[k]["values"].append(ln["value"])
        else:
            merged[k] = dict(ln, values=[ln["value"]])
    lines = sorted(merged.values(), key=lambda m: (-m["qty"] * (m["unit"] or 0)))
    for m in lines:
        m["unit"] = price_at(m["part"], m["qty"] * boards)

    # ---- outputs
    os.makedirs(os.path.join(ROOT, "fab"), exist_ok=True)
    with open(os.path.join(ROOT, "fab", "bom_jlcpcb.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for m in sorted(lines, key=lambda m: m["refs"][0]):
            w.writerow([m["part"]["mpn"], ",".join(sorted(m["refs"])), m["fp"].split(":")[1], m["part"]["code"]])

    pmap = {f"{v}|{m['fp']}": m["part"]["code"] for m in lines for v in m["values"]}
    pmap.update({f"{b['value']}|{b['fp']}": "hand-solder" for b, *_ in offboard})
    pmap.update({f"{b['value']}|{b['fp']}": "none (copper)" for b in nopart})
    pmap.update({f"{b['value']}|{b['fp']}": f"{b['part']['code']} (DNP)" for b in dnp if b.get("part")})
    json.dump(pmap, open(os.path.join(ROOT, "scripts", "parts.json"), "w"), indent=1, sort_keys=True)

    parts_cost = sum(m["qty"] * (m["unit"] or 0) for m in lines)
    n_ext = len({m["part"]["code"] for m in lines if not m["part"]["basic"]})
    n_basic = len({m["part"]["code"] for m in lines if m["part"]["basic"]})
    off_cost = sum(b["qty"] * u for b, _, u, _ in offboard)
    n_parts = sum(m["qty"] for m in lines)
    big = sorted(lines, key=lambda m: -m["qty"] * (m["unit"] or 0))

    md = ["# Bill of materials", "",
          f"Generated by `scripts/bom.py` from the KiCad schematic; prices are JLCPCB's at the quantity for "
          f"**{boards} boards** (the assembly minimum). JLCPCB upload file: `fab/bom_jlcpcb.csv`.", "",
          "## Cost per board", "",
          "| Item | USD |", "|---|---:|",
          f"| Parts assembled by JLCPCB ({n_parts} parts, {len(lines)} lines) | {parts_cost:,.2f} |",
          f"| Extended-part loading fees ({n_ext} types × ${FEE:.0f}, per order, shared by {boards} boards) | "
          f"{n_ext * FEE / boards:,.2f} |",
          f"| Bought elsewhere, hand-soldered ({', '.join(b['value'] for b, *_ in offboard)}) | {off_cost:,.2f} |",
          f"| **Total parts per board** | **{parts_cost + n_ext * FEE / boards + off_cost:,.2f}** |", "",
          f"{n_basic} Basic part types (no fee), {n_ext} extended. PCB fabrication, assembly labour and "
          "shipping are not included.", "",
          "### Where the money goes", "", "| Part | Qty | Line USD | Share |", "|---|---:|---:|---:|"]
    total = parts_cost + off_cost
    rows_big = [(m["part"]["mpn"], m["qty"], m["qty"] * m["unit"]) for m in big[:6]]
    rows_big += [(b["value"], b["qty"], b["qty"] * u) for b, _, u, _ in offboard]
    for name, q, c in sorted(rows_big, key=lambda t: -t[2]):
        md.append(f"| {name} | {q} | {c:,.2f} | {100 * c / total:.0f} % |")
    md += ["", "## Optimizations made (2026-10-22)", ""] + [f"- {c}" for c in CHANGES]
    md += ["", "## Assembled parts", "",
           "| Qty | Value | LCSC | MPN | Maker | Type | Unit USD | Line USD | Stock | Note |",
           "|---:|---|---|---|---|---|---:|---:|---:|---|"]
    for m in big:
        p = m["part"]
        md.append(f"| {m['qty']} | {' / '.join(m['values'])} | {p['code']} | {p['mpn']} | {p['maker']} | "
                  f"{'Basic' if p['basic'] else 'ext'} | {m['unit']:.4f} | {m['qty'] * m['unit']:.2f} | "
                  f"{p['stock']:,} | {m['note']} |")
    md += ["", "## Bought elsewhere (hand-soldered)", "", "| Qty | Part | Source | Unit USD | Note |",
           "|---:|---|---|---:|---|"]
    for b, src, u, note in offboard:
        md.append(f"| {b['qty']} | {b['value']} | {src} | {u:.2f} | {note} |")
    md += ["", "## Not ordered", "", "| Qty | Value | Why |", "|---:|---|---|"]
    for b in nopart:
        md.append(f"| {b['qty']} | {b['value']} | copper only ({b['fp'].split(':')[0]}) |")
    for b in dnp:
        md.append(f"| {b['qty']} | {b['value']} | do not populate (tuning option); fit "
                  f"{b['part']['code'] if b.get('part') else '?'} if needed |")
    if missing:
        md += ["", "## No part found", ""] + [f"- {b['value']} ({b['fp']}) × {b['qty']}" for b in missing]
    open(os.path.join(ROOT, "docs", "bom.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"\nparts {parts_cost:.2f} + fees {n_ext}x{FEE} / {boards} + offboard {off_cost:.2f} per board; "
          f"missing {len(missing)}")


if __name__ == "__main__":
    main()
    if "--no-datasheets" not in sys.argv:  # keep docs/datasheets/ in step with the BOM
        import datasheets
        datasheets.main()
