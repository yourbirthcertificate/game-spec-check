"""Draw the app icon (a tick on a dark rounded square) and save it as .ico.

Standard library only, so the build needs nothing but PyInstaller.
    python tools/make_icon.py build/icon.ico
"""
import math
import os
import struct
import sys
import zlib

BG = (16, 21, 28)
TICK = (75, 211, 156)
# The tick, in the same 32-unit box the page's logo uses.
POINTS = [(8, 17), (13, 22), (24, 10)]


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def render(size):
    """RGBA rows for one square image, anti-aliased from distances."""
    s = size / 32.0
    radius, stroke = 7 * s, 1.75 * s
    half = size / 2.0
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            cx, cy = x + 0.5, y + 0.5
            # distance to the rounded square's edge (negative inside)
            qx, qy = abs(cx - half) - (half - radius), abs(cy - half) - (half - radius)
            box = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
            alpha = max(0.0, min(1.0, 0.5 - box))
            d = min(_seg_dist(cx, cy, POINTS[i][0] * s, POINTS[i][1] * s,
                              POINTS[i + 1][0] * s, POINTS[i + 1][1] * s)
                    for i in range(len(POINTS) - 1))
            mix = max(0.0, min(1.0, 0.5 - (d - stroke)))
            rgb = [round(BG[i] + (TICK[i] - BG[i]) * mix) for i in range(3)]
            row += bytes(rgb + [round(alpha * 255)])
        rows.append(bytes(row))
    return rows


def png(size):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    raw = b"".join(b"\x00" + r for r in render(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(sizes=(16, 24, 32, 48, 64, 128, 256)):
    images = [png(s) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, data in zip(sizes, images):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join("build", "icon.ico")
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with open(target, "wb") as fh:
        fh.write(ico())
    print("wrote", target)
