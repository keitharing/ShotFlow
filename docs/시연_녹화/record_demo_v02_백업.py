"""ShotFlow 시연 영상 녹화 (영상 1 BEFORE THE DUST · 영상 2 FIRST DROP 실제 기록 기준).

- 원본 DB를 건드리지 않도록 data_demo 복사본으로 띄운 서버(8720)를 화면 조작함
  (backend 폴더에서: set SHOTFLOW_DATA=..\\data_demo  →  ..\\.venv\\Scripts\\python.exe -m shotflow serve --port 8720)
- 크롬 헤드리스를 DevTools로 움직여 실제로 누르고 입력하고 파일을 올림. 마우스 커서는 화면에 그려 넣음
- 자막은 장면마다 아래에 넣음. 결과: 1920x1080, 24fps, H.264

사용:  ..\\.venv\\Scripts\\python.exe record_demo.py test    (장면마다 정지 그림 한 장, 배치 확인용)
       ..\\.venv\\Scripts\\python.exe record_demo.py video   (영상)
"""
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from io import BytesIO
from pathlib import Path

import websocket
from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
SF = HERE.parents[1]
CAREER = SF.parents[1]
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
FFMPEG = str(SF / ".venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe")
FONT = str(CAREER / "02_포트폴리오/00_안우혁_포트폴리오/assets/font/NotoSansKR-VF.ttf")
APP = "http://127.0.0.1:8720/"
PORT = 9361
W, H, DSF = 1280, 720, 1.5
OUT_W, OUT_H = 1920, 1080
MODE = sys.argv[1] if len(sys.argv) > 1 else "test"
OUT_DIR = HERE / ("test" if MODE == "test" else "out")
FINAL = CAREER / "02_포트폴리오/00_안우혁_포트폴리오/assets/video/ShotFlow_Demo_v02.mp4"

# 시연에서 다시 등록해 보이는 그림: FIRST DROP A1에 실제로 쓴 시작 그림(ext_006)과 같은 파일
START_IMAGE = SF / "data/imports/shot00024/ext_006.png"

A1, A2, A3 = 24, 25, 26
SHOTS_TEXT = ("A1 | 아주 가까이. 음료 줄기가 병 왼쪽으로 쏟아지고, 줄기가 가늘어지며 끝난다 (4초)\n"
              "A2 | 앞에서 표면 높이로. 같은 자리에 과일 조각이 떨어져 왕관 모양으로 튀어 오른다 (4초)\n"
              "A3 | 정면. 병에 맺힌 큰 방울이 흘러내리며 끝난다 (5초)")

