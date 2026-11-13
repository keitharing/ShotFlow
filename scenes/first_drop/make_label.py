"""영상 2 「FIRST DROP」의 병 라벨 그림을 만든다. 가상의 제품(가상의 상표 DROP)이고 실제 상품이 아니다.

병을 한 바퀴 감는 그림: 가운데(가로 50%)가 병의 앞쪽. 사용: python make_label.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "tex" / "label_drop.png"
W, H = 2048, 600          # 둘레 0.229m × 높이 0.066m 와 비슷한 비율
ORANGE, INK, PAPER = (238, 106, 14), (28, 28, 30), (250, 247, 240)


def font(size, weight="SemiBold"):
    f = ImageFont.truetype(r"C:\Windows\Fonts\bahnschrift.ttf", size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def tracked(d, cx, y, text, f, fill, spacing):
    widths = [d.textlength(ch, font=f) for ch in text]
    x = cx - (sum(widths) + spacing * (len(text) - 1)) / 2
    for ch, w in zip(text, widths):
        d.text((x, y), ch, font=f, fill=fill)
        x += w + spacing


im = Image.new("RGB", (W, H), PAPER)
d = ImageDraw.Draw(im)
d.rectangle((0, 0, W, 46), fill=ORANGE)
d.rectangle((0, H - 46, W, H), fill=ORANGE)
cx = W // 2
# 앞면: 방울 모양 + DROP
d.ellipse((cx - 232 - 38, 238, cx - 232 + 38, 314), fill=ORANGE)
d.polygon([(cx - 232, 152), (cx - 232 - 35, 262), (cx - 232 + 35, 262)], fill=ORANGE)
d.text((cx - 172, 150), "DROP", font=font(172, "Bold"), fill=INK)
tracked(d, cx, 372, "SPARKLING ORANGE", font(40, "SemiBold"), ORANGE, 12)
tracked(d, cx, 444, "REAL FRUIT  ·  330 ML", font(28, "Light"), INK, 7)
# 뒷면 (가로 0%와 100% 근처): 작은 글자
for bx in (W * 0.03, W * 0.97):
    tracked(d, bx, 210, "DROP", font(64, "Bold"), INK, 6)
    tracked(d, bx, 300, "SPARKLING ORANGE", font(24, "Light"), INK, 5)
OUT.parent.mkdir(exist_ok=True)
im.save(OUT)
print("저장", OUT)
