"""Grid maze router for the connections the block layouts leave open (power distribution, buses, the power
sections). Freerouting handles a block well but stalls on the whole board (thousands of pads); this router is
small, knows the project's rules and finishes in minutes.

Two copper layers (F.Cu, B.Cu; the inner layers are GND planes), a 0.1 mm grid, A* with 8 directions, a bend
penalty and a via cost. Every net is routed island by island (an island = copper already connected), from the
largest island outwards, so a net becomes one tree. Clearances follow the net classes and kicad/aesa.kicad_dru
(clock tracks 0.65 mm from other tracks, receive tracks 0.5 mm from digital ones), copper keeps
EDGE_KEEP from the board edge (room for the GND via fence), the array is a keep-out, and front copper inside
the supply areas (VDRV, +3V3A) costs extra so they stay whole.

  python scripts/maze.py [--in board] [--out board] [nets ...]
      route the open nets (all, or the named ones) of kicad/aesa.kicad_pcb (or --in), save to --out
"""

import heapq
import math
import re
import sys
import time
from array import array
from pathlib import Path

import numpy as np
import pcbnew
from matplotlib.path import Path as MPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from route_lib import add_track, add_via  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PCB = REPO / "kicad" / "aesa.kicad_pcb"
G = 0.1                      # grid (mm)
LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu)
FREE, HARD = -1, -2
EDGE_KEEP = 1.9              # copper to board edge: the fence vias sit 1.2 mm in (route.py FENCE_IN)
VIA = (0.6, 0.3)
VIA_COST = 25.0              # in grid steps (2.5 mm of track)
BEND = (0.0, 0.6, 3.0)       # extra cost for a 0 / 45 degree turn (90-degree turns are not allowed)
ZONE_COST = 8.0              # cost factor on front copper inside a supply area
BACK_COST = 1.15             # back copper (where the parts are) costs a little more than free front copper
MARGIN = 1.05                # cells added to every clearance: copper rasterised at cell centres (<= G/2 off) and
                             # diagonal steps passing between cell centres (<= G/sqrt2 closer)
NEAR = 1.6                   # within this of a connection's own pads the base clearance and a narrow track apply
NECKS = (0.25, 0.2, 0.15)    # last resort near fine-pitch pins: narrower still (only within NEAR of the pad)
HEUR_W = 1.25                # weighted A* (a little suboptimal, much faster)
# block areas that other nets should go round (cost factor on both layers), so a rail does not take the
# channels a block's own buses need: (x0, y0, x1, y1, nets allowed at normal cost, factor)
REGIONS = [(15.0, 229.8, 177.0, 281.1, r"^(TX_|\+5V$|VDRV$)", 6.0),        # TX zone
           (15.0, 27.5, 177.0, 69.5, r"^(ADC_|\+3V3$|\+3V3A$)", 6.0)]      # ADC rows
VERBOSE = False
RIP_TRIES = 3                # nets taken up, one at a time, to free an unreachable island
NET_BUDGET = 120.0           # seconds for one net (rip-up included); what is left after it is reported open
NO_STUBS = False

# track widths: (preferred, fallbacks...); near the pads every route narrows to the last value
WIDTHS = [(r"^VIN$", (1.5, 1.0, 0.5)), (r"^\+5V$", (1.0, 0.6, 0.4)),
          (r"^(/Power[^/]*/)?(VIN_JACK|VIN_PROT|VIN_F|5V_REG|SW_5V)$", (1.0, 0.6, 0.4)),
          (r"^(\+3V3|\+3V3A|3V3_MIC|VDRV|[+-]6V5|[+-]5VA)$", (0.5, 0.3)),
          (r"^/Power[^/]*/(.*_REG|SW_.*|VIN65)$", (0.6, 0.4, 0.3)),
          (r"(BCLK|FSYNC|_SCK|LATCH|MIC_CLK)", (0.25, 0.2)),
          (r".", (0.2,))]


def widths(net):
    return next(w for p, w in WIDTHS if re.search(p, net))


def clearance(net):
    return 0.25 if widths(net)[0] >= 1.0 else 0.2


def is_clock(net):
    return bool(re.search(r"(BCLK|FSYNC|_SCK|LATCH|MIC_CLK)", net))


