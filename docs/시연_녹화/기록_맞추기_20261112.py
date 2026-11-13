"""영상1 BEFORE THE DUST · 영상2 FIRST DROP의 승인 기록을 실제 완성본과 맞춥니다 (2026-11-12).

근거: 02_포트폴리오\\02_영상_제작과정\\새영상3편_작업기록.html
- 영상1 본편 v01 = 편집본 #10 rough_v03(결과 버전 70) + ShotFlow 밖 색 · 소리 마무리. 컷 C1 67, C2 68, C3 61, C4 69
- 영상2 본편 v04 = 편집본 #16 rough_v06(결과 버전 130) + ShotFlow 밖 색 · 소리 마무리.
  컷 A1 112, A2 111(6~50번) + 이어 만든 영상 119, A3 128

서비스 함수(add_note)를 그대로 써서 승인합니다. 이미 같은 버전이 승인돼 있으면 건너뜁니다.
실행: backend 폴더에서  ..\\.venv\\Scripts\\python.exe ..\\docs\\시연_녹화\\기록_맞추기_20261112.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from shotflow.db import session_scope  # noqa: E402
from shotflow.models import Version  # noqa: E402
from shotflow.services.review import add_note  # noqa: E402

AUTHOR = "안우혁"
B1 = r"02_포트폴리오\02_영상_제작과정\영상1_무너지는날\본편\BeforeTheDust_v01_16x9.mp4"
B2 = r"02_포트폴리오\02_영상_제작과정\영상2_FirstDrop\본편\FirstDrop_v04_16x9.mp4"

APPROVE = [
    (67, "본편 v01에 쓴 컷. 생성 영상 sd_001(58)에 Blender 파편 · 먼지 층을 합성한 것 (편집본 rough_v03)"),
    (68, "본편 v01에 쓴 컷. 생성 영상 sd_001(60)에 Blender 파편 · 먼지 층을 합성한 것 (편집본 rough_v03)"),
    (61, "본편 v01에 쓴 컷. 효과 합성 없이 생성 영상 그대로 씀 (편집본 rough_v03)"),
    (69, "본편 v01에 쓴 컷. 생성 영상 sd_001(62)에 Blender 먼지 층을 합성한 것 (편집본 rough_v03)"),
    (70, "본편 v01의 바탕 편집본(rough_v03, 편집본 #10). 색 맞추기와 소리 마무리는 ShotFlow 밖 스크립트로 함 → " + B1 + ". 2026-11-11 완료 결정"),
    (112, "본편 v04에 쓴 컷 (편집본 rough_v06)"),
    (119, "본편 v04에 쓴 컷. sd_001(111)의 6~50번 뒤에 이어 만든 영상(이 버전, 효과 없음)을 붙여 한 컷으로 씀 (편집본 rough_v06)"),
    (128, "본편 v04에 쓴 끝 장면. 생성 영상 sd_003(120)에 Blender로 렌더한 병에 스치는 빛을 ShotFlow 합성 작업으로 얹은 것 (편집본 rough_v06)"),
    (130, "본편 v04의 바탕 편집본(rough_v06, 편집본 #16). 한 프레임 색 깜빡임 수정, 색 맞추기와 소리 마무리는 ShotFlow 밖 스크립트로 함 → " + B2 + ". 2026-11-12 완료 결정"),
]
COMMENT = [
    (111, "본편 v04에는 이 영상의 6~50번만 쓰고, 뒤는 이어 만든 영상 fx_001(119)로 이음"),
]


def main():
    with session_scope() as s:
        for vid, body in APPROVE:
            v = s.get(Version, vid)
            if v.shot.approved_version_id == vid:
                print("이미 승인됨", vid, v.shot.code, v.code)
                continue
            before = v.shot.approved_version_id
            add_note(s, v, author=AUTHOR, body=body, decision="approve")
            print("승인", vid, v.shot.sequence.code, v.shot.code, v.code, "(전 승인:", before, ")")
        for vid, body in COMMENT:
            v = s.get(Version, vid)
            if any(n.body == body for n in v.notes):
                print("이미 있는 노트", vid)
                continue
            add_note(s, v, author=AUTHOR, body=body, decision="comment")
            print("노트", vid, v.shot.code, v.code)


if __name__ == "__main__":
    main()
