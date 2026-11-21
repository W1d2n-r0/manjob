"""
아스키 아트 메이커 (ASCII Art Maker / アスキーアートメーカー)
==============================================================
이미지를 2ch 스타일 아스키 아트로 변환하는 데스크톱 프로그램.
Converts images into 2ch-style ASCII art.  画像を2ch風アスキーアートに変換します。

- 선화 / 톤 / 혼합 모드, 전각·반각 혼용 줄 맞춤, 선 방향 인식 매칭
- 피사체 분리(배경 제거), 명암 대비·단계 조절, 투명 배경 PNG 저장
- UI 언어: 한국어 / English / 日本語

필요 패키지 (Requirements):  pip install -r requirements.txt
선택 패키지 (Optional):      pip install mediapipe   + face_landmarker.task
"""

import os
import sys
import threading
import queue
import time

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    import tkinter.font as tkfont
except ImportError:  # 엔진만 쓰는 경우(테스트 등)
    tk = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FACE_MODEL_PATH = os.path.join(APP_DIR, "face_landmarker.task")

# 내부 작업 해상도: 글자 한 칸의 가로 픽셀 수 (세로는 폰트 비율로 자동 계산)
CELL_W = 10

# ============================================================
# 문자셋
# ============================================================

AA_CHARSETS = {
    # 전각 문자는 자동으로 2칸 처리됨
    "2ch (혼합)": (
        " .,'`:;-_=~|/\\()[]{}<>^"
        "ﾉﾊﾍﾅﾆﾂﾌﾒｼﾘﾝｸｿｨｲｰ"          # 반각 가타카나
        "ノハヘナニツフメしつてりんく"     # 전각
        "○●◎∀ωー｀´￣＿／＼∧∨｜"
    ),
    "반각 전용 (어디서나 안전)": (
        " .,'`:;-_=~|/\\()[]{}<>^\"!il1tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"
    ),
    "선 (박스 문자)": " -_|/\\─│╱╲┌┐└┘├┤┬┴┼╭╮╯╰",
    "톤 (농도)": " .'`^\",:;Il!i><~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
    "한글 자모": " .,'-ㅡㅣㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㅗㅜㅓㅏㅛㅠㅕㅑ",
    "점자 느낌": " ⠁⠂⠄⠈⠐⠠⠃⠅⠉⠑⠒⠘⠤⠛⠶⠿⣀⣤⣶⣿",
}

# 명암(톤)용 문자 단계. 폰트에서 실제 잉크량을 재서 자동 정렬하므로 순서는 상관없음.
TONE_RAMPS = {
    "혼합 (추천)": " .'`-~:;=+iIl!|*#",   # 부드러운 단계(~ i I) + 뚜렷한 면(= + #)
    "기본": " .:-=+*#%@",
    "부드럽게": " .'`^\",:;Il!i><~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
    "점": " .:;",
    "가벼움": " .,:;'\"`",
}

# ============================================================
# 폰트 탐색
# ============================================================

# (Tk 폰트 이름, 파일 glob 패턴들, 기본 TTC 인덱스 or 찾을 이름)
FONT_CANDIDATES = [
    ("MS Gothic", ["msgothic.ttc"], "MS Gothic"),
    ("D2Coding", ["D2Coding*.ttf", "D2Coding*.ttc"], 0),
    ("NanumGothicCoding", ["NanumGothicCoding*.ttf"], 0),
    ("BIZ UDGothic", ["BIZ-UDGothicR.ttc"], "BIZ UDGothic"),
    ("Consolas", ["consola.ttf"], 0),
    ("Cascadia Mono", ["CascadiaMono.ttf"], 0),
    ("Courier New", ["cour.ttf"], 0),
    ("Noto Sans Mono CJK JP", ["NotoSansCJK-Regular.ttc", "NotoSansMonoCJK*.ttc"], "Noto Sans Mono CJK JP"),
    ("DejaVu Sans Mono", ["DejaVuSansMono.ttf"], 0),
    ("Menlo", ["Menlo.ttc"], 0),
]