class Grid:
    """Raster of the board's copper: per layer the net code at each cell (FREE / HARD / code), track classes
    for the spacing rules, layer links (vias, THT pads), drilled holes and supply-area fills."""

    def __init__(self, board, keepouts=()):
        bb = board.GetBoardEdgesBoundingBox()
        self.W, self.H = pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())
        self.nx, self.ny = int(math.ceil(self.W / G)) + 1, int(math.ceil(self.H / G)) + 1
        self.occ = np.full((2, self.ny, self.nx), FREE, np.int32)
        self.tcls = np.zeros((2, self.ny, self.nx), np.int8)     # 1 track, 2 clock track, 3 analog track, 4 GND
        self.link = np.full((self.ny, self.nx), FREE, np.int32)
        self.hole = np.zeros((self.ny, self.nx), bool)
        self.zone = np.full((2, self.ny, self.nx), FREE, np.int32)
        self.cost = np.ones((2, self.ny, self.nx), np.float32)
        self.cost[1] = BACK_COST
        self.smd = np.zeros((2, self.ny, self.nx), np.int32) - 1  # own SMD pad areas (no via in a pad)
        self.court = np.zeros((self.ny, self.nx), bool)   # IC courtyards: the clock spacing rule does not apply
        self.board = board
        self.netcode = {}
        self.netname = {}
        for name, ni in board.GetNetsByName().items():
            self.netcode[str(name)] = ni.GetNetCode()
            self.netname[ni.GetNetCode()] = str(name)
        self.gnd = self.netcode.get("GND", -9)
        self._edge(board)
        for x0, y0, x1, y1 in keepouts:
            self.occ[:, self._iy(y0):self._iy(y1) + 1, self._ix(x0):self._ix(x1) + 1] = HARD
        for f in board.GetFootprints():
            for p in f.Pads():
                self.add_pad(p)
            if f.GetReference().startswith("U"):
                for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
                    m = self._poly_mask(f.GetCourtyard(layer))
                    if m is not None:
                        self.court[m[0]] |= m[1]
        for t in board.GetTracks():
            self.add_item(t)
        for z in board.Zones():
            if not z.GetIsRuleArea() and z.GetNetname() in ("VDRV", "+3V3A"):
                for li, layer in enumerate(LAYERS):
                    if z.IsOnLayer(layer):
                        m = self._poly_mask(z.GetFilledPolysList(layer))
                        if m is not None:
                            sl, mask = m
                            self.zone[li][sl][mask] = z.GetNetCode()
                            self.cost[li][sl][mask] = ZONE_COST

    # ---- coordinates ----
    def _ix(self, x):
        return min(self.nx - 1, max(0, int(round(x / G))))

    def _iy(self, y):
        return min(self.ny - 1, max(0, int(round(y / G))))

    def _box(self, x0, y0, x1, y1):
        ix0, ix1 = max(0, int(math.floor(x0 / G))), min(self.nx - 1, int(math.ceil(x1 / G)))
        iy0, iy1 = max(0, int(math.floor(y0 / G))), min(self.ny - 1, int(math.ceil(y1 / G)))
        xs = np.arange(ix0, ix1 + 1) * G
        ys = np.arange(iy0, iy1 + 1) * G
        return (slice(iy0, iy1 + 1), slice(ix0, ix1 + 1)), xs, ys

    def _edge(self, board, bevel=3.0):
        """Cells closer than EDGE_KEEP to the board outline (rectangle with 45-degree corner bevels) are HARD."""
        ys, xs = np.mgrid[0:self.ny, 0:self.nx]
        X, Y = xs * G, ys * G
        bad = (X < EDGE_KEEP) | (Y < EDGE_KEEP) | (X > self.W - EDGE_KEEP) | (Y > self.H - EDGE_KEEP)
        for cx in (0.0, self.W):
            for cy in (0.0, self.H):
                bad |= np.abs(X - cx) + np.abs(Y - cy) < bevel + EDGE_KEEP * math.sqrt(2)
        self.occ[:, bad] = HARD

    @staticmethod
    def _seg_dist(X, Y, x0, y0, x1, y1):
        vx, vy = x1 - x0, y1 - y0
        L = vx * vx + vy * vy
        t = np.zeros_like(X) if L == 0 else np.clip(((X - x0) * vx + (Y - y0) * vy) / L, 0, 1)
        return np.hypot(X - (x0 + t * vx), Y - (y0 + t * vy))

    def _poly_mask(self, polys):
        if polys.OutlineCount() == 0:
            return None
        bb = polys.BBox()
        sl, gx, gy = self._box(pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY()), pcbnew.ToMM(bb.GetRight()),
                               pcbnew.ToMM(bb.GetBottom()))
        GX, GY = np.meshgrid(gx, gy)
        P = np.c_[GX.ravel(), GY.ravel()]
        mask = np.zeros(GX.shape, bool)
        for i in range(polys.OutlineCount()):
            rings = [polys.Outline(i)] + [polys.Hole(i, h) for h in range(polys.HoleCount(i))]
            for k, r in enumerate(rings):
                pts = [(pcbnew.ToMM(r.CPoint(j).x), pcbnew.ToMM(r.CPoint(j).y)) for j in range(r.PointCount())]
                if len(pts) < 3:
                    continue
                m = MPath(pts).contains_points(P).reshape(GX.shape)
                mask = mask | m if k == 0 else mask & ~m
        return sl, mask

    # ---- adding copper ----
    def add_pad(self, p):
        code = p.GetNetCode() if p.GetNetname() else HARD
        tht = p.GetDrillSize().x > 0
        for li, layer in enumerate(LAYERS):
            if not p.IsOnLayer(layer) or (tht and p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
                                          and pcbnew.ToMM(p.GetSize(layer).x) <= pcbnew.ToMM(p.GetDrillSize().x)):
                continue
            poly = pcbnew.SHAPE_POLY_SET()
            p.TransformShapeToPolygon(poly, layer, 0, pcbnew.FromMM(0.005), pcbnew.ERROR_INSIDE)
            m = self._poly_mask(poly)
            if m is None:
                continue
            sl, mask = m
            self.occ[li][sl][mask] = code
            if not tht and code >= 0:
                self.smd[li][sl][mask] = code
        if tht:
            x, y = pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)
            r = pcbnew.ToMM(p.GetDrillSize().x) / 2
            sl, gx, gy = self._box(x - r, y - r, x + r, y + r)
            GX, GY = np.meshgrid(gx, gy)
            d = np.hypot(GX - x, GY - y) <= r
            self.hole[sl] |= d
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:      # copper keeps clear of an unplated hole
                for li in range(2):
                    self.occ[li][sl][d] = HARD
            elif p.GetNetname():
                self.link[sl][d] = p.GetNetCode()

    def _cells(self, t):
        """Raster of a track or via: (kind, layer index or None, slice, copper mask, hole mask or None)."""
        if t.GetClass() == "PCB_VIA":
            v = t.Cast()
            x, y = pcbnew.ToMM(v.GetPosition().x), pcbnew.ToMM(v.GetPosition().y)
            r = pcbnew.ToMM(v.GetWidth(pcbnew.F_Cu)) / 2
            sl, gx, gy = self._box(x - r, y - r, x + r, y + r)
            GX, GY = np.meshgrid(gx, gy)
            d = np.hypot(GX - x, GY - y)
            return "via", None, sl, d <= r, d <= pcbnew.ToMM(v.GetDrillValue()) / 2
        if t.GetLayer() not in LAYERS:
            return None
        x0, y0 = pcbnew.ToMM(t.GetStart().x), pcbnew.ToMM(t.GetStart().y)
        x1, y1 = pcbnew.ToMM(t.GetEnd().x), pcbnew.ToMM(t.GetEnd().y)
        hw = pcbnew.ToMM(t.GetWidth()) / 2
        sl, gx, gy = self._box(min(x0, x1) - hw, min(y0, y1) - hw, max(x0, x1) + hw, max(y0, y1) + hw)
        GX, GY = np.meshgrid(gx, gy)
        return "track", LAYERS.index(t.GetLayer()), sl, self._seg_dist(GX, GY, x0, y0, x1, y1) <= hw, None

    def add_item(self, t):
        c = self._cells(t)
        if c is None:
            return
        kind, li, sl, m, hole = c
        if not m.size:                                   # wholly off the grid
            return
        code, net = t.GetNetCode(), t.GetNetname()
        if kind == "via":
            for k in range(2):
                self.occ[k][sl][m] = code
            self.link[sl][m] = code
            self.hole[sl] |= hole
            return
        self.occ[li][sl][m] = code
        self.tcls[li][sl][m] = 4 if net == "GND" else 3 if net.startswith("RX") else 2 if is_clock(net) else 1

    def remove_items(self, items, code):
        """Clear removed tracks/vias of one net from the raster, then repaint that net's remaining copper
        (pads, tracks, vias) around them, which the clearing may have touched."""
        boxes = []
        for t in items:
            c = self._cells(t)
            if c is None:
                continue
            kind, li, sl, m, hole = c
            for k in (range(2) if kind == "via" else (li,)):
                own = self.occ[k][sl] == code
                self.occ[k][sl][m & own] = FREE
                self.tcls[k][sl][m & own] = 0
            if kind == "via":
                self.link[sl][m & (self.link[sl] == code)] = FREE
                self.hole[sl][hole] = False
            boxes.append(sl)
        if not boxes:
            return
        y0, y1 = min(b[0].start for b in boxes), max(b[0].stop for b in boxes)
        x0, x1 = min(b[1].start for b in boxes), max(b[1].stop for b in boxes)
        bx0, by0, bx1, by1 = x0 * G - 1, y0 * G - 1, x1 * G + 1, y1 * G + 1
        for f in self.board.GetFootprints():
            for p in f.Pads():
                if p.GetNetCode() == code:
                    px, py = pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)
                    if bx0 - 3 <= px <= bx1 + 3 and by0 - 3 <= py <= by1 + 3:
                        self.add_pad(p)
        for t in self.board.GetTracks():          # the removed items are off the board already
            if t.GetNetCode() != code:
                continue
            bb = t.GetBoundingBox()
            if pcbnew.ToMM(bb.GetRight()) >= bx0 and pcbnew.ToMM(bb.GetX()) <= bx1 and \
                    pcbnew.ToMM(bb.GetBottom()) >= by0 and pcbnew.ToMM(bb.GetY()) <= by1:
                self.add_item(t)

    # ---- per-net maps ----
    def maps(self, code, net, hw, crop, near):
        """blocked (2, h, w), via_bad (h, w) for a track of half-width hw of `net` inside crop; `near` (h, w)
        marks cells close to this connection's pads, where only the base clearance applies."""
        sl = crop
        c = clearance(net)
        occ = self.occ[:, sl[0], sl[1]]
        tcls = self.tcls[:, sl[0], sl[1]]
        foreign = (occ != FREE) & (occ != code)
        clock = is_clock(net)
        blocked = np.zeros(occ.shape, bool)
        for li in range(2):
            f = foreign[li]
            b = dilate(f, (hw + c) / G + MARGIN)
            extra = np.zeros_like(f)
            extra |= dilate(f & (tcls[li] == 3), (hw + 0.5) / G + MARGIN)
            if clock:
                extra |= dilate(f & ((tcls[li] == 1) | (tcls[li] == 3)), (hw + 0.65) / G + MARGIN)
            else:
                extra |= dilate(f & (tcls[li] == 2), (hw + 0.65) / G + MARGIN)
            blocked[li] = b | (extra & ~near & ~self.court[sl])
            blocked[li] |= occ[li] == HARD
        vr = VIA[0] / 2
        via_bad = dilate(foreign[0], (vr + c) / G + MARGIN) | dilate(foreign[1], (vr + c) / G + MARGIN)
        via_bad |= dilate(self.hole[sl], (VIA[1] / 2 + 0.5) / G + MARGIN)
        smd = self.smd[:, sl[0], sl[1]]
        via_bad |= dilate((smd[0] >= 0) | (smd[1] >= 0), vr / G + 0.5)
        via_bad |= (occ[0] == HARD) | (occ[1] == HARD)
        return blocked, via_bad