# (자막, [단계...])
# 단계: ("card", 제목, 부제, 초) / ("nav", 주소, 초) / ("hold", 초) / ("click", css, 글자, 초) / ("type", css, 글, 초)
#       ("select", css, 값, 초) / ("upload", css, 파일) / ("scroll", 픽셀, 초) / ("play", 초) / ("hover", css, 글자, 초)
SCENES = [
    (None, [("card", "ShotFlow", "직접 설계하고 개발한 AI 영상 제작 관리 툴\n실제 작업 기록으로 보는 시연: FIRST DROP · BEFORE THE DUST", 4.0)]),
    ("현황판: 영상 6편의 샷 · 버전 · 생성 작업 · 외부 생성 크레딧을 한곳에서 봅니다",
     [("nav", "#/", 4.5)]),
    ("새 영상 등록: 코드 · 제목 · 목표 길이 · 규격 · 샷 목록을 한 번에 넣습니다 (FIRST DROP을 등록할 때 넣은 값)",
     [("click", "button", "새 영상", 0.8),
      ("type", "[data-demo=new-sequence] input[placeholder^='예: FIRST_DROP']", "FIRST_DROP", 0.3),
      ("type", "[data-demo=new-sequence] input[placeholder='예: FIRST DROP']", "FIRST DROP", 0.3),
      ("type", "[data-demo=new-sequence] input[placeholder='예: 13']", "13", 0.3),
      ("type", "[data-demo=new-sequence] textarea", SHOTS_TEXT, 1.6),
      ("hover", "[data-demo=new-sequence] button.primary", "등록", 1.2),
      ("click", "[data-demo=new-sequence] button", "닫기", 0.4)]),
    ("등록된 FIRST DROP: 샷 3개 · 1280×720. 샷마다 지금 승인된 버전과 자동 검수 결과, 아래는 편집본",
     [("click", "a", "FIRST DROP", 4.0), ("scroll", 260, 1.2), ("hold", 1.2)]),
    ("Blender 연동: 장면 하나(병 · 세트 · 카메라 3대)에서 컷마다 렌더 · 깊이 · 마스크를 화면 없이 뽑아 구도를 고정",
     [("nav", "#/scene3d?tab=keyframe", 1.6), ("scrollto", "main h2", "깊이 이미지", 1.2),
      ("pick", "26", 0.6), ("pick", "149", 3.4)]),
    ("샷 A1의 버전: Blender 렌더(b3d) → 시작 그림 후보(ext) → 생성 영상(sd, 현재 승인)",
     [("nav", f"#/shot/{A1}?v=112", 1.6), ("hover", ".version-row", "b3d_004", 0.8), ("hover", ".version-row", "ext_006", 0.8),
      ("hover", ".version-row", "sd_001", 1.2)]),
    ("밖에서 만든 파일 등록: 이미지 모델(Nano Banana Pro)로 그린 시작 그림을 원본 버전과 이어 등록하면 자동 검수가 돕니다 (흰 배경이라 실제 작업 때와 같은 밝기 경고)",
     [("click", "button", "밖에서 만든 파일 등록", 0.8),
      ("upload", "[data-demo=import-version] input[type=file]", str(START_IMAGE)),
      ("select", "[data-demo=import-version] select", "94", 0.5),
      ("type", "[data-demo=import-version] input[placeholder^='예: Nano']", "Nano Banana Pro로 다시 그린 시작 그림", 0.4),
      ("click", "[data-demo=import-version] button.primary", "등록하고 검수", 3.4)]),
    ("외부 생성 묶음: Higgsfield(Seedance 2.5)에 보낼 시작 · 끝 그림과 프롬프트, 길이를 묶고, 결과 영상과 쓴 크레딧을 다시 등록",
     [("nav", "#/scene3d?tab=external", 1.4), ("scrollto", "main *", "#161 ·", 1.6), ("hold", 2.4)]),
    ("샷 A3의 버전 계보: Blender 렌더 → 그림 후보 → 생성 영상 3개 → 빛 효과 합성본(현재 승인)",
     [("nav", f"#/shot/{A3}?v=128", 1.6), ("hover", ".version-row", "b3d_005", 0.7), ("hover", ".version-row", "ext_016", 0.7),
      ("hover", ".version-row", "sd_003", 0.9), ("hover", ".version-row", "sd_003_comp_001", 1.6)]),
    ("리뷰: 생성 영상과 효과 합성본을 나란히 비교하고, 고른 이유는 노트로 남깁니다",
     [("nav", "#/review?a=120&b=128", 2.0), ("play", 3.0), ("hold", 0.6)]),
    ("효과 레이어 합성: Blender로 렌더한 '병에 스치는 빛' 레이어를 A3 생성 영상 위에 얹고, 진하기를 바꿔 가며 미리 봅니다",
     [("nav", "#/scene3d?tab=layer", 1.2), ("pick", "166", 0.4), ("scrollto", "main h2", "합성할 샷", 1.0),
      ("pick", "26", 0.5), ("pick", "120", 1.0), ("slider", "진하기", 0.2, 1.0, 2.6), ("hold", 1.2)]),
    ("편집본 rough_v06: 승인한 컷 4개를 자르고 이어 조립하고, 컷 경계와 소리를 자동 검수",
     [("nav", "#/edit/16", 2.0), ("scrollto", "main h2", "결과", 1.0), ("play", 3.6), ("hold", 0.6)]),
    ("납품 패키지: 승인한 FIRST DROP 컷만 모아 이름 규칙대로 복사하고, 파일마다 SHA-256 체크섬을 남깁니다",
     [("nav", "#/deliveries", 1.6),
      ("type", "section.panel input:not([type=checkbox])", "FIRST_DROP_approved", 0.3),
      ("click", "label", "BASALT RUN", 0.15), ("click", "label", "RAIN ATLAS", 0.15), ("click", "label", "THE LAST LIGHT", 0.15),
      ("click", "label", "BEFORE THE DUST", 0.15), ("click", "label", "빛을 쥔 아이", 0.4),
      ("click", "button", "패키지 만들기", 3.6)]),
    ("BEFORE THE DUST도 같은 흐름. 파편 · 먼지 효과는 ShotFlow 밖 Blender 스크립트로 렌더하고, 합성본을 FX 버전으로 등록",
     [("nav", "#/seq/4", 4.0)]),
    (None, [("card", "ShotFlow", "Python FastAPI · SQLite · React · TypeScript · 자동 테스트 92개\n본편의 색 · 소리 마무리는 ShotFlow 밖 스크립트로 함 · 개인 프로젝트, 2026", 4.0)]),
]