def _font_dirs():
    dirs = []
    if sys.platform.startswith("win"):
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(os.path.join(windir, "Fonts"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    elif sys.platform == "darwin":
        dirs += ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                 os.path.expanduser("~/.local/share/fonts")]
    dirs.append(APP_DIR)
    return [d for d in dirs if os.path.isdir(d)]


def _resolve_ttc_index(path, want):
    """TTC 안에서 원하는 이름의 폰트 인덱스를 찾는다."""
    if isinstance(want, int):
        return want
    for i in range(16):
        try:
            f = ImageFont.truetype(path, 12, index=i)
        except Exception:
            break
        if f.getname()[0].lower() == want.lower():
            return i
    return 0


def find_fonts():
    """설치된 고정폭 폰트 목록: [(표시이름, 경로, 인덱스)]"""
    import fnmatch
    files = []  # (소문자 파일명, 경로)
    for d in _font_dirs():
        for root, _dirs, names in os.walk(d):
            for n in names:
                if n.lower().endswith((".ttf", ".ttc", ".otf")):
                    files.append((n.lower(), os.path.join(root, n)))
    found = []
    for name, patterns, idx in FONT_CANDIDATES:
        path = None
        for pat in patterns:
            hits = sorted(p for n, p in files if fnmatch.fnmatch(n, pat.lower()))
            if hits:
                path = hits[0]
                break
        if path:
            try:
                found.append((name, path, _resolve_ttc_index(path, idx)))
            except Exception:
                pass
    return found


# ============================================================
# 이미지 로드 (한글 경로 대응)
# ============================================================

def load_image_any(path):
    """한글 경로에서도 동작하는 이미지 로더. RGB uint8 반환."""
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        # webp/gif 등은 PIL로 재시도
        pil = Image.open(path)
        return pil_to_rgb(pil)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    elif img.shape[2] == 4:
        # 투명 배경은 흰색으로 합성
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        bgr = img[:, :, :3].astype(np.float32)
        img = (bgr * alpha + 255 * (1 - alpha)).astype(np.uint8)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    else:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return img


def pil_to_rgb(pil):
    if pil.mode in ("RGBA", "LA", "P"):
        pil = pil.convert("RGBA")
        bg = Image.new("RGBA", pil.size, (255, 255, 255, 255))
        bg.alpha_composite(pil)
        pil = bg
    return np.array(pil.convert("RGB"))


# ============================================================
# 특징 추출 (타겟 이미지와 글자 템플릿에 동일하게 적용)
# ============================================================

N_ORIENT = 4  # 0°, 45°, 90°, 135°


def ink_features(ink, blur_sigma, orient_weight):
    """
    ink: float32 (H, W), 0~1 (1 = 잉크)
    반환: (1 + N_ORIENT, H, W)  -> [잉크, 방향0, 방향45, 방향90, 방향135]
    구조 텐서로 획의 방향을 구해 방향별 채널로 나눈다. 같은 함수를
    타겟과 템플릿에 쓰므로 ╱ 와 ╲ 처럼 모양이 비슷한 글자를 확실히 구분한다.
    """
    gx = cv2.Sobel(ink, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(ink, cv2.CV_32F, 0, 1, ksize=3)
    s = 1.2
    jxx = cv2.GaussianBlur(gx * gx, (0, 0), s)
    jyy = cv2.GaussianBlur(gy * gy, (0, 0), s)
    jxy = cv2.GaussianBlur(gx * gy, (0, 0), s)
    # 그래디언트 주방향 -> 획 방향은 +90°
    theta = 0.5 * np.arctan2(2 * jxy, jxx - jyy) + np.pi / 2
    coh = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (jxx + jyy + 1e-6)

    chans = [ink]
    for k in range(N_ORIENT):
        a = k * np.pi / N_ORIENT
        w = ((1 + np.cos(2 * (theta - a))) * 0.5) ** 2
        chans.append(ink * w * coh * orient_weight)
    out = np.stack(chans).astype(np.float32)
    if blur_sigma > 0:
        for i in range(out.shape[0]):
            out[i] = cv2.GaussianBlur(out[i], (0, 0), blur_sigma)
    return out


# ============================================================
# 글자 템플릿
# ============================================================

class GlyphSet:
    """폰트로 글자를 그려 특징 템플릿을 만든다. 반각=1칸, 전각=2칸."""

    def __init__(self, font_path, font_index, charset, allow_wide, blur_sigma, orient_weight):
        self.font_path = font_path
        self.font_index = font_index
        base = ImageFont.truetype(font_path, 64, index=font_index)
        self.base_font = base
        unit = base.getlength("0")
        asc, desc = base.getmetrics()
        self.unit = unit
        self.line_h = asc + desc
        self.aspect = self.line_h / unit                    # 칸 세로/가로 비
        self.cell_w = CELL_W
        self.cell_h = max(4, 2 * int(round(CELL_W * self.aspect / 2)))   # 2x2 풀링 위해 짝수

        notdef = self._render(base, "\U000F0000", 1)        # 없는 글자(두부) 모양
        # 일본어 폰트(MS Gothic 등)는 \ 를 ¥ 모양으로 그린다 -> 다른 곳에 붙여넣으면 모양이 달라지므로 제외
        yen_like = np.abs(self._render(base, "\\", 1) - self._render(base, "\u00a5", 1)).sum() < 1e-3

        blank = np.zeros((self.cell_h, self.cell_w), np.float32)
        singles, wides = [(" ", blank)], []
        seen = set(" ")
        for ch in charset:
            if ch in seen or ch in "\n\r\t":
                continue
            seen.add(ch)
            if ch == "\\" and yen_like:
                continue
            adv = base.getlength(ch)
            cells = int(round(adv / unit))
            if cells not in (1, 2):
                continue
            if cells == 2 and not allow_wide:
                continue
            bmp = self._render(base, ch, cells)
            if bmp.sum() < 1e-3:
                continue
            if cells == 1 and notdef.shape == bmp.shape and np.abs(bmp - notdef).sum() < 1e-3:
                continue  # 폰트에 없는 글자
            (singles if cells == 1 else wides).append((ch, bmp))

        self.single_chars = [c for c, _ in singles]
        self.wide_chars = [c for c, _ in wides]
        self.single_feat, self.single_owner, self.single_pen = \
            self._feats([b for _, b in singles], 1, blur_sigma, orient_weight)
        self.single_norm = (self.single_feat ** 2).sum(1)
        if wides:
            self.wide_feat, self.wide_owner, self.wide_pen = \
                self._feats([b for _, b in wides], 2, blur_sigma, orient_weight)
            self.wide_norm = (self.wide_feat ** 2).sum(1)
        else:
            self.wide_feat = None

    def _render(self, font, ch, cells):
        w = int(round(self.unit * cells))
        img = Image.new("L", (w, self.line_h), 0)
        ImageDraw.Draw(img).text((0, 0), ch, font=font, fill=255)
        arr = np.asarray(img, np.float32) / 255.0
        arr = cv2.resize(arr, (self.cell_w * cells, self.cell_h), interpolation=cv2.INTER_AREA)
        return arr

    def _feats(self, bitmaps, cells, blur_sigma, orient_weight):
        """
        각 글자를 상하좌우로 조금씩 이동한 변형까지 템플릿으로 만든다.
        (선이 칸 중앙에서 약간 벗어나도 올바른 글자를 고르게 하는 위치 관용도)
        반환: (특징 행렬, 글자 인덱스, 이동 페널티 계수)
        """
        pad = 4
        cw, ch = self.cell_w, self.cell_h
        feats, owner, pen = [], [], []
        for i, b in enumerate(bitmaps):
            shifts = [(0, 0)] if b.sum() < 1e-3 else SHIFTS
            for dx, dy in shifts:
                sb = _shift(b, dx, dy)
                f = ink_features(np.pad(sb, pad), blur_sigma, orient_weight)[:, pad:-pad, pad:-pad]
                f = pool2(f)
                feats.append(f.reshape(-1))
                owner.append(i)
                pen.append(abs(dx) / cw + abs(dy) / ch)
        F = np.stack(feats).astype(np.float32)
        norm = (F ** 2).sum(1)
        return F, np.array(owner), np.array(pen, np.float32) * norm


# 위치 관용도용 이동량 (작업 해상도 px, CELL_W=10 기준)
SHIFTS = [(dx, dy) for dx in (-3, -2, -1, 0, 1, 2, 3) for dy in (-3, 0, 3)]


def _shift(img, dx, dy):
    out = np.zeros_like(img)
    h, w = img.shape
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    out[yd, xd] = img[ys, xs]
    return out


def pool2(F):
    """(C, H, W) -> (C, H/2, W/2) 평균 풀링. 블러된 특징이라 정보 손실 거의 없음, 속도 4배."""
    C, H, W = F.shape
    return F.reshape(C, H // 2, 2, W // 2, 2).mean(axis=(2, 4))


_GLYPH_CACHE = {}


def get_glyphset(font_path, font_index, charset, allow_wide, blur_sigma, orient_weight):
    key = (font_path, font_index, charset, allow_wide, round(blur_sigma, 3), round(orient_weight, 3))
    gs = _GLYPH_CACHE.get(key)
    if gs is None:
        gs = GlyphSet(font_path, font_index, charset, allow_wide, blur_sigma, orient_weight)
        if len(_GLYPH_CACHE) > 12:
            _GLYPH_CACHE.clear()
        _GLYPH_CACHE[key] = gs
    return gs


# ============================================================
# 얼굴 윤곽 (선택 기능, MediaPipe)
# ============================================================

class FaceContours:
    def __init__(self):
        self.ok = False
        self.error = ""          # 화면 표시용 키 (UI에서 번역)
        self.error_detail = ""
        self._detector = None
        self._connections = None
        if not os.path.isfile(FACE_MODEL_PATH):
            self.error = "face_missing"
            return
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
            with open(FACE_MODEL_PATH, "rb") as f:   # 한글 경로 대응: 버퍼로 로드
                buf = f.read()
            opts = vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_buffer=buf), num_faces=6)
            self._detector = vision.FaceLandmarker.create_from_options(opts)
            self._mp = mp
            conns = None
            try:
                conns = [(c.start, c.end) for c in vision.FaceLandmarksConnections.FACE_LANDMARKS_CONTOURS]
            except Exception:
                conns = list(mp.solutions.face_mesh.FACEMESH_CONTOURS)
            self._connections = conns
            self.ok = True
        except Exception as e:
            self.error = "face_nomp"
            self.error_detail = str(e)

    def detect(self, rgb):
        """정규화 좌표 [(N,2) 배열, ...] 반환"""
        if not self.ok:
            return []
        h, w = rgb.shape[:2]
        scale = min(1.0, 1280 / max(h, w))
        if scale < 1:
            rgb = cv2.resize(rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        res = self._detector.detect(img)
        return [np.array([(p.x, p.y) for p in face], np.float32) for face in res.face_landmarks]

    def draw(self, ink, faces, thickness=1):
        h, w = ink.shape
        for pts in faces:
            px = (pts * [w, h]).astype(np.int32)
            for a, b in self._connections:
                if a < len(px) and b < len(px):
                    cv2.line(ink, tuple(px[a]), tuple(px[b]), 1.0, thickness, cv2.LINE_AA)
        return ink


_FACE = None


def get_face():
    global _FACE
    if _FACE is None:
        _FACE = FaceContours()
    return _FACE


# ============================================================
# 변환 엔진
# ============================================================

DEFAULT_PARAMS = dict(
    cols=120,              # 가로 글자 수
    mode="line",           # line / tone / hybrid
    edge_method="dog",     # dog / canny
    detail=1.2,            # DoG sigma
    sensitivity=0.5,       # 0~1, 높을수록 선이 많이
    thickness=1.0,         # 선 굵기
    despeckle=6,           # 잡티 제거 (최소 면적, 작업 해상도 px)
    brightness=0.0,        # -1~1
    contrast=1.0,          # 0.2~3
    gamma=1.0,
    clahe=False,
    invert=False,
    tone_ramp="혼합 (추천)",  # 명암용 문자 세트
    tone_contrast=1.5,     # 명암 대비: 밝은 곳은 더 비우고 어두운 곳은 더 진하게 (0 = 원본 그대로)
    tone_levels=4,         # 명암 단계 수: 비슷한 밝기를 묶어서 면을 깔끔하게 (0 = 연속)
    tone_strength=1.0,     # 톤 모드 명암 강도
    hybrid_tone=0.8,       # 혼합 모드 명암 강도 (선이 묻히지 않게 약간 약하게)
    orient_weight=1.4,
    blur_sigma=1.0,
    shift_tol=0.6,         # 위치 관용도 (클수록 선 위치 오차 허용)
    edge_weight=0.6,       # 밝기 경계(밝은 물체 윤곽) 반영 비율
    min_len=0.0,           # 짧은 선 제거: 이 칸 수보다 짧은 선 조각 삭제 (잔털·질감 정리)
    bg_relative=True,      # 혼합 모드: 배경 밝기를 기준으로 명암 (배경을 비움)
    subject=False,         # 피사체 분리 (GrabCut): 자동 크롭 + 배경 제거 + 실루엣 윤곽
    outline=1.0,           # 피사체 실루엣 윤곽 굵기 (0 = 끔)
    allow_wide=True,
    dark_bg=False,
    face=False,
)


def adjust_gray(rgb, p, size):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA if size[0] < rgb.shape[1] else cv2.INTER_CUBIC)
    if p["clahe"]:
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    g = gray.astype(np.float32) / 255.0
    g = (g - 0.5) * p["contrast"] + 0.5 + p["brightness"] * 0.5
    g = np.clip(g, 0, 1)
    if abs(p["gamma"] - 1.0) > 1e-3:
        g = g ** p["gamma"]
    if p["invert"]:
        g = 1.0 - g
    return g


def _robust_max(x, q=99.0):
    v = x[x > 1e-4]
    return float(np.percentile(v, q)) if v.size > 50 else float(x.max() if x.size else 1.0)


def _thin_edges(g):
    """그래디언트 크기 + 비최대 억제(얇은 경계선)"""
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    ang = (np.rad2deg(np.arctan2(gy, gx)) + 180) % 180
    q = ((ang + 22.5) // 45).astype(np.int32) % 4      # 0:가로 1:대각/ 2:세로 3:대각\
    p = np.pad(mag, 1, mode="edge")
    c = p[1:-1, 1:-1]
    nb = [(p[1:-1, :-2], p[1:-1, 2:]),          # 0°  : 좌우 비교
          (p[:-2, 2:], p[2:, :-2]),              # 45°
          (p[:-2, 1:-1], p[2:, 1:-1]),           # 90° : 상하 비교
          (p[:-2, :-2], p[2:, 2:])]              # 135°
    keep = np.zeros_like(mag, bool)
    for k, (a, b) in enumerate(nb):
        keep |= (q == k) & (c >= a) & (c >= b)
    return np.where(keep, mag, 0).astype(np.float32)


def extract_lines(g, p, upscale=1.0):
    """
    선 잉크 맵 (1=선)
    upscale: 작업 해상도 / 원본 해상도. 작은 이미지를 키우면 경계가 흐려지므로
             그만큼 선 검출 스케일을 키워야 얼굴 같은 디테일이 사라지지 않는다.
    """
    scale = CELL_W / 10.0
    min_s = 0.6 * upscale if upscale > 1 else 0.4
    if p["edge_method"] == "canny":
        u8 = (cv2.GaussianBlur(g, (0, 0), max(0.5, min_s, p["detail"] * scale)) * 255).astype(np.uint8)
        hi = int(40 + (1 - p["sensitivity"]) * 200)
        ink = cv2.Canny(u8, hi * 0.4, hi).astype(np.float32) / 255.0
    else:
        s = max(min_s, p["detail"] * scale)
        g1 = cv2.GaussianBlur(g, (0, 0), s)
        g2 = cv2.GaussianBlur(g, (0, 0), s * 1.6)
        # (1) 어두운 선: 그림의 선, 어두운 쪽 경계
        d = g2 - g1
        thr = 0.002 + (1 - p["sensitivity"]) * 0.03
        ink = np.clip((d - thr) / (thr * 0.8 + 0.01), 0, 1).astype(np.float32)

        # (2) 밝기 경계: 밝은 물체/어두운 배경처럼 '선'이 없는 윤곽만 보충한다.
        #     이미 어두운 선이 있는 곳 주변은 제외 (선 양옆에 이중선이 생기지 않게)
        ew = p.get("edge_weight", 0.6)
        if ew > 0:
            edge = _thin_edges(g1)
            edge = edge / (_robust_max(edge) + 1e-6)
            te = (0.08 + (1 - p["sensitivity"]) * 0.3) * 0.6 / max(ew, 0.05)   # 슬라이더를 올릴수록 더 많이 잡음
            e_ink = np.clip((edge - te) / (te * 0.6 + 0.05), 0, 1)
            r = int(np.ceil(s * 1.5)) * 2 + 1
            near = cv2.dilate((ink > 0.25).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r, r)))
            e_ink = e_ink * (1 - near) * min(ew, 1.0)
            # 짧은 점 같은 경계 조각은 노이즈이므로 제거
            b = (e_ink > 0.3).astype(np.uint8)
            n, lab, st, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
            tiny = np.maximum(st[:, cv2.CC_STAT_WIDTH], st[:, cv2.CC_STAT_HEIGHT]) < CELL_W * 0.8
            tiny[0] = False
            e_ink[tiny[lab]] = 0
            ink = np.maximum(ink, e_ink.astype(np.float32))

    # 잡티 제거
    if p["despeckle"] > 0:
        binm = (ink > 0.35).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(binm, connectivity=8)
        small = stats[:, cv2.CC_STAT_AREA] < p["despeckle"] * scale * scale * max(1.0, upscale)
        small[0] = False
        if small.any():
            ink[small[lab]] = 0

    # 짧은 선 제거 (칸 단위 길이) - 질감/잔털 같은 짧은 조각을 지우고 큰 윤곽만 남김
    ml = p.get("min_len", 0.0)
    if ml > 0:
        binm = (ink > 0.3).astype(np.uint8)
        binm = cv2.dilate(binm, np.ones((3, 3), np.uint8))      # 끊긴 선 이어서 판단
        n, lab, st, _ = cv2.connectedComponentsWithStats(binm, connectivity=8)
        ext = np.maximum(st[:, cv2.CC_STAT_WIDTH] / CELL_W, st[:, cv2.CC_STAT_HEIGHT] / (CELL_W * 2))
        short = ext < ml
        short[0] = False
        if short.any():
            ink[short[lab]] = 0

    # 굵기
    t = p["thickness"]
    if t > 1.05:
        k = int(round((t - 1) * 2 * scale)) * 2 + 1
        ink = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    elif t < 0.95:
        ink = ink * t
    return ink