def dilate(mask, r):
    """Binary dilation with a disc of radius r cells (r may be fractional)."""
    R = int(math.floor(r))
    if R <= 0 or not mask.any():
        return mask.copy()
    h = [mask]
    for w in range(1, R + 1):              # horizontal dilations by 0..R
        prev = h[-1]
        cur = prev.copy()
        cur[:, w:] |= mask[:, :-w]
        cur[:, :-w] |= mask[:, w:]
        h.append(cur)
    out = np.zeros_like(mask)
    for dy in range(-R, R + 1):
        w = int(math.floor(math.sqrt(max(0.0, r * r - dy * dy))))
        src = h[w]
        if dy == 0:
            out |= src
        elif dy > 0:
            out[dy:] |= src[:-dy]
        else:
            out[:dy] |= src[-dy:]
    return out


def label(own, link):
    """Connected components of own copper (2, h, w) joined across layers at link cells: (labels, count)."""
    lab = np.zeros(own.shape, np.int32)
    parent = [0]

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for li in range(2):
        prev = None
        for y in range(own.shape[1]):
            row = own[li, y]
            if not row.any():
                prev = None
                continue
            d = np.diff(np.r_[0, row.astype(np.int8), 0])
            starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
            ids = []
            for s, e in zip(starts, ends):
                k = len(parent)
                parent.append(k)
                lab[li, y, s:e] = k
                ids.append(k)
                if prev is not None:   # 8-connected overlap with the row above
                    lo, hi = max(0, s - 1), min(row.size, e + 1)
                    for k2 in np.unique(prev[lo:hi]):
                        if k2:
                            union(k, int(k2))
            prev = lab[li, y]
    ys, xs = np.nonzero(link & own[0] & own[1])
    for y, x in zip(ys, xs):
        union(int(lab[0, y, x]), int(lab[1, y, x]))
    roots = {}
    flat = np.array([find(i) for i in range(len(parent))], np.int32)
    out = flat[lab]
    for v in np.unique(out):
        if v:
            roots[int(v)] = len(roots) + 1
    remap = np.zeros(len(parent), np.int32)
    for v, k in roots.items():
        remap[v] = k
    return remap[out], len(roots)


DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


def astar(blocked, via_bad, cost, src, dst, budget=800_000):
    """A* over (layer, y, x) in a crop. src/dst: (2, h, w) bool. Returns the path [(l, y, x), ...] or None."""
    L, h, w = blocked.shape
    n = h * w
    free = bytearray((~blocked).astype(np.uint8).ravel().tobytes())
    vok = bytearray((~via_bad).astype(np.uint8).ravel().tobytes())
    goal = bytearray(dst.astype(np.uint8).ravel().tobytes())
    cst = array("f", cost.astype(np.float32).ravel().tobytes())
    g = array("d", [math.inf]) * (L * n)
    par = array("i", [-1]) * (L * n)
    dirn = bytearray(b"\x09") * (L * n)
    ys, xs = np.nonzero(dst.any(axis=0))
    gx0, gx1, gy0, gy1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    heap = []
    for li, y, x in zip(*np.nonzero(src)):
        i = int(li) * n + int(y) * w + int(x)
        g[i] = 0.0
        heap.append((0.0, 0.0, i))
    heapq.heapify(heap)
    SQ2 = math.sqrt(2.0)
    steps = [(dx, dy, dy * w + dx, SQ2 if dx and dy else 1.0) for dx, dy in DIRS]
    found = None
    pops = 0
    while heap:
        f, gi, i = heapq.heappop(heap)
        if gi > g[i]:
            continue                     # stale entry
        pops += 1
        if pops > budget:
            break
        if goal[i]:
            found = i
            break
        li, r = divmod(i, n)
        y, x = divmod(r, w)
        d0 = dirn[i]
        for k, (dx, dy, off, base) in enumerate(steps):
            nx_, ny_ = x + dx, y + dy
            if nx_ < 0 or ny_ < 0 or nx_ >= w or ny_ >= h:
                continue
            j = i + off
            if not (free[j] or goal[j]):
                continue
            if dx and dy and not (free[i + dx] or free[i + dy * w]):
                continue
            turn = 0 if d0 == 9 else min((k - d0) % 8, (d0 - k) % 8)
            if turn > 1:                 # 45-degree bends only: never a right angle
                continue
            ng = gi + base * cst[j] + BEND[turn]
            if ng < g[j]:
                g[j] = ng
                par[j] = i
                dirn[j] = k
                hx = max(gx0 - nx_, 0, nx_ - gx1)
                hy = max(gy0 - ny_, 0, ny_ - gy1)
                heapq.heappush(heap, (ng + HEUR_W * (max(hx, hy) + (SQ2 - 1) * min(hx, hy)), ng, j))
        if vok[r]:                       # via to the other layer
            j = (1 - li) * n + r
            if free[j] or goal[j]:
                ng = gi + VIA_COST
                if ng < g[j]:
                    g[j] = ng
                    par[j] = i
                    dirn[j] = 9
                    hx = max(gx0 - x, 0, x - gx1)
                    hy = max(gy0 - y, 0, y - gy1)
                    heapq.heappush(heap, (ng + HEUR_W * (max(hx, hy) + (SQ2 - 1) * min(hx, hy)), ng, j))
    if found is None:
        return None
    i = found
    path = []
    while i != -1:
        li, r = divmod(i, n)
        y, x = divmod(r, w)
        path.append((li, y, x))
        i = par[i]
    return path[::-1]


