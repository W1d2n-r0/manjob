// 페이지의 그림을 살펴보고, 동영상(또는 그림)이 숨어 있으면 그 자리에서 깨운다.
(() => {
  const seen = new WeakSet();

  // 사이트가 보여 주는 그림은 줄이거나 다시 압축한 것일 수 있으니 원본 주소를 앞에 둔다.
  function candidates(img) {
    const src = img.currentSrc || img.src;
    const list = [];
    const a = img.closest("a[href]");
    if (a && /\.(png)(\?|#|$)/i.test(a.href)) list.push(a.href);
    try {
      const u = new URL(src, location.href);
      // 디스코드: media.discordapp.net 미리보기 → 원본
      if (u.hostname === "media.discordapp.net" && u.pathname.startsWith("/attachments/")) {
        for (const k of ["format", "width", "height", "quality"]) u.searchParams.delete(k);
        const cdn = new URL(u); cdn.hostname = "cdn.discordapp.com";
        list.push(cdn.href, u.href);
      }
      // 구글 사진/드라이브/블로그 계열(googleusercontent): 크기 지정 꼬리 대신 원본(=d / s0)
      if (/googleusercontent\.com$/.test(u.hostname)) {
        if (/=[\w-]+$/.test(u.pathname + u.search)) list.push(src.replace(/=[\w-]+$/, "=d"));
        list.push(src.replace(/\/s\d+(-[\w-]+)?\//, "/s0/"));
      }
    } catch (e) {}
    list.push(src);
    return [...new Set(list)];
  }

  async function readHere(src) {
    const r = await fetch(src);
    return inspectBytes(new Uint8Array(await r.arrayBuffer()));
  }

  async function readViaBackground(urls) {
    const res = await chrome.runtime.sendMessage({ type: "inspect", urls });
    if (!res) return null;
    const bin = atob(res.b64), u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    return { name: res.name, mime: res.mime, bytes: u8 };
  }

  async function check(img) {
    if (seen.has(img)) return;
    seen.add(img);
    const src = img.currentSrc || img.src;
    if (!src) return;
    if (/\.(jpe?g|webp|gif|avif|svg|ico)(\?|#|$)/i.test(src) || /^data:image\/(?!png)/.test(src)) return;
    // 너무 작은 그림(아이콘 등)은 건너뜀
    if (img.complete && img.naturalWidth && img.naturalWidth * img.naturalHeight < 4096) return;
    let found = null;
    const local = /^(data:|blob:|file:)/.test(src) || new URL(src, location.href).origin === location.origin;
    if (local) { try { found = await readHere(src); } catch (e) {} }
    if (!found && !/^(data:|blob:)/.test(src)) {
      try { found = await readViaBackground(candidates(img)); } catch (e) { return; }
    }
    if (found) wake(img, found);
  }

  function wake(img, { name, mime, bytes }) {
    const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
    const cs = getComputedStyle(img);
    let el;
    if (mime.startsWith("video/")) {
      el = document.createElement("video");
      el.src = url; el.poster = img.currentSrc || img.src;
      el.autoplay = el.loop = el.muted = el.playsInline = true;
      el.controls = true;
    } else if (mime.startsWith("image/")) {
      el = document.createElement("img"); el.src = url; // 숨은 GIF/APNG 등
    } else return;
    el.title = "숨어 있던 파일: " + name;
    el.className = img.className;
    el.style.cssText = img.style.cssText;
    el.style.width = img.width ? img.width + "px" : cs.width;
    el.style.height = img.height ? img.height + "px" : cs.height;
    el.style.objectFit = cs.objectFit === "fill" ? "contain" : cs.objectFit;
    el.style.background = "#000";
    seen.add(el);
    img.replaceWith(el);
    if (el.play) el.play().catch(() => {});
  }

  function scan(root) {
    if (root.tagName === "IMG") check(root);
    root.querySelectorAll && root.querySelectorAll("img").forEach(check);
  }

  scan(document);
  new MutationObserver(list => {
    for (const m of list) {
      m.addedNodes.forEach(n => n.nodeType === 1 && scan(n));
      if (m.type === "attributes" && m.target.tagName === "IMG") { seen.delete(m.target); check(m.target); }
    }
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["src", "srcset"] });
})();