# ============================================================
# 피사체 분리 (GrabCut) - 사람이 아닌 물체/전신 사진용
# ============================================================

_SUBJECT_CACHE = {}


def _border_bg_model(lab, k=5):
    """
    가장자리 픽셀 색을 군집화해서 배경색을 구한다.
    피사체는 보통 아래쪽(전신/상반신) 등 한두 변에 닿으므로,
    '3개 이상의 변'에 걸쳐 나타나는 색만 배경으로 인정한다.
    """
    h, w = lab.shape[:2]
    bw = max(2, int(min(h, w) * 0.02))
    sides = [lab[:bw].reshape(-1, 3), lab[-bw:].reshape(-1, 3),
             lab[:, :bw].reshape(-1, 3), lab[:, -bw:].reshape(-1, 3)]
    border = np.concatenate(sides).astype(np.float32)
    k = min(k, len(border))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5)
    _, labels, cent = cv2.kmeans(border, k, None, crit, 2, cv2.KMEANS_PP_CENTERS)
    labels = labels.ravel()
    side_frac = []
    i0 = 0
    for sd in sides:
        lb = labels[i0:i0 + len(sd)]
        i0 += len(sd)
        side_frac.append(np.bincount(lb, minlength=k) / max(len(lb), 1))
    side_frac = np.array(side_frac)                          # (4, k)
    n_sides = (side_frac >= 0.10).sum(0)
    sel = n_sides >= 3
    if not sel.any():
        sel = n_sides >= 2
    if not sel.any():
        sel = side_frac.sum(0) == side_frac.sum(0).max()
    bg_cents = cent[sel]
    is_bg = sel[labels]
    return bg_cents, border[is_bg] if is_bg.any() else border, border


def _color_dist(lab, cents):
    d = np.full(lab.shape[:2], 1e9, np.float32)
    for c in cents:
        d = np.minimum(d, np.sqrt(((lab - c) ** 2).sum(-1)))
    return d


def _clean_fg(fg, min_frac=0.015):
    """작은 조각 제거(단, 전체 피사체의 1.5% 이상은 유지 -> 떨어진 머리카락/소품 보존) + 구멍 메우기"""
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    if n > 1:
        areas = st[:, cv2.CC_STAT_AREA].astype(np.float64)
        total = areas[1:].sum()
        keep = areas >= max(total * min_frac, 4)
        keep[0] = False
        fg = keep[lab].astype(np.uint8)
    # 테두리에 닿지 않은 배경 구멍은 피사체 내부로 간주해서 메움
    inv = (1 - fg).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    hh, ww = fg.shape
    for i in range(1, n):
        x, y, bw, bh, _a = st[i]
        if x > 0 and y > 0 and x + bw < ww and y + bh < hh:
            fg[lab == i] = 1
    return fg


def subject_mask(rgb, size=320, iters=4):
    """
    (피사체 마스크(0~1, 원본 크기), 확실함 여부) 반환. 실패하면 None.
    1) 배경이 단색/단순한 경우(일러스트, 캐릭터): 테두리 배경색과 '이어진' 영역만 배경으로 본다.
       -> 머리카락처럼 배경과 다른 색은 모두 피사체로 남고, 화면 끝에 닿은 머리카락도 잘리지 않음.
    2) 배경이 복잡한 경우(사진): 1)의 결과를 힌트로 GrabCut. 사각형 가정을 쓰지 않으므로
       가장자리에 닿은 피사체도 잘려나가지 않는다.
    """
    key = (id(rgb), rgb.shape)
    if key in _SUBJECT_CACHE:
        return _SUBJECT_CACHE[key]
    h, w = rgb.shape[:2]
    sc = min(1.0, size / max(h, w))
    small = cv2.resize(rgb, (max(16, int(w * sc)), max(16, int(h * sc))), interpolation=cv2.INTER_AREA)
    small = cv2.bilateralFilter(small, 5, 30, 5)            # 사진 노이즈/JPEG 잡티 완화
    sh, sw = small.shape[:2]
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab[..., 0] *= 100.0 / 255.0
    lab[..., 1:] -= 128.0

    result = None
    try:
        cents, bg_border, border = _border_bg_model(lab)
        spread = float(np.percentile(_color_dist(bg_border.reshape(-1, 1, 3), cents).ravel(), 80))  # 배경 색 편차
        bdist = _color_dist(border.reshape(-1, 1, 3), cents).ravel()
        thr = float(np.clip(spread * 2.0 + 6.0, 8.0, 24.0))
        dist = _color_dist(lab, cents)

        # 선(어두운 윤곽선)은 배경으로 흘러들지 못하게 막는 벽으로 사용
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 60, 140)
        cand = ((dist < thr) & (edges == 0)).astype(np.uint8)
        n, cc = cv2.connectedComponents(cand, connectivity=4)
        touch = np.unique(np.concatenate([cc[0], cc[-1], cc[:, 0], cc[:, -1]]))
        touch = touch[touch > 0]
        bg = np.isin(cc, touch)
        bg = cv2.dilate(bg.astype(np.uint8), np.ones((3, 3), np.uint8)) & (dist < thr * 1.3)
        uniform = float((bdist < thr).mean())

        strong = uniform >= 0.6
        if strong:
            fg = (1 - bg).astype(np.uint8)
        else:
            # GrabCut: 배경 힌트(테두리와 이어진 배경색) = 아마 배경, 나머지 = 아마 피사체
            m = np.full((sh, sw), cv2.GC_PR_FGD, np.uint8)
            m[bg > 0] = cv2.GC_PR_BGD
            m[(dist < thr * 0.6) & (bg > 0)] = cv2.GC_BGD
            if (m == cv2.GC_PR_FGD).sum() < 50:
                raise cv2.error("no fg seed")
            bgd = np.zeros((1, 65), np.float64)
            fgd = np.zeros((1, 65), np.float64)
            cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), m, None, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)
            fg = np.where((m == cv2.GC_FGD) | (m == cv2.GC_PR_FGD), 1, 0).astype(np.uint8)

        fg = _clean_fg(fg)
        area = fg.mean()
        # 신뢰도 검사: 너무 작거나(눈·코만 잡힌 경우 등) 조각조각이면 실패로 처리 -> 사진이 엉뚱하게 잘리지 않음
        n, _lab, st, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
        biggest = st[1:, cv2.CC_STAT_AREA].max() / max(fg.sum(), 1) if n > 1 else 0
        pts = cv2.findNonZero(fg)
        hull_area = cv2.contourArea(cv2.convexHull(pts)) if pts is not None else 0
        solidity = fg.sum() / max(hull_area, 1)              # 흩어진 조각(눈·코만 잡힘 등)이면 낮음
        ok = ((0.05 if strong else 0.10) < area < (0.97 if strong else 0.92)
              and biggest >= (0.3 if strong else 0.6) and solidity >= 0.4)
        if ok:
            fgf = cv2.GaussianBlur(fg.astype(np.float32), (0, 0), 0.7)
            result = (cv2.resize(fgf, (w, h), interpolation=cv2.INTER_LINEAR), strong)
    except cv2.error:
        result = None
    if len(_SUBJECT_CACHE) > 8:
        _SUBJECT_CACHE.clear()
    _SUBJECT_CACHE[key] = result
    return result


def crop_to_subject(rgb, mask, margin=0.08):
    """마스크 영역으로 잘라서 피사체를 최대한 크게. (잘린 rgb, 잘린 mask, (x0, y0, x1, y1))"""
    ys, xs = np.where(mask > 0.5)
    h, w = rgb.shape[:2]
    if len(xs) == 0:
        return rgb, mask, (0, 0, w, h)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    mx, my = int((x1 - x0) * margin) + 2, int((y1 - y0) * margin) + 2
    x0, y0 = max(0, x0 - mx), max(0, y0 - my)
    x1, y1 = min(w, x1 + mx), min(h, y1 + my)
    return rgb[y0:y1, x0:x1], mask[y0:y1, x0:x1], (x0, y0, x1, y1)