COARSE = 10                  # coarse cells (1 mm) for the global route of long connections
LONG = 400                   # crops wider than this (fine cells) get a coarse corridor first


def coarse_corridor(blocked, via_bad, cost, src, dst, grow):
    """Global route on a 1 mm grid (a coarse cell is open if 30 % of its fine cells are), returned as a fine
    (h, w) mask: the coarse path widened by `grow` coarse cells. None if there is no coarse route."""
    L, h, w = blocked.shape
    H, Wc = -(-h // COARSE), -(-w // COARSE)

    def pool(a, fn):
        pad = np.zeros(a.shape[:-2] + (H * COARSE, Wc * COARSE), a.dtype)
        pad[..., :h, :w] = a
        r = pad.reshape(a.shape[:-2] + (H, COARSE, Wc, COARSE))
        return fn(r, axis=(-3, -1))
    free = pool((~blocked).astype(np.float32), np.mean) >= 0.3
    cb = ~free
    cv = pool(~via_bad, np.any)              # coarse cells with somewhere for a via
    cc = pool(cost, np.mean)
    cs = pool(src, np.any)
    cd = pool(dst, np.any)
    cb &= ~dilate((cs | cd).any(axis=0), 3.5)[None]   # the fine search handles the escape from the pins
    cv |= dilate((cs | cd).any(axis=0), 3.5)
    path = astar(cb & ~cs & ~cd, ~cv, cc, cs, cd, budget=1_000_000)
    if path is None:
        return None
    m = np.zeros((H, Wc), bool)
    for _, y, x in path:
        m[y, x] = True
    m = dilate(m, grow + 0.5)
    return np.repeat(np.repeat(m, COARSE, 0), COARSE, 1)[:h, :w]


def path_items(path, ox, oy):
    """Path cells -> (tracks per layer as point lists, via positions), in mm."""
    runs, vias = [], []
    cur = None
    for li, y, x in path:
        pt = (float((int(ox) + int(x)) * G), float((int(oy) + int(y)) * G))
        if cur is None or cur[0] != li:
            if cur is not None:
                vias.append(pt)
            cur = (li, [pt])
            runs.append(cur)
        else:
            cur[1].append(pt)
    out = []
    for li, pts in runs:
        if len(pts) < 2:
            continue
        keep = [pts[0]]
        for a, b, c in zip(pts, pts[1:], pts[2:]):
            if (round((b[0] - a[0]) / G), round((b[1] - a[1]) / G)) != (round((c[0] - b[0]) / G), round((c[1] - b[1]) / G)):
                keep.append(b)
        keep.append(pts[-1])
        out.append((li, keep))
    return out, vias


CACHE = REPO / "build" / "maze_grid.npz"
ARRAYS = ("occ", "tcls", "link", "hole", "zone", "cost", "smd", "court")


def load_grid(board, src, keepouts):
    """Grid of the board, from build/maze_grid.npz when that was made from the same file (size and time)."""
    if src is None:                       # a board being built in memory: no file to key a cache on
        return Grid(board, keepouts)
    st = Path(src).stat()
    key = f"{Path(src).resolve()}|{st.st_size}|{st.st_mtime_ns}|{keepouts}|{EDGE_KEEP}|{ZONE_COST}|{BACK_COST}|court"
    if CACHE.exists():
        z = np.load(CACHE, allow_pickle=False)
        if str(z["key"]) == key:
            g = Grid.__new__(Grid)
            g.board = board
            for a in ARRAYS:
                setattr(g, a, z[a])
            g.ny, g.nx = g.occ.shape[1:]
            bb = board.GetBoardEdgesBoundingBox()
            g.W, g.H = pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())
            g.netcode, g.netname = {}, {}
            for name, ni in board.GetNetsByName().items():
                g.netcode[str(name)] = ni.GetNetCode()
                g.netname[ni.GetNetCode()] = str(name)
            g.gnd = g.netcode.get("GND", -9)
            return g
    g = Grid(board, keepouts)
    np.savez(CACHE, key=key, **{a: getattr(g, a) for a in ARRAYS})
    return g


