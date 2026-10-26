"""Shared routing helpers for scripts/route.py: copper geometry checks, adding tracks/vias/keep-outs,
running Freerouting on a sub-board, and copying a routed block to its other instances.

Geometry is in mm, board coordinates (y down). Clearance checks are conservative (pads as their
bounding boxes); KiCad's DRC is the final check after every stage.
"""

import math
import re
import subprocess
from pathlib import Path

import pcbnew

REPO = Path(__file__).resolve().parent.parent
# Freerouting 2.5.0 jar on a portable Temurin 25 JRE (the 2.5 native CLI crashes on a missing serialization
# config); the jar needs Java 25, the system Temurin is 21
JAVA = Path(r"C:\GitHub\tools\jre25\jdk-25.0.4.1+1-jre\bin\java.exe")
FREEROUTING = Path(r"C:\GitHub\tools\freerouting\jar\fr-2.5.0.jar")
CLR = 0.25         # copper clearance for our own placements: the largest net-class value in use (HighCurrent)


def mm(v):
    return pcbnew.FromMM(v)


def P(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def xy(v):
    return pcbnew.ToMM(v.x), pcbnew.ToMM(v.y)


def local_ref(ref):
    m = re.match(r"^(#?[A-Z]+)(\d+)$", ref)
    return f"{m.group(1)}{int(m.group(2)[-2:])}"


# ---- obstacles -------------------------------------------------------------------------------------------
class Copper:
    """Index of copper items: pads (boxes), vias (circles) and tracks (segments) per layer."""

    LAYERS = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu)

    BUCKET = 4.0     # mm: items are also filed in square buckets, so a check only looks at its neighbourhood

    def __init__(self, board, region=None):
        """region (x0, y0, x1, y1): only index items near it (much faster for local work)."""
        self.items = []  # (layer, kind, net, geometry)
        self.buckets = {}
        self.region = region
        for fp in board.GetFootprints():
            for p in fp.Pads():
                bb = p.GetBoundingBox()
                box = (pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY()), pcbnew.ToMM(bb.GetRight()),
                       pcbnew.ToMM(bb.GetBottom()))
                if not self._near(box):
                    continue
                for layer in self.LAYERS:
                    if p.IsOnLayer(layer):
                        self._file((layer, "box", p.GetNetname(), box), box)
                if p.GetDrillSize().x > 0:  # hole: keep vias away whatever the net (hole to hole)
                    x, y = xy(p.GetPosition())
                    r = pcbnew.ToMM(p.GetDrillSize().x) / 2
                    self._file((None, "hole", p.GetNetname(), (x, y, r)), (x - r, y - r, x + r, y + r))
        for t in board.GetTracks():
            self.add_track_item(t)

    def _file(self, item, box):
        self.items.append(item)
        b = self.BUCKET
        for i in range(int(box[0] // b), int(box[2] // b) + 1):
            for j in range(int(box[1] // b), int(box[3] // b) + 1):
                self.buckets.setdefault((i, j), []).append(item)

    def _around(self, x0, y0, x1, y1, r):
        b = self.BUCKET
        seen = set()
        for i in range(int((min(x0, x1) - r) // b), int((max(x0, x1) + r) // b) + 1):
            for j in range(int((min(y0, y1) - r) // b), int((max(y0, y1) + r) // b) + 1):
                for it in self.buckets.get((i, j), ()):
                    if id(it) not in seen:
                        seen.add(id(it))
                        yield it

    def _near(self, box):
        r = self.region
        return r is None or not (box[2] < r[0] or box[0] > r[2] or box[3] < r[1] or box[1] > r[3])

    def add_track_item(self, t):
        bb = t.GetBoundingBox()
        if not self._near((pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY()), pcbnew.ToMM(bb.GetRight()),
                           pcbnew.ToMM(bb.GetBottom()))):
            return
        if t.GetClass() == "PCB_VIA":
            t = t.Cast()
            x, y = xy(t.GetPosition())
            r = pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2
            box = (x - r, y - r, x + r, y + r)
            for layer in self.LAYERS:
                self._file((layer, "circle", t.GetNetname(), (x, y, r)), box)
            self._file((None, "hole", t.GetNetname(), (x, y, pcbnew.ToMM(t.GetDrillValue()) / 2)), box)
        else:
            (x0, y0), (x1, y1) = xy(t.GetStart()), xy(t.GetEnd())
            hw = pcbnew.ToMM(t.GetWidth()) / 2
            self._file((t.GetLayer(), "seg", t.GetNetname(), (x0, y0, x1, y1, hw)),
                       (min(x0, x1) - hw, min(y0, y1) - hw, max(x0, x1) + hw, max(y0, y1) + hw))

    @staticmethod
    def _pt_box(px, py, b):
        dx = max(b[0] - px, 0, px - b[2])
        dy = max(b[1] - py, 0, py - b[3])
        return math.hypot(dx, dy)

    @staticmethod
    def _pt_seg(px, py, x0, y0, x1, y1):
        vx, vy = x1 - x0, y1 - y0
        L = vx * vx + vy * vy
        t = 0 if L == 0 else max(0, min(1, ((px - x0) * vx + (py - y0) * vy) / L))
        return math.hypot(px - (x0 + t * vx), py - (y0 + t * vy))

    def dist(self, item, px, py):
        kind, g = item[1], item[3]
        if kind == "box":
            return self._pt_box(px, py, g)
        if kind in ("circle", "hole"):
            return math.hypot(px - g[0], py - g[1]) - g[2]
        return self._pt_seg(px, py, *g[:4]) - g[4]

    def via_ok(self, x, y, r, net, clr=CLR, hole_r=0.15, hole_gap=0.5, net_clr=None):
        """net_clr: optional function net -> clearance to that net's copper, where more than clr (clocks)."""
        for it in self._around(x, y, x, y, r + clr + 4.0):
            if it[1] == "hole":
                if math.hypot(x - it[3][0], y - it[3][1]) - it[3][2] - hole_r < hole_gap:
                    return False
                continue
            if it[2] == net:
                continue
            c = max(clr, net_clr(it[2])) if net_clr else clr
            if self.dist(it, x, y) < r + c:
                return False
        return True

    def seg_ok(self, layer, x0, y0, x1, y1, hw, net, clr=CLR, step=0.05, net_clr=None):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        pts = [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for k in range(n + 1)]
        for it in self._around(x0, y0, x1, y1, hw + clr + 4.0):
            if it[0] != layer or it[2] == net or it[1] == "hole":
                continue
            c = max(clr, net_clr(it[2])) if net_clr else clr
            for px, py in pts:
                if self.dist(it, px, py) < hw + c:
                    return False
        return True


# ---- adding items ----------------------------------------------------------------------------------------
def add_via(board, net, x, y, size=0.6, drill=0.3, locked=True):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(P(x, y))
    v.SetWidth(mm(size))
    v.SetDrill(mm(drill))
    v.SetNet(board.FindNet(net))
    v.SetLocked(locked)
    board.Add(v)
    return v


BEND = 0.25    # mm: every right-angle (or sharper) corner of a drawn polyline becomes a 45-degree bend of this
               # size, the same on every line, so parallel bundles keep (and widen) their spacing in the turn


def bend45(pts, c=BEND):
    """Polyline with each corner of 90 degrees or sharper cut to a 45-degree segment c mm from the corner
    (less where a leg is short)."""
    if c <= 0 or len(pts) < 3:
        return list(pts)
    out = [pts[0]]
    for a, b, d in zip(pts, pts[1:], pts[2:]):
        ua, ub = (a[0] - b[0], a[1] - b[1]), (d[0] - b[0], d[1] - b[1])
        la, lb = math.hypot(*ua), math.hypot(*ub)
        if la < 1e-6 or lb < 1e-6 or (ua[0] * ub[0] + ua[1] * ub[1]) / (la * lb) < -0.01:
            out.append(b)                  # straight on or an obtuse bend: keep the point
            continue
        k = min(c, 0.45 * la, 0.45 * lb)
        out += [(b[0] + ua[0] / la * k, b[1] + ua[1] / la * k), (b[0] + ub[0] / lb * k, b[1] + ub[1] / lb * k)]
    out.append(pts[-1])
    return out


def add_track(board, net, pts, width, layer=pcbnew.B_Cu, locked=True, bend=BEND):
    """Tracks along a polyline, its right-angle corners drawn as 45-degree bends (bend45)."""
    out = []
    pts = bend45(pts, bend)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if abs(x0 - x1) < 1e-6 and abs(y0 - y1) < 1e-6:
            continue
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(P(x0, y0))
        t.SetEnd(P(x1, y1))
        t.SetWidth(mm(width))
        t.SetLayer(layer)
        t.SetNet(board.FindNet(net))
        t.SetLocked(locked)
        board.Add(t)
        out.append(t)
    return out


def add_keepout(board, layers, x0, y0, x1, y1, name, tracks=True, vias=True):
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetDoNotAllowTracks(tracks)
    z.SetDoNotAllowVias(vias)
    z.SetDoNotAllowPads(False)
    z.SetDoNotAllowFootprints(False)
    z.SetDoNotAllowZoneFills(False)
    ls = pcbnew.LSET()
    for layer in layers:
        ls.AddLayer(layer)
    z.SetLayerSet(ls)
    ol = z.Outline()
    ol.NewOutline()
    for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        ol.Append(mm(px), mm(py))
    z.SetZoneName(name)
    board.Add(z)
    return z


# ---- Freerouting ------------------------------------------------------------------------------------------
def freeroute(board_path, work, passes=40):
    """Export DSN, run Freerouting, import the session back into the board file. Returns log text."""
    work.mkdir(parents=True, exist_ok=True)
    b = pcbnew.LoadBoard(str(board_path))
    dsn, ses = work / "route.dsn", work / "route.ses"
    if not pcbnew.ExportSpecctraDSN(b, str(dsn)):
        raise RuntimeError("DSN export failed")
    r = subprocess.run([str(JAVA), "-jar", str(FREEROUTING), "-de", str(dsn), "-do", str(ses), "-mp", str(passes)],
                       capture_output=True, text=True, timeout=4 * 3600)
    if not ses.exists():
        raise RuntimeError("Freerouting produced no session:\n" + r.stdout[-2000:] + r.stderr[-2000:])
    b = pcbnew.LoadBoard(str(board_path))
    if not pcbnew.ImportSpecctraSES(b, str(ses)):
        raise RuntimeError("SES import failed")
    b.Save(str(board_path))
    return r.stdout


# ---- block copies ----------------------------------------------------------------------------------------
def net_map(src_fps, dst_fps):
    """Template net name -> instance net name, from pads of corresponding parts (same local reference)."""
    dst = {local_ref(f.GetReference()): f for f in dst_fps}
    m = {}
    for f in src_fps:
        g = dst.get(local_ref(f.GetReference()))
        if g is None:
            continue
        gp = {p.GetNumber(): p for p in g.Pads()}
        for p in f.Pads():
            q = gp.get(p.GetNumber())
            if q is not None and p.GetNetname():
                m[p.GetNetname()] = q.GetNetname()
    return m


def snapshot(t):
    """Plain-data copy of a track or via (Python proxies of board items can go stale after saving)."""
    t = t.Cast()
    if t.GetClass() == "PCB_VIA":
        x, y = xy(t.GetPosition())
        return ("V", round(x, 4), round(y, 4), t.GetNetname(), pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)),
                pcbnew.ToMM(t.GetDrillValue()))
    (x0, y0), (x1, y1) = xy(t.GetStart()), xy(t.GetEnd())
    return ("T", round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4), t.GetNetname(), pcbnew.ToMM(t.GetWidth()),
            t.GetLayer())


def copy_items(board, snaps, dx, dy, nets):
    """Add snapshots moved by (dx, dy), nets renamed through `nets` (unmapped nets keep their name)."""
    for s in snaps:
        if s[0] == "V":
            _, x, y, net, size, drill = s
            add_via(board, nets.get(net, net), x + dx, y + dy, size, drill)
        else:
            _, x0, y0, x1, y1, net, w, layer = s
            add_track(board, nets.get(net, net), [(x0 + dx, y0 + dy), (x1 + dx, y1 + dy)], w, layer)


# ---- 45-degree corners ------------------------------------------------------------------------------------
def chamfer_corners(board, cmax=1.0, clr=0.2, passes=3, nets=None):
    """Replace every right-angle (or sharper) corner between two tracks of one net, layer and width by a
    45-degree segment of up to cmax mm per leg, where the corner is a plain bend (no pad, via or third track
    there) and the new segment clears everything else by clr. Repeated passes let nested bundles chamfer from
    the inside out. Returns the number of corners cut."""
    from collections import defaultdict
    cu = Copper(board)
    pads = defaultdict(list)
    for f in board.GetFootprints():
        for p in f.Pads():
            pads[p.GetNetname()].append(p)
    vias = {(round(pcbnew.ToMM(v.GetPosition().x), 3), round(pcbnew.ToMM(v.GetPosition().y), 3))
            for v in board.GetTracks() if v.GetClass() == "PCB_VIA"}
    done = 0

    def key(v):
        return round(pcbnew.ToMM(v.x), 3), round(pcbnew.ToMM(v.y), 3)
    for _ in range(passes):
        ends = defaultdict(list)
        for t in board.GetTracks():
            if t.GetClass() == "PCB_VIA" or (nets and t.GetNetname() not in nets):
                continue
            ends[(t.GetNetCode(), t.GetLayer(), key(t.GetStart()))].append((t, "s"))
            ends[(t.GetNetCode(), t.GetLayer(), key(t.GetEnd()))].append((t, "e"))
        cut = 0
        for (code, layer, pt), ts in ends.items():
            if len(ts) != 2 or pt in vias:
                continue
            (a, ea), (b, eb) = ts
            if a.GetWidth() != b.GetWidth():
                continue
            net = a.GetNetname()
            pv = pcbnew.VECTOR2I(pcbnew.FromMM(pt[0]), pcbnew.FromMM(pt[1]))
            if any(p.HitTest(pv) for p in pads.get(net, ())):
                continue

            def other(t, e):
                return xy(t.GetEnd() if e == "s" else t.GetStart())
            (ax, ay), (bx, by) = other(a, ea), other(b, eb)
            cx, cy = pt
            ua, ub = (ax - cx, ay - cy), (bx - cx, by - cy)
            la, lb = math.hypot(*ua), math.hypot(*ub)
            if la < 1e-6 or lb < 1e-6:
                continue
            ua, ub = (ua[0] / la, ua[1] / la), (ub[0] / lb, ub[1] / lb)
            if ua[0] * ub[0] + ua[1] * ub[1] > 0.01:     # an obtuse bend (e.g. 135 degrees) is fine as it is
                continue
            c0 = min(cmax, 0.45 * la, 0.45 * lb)
            hw = pcbnew.ToMM(a.GetWidth()) / 2
            for c in [c0 * f for f in (1.0, 0.7, 0.5, 0.35, 0.25)] + [0.1]:   # the largest cut that clears
                if c > c0 + 1e-9 or c < 0.08:
                    continue
                p1 = (cx + ua[0] * c, cy + ua[1] * c)
                p2 = (cx + ub[0] * c, cy + ub[1] * c)
                if cu.seg_ok(layer, p1[0], p1[1], p2[0], p2[1], hw, net, clr=clr):
                    break
            else:
                continue
            for t, e, q in ((a, ea, p1), (b, eb, p2)):
                if e == "s":
                    t.SetStart(P(*q))
                else:
                    t.SetEnd(P(*q))
            for t in add_track(board, net, [p1, p2], pcbnew.ToMM(a.GetWidth()), layer, locked=a.IsLocked()):
                cu.add_track_item(t)
            cut += 1
        done += cut
        if not cut:
            break
    return done


def right_angles(board):
    """Corners of 90 degrees or sharper still on the board: [(net, layer name, x, y)] (bends between two
    tracks of one net and layer; a corner on a pad or via is not a track bend and is not listed)."""
    from collections import defaultdict
    ends = defaultdict(list)
    vias = {(round(pcbnew.ToMM(v.GetPosition().x), 3), round(pcbnew.ToMM(v.GetPosition().y), 3))
            for v in board.GetTracks() if v.GetClass() == "PCB_VIA"}
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            continue
        for a, b in ((t.GetStart(), t.GetEnd()), (t.GetEnd(), t.GetStart())):
            k = (t.GetNetname(), t.GetLayer(), round(pcbnew.ToMM(a.x), 3), round(pcbnew.ToMM(a.y), 3))
            ends[k].append((pcbnew.ToMM(b.x) - pcbnew.ToMM(a.x), pcbnew.ToMM(b.y) - pcbnew.ToMM(a.y)))
    pads = defaultdict(list)
    for f in board.GetFootprints():
        for p in f.Pads():
            pads[p.GetNetname()].append(p)
    out = []
    for (net, layer, x, y), vs in ends.items():
        if len(vs) != 2 or (x, y) in vias:
            continue
        if any(p.HitTest(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))) for p in pads.get(net, ())):
            continue                                  # a joint on a pad, not a track bend
        (ax, ay), (bx, by) = vs
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la > 1e-6 and lb > 1e-6 and (ax * bx + ay * by) / (la * lb) > -0.01:
            out.append((net, board.GetLayerName(layer), x, y))
    return out
