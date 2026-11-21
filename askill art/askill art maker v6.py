import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

# ============================================================
# V10 CHARACTER SYSTEM
# ============================================================

AA_CHARSETS = {
    "2ch": (
        " "
        ".,'`:;"
        "-_=~"
        "|/\\"
        "()[]{}"
        "<>"
        "ノハヘナニ"
        "ツフメ"
        "しつて"
        "りんく"
        "○●◎"
        "∀"
        "ω"
    ),
    "Line": (
        " "
        "─│"
        "╱╲"
        "┌┐└┘"
        "├┤┬┴┼"
    ),
    "Dense": (
        " "
        ".,:;"
        "iltf"
        "LC"
        "0O"
        "@#%&"
    )
}

# ============================================================
# LINE MAP
# ============================================================

LINE_CHARS = {
    "horizontal": "─",
    "vertical": "│",
    "diag1": "╱",
    "diag2": "╲",
    "corner_ul": "┌",
    "corner_ur": "┐",
    "corner_dl": "└",
    "corner_dr": "┘",
    "cross": "┼"
}

# ============================================================
# FONT TEMPLATE
# ============================================================

def rasterize_char(ch, tile_w, tile_h, font_path=None):
    img = Image.new("L", (tile_w, tile_h), 0)
    draw = ImageDraw.Draw(img)

    try:
        if font_path:
            font = ImageFont.truetype(font_path, int(tile_h * 0.9))
        else:
            font = ImageFont.truetype("NotoSansMonoCJK-Regular.ttc", int(tile_h * 0.9))
    except:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0,0), ch, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    x = (tile_w - tw)//2
    y = (tile_h - th)//2

    draw.text((x,y), ch, fill=255, font=font)
    return np.array(img).astype(np.float32)/255.0

# ============================================================
# TEMPLATE DATABASE
# ============================================================

def build_template_db(charset, tile_size):
    db = {}
    for ch in charset:
        db[ch] = rasterize_char(ch, tile_size, tile_size)
    return db

# ============================================================
# ORIENTATION ENGINE
# ============================================================

def compute_orientation(gray):
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    angle = (np.arctan2(gy, gx) * 180 / np.pi) % 180
    magnitude = np.sqrt(gx*gx + gy*gy)
    return angle, magnitude

# ============================================================
# TILE ORIENTATION
# ============================================================

def tile_angle(tile_angle_map):
    valid = tile_angle_map.flatten()
    if len(valid)==0:
        return None
    return np.median(valid)

# ============================================================
# LINE DETECTOR
# ============================================================

def detect_line_char(angle, density):
    if density < 0.15: return None
    if angle is None: return None
    if angle < 20 or angle > 160: return "│"
    if 70 <= angle <= 110: return "─"
    if 30 <= angle <= 60: return "╱"
    if 120 <= angle <= 150: return "╲"
    return None

# ============================================================
# CORNER DETECTOR
# ============================================================

