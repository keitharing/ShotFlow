"""애니메틱 → 블로킹 기능만 짧게 찍는 시연 (D-015). 녹화 방식은 record_demo.py를 그대로 씀.

data_demo를 초기화하고 8720 서버를 켠 뒤:
    ..\\..\\.venv\\Scripts\\python.exe record_layout.py test    (장면마다 정지 그림 → test\\)
    ..\\..\\.venv\\Scripts\\python.exe record_layout.py video   (영상 → out\\ShotFlow_애니메틱_블로킹.mp4)
"""

from __future__ import annotations

import record_demo as rd

ANIMATIC = str(rd.CAREER / "02_포트폴리오/02_영상_제작과정/애니매틱/FirstDrop_Animatic_v03.mp4")
FIRST_DROP = "5"     # 시퀀스 번호
A3 = "26"            # 샷 번호

rd.OUT_NAME = "ShotFlow_애니메틱_블로킹.mp4"
rd.SCENES = [
    (None, [("card", "애니메틱 → Blender 블로킹",
             "스토리보드 그림 위에 상자와 수평선을 표시하면\n카메라 위치를 계산해 Blender 장면을 만듭니다", 2.4)]),
    ("3D 연동 › 애니메틱 → 블로킹 탭", [("nav", "#/scene3d?tab=layout", 1.2), ("pick", FIRST_DROP, 0.8)]),
    ("애니메틱 올리기 · 컷 경계와 그림 영역을 찾음",
     [("upload", "[data-demo=layout-flow] input[type=file]", ANIMATIC), ("speed", 3),
      ("waitfor", "main *", "그림 있는 컷 3개", 1, 60), ("speed", 1), ("hold", 0.8)]),
    ("컷 4개 중 그림 있는 3컷이 샷 A1 · A2 · A3에 자동 연결 (제목 화면은 빠짐)",
     [("scrollto", "main h2", "컷과 샷 연결", 0.6), ("hold", 2.4)]),
    ("A3 컷을 골라 그림 위에 표시",
     [("click", ".cut-card", "컷 4", 0.4), ("scrollto", "main h2", "그림 위에 표시", 0.6), ("hold", 0.6)]),
    ("병을 상자로 감쌈 (아래 끝 = 바닥에 닿는 곳) → 카메라 높이 · 거리가 바로 계산됨",
     [("draw", ".mark-canvas svg", [(0.428, 0.139), (0.50, 0.55), (0.572, 0.879)], 1.6)]),
    ("바닥의 과일 조각도 표시 · 종류를 '둥근 물체'로",
     [("draw", ".mark-canvas svg", [(0.272, 0.849), (0.389, 0.889)], 0.6),
      ("select", ".mark-object.selected select", "round", 1.2)]),
    ("렌즈 60mm → 카메라 높이 0.128m · 병까지 1.01m (손으로 만든 장면: 0.130m · 0.95m)",
     [("pick", "60", 2.6)]),
    ("수평선 = 카메라 눈높이 · 올리면 내려다보는 카메라로 바뀜",
     [("draw", ".mark-canvas svg", [(0.03, 0.5), (0.03, 0.36)], 1.4),
      ("draw", ".mark-canvas svg", [(0.03, 0.36), (0.03, 0.5)], 0.8)]),
    ("블로킹 만들기 · Blender가 새 .blend를 만들고 카메라에서 본 렌더를 그림에 겹침",
     [("scrollto", "main h2", "Blender 블로킹 만들기", 0.5), ("click", "button", "블로킹 만들기", 0.3), ("speed", 3),
      ("waitfor", "main *", "FIRST_DROP_A3_lay_001.blend", 1, 90), ("speed", 1),
      ("scrollto", "main p", "FIRST_DROP_A3_lay_001.blend", 0.6), ("hold", 0.6)]),
    ("겹쳐 보기 · 회색 대역 병이 그림 속 병 자리에 놓임",
     [("slider", "Blender 렌더 겹치기", 0.6, 1.0, 1.0), ("slider", "Blender 렌더 겹치기", 1.0, 0.0, 1.4),
      ("slider", "Blender 렌더 겹치기", 0.0, 0.6, 1.0), ("hold", 0.6)]),
    ("만든 장면이 3D 키프레임의 장면 목록에 바로 나타남",
     [("click", "button", "3D 키프레임으로", 0.8), ("click", ".scene-card", "FIRST_DROP_A3_lay_001", 1.0)]),
    ("그 장면으로 깊이 · 마스크 렌더 → 다음 단계 AI로 그리기로 이어짐",
     [("scrollto", "main h2", "깊이 이미지 만들기", 0.5), ("pick", A3, 0.4), ("click", "button", "깊이 이미지 만들기", 0.3),
      ("speed", 3), ("waitfor", "main figcaption", "마스크 (SF_MASK", 1, 120), ("speed", 1),
      ("scrollto", "main h2", "깊이 이미지 만들기", 0.5), ("hold", 2.6)]),
    (None, [("card", "애니메틱 → 블로킹",
             "FIRST DROP A3 · 그림에서 계산 0.128m · 1.01m / 손으로 만든 장면 0.130m · 0.95m\n블로킹 생성 약 4초 · 자동 테스트 105개", 2.6)]),
]

if __name__ == "__main__":
    rd.main()
