"""숨은그림 보기 — PNG 속에 숨은 것을 찾아 보여 주는 프로그램 (시험판 0.2)

찾는 것
  · 글 조각 (tEXt / zTXt / iTXt)          → 내용 표시
  · 규칙에 없는 직접 만든 조각 (mOVe 등)    → 크기 표시, 꺼내기
  · IEND 뒤에 붙은 압축파일(ZIP)           → 반디집처럼 목록, 동영상이면 바로 재생
  · 화소 하위 2비트에 숨긴 장면들 (MOVE)    → 움직이는 그림으로 재생
그 밖에
  · 동영상 → 사진 만들기 (ffmpeg 필요 없음)
  · 윈도우 탐색기 오른쪽 클릭 메뉴에 "숨은그림 보기로 열기" 등록/해제

실행:  pip install PySide6   →   python soomgeurim.py [사진.png]
"""
import io, mimetypes, os, struct, sys, tempfile, zipfile, zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sgcore

from PySide6.QtCore import Qt, QTimer, QUrl, QBuffer, QIODevice, QObject, Signal
from PySide6.QtGui import QImage, QPixmap, QAction, QColor
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink, QAudioOutput, QMediaDevices
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QApplication, QMainWindow, QLabel, QSplitter, QStackedWidget, QTreeWidget,
                               QTreeWidgetItem, QPlainTextEdit, QVBoxLayout, QHBoxLayout, QWidget, QPushButton,
                               QFileDialog, QMessageBox, QSizePolicy)

PNG_SIG = b"\x89PNG\r\n\x1a\n"
STANDARD = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"cHRM", b"gAMA", b"iCCP", b"sBIT", b"sRGB", b"cICP",
            b"mDCV", b"cLLI", b"tEXt", b"zTXt", b"iTXt", b"bKGD", b"hIST", b"pHYs", b"sPLT", b"eXIf", b"tIME",
            b"acTL", b"fcTL", b"fdAT"}
VIDEO_EXT = {".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".ogv"}


# ───────────────────────── 읽기 (화면과 상관없는 부분) ─────────────────────────
class Found:
    """찾아낸 것 하나."""
    def __init__(self, kind, name, size, data=None, note="", zip_name=None):
        self.kind, self.name, self.size, self.data, self.note, self.zip_name = kind, name, size, data, note, zip_name


def scan_png(b: bytes):
    """PNG 바이트를 훑어서 (찾은 것 목록, 오류문) 을 돌려준다."""
    if not b.startswith(PNG_SIG):
        return [], "PNG 파일이 아닙니다."
    found, pos, iend_end = [], 8, None
    while pos + 8 <= len(b):
        ln = struct.unpack(">I", b[pos:pos + 4])[0]
        typ = b[pos + 4:pos + 8]
        data = b[pos + 8:pos + 8 + ln]
        if typ == b"tEXt":
            k, _, v = data.partition(b"\0")
            found.append(Found("text", f"글 · {k.decode('latin1')}", ln, v.decode("latin1")))
        elif typ == b"zTXt":
            k, _, v = data.partition(b"\0")
            try: txt = zlib.decompress(v[1:]).decode("latin1")
            except Exception: txt = "(풀 수 없음)"
            found.append(Found("text", f"글(압축) · {k.decode('latin1')}", ln, txt))
        elif typ == b"iTXt":
            k, _, rest = data.partition(b"\0")
            flag = rest[0]; rest = rest[2:]
            _, _, rest = rest.partition(b"\0"); _, _, txt = rest.partition(b"\0")
            if flag: txt = zlib.decompress(txt)
            found.append(Found("text", f"글(국제) · {k.decode('latin1')}", ln, txt.decode("utf-8", "replace")))
        elif typ == b"eXIf":
            found.append(Found("blob", "EXIF 정보", ln, data))
        elif typ == b"acTL":
            n = struct.unpack(">I", data[:4])[0]
            found.append(Found("info", f"APNG 움직임 조각 (장면 {n}개)", ln, note="브라우저에서 원래 움직이는 PNG"))
        elif typ not in STANDARD:
            name = typ.decode("latin1")
            if typ == b"mOVe" and data[4:8] == b"MOVE":
                found.append(Found("move", f"직접 만든 조각 · {name} (숨은 장면)", ln, data))
            else:
                found.append(Found("blob", f"직접 만든 조각 · {name}", ln, data))
        pos += 12 + ln
        if typ == b"IEND":
            iend_end = pos
            break
    if iend_end is None:
        return found, "IEND가 없습니다 (망가진 파일일 수 있음)."

    tail = b[iend_end:]
    if tail:
        try:
            z = zipfile.ZipFile(io.BytesIO(b))
            for info in z.infolist():
                if info.is_dir(): continue
                ext = os.path.splitext(info.filename)[1].lower()
                kind = "zipvideo" if ext in VIDEO_EXT else "zipfile"
                found.append(Found(kind, f"압축파일 · {info.filename}", info.file_size, zip_name=info.filename))
        except zipfile.BadZipFile:
            found.append(Found("blob", "IEND 뒤 덧붙은 자료", len(tail), tail))
    return found, None


def lsb_payload(img: QImage):
    """화소 하위 2비트(R,G,B 순서)에서 MOVE 자료를 꺼낸다. 없으면 None."""
    img = img.convertToFormat(QImage.Format_RGBA8888)
    raw = bytes(img.constBits())[: img.width() * img.height() * 4]

    def val(k):  # k번째 2비트 값
        p, c = divmod(k, 3)
        return raw[p * 4 + c] & 3

    def read(start, n):
        out = bytearray(n)
        for i in range(n):
            k = (start + i) * 4
            out[i] = (val(k) << 6) | (val(k + 1) << 4) | (val(k + 2) << 2) | val(k + 3)
        return bytes(out)

    cap = img.width() * img.height() * 3 // 4
    if cap < 80: return None
    head = read(0, 8)
    n = struct.unpack(">I", head[:4])[0]
    if head[4:8] != b"MOVE" or n > cap - 4:
        return None
    return read(4, n)


def decode_move(p: bytes):
    """MOVE 자료 → (QImage 목록, 초당 장면 수)."""
    W, H, N, fps, _scale = struct.unpack(">HHHHB", p[4:13])
    pal = p[13:61]
    L = struct.unpack(">I", p[61:65])[0]
    raw = zlib.decompress(p[65:65 + L])
    size = W * H
    lut = [QColor(pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2]).rgb() for i in range(16)]
    frames, prev = [], raw[:size]
    for i in range(N):
        cur = prev if i == 0 else bytes(a ^ c for a, c in zip(prev, raw[i * size:(i + 1) * size]))
        img = QImage(cur, W, H, W, QImage.Format_Indexed8).copy()
        img.setColorTable(lut)
        frames.append(img)
        prev = cur
    return frames, fps


