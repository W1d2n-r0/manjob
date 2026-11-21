// 다른 사이트 그림은 페이지 안에서 못 읽으므로(CORS) 여기서 대신 받아 와서 살펴본다.
importScripts("parse.js");

const cache = new Map(); // 주소 -> 결과(없으면 null)

function toB64(u8) {
  let s = "";
  for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  return btoa(s);
}

async function inspectUrl(url) {
  if (cache.has(url)) return cache.get(url);
  let result = null;
  try {
    const r = await fetch(url, { credentials: "omit" });
    if (r.ok) {
      const u8 = new Uint8Array(await r.arrayBuffer());
      const v = await inspectBytes(u8);
      if (v) result = { name: v.name, mime: v.mime, b64: toB64(v.bytes) };
    }
  } catch (e) { /* 못 받으면 넘어감 */ }
  if (cache.size > 300) cache.clear();
  cache.set(url, result);
  return result;
}

// 후보 주소를 차례로 시도 (원본 주소가 앞에 옴)
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg || msg.type !== "inspect") return;
  (async () => {
    for (const u of msg.urls) {
      const r = await inspectUrl(u);
      if (r) return reply(r);
    }
    reply(null);
  })();
  return true;
});
