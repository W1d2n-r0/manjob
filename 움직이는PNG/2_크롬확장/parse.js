// 사진 속 숨은 파일 찾기 — 확장의 백그라운드·페이지 양쪽과 시험 코드가 같이 씀
const MIME = { mp4: "video/mp4", m4v: "video/mp4", webm: "video/webm", mov: "video/quicktime", ogv: "video/ogg", gif: "image/gif" };

// ── 1. PNG 끝(IEND) 뒤에 덧붙은 압축파일 속 동영상
function findAppendedVideo(b) {
  const u32 = o => ((b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]) >>> 0;
  const le16 = o => b[o] | (b[o + 1] << 8);
  const le32 = o => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
  const sig = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (b.length < 8 || sig.some((v, i) => b[i] !== v)) return null;
  let pos = 8, end = -1;
  while (pos + 8 <= b.length) {
    const ln = u32(pos), t = String.fromCharCode(b[pos + 4], b[pos + 5], b[pos + 6], b[pos + 7]);
    pos += 12 + ln;
    if (t === "IEND") { end = pos; break; }
  }
  if (end < 0) return null;
  pos = end;
  while (pos + 30 <= b.length && le32(pos) === 0x04034b50) {
    const method = le16(pos + 8), size = le32(pos + 18);
    const nameLen = le16(pos + 26), extraLen = le16(pos + 28);
    const name = new TextDecoder().decode(b.subarray(pos + 30, pos + 30 + nameLen));
    const start = pos + 30 + nameLen + extraLen;
    const ext = (name.split(".").pop() || "").toLowerCase();
    if (method === 0 && size > 0 && MIME[ext]) return { name, mime: MIME[ext], bytes: b.subarray(start, start + size) };
    if (size === 0) break;
    pos = start + size;
  }
  return null;
}

// ── 2. 화소 하위 비트에 숨긴 파일 (sgcore.py와 같은 배치)
const CRC_T = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
function crc32(u8) { let c = 0xFFFFFFFF; for (let i = 0; i < u8.length; i++) c = CRC_T[(c ^ u8[i]) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }

function findPixelFile(rgba) {
  // rgba: RGBA 순서 바이트 (getImageData().data). RGB만 순서대로 쓴다.
  const total = (rgba.length / 4) * 3;
  const at = i => rgba[((i / 3) | 0) * 4 + (i % 3)];
  if (total < 104) return null;
  const head = new Uint8Array(12);
  for (let i = 0; i < 96; i++) head[i >> 3] |= (at(i) & 1) << (7 - (i & 7));
  if (head[0] !== 0x53 || head[1] !== 0x47) return null; // 'SG'
  const k = head[2];
  if (k < 1 || k > 4) return null;
  const n = ((head[4] << 24) | (head[5] << 16) | (head[6] << 8) | head[7]) >>> 0;
  const crc = ((head[8] << 24) | (head[9] << 16) | (head[10] << 8) | head[11]) >>> 0;
  const need = Math.ceil((n * 8) / k);
  if (96 + need > total) return null;
  const body = new Uint8Array(n), mask = (1 << k) - 1;
  let acc = 0, nbits = 0, o = 0;
  for (let i = 0; i < need && o < n; i++) {
    acc = (acc << k) | (at(96 + i) & mask); nbits += k;
    if (nbits >= 8) { nbits -= 8; body[o++] = (acc >> nbits) & 255; acc &= (1 << nbits) - 1; }
  }
  if (crc32(body) !== crc) return null;
  const nl = body[0], name = new TextDecoder().decode(body.subarray(1, 1 + nl));
  const ml = body[1 + nl], mime = new TextDecoder().decode(body.subarray(2 + nl, 2 + nl + ml));
  return { name, mime, bytes: body.subarray(2 + nl + ml) };
}

// ── 둘 다 시도 (createImageBitmap이 있는 곳에서만 화소를 읽음)
async function inspectBytes(u8) {
  const a = findAppendedVideo(u8);
  if (a) return a;
  if (typeof createImageBitmap !== "function" || typeof OffscreenCanvas !== "function") return null;
  let bmp;
  try {
    bmp = await createImageBitmap(new Blob([u8]), { colorSpaceConversion: "none", premultiplyAlpha: "none" });
  } catch (e) { return null; }
  if (bmp.width * bmp.height < 64) return null;
  const c = new OffscreenCanvas(bmp.width, bmp.height);
  const g = c.getContext("2d", { willReadFrequently: true });
  g.drawImage(bmp, 0, 0);
  const d = g.getImageData(0, 0, bmp.width, bmp.height).data;
  return findPixelFile(d);
}

if (typeof module !== "undefined") module.exports = { findAppendedVideo, findPixelFile, inspectBytes };