def png_bytes(img: QImage) -> bytes:
    buf = QBuffer(); buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def to_rgb(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    a = np.frombuffer(img.constBits(), np.uint8, count=bpl * h).reshape(h, bpl)
    return a[:, :w * 3].reshape(h, w, 3).copy()


def from_rgb(a: np.ndarray) -> QImage:
    a = np.ascontiguousarray(a)
    h, w, _ = a.shape
    return QImage(a.data, w, h, w * 3, QImage.Format_RGB888).copy()


def hide_in_pixels(file_path, cover: QImage, out_path):
    """파일 하나를 겉그림 화소에 통째로 숨겨 PNG로 저장. 필요하면 겉그림을 키운다. → (가로, 세로, k)"""
    data = open(file_path, "rb").read()
    mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    body = sgcore.pack_body(os.path.basename(file_path), mime, data)
    w, h, k = sgcore.plan(cover.width(), cover.height(), len(body))
    if (w, h) != (cover.width(), cover.height()):
        cover = cover.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out = from_rgb(sgcore.embed(to_rgb(cover), body, k))
    out.save(out_path, "PNG")
    return w, h, k


def make_video_png(video_path, cover: QImage, out_path, mode="pixel"):
    """표지 그림 + 동영상 → 사진 한 장. mode: pixel(화소에 숨김) / append(뒤에 덧붙임)"""
    if mode == "pixel":
        return hide_in_pixels(video_path, cover, out_path)
    png = png_bytes(cover)
    note = b"Comment\x00This picture contains a video. Open it with soomgeurim."
    chunk = struct.pack(">I", len(note)) + b"tEXt" + note + struct.pack(">I", zlib.crc32(b"tEXt" + note) & 0xFFFFFFFF)
    png = png[:-12] + chunk + png[-12:]
    with open(out_path, "wb") as f:
        f.write(png)
    with zipfile.ZipFile(out_path, "a", compression=zipfile.ZIP_STORED) as z:
        z.write(video_path, arcname=os.path.basename(video_path))


def human(n):
    for u in ("바이트", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:,.0f} {u}" if u == "바이트" else f"{n:,.1f} {u}"
        n /= 1024


# ───────────────────────── 동영상 표지 뽑기 ─────────────────────────
class FrameGrabber(QObject):
    done = Signal(QImage)

    def __init__(self, path, at_ms=1000):
        super().__init__()
        self.at = at_ms
        self.player = QMediaPlayer(self)
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._frame)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.last = None
        self.player.play()
        QTimer.singleShot(8000, self._give_up)

    def _frame(self, fr):
        if not fr.isValid(): return
        self.last = fr.toImage()
        if self.player.position() >= self.at:
            self._finish()

    def _give_up(self):
        if self.player is not None: self._finish()

    def _finish(self):
        if self.player is None: return
        self.player.stop(); self.player = None
        self.done.emit(self.last if self.last is not None else QImage())