def detect_corner(tile):
    h,w = tile.shape
    top = tile[:h//3,:].mean()
    bottom = tile[h-h//3:,:].mean()
    left = tile[:, :w//3].mean()
    right = tile[:, w-w//3:].mean()

    t = top > 0.25
    b = bottom > 0.25
    l = left > 0.25
    r = right > 0.25

    if t and l and not b and not r: return "┘"
    if t and r and not b and not l: return "└"
    if b and l and not t and not r: return "┐"
    if b and r and not t and not l: return "┌"
    if t and b and l and r: return "┼"
    return None

def match_iou(tile, db):
    tile_bin = (tile > 0.3).astype(np.float32)
    best_char = " "
    best_score = -1

    for ch,pat in db.items():
        if ch==" ": continue
        pat_bin = (pat > 0.3).astype(np.float32)
        inter = np.minimum(tile_bin, pat_bin).sum()
        union = np.maximum(tile_bin, pat_bin).sum()
        score = inter / (union + 1e-8)

        if score > best_score:
            best_score = score
            best_char = ch
    return best_char

def render_aa_line_first(edge_map, angle_map, template_db, tile_size):
    h,w = edge_map.shape
    rows = h // tile_size
    cols = w // tile_size
    result = []

    for r in range(rows):
        row = []
        for c in range(cols):
            tile = edge_map[r*tile_size:(r+1)*tile_size, c*tile_size:(c+1)*tile_size]
            ang_tile = angle_map[r*tile_size:(r+1)*tile_size, c*tile_size:(c+1)*tile_size]
            density = tile.mean()
            corner = detect_corner(tile)

            if corner:
                row.append(corner)
                continue

            line = detect_line_char(tile_angle(ang_tile), density)
            if line:
                row.append(line)
                continue

            row.append(match_iou(tile, template_db))
        result.append("".join(row))
    return "\n".join(result)

# ============================================================
# USER INTERFACE (Tkinter)
# ============================================================

class AsciiArtApp:
    def __init__(self, master):
        self.master = master
        self.master.title("아스키 아트 메이커 v6")
        self.master.geometry("1000x700")
        
        self.img_path = None
        self.setup_ui()

    def setup_ui(self):
        # 상단 컨트롤 패널
        control_frame = tk.Frame(self.master)
        control_frame.pack(fill=tk.X, padx=10, pady=10)

        # 1. 이미지 불러오기 버튼
        btn_load = tk.Button(control_frame, text="이미지 열기", command=self.load_image)
        btn_load.pack(side=tk.LEFT, padx=5)

        # 2. 문자셋 선택
        tk.Label(control_frame, text="문자셋:").pack(side=tk.LEFT, padx=(15, 2))
        self.charset_var = tk.StringVar(value="2ch")
        self.charset_menu = ttk.Combobox(control_frame, textvariable=self.charset_var, values=list(AA_CHARSETS.keys()), state="readonly", width=8)
        self.charset_menu.pack(side=tk.LEFT, padx=2)

        # 3. 타일 크기 선택
        tk.Label(control_frame, text="타일 크기:").pack(side=tk.LEFT, padx=(15, 2))
        self.tile_size_var = tk.IntVar(value=8)
        spin_tile = tk.Spinbox(control_frame, from_=4, to=50, textvariable=self.tile_size_var, width=5)
        spin_tile.pack(side=tk.LEFT, padx=2)

        # 4. 변환 및 복사 버튼
        btn_convert = tk.Button(control_frame, text="► 변환하기", command=self.generate_art, bg="#4CAF50", fg="white")
        btn_convert.pack(side=tk.LEFT, padx=20)
        
        btn_copy = tk.Button(control_frame, text="결과 복사", command=self.copy_to_clipboard)
        btn_copy.pack(side=tk.RIGHT, padx=5)

        # 하단 텍스트 출력 패널 (고정폭 폰트 적용)
        self.text_area = tk.Text(self.master, wrap=tk.NONE, bg="#1e1e1e", fg="#d4d4d4")
        self.text_area.pack(expand=True, fill=tk.BOTH, padx=10, pady=(0, 10))
        
        # 아스키 아트가 찌그러지지 않도록 고정폭(Monospace) 폰트 설정
        self.mono_font = tkfont.Font(family="Consolas", size=8)
        self.text_area.configure(font=self.mono_font)

        # 스크롤바 추가
        scroll_y = tk.Scrollbar(self.text_area, command=self.text_area.yview)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.text_area.configure(yscrollcommand=scroll_y.set)

    def load_image(self):
        path = filedialog.askopenfilename(filetypes=[("Image files", "*.jpg *.jpeg *.png *.bmp")])
        if path:
            self.img_path = path
            messagebox.showinfo("안내", "이미지를 성공적으로 불러왔습니다.")

    def generate_art(self):
        if not self.img_path:
            messagebox.showwarning("경고", "먼저 이미지를 열어주세요.")
            return

        try:
            # 1. 이미지 로드 (흑백)
            img = cv2.imread(self.img_path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise ValueError("이미지 파일을 읽을 수 없습니다.")

            # 너무 큰 이미지는 UI 멈춤 방지를 위해 적절히 리사이즈
            max_width = 800
            h, w = img.shape
            if w > max_width:
                ratio = max_width / w
                img = cv2.resize(img, (max_width, int(h * ratio)))

            # 2. Canny Edge 추출 및 정규화 (작성하신 코드가 0.3을 기준으로 하므로 Canny가 적합)
            edges = cv2.Canny(img, 50, 150)
            edge_map = edges.astype(np.float32) / 255.0

            # 3. 그래디언트/방향성 맵 생성
            angle_map, _ = compute_orientation(img.astype(np.float32) / 255.0)

            # 4. DB 구성 및 렌더링
            charset_name = self.charset_var.get()
            charset = AA_CHARSETS.get(charset_name, AA_CHARSETS["2ch"])
            tile_size = self.tile_size_var.get()

            db = build_template_db(charset, tile_size)
            result = render_aa_line_first(edge_map, angle_map, db, tile_size)

            # 5. UI 출력
            self.text_area.delete(1.0, tk.END)
            self.text_area.insert(tk.END, result)

        except Exception as e:
            messagebox.showerror("에러", f"변환 중 오류가 발생했습니다:\n{str(e)}")

    def copy_to_clipboard(self):
        result = self.text_area.get(1.0, tk.END)
        self.master.clipboard_clear()
        self.master.clipboard_append(result)
        messagebox.showinfo("안내", "클립보드에 복사되었습니다.")

# 프로그램 실행 포인트
if __name__ == "__main__":
    root = tk.Tk()
    app = AsciiArtApp(root)
    root.mainloop()