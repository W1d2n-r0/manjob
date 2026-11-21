"""멈춘 PNG 안에 움직임을 숨기는 시험판.

숨기는 곳 두 군데:
  1) 화소의 하위 2비트 (RGB 각 채널) - 스테가노그래피
  2) 직접 만든 보조 조각 'mOVe' - 메타데이터
일반 그림 보기에서는 1번째 장면만 멈춘 채로 보인다.
"""
import math, struct, zlib, random
import numpy as np
from PIL import Image

W = H = 128          # 그림 원본 크기 (2배로 키워 저장)
SCALE = 2
N = 32               # 장면 수
FPS = 12

# 16색 판
PAL = [
    (12, 14, 40), (24, 30, 70), (40, 52, 110), (70, 80, 150),     # 밤하늘 0-3
    (250, 240, 200), (255, 220, 120),                             # 달, 별 4-5
    (20, 60, 110), (30, 90, 150), (60, 140, 190), (170, 220, 240),# 바다 6-9
    (90, 50, 30), (140, 80, 40), (235, 235, 225), (200, 60, 50),  # 배 10-13
    (8, 8, 20), (255, 255, 255),                                  # 14-15
]

rng = random.Random(7)
stars = [(rng.randrange(W), rng.randrange(60), rng.random() * 6.28) for _ in range(40)]

def frame(t):
    a = np.zeros((H, W), np.uint8)
    ph = 2 * math.pi * t / N
    # 하늘 띠
    for y in range(80):
        a[y, :] = min(3, y // 20)
    # 별 반짝임
    for (x, y, p) in stars:
        if math.sin(ph * 2 + p) > 0.2:
            a[y, x] = 5 if math.sin(ph * 2 + p) > 0.8 else 4
    # 달
    cx, cy = 96, 24
    yy, xx = np.ogrid[:H, :W]
    a[(xx - cx) ** 2 + (yy - cy) ** 2 <= 100] = 4
    a[(xx - cx - 4) ** 2 + (yy - cy + 2) ** 2 <= 70] = a[0, 0]  # 초승달
    # 바다 + 물결
    for x in range(W):
        top = 80 + int(round(2.2 * math.sin(x / 9 + ph) + 1.2 * math.sin(x / 4 - 2 * ph)))
        for y in range(top, H):
            d = y - top
            a[y, x] = 9 if d == 0 else 8 if d < 4 else 7 if d < 20 else 6
    # 달빛 반사
    for y in range(84, H, 3):
        w = 3 + (y - 84) // 6
        off = int(3 * math.sin(y / 3 + ph * 2))
        a[y, max(0, cx - w + off):cx + w + off:2] = 9
    # 흔들리는 배
    bx = 40 + int(round(4 * math.sin(ph)))
    by = 78 + int(round(2 * math.sin(ph * 2 + 1)))
    tilt = math.sin(ph * 2) * 0.12
    for i in range(-14, 15):
        yo = int(round(i * tilt))
        for d in range(0, 5 - abs(i) // 4):
            y = by + yo + d
            if 0 <= y < H: a[y, bx + i] = 11 if d < 2 else 10
    mx = bx + int(round(-8 * tilt))
    for y in range(by - 22, by):
        a[y, bx] = 14
    for y in range(by - 20, by - 3):
        span = (y - (by - 20)) * 10 // 17
        for x in range(span):
            a[y + int(round(x * tilt)), bx + 2 + x] = 12
    a[by - 23, bx - 1:bx + 2] = 13
    return a

frames = [frame(t) for t in range(N)]

# ---- 움직임 자료 만들기: 앞 장면과의 차이(XOR)를 묶어 압축
raw = bytearray(frames[0].tobytes())
for i in range(1, N):
    raw += (frames[i] ^ frames[i - 1]).tobytes()
body = zlib.compress(bytes(raw), 9)
pal_bytes = bytes(c for rgb in PAL for c in rgb)
payload = b"MOVE" + struct.pack(">HHHHB", W, H, N, FPS, SCALE) + pal_bytes + struct.pack(">I", len(body)) + body
payload = struct.pack(">I", len(payload)) + payload  # 맨 앞에 전체 길이

# ---- 1번째 장면을 겉그림으로
img = np.array(PAL, np.uint8)[frames[0]]
img = img.repeat(SCALE, 0).repeat(SCALE, 1).copy()

# ---- 화소 하위 2비트에 숨기기
cap = img.size * 2 // 8
assert len(payload) <= cap, (len(payload), cap)
bits = np.unpackbits(np.frombuffer(payload, np.uint8))
pairs = bits.reshape(-1, 2)
vals = (pairs[:, 0] << 1) | pairs[:, 1]
flat = img.reshape(-1)
flat[:len(vals)] = (flat[:len(vals)] & 0xFC) | vals
img = flat.reshape(img.shape)

Image.fromarray(img, "RGB").save("moving_tmp.png", optimize=True)

# ---- 보조 조각 'mOVe' + 설명 글 조각 끼워 넣기
def chunk(typ, data):
    return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

png = open("moving_tmp.png", "rb").read()
pos = 8
out = bytearray(png[:8])
while pos < len(png):
    ln = struct.unpack(">I", png[pos:pos + 4])[0]
    typ = png[pos + 4:pos + 8]
    if typ == b"IDAT" and b"mOVe" not in out:
        out += chunk(b"tEXt", b"Comment\x00This picture is asleep. Wake it up.")
        out += chunk(b"mOVe", payload)
    out += png[pos:pos + 12 + ln]
    pos += 12 + ln
open("moving.png", "wb").write(out)

print("frames", N, "payload", len(payload), "bytes / capacity", cap, "file", len(out))