CURSOR_JS = r"""(() => {
  if (window.__cur) return true;
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;z-index:2147483647;left:0;top:0;width:26px;height:26px;pointer-events:none;transition:transform .5s cubic-bezier(.3,.7,.3,1);transform:translate(900px,520px)';
  c.innerHTML = '<svg width="26" height="26" viewBox="0 0 26 26"><path d="M3 2 L3 21 L8.5 16 L12 24 L15.5 22.5 L12 14.8 L19.5 14.8 Z" fill="#fff" stroke="#111" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  document.body.appendChild(c);
  const r = document.createElement('div');
  r.style.cssText = 'position:fixed;z-index:2147483646;left:0;top:0;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;border:3px solid #ffb547;opacity:0;pointer-events:none;transition:opacity .35s, transform .35s';
  document.body.appendChild(r);
  window.__cur = c; window.__ring = r;
  window.__find = (sel, text) => {
    const all = [...document.querySelectorAll(sel)].filter(e => e.offsetParent !== null && (!text || e.textContent.includes(text)));
    // 글자로 찾을 때는 그 글자를 가진 가장 안쪽 요소 (같은 글자를 가진 바깥 상자 말고)
    return text ? (all.find(e => ![...e.children].some(c => c.textContent.includes(text))) || all[0]) : all[0];
  };
  window.__move = (sel, text) => {
    const el = window.__find(sel, text); if (!el) return false;
    el.scrollIntoView({block: 'nearest'});
    const b = el.getBoundingClientRect();
    window.__cur.style.transform = `translate(${b.left + Math.min(b.width / 2, 60)}px, ${b.top + b.height / 2}px)`;
    window.__target = el; return true;
  };
  window.__click = () => {
    const el = window.__target; if (!el) return false;
    const m = /translate\(([-\d.]+)px, ([-\d.]+)px\)/.exec(window.__cur.style.transform);
    window.__ring.style.left = m[1] + 'px'; window.__ring.style.top = m[2] + 'px';
    window.__ring.style.transition = 'none'; window.__ring.style.opacity = 1; window.__ring.style.transform = 'scale(.4)';
    requestAnimationFrame(() => { window.__ring.style.transition = 'opacity .45s, transform .45s'; window.__ring.style.opacity = 0; window.__ring.style.transform = 'scale(1.4)'; });
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') el.focus(); else el.click();
    return true;
  };
  return true;
})()"""
SCROLLER_JS = r"""(() => {
  const c = [...document.querySelectorAll('*')].filter(e => e.scrollHeight > e.clientHeight + 40 && /auto|scroll/.test(getComputedStyle(e).overflowY))
    .sort((a, b) => b.clientHeight * b.clientWidth - a.clientHeight * a.clientWidth)[0] || document.scrollingElement;
  window.__sc = c; return c.scrollHeight - c.clientHeight;
})()"""


