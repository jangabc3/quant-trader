"""QTrader 아이콘(packaging/qtrader.ico) 만들기: 파란 둥근 사각형 + 흰 상승 선. 외부 라이브러리 없이.
사용: python packaging/make_icon.py packaging/qtrader.ico 미리보기.png
"""
import math, struct, sys, zlib

def render(n):
    ss = 4
    pts = [(0.20, 0.68), (0.40, 0.50), (0.55, 0.60), (0.80, 0.30)]  # 상승 선 (0~1)
    arrow = (0.80, 0.30)
    lw = 0.075
    r = 0.22  # 모서리
    def seg_d(px, py, a, b):
        ax, ay = a; bx, by = b
        dx, dy = bx - ax, by - ay
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
    def inside_round(x, y):
        cx = min(max(x, r), 1 - r); cy = min(max(y, r), 1 - r)
        return math.hypot(x - cx, y - cy) <= r
    # 화살촉 삼각형
    tri = [(0.86, 0.22), (0.66, 0.26), (0.82, 0.42)]
    def in_tri(x, y):
        (x1, y1), (x2, y2), (x3, y3) = tri
        d = lambda ax, ay, bx, by, cx, cy: (ax - cx) * (by - cy) - (bx - cx) * (ay - cy)
        d1, d2, d3 = d(x, y, x1, y1, x2, y2), d(x, y, x2, y2, x3, y3), d(x, y, x3, y3, x1, y1)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))
    rows = []
    for j in range(n):
        row = bytearray()
        for i in range(n):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sj in range(ss):
                for si in range(ss):
                    x = (i + (si + 0.5) / ss) / n; y = (j + (sj + 0.5) / ss) / n
                    if not inside_round(x, y):
                        continue
                    t = (x + y) / 2  # 대각선 그라데이션
                    c = (int(0x4a + (0x1b - 0x4a) * t), int(0x9a + (0x5e - 0x9a) * t), int(0xff + (0xd6 - 0xff) * t))
                    on = min(seg_d(x, y, pts[k], pts[k + 1]) for k in range(len(pts) - 1)) <= lw / 2 or in_tri(x, y)
                    if on:
                        c = (255, 255, 255)
                    acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]; acc[3] += 255
            k = ss * ss
            a = acc[3] / k
            if a:
                row += bytes((int(acc[0] / (acc[3] / 255)), int(acc[1] / (acc[3] / 255)), int(acc[2] / (acc[3] / 255)), int(a)))
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))
    return rows

def png(n, rows):
    raw = b"".join(b"\0" + r for r in rows)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")

sizes = [256, 64, 48, 32, 16]
imgs = [png(n, render(n)) for n in sizes]
hdr = struct.pack("<HHH", 0, 1, len(imgs))
off = 6 + 16 * len(imgs)
ent, data = b"", b""
for n, im in zip(sizes, imgs):
    ent += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(im), off + len(data))
    data += im
open(sys.argv[1], "wb").write(hdr + ent + data)
open(sys.argv[2], "wb").write(imgs[0])
print("ok", len(hdr + ent + data))