def build_target(rgb, p, gs, faces=None, mask=None, strong=True):
    """
    작업 해상도의 선 잉크 맵, 칸별 어두움(0~1), (rows, cols) 반환
    """
    h, w = rgb.shape[:2]
    cols = int(p["cols"])
    cw, ch = gs.cell_w, gs.cell_h
    rows = max(1, int(round(cols * cw * h / w / ch)))
    W, H = cols * cw, rows * ch
    g = adjust_gray(rgb, p, (W, H))

    dark = g if p["dark_bg"] else 1.0 - g
    m = None
    if mask is not None:
        m = cv2.resize(mask, (W, H), interpolation=cv2.INTER_LINEAR)
        dark = dark * m                                   # 배경은 명암 없음
    cell_dark = dark.reshape(rows, ch, cols, cw).mean(axis=(1, 3))
    # 혼합 모드: 배경 밝기를 0으로 보고 그보다 어두운 부분만 채움 (배경이 문자로 뒤덮이는 것 방지)
    if p["mode"] == "hybrid" and p.get("bg_relative", True) and m is None:
        border = np.concatenate([cell_dark[0], cell_dark[-1], cell_dark[:, 0], cell_dark[:, -1]])
        bg = float(np.median(border))
        cell_dark = np.clip((cell_dark - bg) / max(1.0 - bg, 0.2), 0, 1)

    ink = None
    if p["mode"] != "tone":
        ink = extract_lines(g, p, upscale=W / w)
        if p["face"] and faces:
            get_face().draw(ink, faces, thickness=max(1, int(round(p["thickness"] * CELL_W / 10))))
        if m is not None:
            # 배경 쪽 선 제거 (경계 바로 바깥은 살짝 남김)
            # 확실한 분리(단순 배경)면 경계 바로 바깥까지만, 사진처럼 불확실하면 넉넉하게(약 2칸) 남김
            r = 5 if strong else int(CELL_W * 4) | 1
            near = cv2.dilate((m > 0.5).astype(np.uint8),
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r, r))).astype(np.float32)
            if not strong:
                # 피사체 안쪽(구멍 포함)의 선은 절대 지우지 않음
                filled = (m > 0.5).astype(np.uint8)
                cnts, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                hull = np.zeros_like(filled)
                cv2.drawContours(hull, [cv2.convexHull(c) for c in cnts], -1, 1, -1)
                near = np.maximum(near, hull.astype(np.float32))
            ink = ink * near
            # 실루엣 윤곽선
            if p.get("outline", 1.0) > 0:
                cnts, _ = cv2.findContours((m > 0.5).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                th = max(1, int(round(p["outline"] * 1.5 * CELL_W / 10)))
                ol = np.zeros_like(ink)
                cv2.drawContours(ol, cnts, -1, 1.0, th, cv2.LINE_AA)
                # 피사체가 화면 끝에 닿아 잘린 부분은 윤곽이 아니므로 그리지 않음
                bw = th + 3
                ol[:bw] = 0; ol[-bw:] = 0; ol[:, :bw] = 0; ol[:, -bw:] = 0
                ink = np.maximum(ink, ol)
        ink = np.clip(ink, 0, 1).astype(np.float32)
    return ink, cell_dark, rows, cols


class ToneRamp:
    """명암 단계 문자. 폰트로 그려서 잉크량 순으로 정렬 (반각 문자만)."""

    def __init__(self, font_path, font_index, chars):
        font = ImageFont.truetype(font_path, 64, index=font_index)
        unit = font.getlength("0")
        asc, desc = font.getmetrics()
        items = [(" ", 0.0)]
        for c in dict.fromkeys(chars):
            if c == " " or int(round(font.getlength(c) / unit)) != 1:
                continue
            img = Image.new("L", (int(round(unit)), asc + desc), 0)
            ImageDraw.Draw(img).text((0, 0), c, font=font, fill=255)
            cov = np.asarray(img, np.float32).mean() / 255.0
            if cov > 1e-4:
                items.append((c, cov))
        items.sort(key=lambda x: x[1])
        self.chars = np.array([c for c, _ in items])
        cov = np.array([v for _, v in items], np.float32)
        self.levels = cov / max(cov[-1], 1e-6)      # 0 ~ 1 로 정규화

    def pick(self, v):
        """v: (rows, cols) 0~1 목표 어두움 -> 문자 배열"""
        idx = np.abs(v[..., None] - self.levels[None, None, :]).argmin(-1)
        return self.chars[idx]


_RAMP_CACHE = {}


def get_ramp(font_path, font_index, name):
    key = (font_path, font_index, name)
    if key not in _RAMP_CACHE:
        _RAMP_CACHE[key] = ToneRamp(font_path, font_index, TONE_RAMPS.get(name, TONE_RAMPS["혼합 (추천)"]))
    return _RAMP_CACHE[key]


def shape_tone(cell_dark, strength, contrast, levels):
    """
    칸별 어두움 -> 0~1 명암값.
    1) 피사체 안에서 가장 밝은/어두운 값 기준으로 범위를 넓히고
    2) S자 곡선으로 대비를 키워 밝은 곳은 비우고 어두운 곳은 진하게
    3) 단계 수만큼 묶어서 같은 밝기의 면은 같은 문자로 깔끔하게 채운다
    """
    v = np.clip(cell_dark, 0, 1).astype(np.float32)
    active = v > 0.02
    if active.sum() > 8:
        lo = float(np.percentile(v[active], 3))
        hi = float(np.percentile(v[active], 97))
        if hi - lo > 0.05:
            v = np.where(active, np.clip((v - lo) / (hi - lo), 0, 1), 0)
    if contrast > 0.01:
        k = contrast * 5.0
        f = lambda x: 1.0 / (1.0 + np.exp(-k * (x - 0.5)))
        a0, a1 = f(0.0), f(1.0)
        v = np.where(active, (f(v) - a0) / (a1 - a0), 0)
    v = np.clip(v * strength, 0, 1)                           # 강도: 전체 농도
    if levels and levels >= 2:
        idx = np.clip(np.floor(v * levels), 0, levels - 1)
        v = np.where(active, idx / (levels - 1), 0)          # 가장 밝은 단계 = 공백
    return v.astype(np.float32)


def tone_chars(cell_dark, strength, ramp, contrast=0.0, levels=0):
    v = shape_tone(cell_dark, strength, contrast, levels)
    if levels and levels >= 2:
        # 단계마다 서로 다른 문자가 되도록 고름 (명암 차이가 확실히 보이게)
        targets = np.linspace(0, 1, int(levels))
        picked, used = [], set()
        for t in targets:
            order = np.argsort(np.abs(ramp.levels - t))
            j = next((int(o) for o in order if int(o) not in used), int(order[0]))
            used.add(j)
            picked.append(ramp.chars[j])
        picked[0] = " "
        idx = np.clip(np.round(v * (levels - 1)).astype(int), 0, int(levels) - 1)
        return np.array(picked)[idx]
    v = np.where(v < 0.06, 0, v)
    return ramp.pick(v)


def _best_match(X, G, g_term, owner, chunk=4096):
    """각 행 x에 대해 min_k ||x - g_k||^2 + 페널티 를 청크 단위로 계산 (메모리 절약)"""
    n = X.shape[0]
    best = np.empty(n, np.float32)
    arg = np.empty(n, np.int64)
    Gt = G.T
    for i in range(0, n, chunk):
        x = X[i:i + chunk]
        cost = g_term[None, :] - 2.0 * (x @ Gt)
        k = cost.argmin(1)
        best[i:i + chunk] = cost[np.arange(len(k)), k] + (x * x).sum(1)
        arg[i:i + chunk] = owner[k]
    return best, arg


def match_and_layout(ink, rows, cols, gs, p, fill=None):
    """
    특징 매칭 + 행 단위 DP로 반각/전각 배치
    fill: (rows, cols) 문자 배열. 선이 없어 공백이 된 칸을 이 문자(명암)로 채운다.
    """
    cw, ch = gs.cell_w, gs.cell_h
    F = pool2(ink_features(ink, p["blur_sigma"], p["orient_weight"]))   # (C, H/2, W/2)
    C = F.shape[0]
    ch2, cw2 = ch // 2, cw // 2
    T = F.reshape(C, rows, ch2, cols, cw2).transpose(1, 3, 0, 2, 4)     # (rows, cols, C, ch2, cw2)
    sp = p.get("shift_tol", 0.6)

    single = np.ascontiguousarray(T).reshape(rows * cols, -1)
    m1, a1 = _best_match(single, gs.single_feat, gs.single_norm + gs.single_pen / max(sp, 1e-3), gs.single_owner)
    m1, a1 = m1.reshape(rows, cols), a1.reshape(rows, cols)

    use_wide = gs.wide_feat is not None and cols >= 2
    if use_wide:
        wide = np.concatenate([T[:, :-1], T[:, 1:]], axis=-1)            # (rows, cols-1, C, ch2, 2*cw2)
        wide = np.ascontiguousarray(wide).reshape(rows * (cols - 1), -1)
        m2, a2 = _best_match(wide, gs.wide_feat, gs.wide_norm + gs.wide_pen / max(sp, 1e-3), gs.wide_owner)
        m2, a2 = m2.reshape(rows, cols - 1), a2.reshape(rows, cols - 1)
        del wide

    # DP: best[c] = c칸까지 채웠을 때 최소 비용 (모든 행 동시 계산)
    best = np.zeros((rows, cols + 1), np.float32)
    take2 = np.zeros((rows, cols + 1), bool)
    best[:, 1] = m1[:, 0]
    for c in range(2, cols + 1):
        v1 = best[:, c - 1] + m1[:, c - 1]
        if use_wide:
            v2 = best[:, c - 2] + m2[:, c - 2]
            t2 = v2 < v1
            best[:, c] = np.where(t2, v2, v1)
            take2[:, c] = t2
        else:
            best[:, c] = v1

    sc, wc = gs.single_chars, gs.wide_chars
    lines = []
    for r in range(rows):
        out = []
        c = cols
        while c > 0:
            if take2[r, c]:
                out.append(wc[a2[r, c - 2]])
                c -= 2
            else:
                chs = sc[a1[r, c - 1]]
                if chs == " " and fill is not None:
                    chs = fill[r, c - 1]
                out.append(chs)
                c -= 1
        lines.append("".join(reversed(out)).rstrip())
    return "\n".join(lines)


def convert(rgb, font_path, font_index, charset, p, faces=None):
    gs = get_glyphset(font_path, font_index, charset, p["allow_wide"], p["blur_sigma"], p["orient_weight"])
    mask = None
    strong = True
    if p.get("subject"):
        res = subject_mask(rgb)
        if res is not None:
            full, strong = res
            h, w = rgb.shape[:2]
            rgb, mask, (x0, y0, x1, y1) = crop_to_subject(rgb, full)
            if faces:   # 얼굴 좌표(정규화)를 잘린 이미지 기준으로 변환
                cw_, ch_ = x1 - x0, y1 - y0
                faces = [np.stack([(f[:, 0] * w - x0) / cw_, (f[:, 1] * h - y0) / ch_], 1) for f in faces]
    ink, cell_dark, rows, cols = build_target(rgb, p, gs, faces, mask, strong)
    mode = p["mode"]
    if mode == "tone":
        # 고전 아스키 아트 방식: 칸의 밝기만으로 문자 선택 (작은 크기에서 실루엣이 가장 잘 보임)
        chars = tone_chars(cell_dark, p["tone_strength"], get_ramp(font_path, font_index, p["tone_ramp"]),
                           p["tone_contrast"], int(p["tone_levels"]))
        text = "\n".join("".join(row).rstrip() for row in chars)
        preview = np.repeat(np.repeat(cell_dark, gs.cell_h, 0), gs.cell_w, 1)
        sv = shape_tone(cell_dark, p["tone_strength"], p["tone_contrast"], int(p["tone_levels"]))
        preview = np.repeat(np.repeat(sv, gs.cell_h, 0), gs.cell_w, 1)
        return text, preview.astype(np.float32)
    fill = None
    if mode == "hybrid":
        # 선은 선대로 매칭하고, 선이 없는 칸만 명암 문자로 채운다
        fill = tone_chars(cell_dark, p["hybrid_tone"], get_ramp(font_path, font_index, p["tone_ramp"]),
                          p["tone_contrast"], int(p["tone_levels"]))
    text = match_and_layout(ink, rows, cols, gs, p, fill)
    return text, ink


def render_text_png(text, font_path, font_index, size=16, dark=False, transparent=False, trim=True):
    """
    텍스트를 PNG 이미지로 렌더링.
    transparent=True 면 배경을 투명하게 하고, 글자 영역에 딱 맞게 잘라낸다 (오브젝트 스티커처럼 사용).
    """
    font = ImageFont.truetype(font_path, size, index=font_index)
    asc, desc = font.getmetrics()
    lh = asc + desc
    lines = text.split("\n")
    pad = 10
    w = int(max([font.getlength(l) for l in lines] + [10])) + pad * 2
    h = lh * len(lines) + pad * 2
    fg = (230, 230, 230, 255) if dark else (0, 0, 0, 255)
    bg = (0, 0, 0, 0) if transparent else ((30, 30, 30, 255) if dark else (255, 255, 255, 255))
    img = Image.new("RGBA", (w, h), bg)
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        d.text((pad, pad + i * lh), l, font=font, fill=fg)
    if transparent:
        if trim:
            box = img.getchannel("A").getbbox()
            if box:
                m = max(2, size // 4)
                box = (max(0, box[0] - m), max(0, box[1] - m), min(w, box[2] + m), min(h, box[3] + m))
                img = img.crop(box)
        return img
    return img.convert("RGB")


# ============================================================
# UI
# ============================================================


# ============================================================
# 다국어 (한국어 / English / 日本語)
# ============================================================

LANGS = [("ko", "한국어"), ("en", "English"), ("ja", "日本語")]

STRINGS = {
    "app_title":     ("아스키 아트 메이커", "ASCII Art Maker", "アスキーアートメーカー"),
    "open":          ("이미지 열기", "Open Image", "画像を開く"),
    "paste":         ("붙여넣기 (Ctrl+V)", "Paste (Ctrl+V)", "貼り付け (Ctrl+V)"),
    "convert":       ("► 변환", "► Convert", "► 変換"),
    "live":          ("실시간", "Live", "リアルタイム"),
    "copy":          ("복사", "Copy", "コピー"),
    "save_txt":      ("TXT 저장", "Save TXT", "TXT保存"),
    "save_png":      ("PNG 저장", "Save PNG", "PNG保存"),
    "transparent":   ("투명 배경", "Transparent", "透過背景"),
    "lang":          ("언어", "Language", "言語"),
    "st_ready":      ("이미지를 열거나 붙여넣으세요.", "Open or paste an image.", "画像を開くか貼り付けてください。"),

    "g_output":      ("출력", "Output", "出力"),
    "cols":          ("가로 글자 수", "Width (characters)", "横の文字数"),
    "font":          ("폰트", "Font", "フォント"),
    "font_file":     ("파일…", "File…", "ファイル…"),
    "charset":       ("문자셋", "Charset", "文字セット"),
    "cs_2ch":        ("2ch (혼합)", "2ch (mixed)", "2ch (ミックス)"),
    "cs_half":       ("반각 전용 (어디서나 안전)", "Half-width only (safe anywhere)", "半角のみ (どこでも安全)"),
    "cs_line":       ("선 (박스 문자)", "Lines (box drawing)", "線 (罫線文字)"),
    "cs_tone":       ("톤 (농도)", "Tone (density)", "トーン (濃度)"),
    "cs_hangul":     ("한글 자모", "Korean jamo", "ハングル字母"),
    "cs_braille":    ("점자 느낌", "Braille style", "点字風"),
    "cs_custom":     ("직접 입력", "Custom", "カスタム"),
    "allow_wide":    ("전각(2칸) 글자 허용", "Allow full-width (2-cell) chars", "全角(2マス)文字を許可"),
    "dark_bg":       ("어두운 배경", "Dark background", "暗い背景"),

    "g_mode":        ("모드", "Mode", "モード"),
    "m_line":        ("선화 (윤곽선)", "Line art (outlines)", "線画 (輪郭線)"),
    "m_tone":        ("톤 (명암)", "Tone (shading)", "トーン (陰影)"),
    "m_hybrid":      ("혼합 (선 + 명암)", "Hybrid (lines + shading)", "ミックス (線 + 陰影)"),

    "g_lines":       ("선 추출", "Line extraction", "線の抽出"),
    "dog":           ("DoG (추천)", "DoG (recommended)", "DoG (推奨)"),
    "detail":        ("선 스케일 (작을수록 섬세)", "Line scale (smaller = finer)", "線スケール (小さいほど細かい)"),
    "sensitivity":   ("감도", "Sensitivity", "感度"),
    "thickness":     ("선 굵기", "Line thickness", "線の太さ"),
    "despeckle":     ("잡티 제거", "Despeckle", "ノイズ除去"),
    "min_len":       ("짧은 선 제거 (칸, 질감·털 정리)", "Remove short lines (cells; texture, fur)",
                      "短い線を除去 (マス単位・質感や毛)"),
    "edge_weight":   ("밝기 경계 반영 (밝은 물체 윤곽)", "Brightness edges (bright object outlines)",
                      "明るさの境界 (明るい物体の輪郭)"),
    "face":          ("얼굴 윤곽 보정 (MediaPipe)", "Face contour assist (MediaPipe)", "顔の輪郭補正 (MediaPipe)"),
    "face_missing":  ("face_landmarker.task 없음", "face_landmarker.task not found", "face_landmarker.task がありません"),
    "face_nomp":     ("mediapipe 사용 불가", "mediapipe unavailable", "mediapipe が使えません"),

    "g_subject":     ("피사체 (물체·전신용)", "Subject (objects, full body)", "被写体 (物体・全身用)"),
    "subject":       ("피사체 분리: 자동 크롭 + 배경 제거", "Isolate subject: auto-crop + remove background",
                      "被写体分離: 自動トリミング + 背景除去"),
    "outline":       ("실루엣 윤곽 굵기 (0=끔)", "Silhouette outline (0 = off)", "シルエット輪郭の太さ (0=オフ)"),
    "bg_relative":   ("혼합 모드: 배경 명암 비우기", "Hybrid: leave background unshaded", "ミックス: 背景の陰影を空ける"),
    "subject_hint":  ("배경이 단순할수록 잘 됩니다 (처음 1~2초 소요)", "Works best on simple backgrounds (first run 1–2 s)",
                      "背景が単純なほどうまくいきます (初回1〜2秒)"),

    "g_adjust":      ("이미지 보정", "Image adjustment", "画像補正"),
    "brightness":    ("밝기", "Brightness", "明るさ"),
    "contrast":      ("대비", "Contrast", "コントラスト"),
    "gamma":         ("감마", "Gamma", "ガンマ"),
    "clahe":         ("국소 대비 향상 (CLAHE)", "Local contrast boost (CLAHE)", "局所コントラスト強調 (CLAHE)"),
    "invert":        ("색 반전", "Invert", "色反転"),

    "g_match":       ("매칭", "Matching", "マッチング"),
    "ramp":          ("명암 문자", "Shading chars", "陰影文字"),
    "r_mix":         ("혼합 (추천)", "Mixed (recommended)", "ミックス (推奨)"),
    "r_basic":       ("기본", "Basic", "基本"),
    "r_smooth":      ("부드럽게", "Smooth", "なめらか"),
    "r_dots":        ("점", "Dots", "点"),
    "r_light":       ("가벼움", "Light", "軽め"),
    "tone_strength": ("톤 모드 명암 강도", "Tone mode strength", "トーンモードの濃さ"),
    "hybrid_tone":   ("혼합 모드 명암 강도", "Hybrid shading strength", "ミックスモードの濃さ"),
    "tone_contrast": ("명암 대비 (밝은 곳 비우기·어두운 곳 진하게)", "Shading contrast (clear lights, deepen darks)",
                      "陰影コントラスト (明部を空け、暗部を濃く)"),
    "tone_levels":   ("명암 단계 수 (0 = 연속)", "Shading levels (0 = continuous)", "陰影の段階数 (0 = 連続)"),
    "orient_weight": ("방향 가중치 (선 각도 정확도)", "Direction weight (line angle accuracy)", "方向の重み (線の角度精度)"),
    "blur_sigma":    ("매칭 부드러움", "Matching softness", "マッチングの柔らかさ"),
    "shift_tol":     ("위치 관용도", "Position tolerance", "位置の許容度"),

    "g_preview":     ("미리보기", "Preview", "プレビュー"),
    "pv_ink":        ("추출 결과", "Extracted", "抽出結果"),
    "pv_src":        ("원본", "Original", "元画像"),
    "pv_mask":       ("피사체 영역", "Subject area", "被写体領域"),
    "reset":         ("설정 초기화", "Reset settings", "設定をリセット"),
    "zoom":          ("글자 크기", "Text size", "文字サイズ"),
    "zoom_hint":     ("(Ctrl + 휠로 확대/축소)", "(Ctrl + wheel to zoom)", "(Ctrl + ホイールで拡大縮小)"),

    "font_title":    ("폰트", "Font", "フォント"),
    "no_fonts":      ("고정폭 폰트를 찾지 못했습니다. '파일…'로 직접 선택해주세요.",
                      "No monospace font was found. Please pick one with 'File…'.",
                      "等幅フォントが見つかりません。「ファイル…」で選んでください。"),
    "font_open_err": ("폰트를 열 수 없습니다:\n{e}", "Could not open the font:\n{e}", "フォントを開けません:\n{e}"),
    "font_not_inst": ("'{fam}'이(가) 시스템에 설치되어 있지 않아 화면에는 다른 폰트로 보일 수 있습니다.\n"
                      "PNG 저장 결과는 정확합니다. 폰트를 설치하면 화면에도 정확히 보입니다.",
                      "'{fam}' is not installed, so the preview may use a different font.\n"
                      "Saved PNGs are still exact. Install the font to see it on screen too.",
                      "「{fam}」がインストールされていないため、画面では別のフォントで表示される場合があります。\n"
                      "PNG保存は正確です。フォントをインストールすると画面でも正しく表示されます。"),
    "no_font":       ("사용할 폰트가 없습니다.", "No font is available.", "使用できるフォントがありません。"),
    "error":         ("에러", "Error", "エラー"),
    "img_load_err":  ("이미지를 불러올 수 없습니다:\n{e}", "Could not load the image:\n{e}", "画像を読み込めません:\n{e}"),
    "paste_title":   ("붙여넣기", "Paste", "貼り付け"),
    "clip_err":      ("클립보드를 읽을 수 없습니다:\n{e}", "Could not read the clipboard:\n{e}",
                      "クリップボードを読み取れません:\n{e}"),
    "clip_none":     ("클립보드에 이미지가 없습니다.", "There is no image on the clipboard.", "クリップボードに画像がありません。"),
    "open_first":    ("먼저 이미지를 열어주세요.", "Please open an image first.", "先に画像を開いてください。"),
    "converting":    ("변환 중…", "Converting…", "変換中…"),
    "st_error":      ("오류", "Error", "エラー"),
    "convert_err":   ("변환 중 오류가 발생했습니다:\n{e}", "An error occurred during conversion:\n{e}",
                      "変換中にエラーが発生しました:\n{e}"),
    "faces":         (" · 얼굴 {n}개", " · {n} face(s)", " · 顔 {n}個"),
    "done":          ("{name} · 완료 {ms} ms{extra}", "{name} · done in {ms} ms{extra}", "{name} · 完了 {ms} ms{extra}"),
    "info":          ("{cols}칸 × {lines}줄 · {chars}자", "{cols} cols × {lines} lines · {chars} chars",
                      "{cols}マス × {lines}行 · {chars}文字"),
    "mask_fail":     ("피사체를 확실히 구분하지 못해 분리를 건너뜁니다 (전체 사용)",
                      "Could not clearly isolate the subject; using the whole image",
                      "被写体をはっきり分離できないため、画像全体を使います"),
    "copied":        ("클립보드에 복사했습니다.", "Copied to clipboard.", "クリップボードにコピーしました。"),
    "saved":         ("저장: {f}", "Saved: {f}", "保存: {f}"),
    "img_files":     ("이미지 파일", "Image files", "画像ファイル"),
    "all_files":     ("모든 파일", "All files", "すべてのファイル"),
}

_LANG_INDEX = {code: i for i, (code, _n) in enumerate(LANGS)}
_CURRENT_LANG = "ko"


def tr(key, **kw):
    vals = STRINGS.get(key)
    if vals is None:
        return key
    s = vals[_LANG_INDEX.get(_CURRENT_LANG, 0)]
    return s.format(**kw) if kw else s


# 내부 이름(엔진에서 쓰는 키) <-> 번역 키
CHARSET_KEYS = [("2ch (혼합)", "cs_2ch"), ("반각 전용 (어디서나 안전)", "cs_half"), ("선 (박스 문자)", "cs_line"),
                ("톤 (농도)", "cs_tone"), ("한글 자모", "cs_hangul"), ("점자 느낌", "cs_braille")]
RAMP_KEYS = [("혼합 (추천)", "r_mix"), ("기본", "r_basic"), ("부드럽게", "r_smooth"), ("점", "r_dots"), ("가벼움", "r_light")]
CUSTOM = "__custom__"

SETTINGS_PATH = os.path.join(APP_DIR, "askill_settings.json")


def _detect_lang():
    import locale
    cand = ""
    try:
        cand = (locale.getlocale()[0] or "")
    except Exception:
        pass
    if not cand:
        cand = os.environ.get("LANG", "")
    c = cand.lower()
    if c.startswith("ko") or "korean" in c:
        return "ko"
    if c.startswith("ja") or "japanese" in c:
        return "ja"
    return "en"


def load_settings():
    import json
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {}


def save_settings(d):
    import json
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fp:
            json.dump(d, fp, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ============================================================
# UI
# ============================================================

if tk is not None:

    class AsciiArtApp:
        def __init__(self, master):
            global _CURRENT_LANG
            self.master = master
            self.master.geometry("1360x860")
            self.master.minsize(900, 600)

            self.settings = load_settings()
            lang = self.settings.get("lang") or _detect_lang()
            _CURRENT_LANG = lang if lang in _LANG_INDEX else "en"

            self.rgb = None
            self.img_name = "output"
            self.faces = None
            self.result_text = ""
            self.last_ink = None
            self._job = 0
            self._pending = None
            self._busy = False
            self._dirty = False
            self._results = queue.Queue()
            self._status_key = ("st_ready", {})

            self.fonts = find_fonts()

            self._build_ui()
            self._bind_keys()
            self._poll_results()
            if not self.fonts:
                messagebox.showwarning(tr("font_title"), tr("no_fonts"))

        # ---------------- 상태 표시 (언어 변경 시 다시 번역되도록 키로 저장) ----------------
        def set_status(self, key, **kw):
            self._status_key = (key, kw)
            self.status.configure(text=tr(key, **kw) if key in STRINGS else key)

        # ---------------- 언어 변경: 값은 유지하고 화면만 다시 그림 ----------------
        def set_language(self, code):
            global _CURRENT_LANG
            if code == _CURRENT_LANG:
                return
            snap = self._snapshot()
            _CURRENT_LANG = code
            self.settings["lang"] = code
            save_settings(self.settings)
            for w in self.master.winfo_children():
                w.destroy()
            self._build_ui()
            self._restore(snap)

        def _snapshot(self):
            return dict(
                vars={k: v.get() for k, v in self.vars.items()},
                mode=self.mode_var.get(), edge=self.edge_var.get(), ramp=self.ramp_key,
                charset=self.charset_key, custom=self.custom_charset.get(), font=self.font_var.get(),
                zoom=self.zoom_var.get(), preview=self.preview_var.get(), auto=self.auto_var.get(),
                transparent=self.png_transparent.get(), info=self._info_args,
            )

        def _restore(self, s):
            for k, v in s["vars"].items():
                if k in self.vars:
                    self.vars[k].set(v)
            self.mode_var.set(s["mode"])
            self.edge_var.set(s["edge"])
            self._set_ramp(s["ramp"])
            self.custom_charset.set(s["custom"])
            self._set_charset(s["charset"])
            self.font_var.set(s["font"])
            self.zoom_var.set(s["zoom"])
            self.preview_var.set(s["preview"])
            self.auto_var.set(s["auto"])
            self.png_transparent.set(s["transparent"])
            self._apply_font()
            self._apply_theme(schedule=False)
            if self.result_text:
                self.text_area.insert("1.0", self.result_text)
            self._info_args = s["info"]
            if self._info_args:
                self.info.configure(text=tr("info", **self._info_args))
            key, kw = self._status_key
            self.set_status(key, **kw)
            self._update_preview()

        # ---------------- UI 구성 ----------------
        def _build_ui(self):
            self.master.title(tr("app_title"))
            self._info_args = None

            top = ttk.Frame(self.master, padding=(8, 6))
            top.pack(fill=tk.X)
            ttk.Button(top, text=tr("open"), command=self.load_image).pack(side=tk.LEFT, padx=2)
            ttk.Button(top, text=tr("paste"), command=self.paste_image).pack(side=tk.LEFT, padx=2)
            ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
            ttk.Button(top, text=tr("convert"), command=self.request_convert).pack(side=tk.LEFT, padx=2)
            self.auto_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(top, text=tr("live"), variable=self.auto_var).pack(side=tk.LEFT, padx=4)
            ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
            ttk.Button(top, text=tr("copy"), command=self.copy_to_clipboard).pack(side=tk.LEFT, padx=2)
            ttk.Button(top, text=tr("save_txt"), command=self.save_txt).pack(side=tk.LEFT, padx=2)
            ttk.Button(top, text=tr("save_png"), command=self.save_png).pack(side=tk.LEFT, padx=2)
            self.png_transparent = tk.BooleanVar(value=True)
            ttk.Checkbutton(top, text=tr("transparent"), variable=self.png_transparent).pack(side=tk.LEFT, padx=2)

            # 언어 선택 (오른쪽 끝)
            names = [n for _c, n in LANGS]
            self.lang_var = tk.StringVar(value=dict(LANGS)[_CURRENT_LANG])
            lc = ttk.Combobox(top, textvariable=self.lang_var, values=names, state="readonly", width=9)
            lc.pack(side=tk.RIGHT, padx=(4, 2))
            # 콤보박스 이벤트 안에서 자기 자신을 지우지 않도록 잠깐 뒤에 실행
            lc.bind("<<ComboboxSelected>>", lambda e: self.master.after(
                10, lambda: self.set_language({n: c for c, n in LANGS}[self.lang_var.get()])))
            ttk.Label(top, text="🌐 " + tr("lang")).pack(side=tk.RIGHT)
            self.status = ttk.Label(top, text=tr("st_ready"), foreground="#666")
            self.status.pack(side=tk.RIGHT, padx=10)

            body = ttk.PanedWindow(self.master, orient=tk.HORIZONTAL)
            body.pack(fill=tk.BOTH, expand=True)

            # 왼쪽 설정 패널 (스크롤)
            left_outer = ttk.Frame(body, width=340)
            canvas = tk.Canvas(left_outer, width=330, highlightthickness=0)
            sb = ttk.Scrollbar(left_outer, orient=tk.VERTICAL, command=canvas.yview)
            self.panel = ttk.Frame(canvas, padding=6)
            self.panel.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=self.panel, anchor="nw")
            canvas.configure(yscrollcommand=sb.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            sb.pack(side=tk.RIGHT, fill=tk.Y)
            self._panel_canvas = canvas
            body.add(left_outer, weight=0)

            self.vars = {}
            P = DEFAULT_PARAMS

            g = self._group("g_output")
            self._scale(g, "cols", "cols", 20, 400, 1, P["cols"])
            fr = ttk.Frame(g); fr.pack(fill=tk.X, pady=2)
            ttk.Label(fr, text=tr("font")).pack(side=tk.LEFT)
            names = [f[0] for f in self.fonts]
            default_font = next((n for n in ("MS Gothic", "D2Coding", "NanumGothicCoding", "Noto Sans Mono CJK JP")
                                 if n in names), names[0] if names else "")
            self.font_var = tk.StringVar(value=default_font)
            self.font_combo = ttk.Combobox(fr, textvariable=self.font_var, values=names, state="readonly", width=18)
            self.font_combo.pack(side=tk.LEFT, padx=4)
            self.font_combo.bind("<<ComboboxSelected>>", lambda e: self._on_font())
            ttk.Button(fr, text=tr("font_file"), width=6, command=self.pick_font_file).pack(side=tk.LEFT)

            fr = ttk.Frame(g); fr.pack(fill=tk.X, pady=2)
            ttk.Label(fr, text=tr("charset")).pack(side=tk.LEFT)
            self.charset_key = CHARSET_KEYS[0][0]
            self.charset_var = tk.StringVar(value=tr(CHARSET_KEYS[0][1]))
            cb = ttk.Combobox(fr, textvariable=self.charset_var,
                              values=[tr(k) for _n, k in CHARSET_KEYS] + [tr("cs_custom")],
                              state="readonly", width=24)
            cb.pack(side=tk.LEFT, padx=4)
            cb.bind("<<ComboboxSelected>>", lambda e: self._on_charset())
            self.custom_charset = tk.StringVar(value=AA_CHARSETS[CHARSET_KEYS[0][0]])
            ent = ttk.Entry(g, textvariable=self.custom_charset)
            ent.pack(fill=tk.X, pady=2)
            ent.bind("<KeyRelease>", lambda e: (self._set_charset(CUSTOM), self.schedule()))
            self._check(g, "allow_wide", "allow_wide", P["allow_wide"])
            self._check(g, "dark_bg", "dark_bg", P["dark_bg"], cmd=self._apply_theme)

            g = self._group("g_mode")
            self.mode_var = tk.StringVar(value=P["mode"])
            for val, key in (("line", "m_line"), ("tone", "m_tone"), ("hybrid", "m_hybrid")):
                ttk.Radiobutton(g, text=tr(key), value=val, variable=self.mode_var,
                                command=self.schedule).pack(anchor="w")

            g = self._group("g_lines")
            self.edge_var = tk.StringVar(value=P["edge_method"])
            fr = ttk.Frame(g); fr.pack(fill=tk.X)
            ttk.Radiobutton(fr, text=tr("dog"), value="dog", variable=self.edge_var,
                            command=self.schedule).pack(side=tk.LEFT)
            ttk.Radiobutton(fr, text="Canny", value="canny", variable=self.edge_var,
                            command=self.schedule).pack(side=tk.LEFT, padx=8)
            self._scale(g, "detail", "detail", 0.4, 4.0, 0.1, P["detail"])
            self._scale(g, "sensitivity", "sensitivity", 0.0, 1.0, 0.01, P["sensitivity"])
            self._scale(g, "thickness", "thickness", 0.5, 3.0, 0.1, P["thickness"])
            self._scale(g, "despeckle", "despeckle", 0, 60, 1, P["despeckle"])
            self._scale(g, "min_len", "min_len", 0.0, 4.0, 0.1, P["min_len"])
            self._scale(g, "edge_weight", "edge_weight", 0.0, 1.5, 0.05, P["edge_weight"])
            face = get_face()
            self._check(g, "face", "face", False, state=("normal" if face.ok else "disabled"))
            if not face.ok:
                ttk.Label(g, text=f"  ({tr(face.error)})", foreground="#999").pack(anchor="w")

            g = self._group("g_subject")
            self._check(g, "subject", "subject", P["subject"])
            self._scale(g, "outline", "outline", 0.0, 3.0, 0.1, P["outline"])
            self._check(g, "bg_relative", "bg_relative", P["bg_relative"])
            ttk.Label(g, text="  " + tr("subject_hint"), foreground="#888", wraplength=300).pack(anchor="w")

            g = self._group("g_adjust")
            self._scale(g, "brightness", "brightness", -1.0, 1.0, 0.01, P["brightness"])
            self._scale(g, "contrast", "contrast", 0.2, 3.0, 0.05, P["contrast"])
            self._scale(g, "gamma", "gamma", 0.3, 3.0, 0.05, P["gamma"])
            self._check(g, "clahe", "clahe", P["clahe"])
            self._check(g, "invert", "invert", P["invert"])

            g = self._group("g_match")
            fr = ttk.Frame(g); fr.pack(fill=tk.X, pady=2)
            ttk.Label(fr, text=tr("ramp")).pack(side=tk.LEFT)
            self.ramp_key = P["tone_ramp"]
            self.ramp_var = tk.StringVar(value=tr(dict(RAMP_KEYS)[self.ramp_key]))
            rc = ttk.Combobox(fr, textvariable=self.ramp_var, values=[tr(k) for _n, k in RAMP_KEYS],
                              state="readonly", width=18)
            rc.pack(side=tk.LEFT, padx=4)
            rc.bind("<<ComboboxSelected>>", lambda e: self._on_ramp())
            self._scale(g, "tone_strength", "tone_strength", 0.2, 2.5, 0.05, P["tone_strength"])
            self._scale(g, "hybrid_tone", "hybrid_tone", 0.0, 2.0, 0.05, P["hybrid_tone"])
            self._scale(g, "tone_contrast", "tone_contrast", 0.0, 3.0, 0.05, P["tone_contrast"])
            self._scale(g, "tone_levels", "tone_levels", 0, 8, 1, P["tone_levels"])
            self._scale(g, "orient_weight", "orient_weight", 0.0, 3.0, 0.1, P["orient_weight"])
            self._scale(g, "blur_sigma", "blur_sigma", 0.3, 2.5, 0.1, P["blur_sigma"])
            self._scale(g, "shift_tol", "shift_tol", 0.1, 3.0, 0.05, P["shift_tol"])

            g = self._group("g_preview")
            self.preview_var = tk.StringVar(value="ink")
            fr = ttk.Frame(g); fr.pack(fill=tk.X)
            for val, key in (("ink", "pv_ink"), ("src", "pv_src"), ("mask", "pv_mask")):
                ttk.Radiobutton(fr, text=tr(key), value=val, variable=self.preview_var,
                                command=self._update_preview).pack(side=tk.LEFT, padx=(0, 6))
            from PIL import ImageTk
            self._preview_img = ImageTk.PhotoImage(Image.new("RGB", (300, 220), (221, 221, 221)))
            self.preview = tk.Label(g, image=self._preview_img, bd=0)
            self.preview.pack(pady=4)
            ttk.Button(g, text=tr("reset"), command=self.reset_params).pack(anchor="e")

            # 오른쪽 결과
            right = ttk.Frame(body)
            body.add(right, weight=1)
            txt_frame = ttk.Frame(right)
            txt_frame.pack(fill=tk.BOTH, expand=True, padx=(4, 8), pady=(0, 4))
            self.text_area = tk.Text(txt_frame, wrap=tk.NONE, undo=False, padx=6, pady=6,
                                     spacing1=0, spacing2=0, spacing3=0, borderwidth=0)
            ys = ttk.Scrollbar(txt_frame, orient=tk.VERTICAL, command=self.text_area.yview)
            xs = ttk.Scrollbar(txt_frame, orient=tk.HORIZONTAL, command=self.text_area.xview)
            self.text_area.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
            self.text_area.grid(row=0, column=0, sticky="nsew")
            ys.grid(row=0, column=1, sticky="ns")
            xs.grid(row=1, column=0, sticky="ew")
            txt_frame.rowconfigure(0, weight=1)
            txt_frame.columnconfigure(0, weight=1)
            self.text_area.bind("<Control-MouseWheel>", self._on_zoom_wheel)
            self.text_area.bind("<Control-Button-4>", lambda e: self._zoom(1))
            self.text_area.bind("<Control-Button-5>", lambda e: self._zoom(-1))

            bottom = ttk.Frame(right)
            bottom.pack(fill=tk.X, padx=(4, 8), pady=(0, 6))
            ttk.Label(bottom, text=tr("zoom")).pack(side=tk.LEFT)
            self.zoom_var = tk.IntVar(value=8)
            zs = ttk.Spinbox(bottom, from_=4, to=40, textvariable=self.zoom_var, width=4, command=self._apply_font)
            zs.pack(side=tk.LEFT, padx=4)
            zs.bind("<Return>", lambda e: self._apply_font())
            ttk.Label(bottom, text=tr("zoom_hint"), foreground="#888").pack(side=tk.LEFT)
            self.info = ttk.Label(bottom, text="", foreground="#666")
            self.info.pack(side=tk.RIGHT)

            self.mono_font = tkfont.Font(family="Courier New", size=8)
            self.text_area.configure(font=self.mono_font)
            self._apply_font()
            self._apply_theme(schedule=False)

        def _group(self, key):
            g = ttk.LabelFrame(self.panel, text=tr(key), padding=6)
            g.pack(fill=tk.X, pady=4)
            return g

        def _scale(self, parent, key, label_key, lo, hi, res, val):
            var = tk.DoubleVar(value=val)
            s = tk.Scale(parent, from_=lo, to=hi, resolution=res, orient=tk.HORIZONTAL, label=tr(label_key),
                         variable=var, length=300, highlightthickness=0, command=lambda _v: self.schedule())
            s.pack(fill=tk.X)
            self.vars[key] = var

        def _check(self, parent, key, label_key, val, cmd=None, state="normal"):
            var = tk.BooleanVar(value=val)
            cb = ttk.Checkbutton(parent, text=tr(label_key), variable=var, command=cmd or self.schedule)
            cb.pack(anchor="w")
            if state == "disabled":
                cb.state(["disabled"])
            self.vars[key] = var

        def _bind_keys(self):
            # bind_all 은 위젯을 다시 만들어도 유지되므로 한 번만 등록
            def on_paste(e):
                w = e.widget
                if hasattr(w, "winfo_class") and w.winfo_class() in ("TEntry", "Entry"):
                    return None
                self.paste_image()
                return "break"
            self.master.bind_all("<Control-v>", on_paste)
            self.master.bind_all("<Control-s>", lambda e: self.save_txt())
            self.master.bind_all("<Control-o>", lambda e: self.load_image())
            self.master.bind_all("<MouseWheel>", self._on_panel_wheel, add="+")

        def _on_panel_wheel(self, e):
            try:
                w = self.master.winfo_containing(e.x_root, e.y_root)
            except Exception:
                return
            while w is not None:
                if w is self._panel_canvas:
                    if not isinstance(e.widget, tk.Scale):
                        self._panel_canvas.yview_scroll(int(-e.delta / 120), "units")
                    return
                w = w.master

        def _on_zoom_wheel(self, e):
            self._zoom(1 if e.delta > 0 else -1)
            return "break"

        def _zoom(self, d):
            self.zoom_var.set(max(4, min(40, self.zoom_var.get() + d)))
            self._apply_font()

        # ---------------- 상태/파라미터 ----------------
        def params(self):
            p = dict(DEFAULT_PARAMS)
            for k, v in self.vars.items():
                p[k] = v.get()
            p["cols"] = int(p["cols"])
            p["despeckle"] = int(p["despeckle"])
            p["tone_levels"] = int(p["tone_levels"])
            p["mode"] = self.mode_var.get()
            p["tone_ramp"] = self.ramp_key
            p["edge_method"] = self.edge_var.get()
            return p

        def reset_params(self):
            for k, v in self.vars.items():
                if k in DEFAULT_PARAMS:
                    v.set(DEFAULT_PARAMS[k])
            self.mode_var.set(DEFAULT_PARAMS["mode"])
            self._set_ramp(DEFAULT_PARAMS["tone_ramp"])
            self.edge_var.set(DEFAULT_PARAMS["edge_method"])
            self._apply_theme()

        def current_font(self):
            name = self.font_var.get()
            for n, path, idx in self.fonts:
                if n == name:
                    return n, path, idx
            return None

        def current_charset(self):
            if self.charset_key in AA_CHARSETS:
                return AA_CHARSETS[self.charset_key]
            return self.custom_charset.get() or " .:-=+*#%@"

        def _set_charset(self, key):
            self.charset_key = key
            self.charset_var.set(tr("cs_custom") if key == CUSTOM else tr(dict(CHARSET_KEYS)[key]))

        def _on_charset(self):
            disp = self.charset_var.get()
            key = next((n for n, k in CHARSET_KEYS if tr(k) == disp), CUSTOM)
            self.charset_key = key
            if key in AA_CHARSETS:
                self.custom_charset.set(AA_CHARSETS[key])
            self.schedule()

        def _set_ramp(self, key):
            self.ramp_key = key
            self.ramp_var.set(tr(dict(RAMP_KEYS).get(key, "r_mix")))

        def _on_ramp(self):
            disp = self.ramp_var.get()
            self.ramp_key = next((n for n, k in RAMP_KEYS if tr(k) == disp), RAMP_KEYS[0][0])
            self.schedule()

        def _on_font(self):
            self._apply_font()
            self.schedule()

        def pick_font_file(self):
            path = filedialog.askopenfilename(filetypes=[("Font", "*.ttf *.ttc *.otf")])
            if not path:
                return
            try:
                f = ImageFont.truetype(path, 12)
                fam = f.getname()[0]
            except Exception as e:
                messagebox.showerror(tr("font_title"), tr("font_open_err", e=e))
                return
            self.fonts = [x for x in self.fonts if x[0] != fam] + [(fam, path, 0)]
            self.font_combo.configure(values=[x[0] for x in self.fonts])
            self.font_var.set(fam)
            if fam not in tkfont.families():
                messagebox.showinfo(tr("font_title"), tr("font_not_inst", fam=fam))
            self._on_font()

        def _apply_font(self):
            f = self.current_font()
            fam = f[0] if f else "Courier New"
            try:
                size = int(self.zoom_var.get())
            except Exception:
                size = 8
            self.mono_font.configure(family=fam, size=size)

        def _apply_theme(self, schedule=True):
            dark = self.vars["dark_bg"].get()
            if dark:
                self.text_area.configure(bg="#1e1e1e", fg="#e6e6e6", insertbackground="#e6e6e6")
            else:
                self.text_area.configure(bg="#ffffff", fg="#000000", insertbackground="#000000")
            if schedule:
                self.schedule()

        # ---------------- 이미지 입력 ----------------
        def load_image(self):
            path = filedialog.askopenfilename(
                filetypes=[(tr("img_files"), "*.jpg *.jpeg *.png *.bmp *.webp *.gif *.tif *.tiff"),
                           (tr("all_files"), "*.*")])
            if not path:
                return
            try:
                rgb = load_image_any(path)
            except Exception as e:
                messagebox.showerror(tr("error"), tr("img_load_err", e=e))
                return
            self.img_name = os.path.splitext(os.path.basename(path))[0]
            self._set_image(rgb)

        def paste_image(self):
            try:
                from PIL import ImageGrab
                data = ImageGrab.grabclipboard()
            except Exception as e:
                messagebox.showerror(tr("paste_title"), tr("clip_err", e=e))
                return
            if isinstance(data, list) and data:          # 탐색기에서 파일 복사한 경우
                try:
                    rgb = load_image_any(data[0])
                    self.img_name = os.path.splitext(os.path.basename(data[0]))[0]
                except Exception:
                    rgb = None
            elif isinstance(data, Image.Image):
                rgb = pil_to_rgb(data)
                self.img_name = "clipboard"
            else:
                rgb = None
            if rgb is None:
                self.set_status("clip_none")
                return
            self._set_image(rgb)

        def _set_image(self, rgb):
            self.rgb = rgb
            self.faces = None
            h, w = rgb.shape[:2]
            self.set_status(f"{self.img_name}  ({w}×{h})")
            self._update_preview()
            self.request_convert()

        # ---------------- 변환 ----------------
        def schedule(self, *_):
            if not self.auto_var.get() or self.rgb is None:
                return
            if self._pending is not None:
                self.master.after_cancel(self._pending)
            self._pending = self.master.after(220, self.request_convert)

        def request_convert(self):
            self._pending = None
            if self.rgb is None:
                self.set_status("open_first")
                return
            f = self.current_font()
            if not f:
                messagebox.showwarning(tr("font_title"), tr("no_font"))
                return
            if self._busy:            # 작업 중이면 끝난 뒤 최신 설정으로 한 번 더
                self._dirty = True
                return
            self._busy = True
            self._dirty = False
            self._job += 1
            job = self._job
            p = self.params()
            charset = self.current_charset()
            rgb = self.rgb
            need_face = p["face"] and self.faces is None and p["mode"] != "tone"
            self.set_status("converting")

            def work():
                t0 = time.perf_counter()
                try:
                    faces = self.faces
                    if need_face:
                        faces = get_face().detect(rgb)
                    text, ink = convert(rgb, f[1], f[2], charset, p, faces)
                    err = None
                except Exception as e:
                    text, ink, faces, err = None, None, None, e
                dt = time.perf_counter() - t0
                self._results.put((job, text, ink, faces, err, dt, need_face))

            threading.Thread(target=work, daemon=True).start()

        def _poll_results(self):
            # Tkinter는 스레드 안전하지 않으므로 결과는 메인 스레드에서 큐로 받는다
            try:
                while True:
                    self._on_done(*self._results.get_nowait())
            except queue.Empty:
                pass
            self.master.after(40, self._poll_results)

        def _on_done(self, job, text, ink, faces, err, dt, face_run):
            self._busy = False
            if self._dirty:
                self.master.after(1, self.request_convert)
            if job != self._job:
                return
            if err is not None:
                self.set_status("st_error")
                messagebox.showerror(tr("error"), tr("convert_err", e=err))
                return
            if face_run:
                self.faces = faces
            self.result_text = text
            self.last_ink = ink
            y = self.text_area.yview()[0]
            x = self.text_area.xview()[0]
            self.text_area.delete("1.0", tk.END)
            self.text_area.insert("1.0", text)
            self.text_area.yview_moveto(y)
            self.text_area.xview_moveto(x)
            lines = text.count("\n") + 1
            extra = tr("faces", n=len(faces)) if (self.params()["face"] and faces is not None) else ""
            self.set_status("done", name=self.img_name, ms=f"{dt * 1000:.0f}", extra=extra)
            self._info_args = dict(cols=self.params()["cols"], lines=lines, chars=len(text))
            self.info.configure(text=tr("info", **self._info_args))
            self._update_preview()

        def _update_preview(self):
            if self.rgb is None:
                return
            mode = self.preview_var.get()
            if mode == "ink" and self.last_ink is not None:
                arr = ((1 - self.last_ink) * 255).astype(np.uint8)
                pil = Image.fromarray(arr)
            elif mode == "mask":
                res = subject_mask(self.rgb)   # 캐시되어 있어 빠름
                if res is None:
                    pil = Image.fromarray((self.rgb * 0.35).astype(np.uint8))
                    self.set_status("mask_fail")
                else:
                    m = res[0][..., None]
                    pil = Image.fromarray((self.rgb * (0.2 + 0.8 * m)).astype(np.uint8))
            else:
                pil = Image.fromarray(self.rgb)
            pil.thumbnail((300, 220))
            from PIL import ImageTk
            self._preview_img = ImageTk.PhotoImage(pil)
            self.preview.configure(image=self._preview_img, width=pil.width, height=pil.height)

        # ---------------- 출력 ----------------
        def copy_to_clipboard(self):
            if not self.result_text:
                return
            self.master.clipboard_clear()
            self.master.clipboard_append(self.result_text)
            self.set_status("copied")

        def save_txt(self):
            if not self.result_text:
                return
            path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=f"{self.img_name}_aa.txt",
                                                filetypes=[("Text", "*.txt")])
            if path:
                with open(path, "w", encoding="utf-8") as fp:
                    fp.write(self.result_text)
                self.set_status("saved", f=os.path.basename(path))

        def save_png(self):
            if not self.result_text:
                return
            f = self.current_font()
            path = filedialog.asksaveasfilename(defaultextension=".png", initialfile=f"{self.img_name}_aa.png",
                                                filetypes=[("PNG", "*.png")])
            if path:
                img = render_text_png(self.result_text, f[1], f[2], size=max(8, self.zoom_var.get() * 2),
                                      dark=self.vars["dark_bg"].get(),
                                      transparent=self.png_transparent.get())
                img.save(path)
                self.set_status("saved", f=os.path.basename(path))


def main():
    if tk is None:
        print("tkinter is required. / tkinter가 필요합니다. / tkinter が必要です。")
        return
    if sys.platform.startswith("win"):
        try:  # 고해상도 모니터에서 흐릿하게 보이는 문제 방지
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if sys.platform.startswith("win") else "clam")
    except Exception:
        pass
    AsciiArtApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