class Browser:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    def cmd(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def shot(self):
        r = self.cmd("Page.captureScreenshot", format="jpeg", quality=92)
        return Image.open(BytesIO(base64.b64decode(r["data"]))).convert("RGB")


def font(size, bold=False):
    f = ImageFont.truetype(FONT, size)
    try:
        f.set_variation_by_axes([700 if bold else 500])
    except Exception:
        pass
    return f


def caption(img, text):
    if img.size != (OUT_W, OUT_H):
        img = img.resize((OUT_W, OUT_H), Image.LANCZOS)
    if not text:
        return img
    d = ImageDraw.Draw(img, "RGBA")
    f = font(34)
    # 두 줄까지 자동 줄바꿈
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if d.textlength(test, font=f) > OUT_W - 260 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    lines.append(cur)
    lh = 50
    box_h = lh * len(lines) + 34
    y0 = OUT_H - box_h - 40
    d.rounded_rectangle([110, y0, OUT_W - 110, y0 + box_h], radius=14, fill=(10, 12, 16, 214))
    for i, line in enumerate(lines):
        tw = d.textlength(line, font=f)
        d.text(((OUT_W - tw) / 2, y0 + 17 + i * lh), line, font=f, fill=(245, 246, 248, 255))
    return img


def card(title, sub):
    img = Image.new("RGB", (OUT_W, OUT_H), (17, 19, 23))
    d = ImageDraw.Draw(img)
    ft, fs = font(96, True), font(36)
    tw = d.textlength(title, font=ft)
    d.text(((OUT_W - tw) / 2, 380), title, font=ft, fill=(245, 246, 248))
    for i, line in enumerate(sub.split("\n")):
        lw = d.textlength(line, font=fs)
        d.text(((OUT_W - lw) / 2, 540 + i * 60), line, font=fs, fill=(170, 176, 184))
    return img


class Recorder:
    def __init__(self, b, frames_dir):
        self.b, self.dir, self.frames, self.cap = b, frames_dir, [], None
        self.last = time.time()

    def add(self, img, dur, cap=None):
        """img: PIL 그림(카드) 또는 화면 캡처 JPEG 바이트. 자막은 녹화가 끝난 뒤 한꺼번에 얹음(캡처를 빠르게)."""
        p = self.dir / f"{len(self.frames):05d}.jpg"
        if isinstance(img, bytes):
            p.write_bytes(img)
        else:
            img.save(p, quality=93)
        self.frames.append((p, dur, cap))

    def record(self, seconds, every=None):
        """Capture frames for `seconds`; `every` runs before each capture (for animation)."""
        end = time.time() + seconds
        while True:
            if every:
                every()
            raw = base64.b64decode(self.b.cmd("Page.captureScreenshot", format="jpeg", quality=90)["data"])
            now = time.time()
            if self.frames and MODE != "test":
                p, _, c = self.frames[-1]
                self.frames[-1] = (p, max(now - self.last, 1 / 30), c)
            self.last = now
            self.add(raw, 1 / 12, self.cap)
            if MODE == "test" or now >= end:
                break

    def finish(self):
        """Put captions on every captured frame (cards already have none)."""
        for p, _, cap in self.frames:
            img = Image.open(p).convert("RGB")
            if img.size != (OUT_W, OUT_H) or cap:
                caption(img, cap).save(p, quality=93)


def run_step(rec, b, step):
    kind = step[0]
    fast = MODE == "test"
    if kind == "card":
        _, title, sub, sec = step
        rec.add(card(title, sub), 0.5 if fast else sec)
        rec.last = time.time()
    elif kind == "nav":
        _, url, sec = step
        b.cmd("Page.navigate", url=APP + url)
        time.sleep(1.2)
        b.js(CURSOR_JS)
        rec.record(0 if fast else sec)
    elif kind == "hold":
        rec.record(0 if fast else step[1])
    elif kind in ("click", "hover"):
        _, sel, text, sec = step
        b.js(CURSOR_JS)
        if not b.js(f"window.__move({json.dumps(sel)}, {json.dumps(text)})"):
            raise RuntimeError(f"찾을 수 없음: {sel} '{text}'")
        rec.record(0 if fast else 0.6)
        if kind == "click":
            b.js("window.__click()")
            time.sleep(0.3)
            b.js(CURSOR_JS)
        rec.record(0 if fast else sec)
    elif kind == "type":
        _, sel, text, sec = step
        if not b.js(f"window.__move({json.dumps(sel)}, '')"):
            raise RuntimeError(f"입력칸 없음: {sel}")
        rec.record(0 if fast else 0.5)
        b.js("window.__click()")
        chunk = 1 if len(text) < 40 else 6
        for i in range(0, len(text), chunk):
            part = text[i:i + chunk]
            for line_i, piece in enumerate(part.split("\n")):
                if line_i:
                    b.cmd("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", text="\r", windowsVirtualKeyCode=13)
                    b.cmd("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)
                if piece:
                    b.cmd("Input.insertText", text=piece)
            if not fast:
                rec.record(0)
        rec.record(0 if fast else sec)
    elif kind == "select":
        _, sel, value, sec = step
        b.js(f"""(() => {{ const el = window.__find({json.dumps(sel)}); window.__move({json.dumps(sel)}, '');
            const s = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
            s.call(el, {json.dumps(value)}); el.dispatchEvent(new Event('change', {{bubbles: true}})); return true; }})()""")
        rec.record(0 if fast else sec)
    elif kind == "pick":
        # 그 값을 가진 option이 있는 select를 찾아 고름 (React가 알아채도록 change 이벤트까지)
        _, value, sec = step
        probe = f"[...document.querySelectorAll('select')].some(s => s.offsetParent !== null && [...s.options].some(o => o.value === {json.dumps(value)}))"
        for _ in range(30):          # 앞 단계에서 목록이 다시 불러와지는 동안 기다림
            if b.js(probe):
                break
            time.sleep(0.2)
        ok = b.js(f"""(() => {{ const v = {json.dumps(value)};
            const el = [...document.querySelectorAll('select')].find(s => s.offsetParent !== null && [...s.options].some(o => o.value === v));
            if (!el) return false;
            const b = el.getBoundingClientRect();
            window.__cur.style.transform = `translate(${{b.left + 40}}px, ${{b.top + b.height / 2}}px)`;
            const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
            setTimeout(() => {{ set.call(el, v); el.dispatchEvent(new Event('change', {{bubbles: true}})); }}, 450);
            return true; }})()""")
        if not ok:
            raise RuntimeError(f"고를 값 없음: {value}")
        rec.record(0 if fast else max(sec, 0.6))
        if fast:
            time.sleep(0.6)
            rec.record(0)
    elif kind == "scrollto":
        _, sel, text, sec = step
        target = None
        for _ in range(40):          # 목록이 다 불러와질 때까지 기다림
            b.js(SCROLLER_JS)
            target = b.js(f"""(() => {{ const el = window.__find({json.dumps(sel)}, {json.dumps(text)}); if (!el) return null;
                return window.__sc.scrollTop + el.getBoundingClientRect().top - 80; }})()""")
            if target is not None:
                break
            time.sleep(0.2)
        if target is None:
            raise RuntimeError(f"스크롤 대상 없음: {sel} '{text}'")
        start, t0 = b.js("window.__sc.scrollTop") or 0, time.time()
        if fast:
            b.js(f"window.__sc.scrollTop = {target}")
            time.sleep(0.4)
            rec.record(0)
        else:
            def step_fn():
                k = min(1.0, (time.time() - t0) / sec)
                k = k * k * (3 - 2 * k)
                b.js(f"window.__sc.scrollTop = {start + (target - start) * k}")
            rec.record(sec, every=step_fn)
    elif kind == "slider":
        _, label, v0, v1, sec = step
        find = f"""[...document.querySelectorAll('label')].filter(l => l.offsetParent !== null && l.textContent.includes({json.dumps(label)}))
                   .map(l => l.querySelector('input[type=range]')).find(Boolean)"""
        if not b.js(f"(() => {{ const el = {find}; if (!el) return false; const r = el.getBoundingClientRect();"
                    f" window.__cur.style.transform = `translate(${{r.left + 20}}px, ${{r.top + r.height / 2}}px)`; return true; }})()"):
            raise RuntimeError(f"슬라이더 없음: {label}")
        t0 = time.time()

        def slide():
            k = min(1.0, (time.time() - t0) / sec)
            b.js(f"""(() => {{ const el = {find}; const r = el.getBoundingClientRect();
                const lo = Number(el.min || 0), hi = Number(el.max || 1), val = {v0} + ({v1} - {v0}) * {k};
                const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                set.call(el, String(val)); el.dispatchEvent(new Event('input', {{bubbles: true}})); el.dispatchEvent(new Event('change', {{bubbles: true}}));
                window.__cur.style.transition = 'none';
                window.__cur.style.transform = `translate(${{r.left + r.width * (val - lo) / (hi - lo)}}px, ${{r.top + r.height / 2}}px)`; return true; }})()""")
        if fast:
            t0 -= sec
            slide()
            time.sleep(1.0)
            rec.record(0)
        else:
            rec.record(sec, every=slide)
            b.js("window.__cur.style.transition = 'transform .5s cubic-bezier(.3,.7,.3,1)'")
    elif kind == "upload":
        _, sel, path = step
        doc = b.cmd("DOM.getDocument", depth=-1, pierce=True)
        node = b.cmd("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector=sel)
        if not node.get("nodeId"):
            raise RuntimeError(f"파일 칸 없음: {sel}")
        b.cmd("DOM.setFileInputFiles", files=[path], nodeId=node["nodeId"])
        b.js(f"window.__move({json.dumps(sel)}, '')")
        rec.record(0 if fast else 0.8)
    elif kind == "scroll":
        _, px, sec = step
        maxi = b.js(SCROLLER_JS) or 0
        target = min(px, maxi)
        if fast:
            b.js(f"window.__sc.scrollTop = {target}")
            rec.record(0)
        else:
            start, t0 = b.js("window.__sc.scrollTop") or 0, time.time()

            def step_fn():
                k = min(1.0, (time.time() - t0) / sec)
                k = k * k * (3 - 2 * k)
                b.js(f"window.__sc.scrollTop = {start + (target - start) * k}")
            rec.record(sec, every=step_fn)
    elif kind == "play":
        b.js("(() => { const v = document.querySelector('video'); if (v) { v.muted = true; v.play(); } return !!v; })()")
        rec.record(0 if fast else step[1])
        b.js("(() => { const v = document.querySelector('video'); if (v) v.pause(); return true; })()")


def main():
    frames_dir = Path(tempfile.mkdtemp(prefix="sf_demo_"))
    profile = tempfile.mkdtemp(prefix="sf_chrome_")
    if True:
        proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--remote-allow-origins=http://127.0.0.1:{PORT}",
                                 f"--user-data-dir={profile}", "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required", "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(60):
                try:
                    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                    break
                except OSError:
                    time.sleep(0.2)
            ws = websocket.create_connection(next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page"),
                                             timeout=120, suppress_origin=True)
            b = Browser(ws)
            b.cmd("Page.enable")
            b.cmd("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=DSF, mobile=False)
            b.cmd("Page.navigate", url=APP)
            time.sleep(2.5)
            rec = Recorder(b, frames_dir)
            for i, (cap, steps) in enumerate(SCENES):
                rec.cap = cap
                for st in steps:
                    try:
                        run_step(rec, b, st)
                    except RuntimeError as exc:
                        if MODE != "test":
                            raise
                        print("  경고:", exc)
                print(f"장면 {i + 1}/{len(SCENES)} 끝 · 프레임 {len(rec.frames)}")
            ws.close()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            shutil.rmtree(profile, ignore_errors=True)

    rec.finish()
    OUT_DIR.mkdir(exist_ok=True)
    if MODE == "test":
        for f in OUT_DIR.glob("*.jpg"):
            f.unlink()
        for i, (p, _, _c) in enumerate(rec.frames):
            shutil.copy(p, OUT_DIR / f"{i:02d}.jpg")
        print("정지 그림", len(rec.frames), "→", OUT_DIR)
    else:
        lst = frames_dir / "list.txt"
        with open(lst, "w", encoding="utf-8") as fh:
            fh.write("ffconcat version 1.0\n")
            for p, dur, _c in rec.frames:
                fh.write(f"file '{p.as_posix()}'\nduration {dur:.4f}\n")
            fh.write(f"file '{rec.frames[-1][0].as_posix()}'\n")
        out = OUT_DIR / "ShotFlow_Demo_v02.mp4"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest",
                        "-vf", "fps=24,scale=out_range=tv,format=yuv420p", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18", "-preset", "slow",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)], check=True)
        total = sum(d for _, d, _c in rec.frames)
        print(f"영상 {out} · 약 {total:.1f}초 · 프레임 {len(rec.frames)}")
    shutil.rmtree(frames_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
