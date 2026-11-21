"""동영상을 PNG 한 장으로 만들기.

겉: 동영상의 한 장면 (어떤 그림 보기에서도 평범한 PNG)
속: PNG 끝(IEND) 뒤에 압축파일(ZIP)을 붙이고 그 안에 동영상 원본을 넣음
  -> 반디집, 7-Zip 등으로 열면 동영상이 나옴
  -> 크롬 확장 '잠든 그림 깨우기'가 깔려 있으면 웹에서 바로 재생

쓰는 법:
  python vid2png.py 입력.mp4 [출력.png] [--at 초] [--width 가로]
  python vid2png.py --extract 사진.png        (동영상 꺼내기)
필요: Python 3, ffmpeg (표지 장면 뽑을 때만)
"""
import argparse, os, struct, subprocess, sys, tempfile, zipfile, zlib


def chunk(typ, data):
    return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)


def png_end(b):
    """IEND 조각이 끝나는 위치."""
    assert b[:8] == b"\x89PNG\r\n\x1a\n", "PNG가 아닙니다"
    pos = 8
    while pos < len(b):
        ln = struct.unpack(">I", b[pos:pos + 4])[0]
        typ = b[pos + 4:pos + 8]
        pos += 12 + ln
        if typ == b"IEND":
            return pos
    raise ValueError("IEND를 못 찾았습니다")


def make_cover(video, at, width, out):
    vf = f"scale={width}:-2" if width else "null"
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", video, "-frames:v", "1", "-vf", vf, out]
    r = subprocess.run(cmd)
    if r.returncode or not os.path.exists(out):  # 영상이 짧으면 첫 장면으로
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-frames:v", "1", "-vf", vf, out], check=True)


def encode(video, out, at, width):
    with tempfile.TemporaryDirectory() as td:
        cover = os.path.join(td, "cover.png")
        make_cover(video, at, width, cover)
        png = open(cover, "rb").read()
    png = png[:png_end(png)]
    # 설명 글 조각을 IEND 앞에 끼움 (사람이 메타데이터 보기로 열면 보임)
    note = b"Comment\x00This picture contains a video. Open with Bandizip/7-Zip or the browser extension."
    iend = png[-12:]
    png = png[:-12] + chunk(b"tEXt", note) + iend
    with open(out, "wb") as f:
        f.write(png)
    # 'a' 모드: ZIP이 아닌 파일 끝에 압축파일을 덧붙임 (자리값은 파일 맨 앞 기준으로 기록됨)
    # 동영상은 이미 압축돼 있으니 다시 압축하지 않음(STORED) -> 브라우저가 바로 잘라 쓸 수 있음
    with zipfile.ZipFile(out, "a", compression=zipfile.ZIP_STORED) as z:
        z.write(video, arcname=os.path.basename(video))
    v, p = os.path.getsize(video), os.path.getsize(out)
    print(f"완료: {out}  (동영상 {v:,}바이트 -> 사진 {p:,}바이트, 표지 몫 {p - v:,})")


def extract(png_path, dest):
    with zipfile.ZipFile(png_path) as z:
        for n in z.namelist():
            z.extract(n, dest)
            print("꺼냄:", os.path.join(dest, n))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--at", type=float, default=1.0, help="표지로 쓸 장면 위치(초)")
    ap.add_argument("--width", type=int, default=0, help="표지 가로 크기 (0이면 원본)")
    ap.add_argument("--extract", action="store_true")
    a = ap.parse_args()
    if a.extract:
        extract(a.input, a.output or os.path.dirname(os.path.abspath(a.input)))
    else:
        encode(a.input, a.output or os.path.splitext(a.input)[0] + ".png", a.at, a.width)