class Router:
    def __init__(self, board, keepouts=(), log=print, src=PCB):
        t = time.time()
        self.board = board
        self.grid = load_grid(board, src, keepouts)
        self.log = log
        self.log(f"grid {self.grid.nx} x {self.grid.ny}, built in {time.time() - t:.0f} s")
        self.added = {}            # net -> tracks and vias this router added (what rip-up may take up)
        self.routed = set()        # nets routed so far, in order (only these are taken up: a net that has only
                                   # its escape stubs would otherwise be routed early, out of order)
        self.failed_after_rip = {}

    def own(self, code, sl):
        g = self.grid
        own = (g.occ[:, sl[0], sl[1]] == code) | (g.zone[:, sl[0], sl[1]] == code)
        return own, g.link[sl] == code

    def route_net(self, net, depth=0):
        """Join the islands of one net into one tree, nearest island first. An island that cannot be reached
        gets one round of rip-up: the nets this router laid next to it are taken up one at a time (at most
        RIP_TRIES), the connection is tried again, and the nets taken up are routed again afterwards.
        Returns (made, failed)."""
        g = self.grid
        code = g.netcode[net]
        full = (slice(0, g.ny), slice(0, g.nx))
        made = failed = 0
        skip = set()                              # a cell of each island that could not be reached
        t_end = time.time() + NET_BUDGET
        while True:
            if time.time() > t_end:
                self.log(f"    {net}: out of time ({NET_BUDGET:.0f} s), rest left open")
                failed += 1
                break
            own, link = self.own(code, full)
            lab, n = label(own, link)
            if n <= 1:
                break
            sizes = np.bincount(lab.ravel())[1:]
            tree = int(np.argmax(sizes)) + 1
            others = [k for k in range(1, n + 1) if k != tree and not any(lab[c] == k for c in skip)]
            if not others:
                break
            k, (ty, tx), (py, px) = self.nearest(lab, tree, others)
            ok = self.connect(net, code, lab == tree, lab == k, (ty, tx), (py, px))
            if not ok and depth == 0 and time.time() < t_end:
                ok = self.rip_and_retry(net, code, lab, tree, k, (ty, tx), (py, px))
            if ok:
                made += 1
            else:
                failed += 1
                li, y, x = [int(v[0]) for v in np.nonzero(lab == k)]
                skip.add((li, y, x))
                self.log(f"    {net}: no route to the island at ({x * G:.1f}, {y * G:.1f})")
        return made, failed

    def blockers(self, island, code, reach=2.5):
        """Nets this router laid within `reach` mm of an island, the most copper first."""
        g = self.grid
        _, ys, xs = np.nonzero(island)
        m = int(reach / G)
        sl = (slice(max(0, ys.min() - m), ys.max() + m + 1), slice(max(0, xs.min() - m), xs.max() + m + 1))
        occ = g.occ[:, sl[0], sl[1]]
        codes, counts = np.unique(occ[occ >= 0], return_counts=True)
        out = []
        for c, n in sorted(zip(codes.tolist(), counts.tolist()), key=lambda t: -t[1]):
            name = g.netname.get(c)
            if c != code and name != "GND" and name in self.routed and self.added.get(name):
                out.append(name)
        return out

    def rip(self, net):
        """Take up everything this router laid for a net (board and raster)."""
        items = self.added.pop(net, [])
        for t in items:
            self.board.Remove(t)
        self.grid.remove_items(items, self.grid.netcode[net])
        return len(items)

    def rip_and_retry(self, net, code, lab, tree, k, t, p):
        ripped, ok = [], False
        for other in self.blockers(lab == k, code)[:RIP_TRIES]:
            self.rip(other)
            ripped.append(other)
            if self.connect(net, code, lab == tree, lab == k, t, p):
                ok = True
                break
        for other in ripped:                      # put the nets taken up back
            m, f = self.route_net(other, depth=1)
            self.log(f"    rip-up for {net}: {other} taken up and routed again (+{m}, fail {f})")
            self.failed_after_rip[other] = f
        return ok

    @staticmethod
    def nearest(lab, tree, others, sample=1500):
        """The unjoined island closest to the tree, with the closest cell pair (island cell, tree cell)."""
        rng = np.random.default_rng(0)

        def cells(mask):
            ys, xs = np.nonzero(mask.any(axis=0))
            pts = np.c_[ys, xs]
            return pts if len(pts) <= sample else pts[rng.choice(len(pts), sample, replace=False)]
        T = cells(lab == tree)
        best = None
        for k in others:
            K = cells(lab == k)
            d = np.hypot(K[:, None, 0] - T[None, :, 0], K[:, None, 1] - T[None, :, 1])
            a, b = np.unravel_index(np.argmin(d), d.shape)
            if best is None or d[a, b] < best[0]:
                best = (d[a, b], k, tuple(K[a]), tuple(T[b]))
        return best[1], best[2], best[3]

    def connect(self, net, code, src, dst, t, p):
        """One connection from src copper to dst copper (t, p: a close cell pair), in a crop around them that
        grows until a route is found; adds it to the board and the grid."""
        g = self.grid
        (ty, tx), (py, px) = t, p
        for pad_mm in (6.0, 20.0, 60.0, 400.0):
            m = int(pad_mm / G)
            y0, y1 = max(0, min(ty, py) - m), min(g.ny, max(ty, py) + m + 1)
            x0, x1 = max(0, min(tx, px) - m), min(g.nx, max(tx, px) + m + 1)
            sl = (slice(y0, y1), slice(x0, x1))
            s, d = src[:, sl[0], sl[1]], dst[:, sl[0], sl[1]]
            if not s.any() or not d.any():
                continue
            ends = (s | d).any(axis=0)
            pads = ((g.smd[:, sl[0], sl[1]] == code).any(axis=0)) | (g.link[sl] == code)
            near = dilate(ends & pads, NEAR / G)
            ws = widths(net)
            tries = [(wd, ws[-1]) for wd in ws] + [(ws[-1], n) for n in NECKS if n < ws[-1]]
            for wd, narrow in tries:
                blocked, via_bad = g.maps(code, net, wd / 2, sl, near)
                if wd != narrow:      # a narrow track is allowed near the pads
                    nb, _ = g.maps(code, net, narrow / 2, sl, near)
                    blocked = np.where(near[None], nb, blocked)
                t0 = time.time()
                cost = self.cost_for(net, sl)
                path = None
                if max(x1 - x0, y1 - y0) > LONG:      # long: fine route inside a coarse corridor
                    for grow in (2, 5):
                        cor = coarse_corridor(blocked, via_bad, cost, s, d, grow)
                        if cor is None:
                            break
                        path = astar(blocked | ~cor[None], via_bad | ~cor, cost, s, d)
                        if path:
                            break
                else:
                    path = astar(blocked, via_bad, cost, s, d)
                if VERBOSE:
                    self.log(f"      crop {x1 - x0}x{y1 - y0} w {wd}/{narrow}: {'ok' if path else 'no'} "
                             f"{time.time() - t0:.1f} s")
                if path:
                    self.commit(net, path, x0, y0, wd, narrow, near)
                    return True
            if y0 == 0 and x0 == 0 and y1 == g.ny and x1 == g.nx:
                break
        return False

    def cost_for(self, net, sl):
        """Cost map of a crop for one net: the base costs, raised in block areas it does not belong to."""
        g = self.grid
        cost = g.cost[:, sl[0], sl[1]]
        for x0, y0, x1, y1, allowed, k in REGIONS:
            if re.search(allowed, net):
                continue
            ys = slice(max(0, int(y0 / G) - sl[0].start), max(0, int(y1 / G) - sl[0].start))
            xs = slice(max(0, int(x0 / G) - sl[1].start), max(0, int(x1 / G) - sl[1].start))
            if ys.stop > ys.start and xs.stop > xs.start:
                cost = cost.copy() if cost.base is not None else cost
                cost[:, ys, xs] *= k
        return cost

    def commit(self, net, path, ox, oy, wd, narrow, near):
        """Add the path as tracks (narrow where it runs near the pads) and vias."""
        layer_ids = {0: pcbnew.F_Cu, 1: pcbnew.B_Cu}
        items = []
        run = []

        def flush():
            pts = [((ox + x) * G, (oy + y) * G) for _, y, x in run]
            if len(pts) >= 2:
                w = narrow if any(near[y, x] for _, y, x in run) else wd
                keep = [pts[0]]
                for a, b, c in zip(pts, pts[1:], pts[2:]):
                    if (round((b[0] - a[0]) / G), round((b[1] - a[1]) / G)) !=                             (round((c[0] - b[0]) / G), round((c[1] - b[1]) / G)):
                        keep.append(b)
                keep.append(pts[-1])
                items.extend(add_track(self.board, net, [(float(x), float(y)) for x, y in keep], w,
                                       layer_ids[run[0][0]], locked=False))
        for c in path:
            if run and c[0] != run[-1][0]:            # layer change: a via
                flush()
                _, y, x = c
                items.append(add_via(self.board, net, float((ox + x) * G), float((oy + y) * G), *VIA,
                                     locked=False))
                run = [c]
                continue
            if run and bool(near[c[1], c[2]]) != bool(near[run[-1][1], run[-1][2]]):
                flush()                               # width changes at the edge of the near zone
                run = [run[-1]]
            run.append(c)
        flush()
        for it in items:
            self.grid.add_item(it)
        self.added.setdefault(net, []).extend(items)


