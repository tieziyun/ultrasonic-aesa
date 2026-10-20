"""Minimal LTspice .raw reader (binary or ASCII), stdlib + numpy-free.

  from ltraw import read_raw
  d = read_raw("rx_noise.raw")   # {"frequency": [...], "V(onoise)": [...], ...}
Complex data (AC) comes back as Python complex numbers.
"""

import struct


def read_raw(path):
    data = open(path, "rb").read()
    utf16 = data[1:2] == b"\x00"
    enc = "utf-16-le" if utf16 else "latin-1"
    marker_bin = "Binary:\n".encode(enc)
    marker_txt = "Values:\n".encode(enc)
    kb, kt = data.find(marker_bin), data.find(marker_txt)
    binary = kb >= 0 and (kt < 0 or kb < kt)
    k = kb if binary else kt
    head = data[:k].decode(enc)
    body = data[k + len(marker_bin if binary else marker_txt):]
    lines = head.splitlines()
    info, names = {}, []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("Variables:"):
            i += 1
            while i < len(lines) and lines[i].startswith(("\t", " ")):
                names.append(lines[i].split()[1])
                i += 1
            continue
        if ":" in line:
            a, b = line.split(":", 1)
            info[a.strip()] = b.strip()
        i += 1
    nvar, npts = int(info["No. Variables"]), int(info["No. Points"])
    flags = info.get("Flags", "")
    cols = {n: [] for n in names}
    if binary:
        off = 0
        for _ in range(npts):
            for j, n in enumerate(names):
                if "complex" in flags:
                    re, im = struct.unpack_from("<dd", body, off)
                    off += 16
                    cols[n].append(complex(re, im))
                elif j == 0 or "double" in flags:
                    cols[n].append(struct.unpack_from("<d", body, off)[0])
                    off += 8
                else:
                    cols[n].append(struct.unpack_from("<f", body, off)[0])
                    off += 4
    else:
        tok = body.decode(enc).split()
        p = 0
        for _ in range(npts):
            p += 1  # point index
            for n in names:
                v = tok[p]
                p += 1
                if "," in v:
                    a, b = v.split(",")
                    cols[n].append(complex(float(a), float(b)))
                else:
                    cols[n].append(float(v))
    if names and names[0] == "time":
        cols["time"] = [abs(t) for t in cols["time"]]  # LTspice marks some steps negative
    return cols


def integrate(x, y, lo, hi):
    """Trapezoid integral of y dx over [lo, hi] (x ascending)."""
    s = 0.0
    for a, b, ya, yb in zip(x, x[1:], y, y[1:]):
        if b <= lo or a >= hi:
            continue
        s += (b - a) * (ya + yb) / 2
    return s
