"""3D models for the project's own footprints (kicad/aesa.pretty), written as STEP so they show in
KiCad's 3D viewer, in kicad-cli renders and in the board's STEP export.

CUSP-TR80 (Same Sky CUSP-TR80-15-2500-TH, datasheet p.1-2): ABS can Ø9.8 x 7.0 mm with an open
mesh top, two pins Ø0.7 mm at 5.0 mm pitch. Same Sky's own model needs a login, so it is
built here from cylinders: body, mesh top, pins. Origin = footprint origin (between the pins).

  python scripts/make_3d.py
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "kicad" / "aesa.3dshapes"


class Step:
    def __init__(self):
        self.ents = []

    def add(self, text):
        self.ents.append(text)
        return f"#{len(self.ents)}"

    def pt(self, x, y, z):
        return self.add(f"CARTESIAN_POINT('',({x:.4f},{y:.4f},{z:.4f}))")

    def dir(self, x, y, z):
        return self.add(f"DIRECTION('',({x:.4f},{y:.4f},{z:.4f}))")

    def ax(self, x, y, z):
        return self.add(f"AXIS2_PLACEMENT_3D('',{self.pt(x, y, z)},{self.dir(0, 0, 1)},{self.dir(1, 0, 0)})")

    def cylinder(self, cx, cy, r, z0, z1):
        """Closed B-rep of an upright cylinder: bottom disc, side, top disc."""
        p0, p1 = self.pt(cx + r, cy, z0), self.pt(cx + r, cy, z1)
        v0, v1 = self.add(f"VERTEX_POINT('',{p0})"), self.add(f"VERTEX_POINT('',{p1})")
        c0 = self.add(f"CIRCLE('',{self.ax(cx, cy, z0)},{r:.4f})")
        c1 = self.add(f"CIRCLE('',{self.ax(cx, cy, z1)},{r:.4f})")
        vec = self.add(f"VECTOR('',{self.dir(0, 0, 1)},{z1 - z0:.4f})")
        seam = self.add(f"LINE('',{self.pt(cx + r, cy, z0)},{vec})")
        e0 = self.add(f"EDGE_CURVE('',{v0},{v0},{c0},.T.)")
        e1 = self.add(f"EDGE_CURVE('',{v1},{v1},{c1},.T.)")
        es = self.add(f"EDGE_CURVE('',{v0},{v1},{seam},.T.)")

        def face(edges, surf, sense):
            oes = ",".join(self.add(f"ORIENTED_EDGE('',*,*,{e},{'.T.' if o else '.F.'})") for e, o in edges)
            loop = self.add(f"EDGE_LOOP('',({oes}))")
            bound = self.add(f"FACE_OUTER_BOUND('',{loop},.T.)")
            return self.add(f"ADVANCED_FACE('',({bound}),{surf},{sense})")

        bottom = face([(e0, False)], self.add(f"PLANE('',{self.ax(cx, cy, z0)})"), ".F.")
        top = face([(e1, True)], self.add(f"PLANE('',{self.ax(cx, cy, z1)})"), ".T.")
        side = face([(e0, True), (es, True), (e1, False), (es, False)],
                    self.add(f"CYLINDRICAL_SURFACE('',{self.ax(cx, cy, z0)},{r:.4f})"), ".T.")
        shell = self.add(f"CLOSED_SHELL('',({bottom},{side},{top}))")
        return self.add(f"MANIFOLD_SOLID_BREP('',{shell})")

    def style(self, solid, rgb):
        col = self.add(f"COLOUR_RGB('',{rgb[0]:.3f},{rgb[1]:.3f},{rgb[2]:.3f})")
        fill = self.add(f"FILL_AREA_STYLE('',({self.add(f'FILL_AREA_STYLE_COLOUR({chr(39)}{chr(39)},{col})')}))")
        sss = self.add(f"SURFACE_SIDE_STYLE('',({self.add(f'SURFACE_STYLE_FILL_AREA({fill})')}))")
        usage = self.add(f"SURFACE_STYLE_USAGE(.BOTH.,{sss})")
        psa = self.add(f"PRESENTATION_STYLE_ASSIGNMENT(({usage}))")
        return self.add(f"STYLED_ITEM('',({psa}),{solid})")

    def write(self, path, name, solids):
        """solids: list of (solid entity, rgb)."""
        styled = [self.style(s, rgb) for s, rgb in solids]
        actx = self.add("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
        self.add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,{actx})")
        pctx = self.add(f"PRODUCT_CONTEXT('',{actx},'mechanical')")
        prod = self.add(f"PRODUCT('{name}','{name}','',({pctx}))")
        pdf = self.add(f"PRODUCT_DEFINITION_FORMATION('','',{prod})")
        pdctx = self.add(f"PRODUCT_DEFINITION_CONTEXT('part definition',{actx},'design')")
        pd = self.add(f"PRODUCT_DEFINITION('design','',{pdf},{pdctx})")
        pds = self.add(f"PRODUCT_DEFINITION_SHAPE('','',{pd})")
        length = self.add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
        angle = self.add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
        solid_ang = self.add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
        unc = self.add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),{length},'distance_accuracy_value','')")
        ctx = self.add(f"(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(({unc}))"
                       f"GLOBAL_UNIT_ASSIGNED_CONTEXT(({length},{angle},{solid_ang}))"
                       f"REPRESENTATION_CONTEXT('',''))")
        items = ",".join([self.ax(0, 0, 0)] + [s for s, _ in solids])
        rep = self.add(f"ADVANCED_BREP_SHAPE_REPRESENTATION('{name}',({items}),{ctx})")
        self.add(f"SHAPE_DEFINITION_REPRESENTATION({pds},{rep})")
        self.add(f"MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',({','.join(styled)}),{ctx})")
        body = "\n".join(f"#{k + 1}={e};" for k, e in enumerate(self.ents))
        path.write_text(
            "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('" + name + "'),'2;1');\n"
            f"FILE_NAME('{path.name}','2026-10-24T00:00:00',(''),(''),'','make_3d.py','');\n"
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n"
            + body + "\nENDSEC;\nEND-ISO-10303-21;\n", encoding="ascii")


def cusp_tr80():
    s = Step()
    solids = [(s.cylinder(0, 0, 4.9, 0.0, 6.6), (0.10, 0.10, 0.10)),     # ABS can
              (s.cylinder(0, 0, 4.2, 6.6, 7.0), (0.35, 0.35, 0.38)),     # open mesh top
              (s.cylinder(-2.5, 0, 0.35, -3.5, 0.0), (0.82, 0.82, 0.78)),  # pin 1 (+)
              (s.cylinder(2.5, 0, 0.35, -3.5, 0.0), (0.82, 0.82, 0.78))]   # pin 2
    s.write(OUT / "CUSP-TR80.step", "CUSP-TR80", solids)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    cusp_tr80()
    print("written:", *sorted(p.name for p in OUT.glob("*.step")))
