"""화소 모드 — RGB 하위 비트에 파일 하나를 통째로 숨기기 / 꺼내기.

배치 (RGB 채널 값을 왼쪽 위부터 R,G,B,R,G,B… 순서로 한 줄로 늘어놓음, 알파는 안 씀)
  · 앞 96개 값의 맨 아래 1비트 = 머리말 12바이트
        'SG'(2) · k(1, 값당 쓰는 비트 수 1~4) · 0(1) · 본문 길이(4) · 본문 CRC32(4)
  · 97번째 값부터 = 본문, 값마다 하위 k비트
본문 = 이름길이(1) 이름(UTF-8) 형식길이(1) 형식(예: video/mp4) 자료
"""
import struct, zlib
import numpy as np

HEAD_VALUES = 96


def pack_body(name: str, mime: str, data: bytes) -> bytes:
    n, m = name.encode("utf-8")[:255], mime.encode("ascii")[:255]
    return bytes([len(n)]) + n + bytes([len(m)]) + m + data


def unpack_body(b: bytes):
    i = 0
    nl = b[i]; name = b[i + 1:i + 1 + nl].decode("utf-8", "replace"); i += 1 + nl
    ml = b[i]; mime = b[i + 1:i + 1 + ml].decode("ascii", "replace"); i += 1 + ml
    return name, mime, b[i:]


def capacity(w, h, k):
    return max(0, (w * h * 3 - HEAD_VALUES) * k // 8)


def embed(rgb: np.ndarray, body: bytes, k: int) -> np.ndarray:
    """rgb: (H,W,3) uint8. 복사본에 숨겨서 돌려준다."""
    out = rgb.copy()
    flat = out.reshape(-1)
    need = (len(body) * 8 + k - 1) // k
    if HEAD_VALUES + need > flat.size:
        raise ValueError("그림이 너무 작습니다")
    head = b"SG" + bytes([k, 0]) + struct.pack(">II", len(body), zlib.crc32(body) & 0xFFFFFFFF)
    hb = np.unpackbits(np.frombuffer(head, np.uint8))
    flat[:HEAD_VALUES] = (flat[:HEAD_VALUES] & 0xFE) | hb
    bits = np.unpackbits(np.frombuffer(body, np.uint8))
    bits = np.concatenate([bits, np.zeros((-len(bits)) % k, np.uint8)]).reshape(-1, k)
    weights = (1 << np.arange(k - 1, -1, -1)).astype(np.uint8)
    vals = (bits * weights).sum(1).astype(np.uint8)
    mask = np.uint8(0xFF ^ ((1 << k) - 1))
    seg = flat[HEAD_VALUES:HEAD_VALUES + len(vals)]
    flat[HEAD_VALUES:HEAD_VALUES + len(vals)] = (seg & mask) | vals
    return out


def extract(rgb: np.ndarray):
    """숨은 파일이 있으면 (이름, 형식, 자료), 없거나 망가졌으면 None."""
    flat = rgb.reshape(-1)
    if flat.size < HEAD_VALUES + 8:
        return None
    head = np.packbits(flat[:HEAD_VALUES] & 1).tobytes()
    if head[:2] != b"SG" or not 1 <= head[2] <= 4:
        return None
    k = head[2]
    n, crc = struct.unpack(">II", head[4:12])
    need = (n * 8 + k - 1) // k
    if HEAD_VALUES + need > flat.size:
        return None
    vals = flat[HEAD_VALUES:HEAD_VALUES + need] & ((1 << k) - 1)
    bits = ((vals[:, None] >> np.arange(k - 1, -1, -1)) & 1).astype(np.uint8).reshape(-1)[: n * 8]
    body = np.packbits(bits).tobytes()
    if zlib.crc32(body) & 0xFFFFFFFF != crc:
        return None
    return unpack_body(body)


def plan(w, h, nbytes, max_k=2):
    """겉그림을 얼마나 키우고 k를 몇으로 할지. → (새 가로, 새 세로, k)"""
    for k in range(1, max_k + 1):
        if capacity(w, h, k) >= nbytes:
            return w, h, k
    k = max_k
    s = ((nbytes * 8 / k + HEAD_VALUES) / (w * h * 3)) ** 0.5
    nw, nh = int(w * s) + 2, int(h * s) + 2
    while capacity(nw, nh, k) < nbytes:
        nw += 2; nh = int(nw * h / w) + 2
    return nw, nh, k