# ───────────────────────── 화면 ─────────────────────────
class Viewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("숨은그림 보기")
        self.resize(1100, 700)
        self.setAcceptDrops(True)
        self.path, self.bytes, self.still = None, b"", None
        self.tmpdir = tempfile.TemporaryDirectory()
        self.anim, self.anim_i = [], 0
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick)

        # 왼쪽: 보기 칸
        self.picture = QLabel("PNG 파일을 끌어다 놓으세요", alignment=Qt.AlignCenter)
        self.picture.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.picture.setStyleSheet("background:#111;color:#888;font-size:18px")
        self.video = QVideoWidget(); self.video.setStyleSheet("background:#000")
        self.player = QMediaPlayer(self)
        self.audio = None
        if os.environ.get("SOOMGEURIM_NO_AUDIO") != "1" and QMediaDevices.audioOutputs():  # 소리 장치가 없는 컴퓨터에서는 만들지 않음
            self.audio = QAudioOutput(self); self.audio.setMuted(True)
            self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.setLoops(QMediaPlayer.Infinite)
        self.stack = QStackedWidget(); self.stack.addWidget(self.picture); self.stack.addWidget(self.video)

        # 오른쪽: 찾은 것 목록
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["찾은 것", "크기"])
        self.tree.setColumnWidth(0, 230)
        self.tree.itemSelectionChanged.connect(self._select)
        self.tree.itemDoubleClicked.connect(lambda *_: self._extract())
        self.text = QPlainTextEdit(readOnly=True); self.text.setMaximumHeight(140)
        b_open = QPushButton("열기"); b_open.clicked.connect(self._open_dialog)
        b_ext = QPushButton("꺼내기"); b_ext.clicked.connect(self._extract)
        b_sound = QPushButton("소리 켜기/끄기"); b_sound.clicked.connect(lambda: self.audio and self.audio.setMuted(not self.audio.isMuted()))
        row = QHBoxLayout(); [row.addWidget(x) for x in (b_open, b_ext, b_sound)]
        side = QWidget(); lay = QVBoxLayout(side); lay.addWidget(self.tree); lay.addWidget(self.text); lay.addLayout(row)

        split = QSplitter(); split.addWidget(self.stack); split.addWidget(side); split.setSizes([720, 380])
        self.setCentralWidget(split)

        m = self.menuBar().addMenu("파일")
        for title, fn in (("열기…", self._open_dialog),
                          ("동영상을 사진으로 — 화소에 숨기기…", lambda: self._make_dialog("pixel")),
                          ("동영상을 사진으로 — 뒤에 덧붙이기…", lambda: self._make_dialog("append")),
                          ("아무 파일이나 그림 화소에 숨기기…", self._hide_any)):
            a = QAction(title, self); a.triggered.connect(fn); m.addAction(a)
        if sys.platform == "win32":
            w = self.menuBar().addMenu("윈도우")
            a = QAction("탐색기 메뉴에 등록", self); a.triggered.connect(lambda: self._register(True)); w.addAction(a)
            a = QAction("탐색기 메뉴에서 빼기", self); a.triggered.connect(lambda: self._register(False)); w.addAction(a)
        self.statusBar().showMessage("준비")

    # ── 열기
    def open_file(self, path):
        try:
            self.bytes = open(path, "rb").read()
        except OSError as e:
            QMessageBox.warning(self, "열기 실패", str(e)); return
        self.path = path
        self.setWindowTitle(f"숨은그림 보기 — {os.path.basename(path)}")
        self._stop()
        self.still = QImage.fromData(self.bytes)
        found, err = scan_png(self.bytes)
        if not self.still.isNull():
            p = lsb_payload(self.still)
            if p: found.insert(0, Found("move", "화소 하위 2비트 (숨은 장면)", len(p), p))
            r = sgcore.extract(to_rgb(self.still))
            if r:
                name, mime, data = r
                f = Found("pixvideo" if mime.startswith("video/") else "pixfile", f"화소 속 파일 · {name}", len(data), data,
                          note=f"형식: {mime}\n메타데이터를 지우는 사이트에 올려도 살아남는 자리입니다.")
                f.fname = name
                found.insert(0, f)
        self.tree.clear()
        top = QTreeWidgetItem(["겉그림", f"{self.still.width()}×{self.still.height()}" if not self.still.isNull() else "-"])
        top.setData(0, Qt.UserRole, Found("still", "겉그림", len(self.bytes)))
        self.tree.addTopLevelItem(top)
        for f in found:
            it = QTreeWidgetItem([f.name, human(f.size)]); it.setData(0, Qt.UserRole, f)
            self.tree.addTopLevelItem(it)
        hidden = len(found)
        self.statusBar().showMessage(err or (f"숨은 것 {hidden}개를 찾았습니다." if hidden else "숨은 것이 없는 평범한 PNG입니다."))
        # 움직일 수 있는 것이 있으면 바로 재생
        pick = next((i for i in range(1, self.tree.topLevelItemCount())
                     if self.tree.topLevelItem(i).data(0, Qt.UserRole).kind in ("pixvideo", "zipvideo", "move")), 0)
        self.tree.setCurrentItem(self.tree.topLevelItem(pick))

    def _open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "PNG 열기", "", "PNG (*.png);;모든 파일 (*)")
        if p: self.open_file(p)

    # ── 고른 것 보여 주기
    def _selected(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _select(self):
        f = self._selected()
        if not f: return
        self._stop()
        self.text.setPlainText(f.data if f.kind == "text" else f.note)
        if f.kind == "pixvideo":
            out = os.path.join(self.tmpdir.name, os.path.basename(f.fname))
            with open(out, "wb") as o: o.write(f.data)
            self.stack.setCurrentWidget(self.video)
            self.player.setSource(QUrl.fromLocalFile(out)); self.player.play()
        elif f.kind == "zipvideo":
            out = os.path.join(self.tmpdir.name, os.path.basename(f.zip_name))
            with zipfile.ZipFile(io.BytesIO(self.bytes)) as z, open(out, "wb") as o:
                o.write(z.read(f.zip_name))
            self.stack.setCurrentWidget(self.video)
            self.player.setSource(QUrl.fromLocalFile(out)); self.player.play()
        elif f.kind == "move":
            try:
                self.anim, fps = decode_move(f.data)
            except Exception as e:
                self.text.setPlainText(f"장면을 풀지 못했습니다: {e}"); self._show(self.still); return
            self.anim_i = 0; self.timer.start(int(1000 / max(1, fps))); self._tick()
        else:
            self._show(self.still)

    def _show(self, img):
        self.stack.setCurrentWidget(self.picture)
        if img is None or img.isNull(): return
        pm = QPixmap.fromImage(img).scaled(self.picture.size(), Qt.KeepAspectRatio,
                                           Qt.FastTransformation if img.width() < 400 else Qt.SmoothTransformation)
        self.picture.setPixmap(pm)

    def _tick(self):
        if not self.anim: return
        self._show(self.anim[self.anim_i]); self.anim_i = (self.anim_i + 1) % len(self.anim)

    def _stop(self):
        self.timer.stop(); self.anim = []; self.player.stop()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.stack.currentWidget() is self.picture and not self.timer.isActive():
            self._show(self.still)

    # ── 꺼내기
    def _extract(self):
        f = self._selected()
        if not f or not self.path: return
        base = os.path.splitext(os.path.basename(self.path))[0]
        if f.kind in ("pixvideo", "pixfile"):
            name, data = f.fname, f.data
        elif f.zip_name:
            name, data = os.path.basename(f.zip_name), zipfile.ZipFile(io.BytesIO(self.bytes)).read(f.zip_name)
        elif f.kind == "text":
            name, data = f"{base}_글.txt", f.data.encode("utf-8")
        elif f.kind == "move" and self.anim:
            d = QFileDialog.getExistingDirectory(self, "장면들을 저장할 폴더")
            if d:
                for i, im in enumerate(self.anim): im.save(os.path.join(d, f"{base}_{i:03d}.png"))
                self.statusBar().showMessage(f"장면 {len(self.anim)}개를 저장했습니다.")
            return
        elif f.data:
            name, data = f"{base}_{f.name.split('·')[-1].strip()}.bin", f.data
        else:
            return
        out, _ = QFileDialog.getSaveFileName(self, "꺼내서 저장", os.path.join(os.path.dirname(self.path), name))
        if out:
            open(out, "wb").write(data)
            self.statusBar().showMessage(f"저장했습니다: {out}")

    # ── 동영상 → 사진
    def _make_dialog(self, mode="pixel"):
        v, _ = QFileDialog.getOpenFileName(self, "넣을 동영상", "", "동영상 (*.mp4 *.webm *.mov *.m4v *.mkv);;모든 파일 (*)")
        if not v: return
        out, _ = QFileDialog.getSaveFileName(self, "만들 사진", os.path.splitext(v)[0] + ".png", "PNG (*.png)")
        if not out: return
        self.statusBar().showMessage("표지 장면을 뽑는 중…")
        self._grab = FrameGrabber(v)

        def finish(img):
            self._finish_make(v, img, out, mode)
        self._grab.done.connect(finish)

    def _finish_make(self, src, img, out, mode):
        if img.isNull():
            img = QImage(640, 360, QImage.Format_RGB32); img.fill(QColor("#222"))
        try:
            r = make_video_png(src, img, out, mode)
        except Exception as e:
            QMessageBox.warning(self, "실패", str(e)); return
        size = os.path.getsize(out)
        msg = f"만들었습니다: {out} ({human(size)})"
        if r: msg += f" · 겉그림 {r[0]}×{r[1]}, 채널당 {r[2]}비트"
        if size > 10 * 1024 * 1024: msg += " · 10MB가 넘어서 디스코드 무료 계정으로는 못 올립니다"
        self.open_file(out)
        self.statusBar().showMessage(msg)

    def _hide_any(self):
        src, _ = QFileDialog.getOpenFileName(self, "숨길 파일", "", "모든 파일 (*)")
        if not src: return
        cov, _ = QFileDialog.getOpenFileName(self, "겉그림 (취소하면 검은 그림)", "", "그림 (*.png *.jpg *.jpeg *.webp *.bmp)")
        img = QImage(cov) if cov else QImage()
        out, _ = QFileDialog.getSaveFileName(self, "만들 사진", os.path.splitext(src)[0] + "_숨김.png", "PNG (*.png)")
        if out: self._finish_make(src, img, out, "pixel")

    # ── 윈도우 탐색기 등록 (관리자 권한 필요 없음, 현재 사용자에게만)
    def _register(self, on):
        import winreg
        key = r"Software\Classes\SystemFileAssociations\.png\shell\soomgeurim"
        try:
            if on:
                if getattr(sys, "frozen", False):
                    cmd = f'"{sys.executable}" "%1"'
                else:
                    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                    cmd = f'"{pyw}" "{os.path.abspath(__file__)}" "%1"'
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
                    winreg.SetValue(k, "", winreg.REG_SZ, "숨은그림 보기로 열기")
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + r"\command") as k:
                    winreg.SetValue(k, "", winreg.REG_SZ, cmd)
                QMessageBox.information(self, "등록", "PNG 파일 오른쪽 클릭 메뉴에 '숨은그림 보기로 열기'를 넣었습니다.\n(윈도우 11은 '더 많은 옵션 표시' 안에 있습니다)")
            else:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key + r"\command")
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
                QMessageBox.information(self, "해제", "탐색기 메뉴에서 뺐습니다.")
        except OSError as e:
            QMessageBox.warning(self, "실패", str(e))

    # ── 끌어다 놓기
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e):
        p = e.mimeData().urls()[0].toLocalFile()
        if p.lower().endswith((".mp4", ".webm", ".mov", ".m4v", ".mkv")):
            out = os.path.splitext(p)[0] + ".png"
            if QMessageBox.question(self, "동영상", f"이 동영상을 사진으로 만들까요?\n→ {out}") == QMessageBox.Yes:
                self._grab = FrameGrabber(p)
                self._grab.done.connect(lambda img: self._finish_make(p, img, out, "pixel"))
        else:
            self.open_file(p)


def main():
    app = QApplication(sys.argv)
    w = Viewer(); w.show()
    if len(sys.argv) > 1: w.open_file(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