def escape_stubs(router, nets, pitch_max=0.65, w=0.2):
    """Fan-out: a straight stub from every fine-pitch IC pin of an open net out to just past the courtyard,
    before any routing, so a neighbour routed earlier cannot box the pin in. Returns the number drawn."""
    from route_lib import Copper
    g, board = router.grid, router.board
    cu = Copper(board)
    ends = {}                                                  # net -> track ends (pins routed by hand get no stub)
    for t in board.GetTracks():
        if t.GetClass() == "PCB_TRACK":
            ends.setdefault(t.GetNetname(), []).extend((e, t.GetWidth() // 2) for e in (t.GetStart(), t.GetEnd()))
    made = 0
    for f in board.GetFootprints():
        if not f.GetReference().startswith("U"):
            continue
        pads = [p for p in f.Pads() if p.GetDrillSize().x == 0]
        if len(pads) < 6:
            continue
        xs = [pcbnew.ToMM(p.GetPosition().x) for p in pads]
        ys = [pcbnew.ToMM(p.GetPosition().y) for p in pads]
        pts = sorted(zip(xs, ys))
        pitch = min((math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(pts, pts[1:])), default=9)
        if pitch > pitch_max:
            continue
        court = f.GetCourtyard(pcbnew.B_CrtYd if f.IsFlipped() else pcbnew.F_CrtYd).BBox()
        cx0, cy0, cx1, cy1 = (pcbnew.ToMM(court.GetX()), pcbnew.ToMM(court.GetY()), pcbnew.ToMM(court.GetRight()),
                              pcbnew.ToMM(court.GetBottom()))
        ox, oy = (cx0 + cx1) / 2, (cy0 + cy1) / 2
        for p in pads:
            net = p.GetNetname()
            if net not in nets or net == "GND" or any(p.HitTest(e, r) for e, r in ends.get(net, ())):
                continue
            bb = p.GetBoundingBox()
            if min(pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())) > 1.4:
                continue                                       # exposed pad
            px, py = pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)
            dx, dy = px - ox, py - oy
            if abs(dx) >= abs(dy):                            # pin on a left/right side: stub along x
                ex, ey = (cx1 + 0.15 if dx > 0 else cx0 - 0.15), py
            else:
                ex, ey = px, (cy1 + 0.15 if dy > 0 else cy0 - 0.15)
            li = 1 if p.IsOnLayer(pcbnew.B_Cu) else 0
            # exact geometry here (the grid's margin would close the gaps between 0.5 mm pitch pins)
            if not cu.seg_ok(LAYERS[li], px, py, ex, ey, w / 2, net, clr=0.2):
                continue
            for t in add_track(board, net, [(px, py), (ex, ey)], w, LAYERS[li], locked=False):
                g.add_item(t)
                cu.add_track_item(t)
                router.added.setdefault(net, []).append(t)
            made += 1
    return made


def open_nets(board):
    """Nets with more than one island (KiCad connectivity), largest first."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from open_nets import islands
    return islands(board)


ORDER = [r"^(/Power|Net-\((U95|D95|C95|C99))",         # power-section internals, header filters
         r"^VIN$",
         r"^TX_(SCK|LATCH)", r"^TX_(OE|CLR)", r"^TX_SDI", r"^TX_",    # TX buses (least room): clocks (wider
                                                                      # spacing), then the shared lines
         r"^(\+3V3|3V3_MIC|[+-]6V5|VDRV|[+-]5VA|\+3V3A)$", r"^\+5V$",  # the other rails find their way round
         r"^ADC_", r"^MIC_", r"."]


def order(nets):
    """Routing order: power sections, VIN, the TX buses, the other rails, the ADC and mic buses."""
    return sorted(nets, key=lambda n: (next(i for i, p in enumerate(ORDER) if re.search(p, n)), n))


def main(args):
    src = dst = PCB
    if "--in" in args:
        i = args.index("--in")
        src = dst = Path(args[i + 1])
        args = args[:i] + args[i + 2:]
    if "--out" in args:
        i = args.index("--out")
        dst = Path(args[i + 1])
        args = args[:i] + args[i + 2:]
    global VERBOSE, NO_STUBS
    VERBOSE = "-v" in args
    NO_STUBS = "--no-stubs" in args
    names = [a for a in args if a not in ("-v", "--no-stubs")]
    board = pcbnew.LoadBoard(str(src))
    import place
    keep = [(place.MARGIN - 0.5, place.TOP - 0.3, place.W - place.MARGIN + 0.5, place.ARRAY_END + 0.3)]
    r = Router(board, keep, src=src)
    todo = names or order([n for n in open_nets(board) if n != "GND"])
    if not NO_STUBS:
        print(f"escape stubs: {escape_stubs(r, set(todo))}", flush=True)
    t0 = time.time()
    tot_m = tot_f = 0
    for net in todo:
        t = time.time()
        m, f = r.route_net(net)
        r.routed.add(net)
        tot_m += m
        tot_f += f
        print(f"{net:32s} +{m} fail {f}  {time.time() - t:5.1f} s", flush=True)
    for k in range(2):                     # nets left open (also by a rip-up) get another go
        left = [n for n in order(list(open_nets(board))) if n in set(todo)]
        if not left:
            break
        print(f"== pass {k + 2}: {len(left)} nets still open", flush=True)
        for net in left:
            t = time.time()
            m, f = r.route_net(net)
            print(f"{net:32s} +{m} fail {f}  {time.time() - t:5.1f} s", flush=True)
    left = {n: v for n, v in open_nets(board).items() if n in set(todo)}
    print(f"still open: {len(left)} nets, {sum(len(v) - 1 for v in left.values())} connections: "
          + ", ".join(sorted(left)[:40]), flush=True)
    board.Save(str(dst))
    print(f"made {tot_m}, failed {tot_f}, {time.time() - t0:.0f} s; saved {dst}")


if __name__ == "__main__":
    main(sys.argv[1:])
