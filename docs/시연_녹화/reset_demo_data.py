"""녹화 전에 data_demo를 원본 기록 상태로 되돌림 (원본 data는 읽기만 함). 8720 서버와 그 ComfyUI를 끈 뒤 실행."""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SF = Path(__file__).resolve().parents[2]
DEMO = SF / "data_demo"

for name in ["deliveries", "imports", "generated", "comfy", "animatics", "layouts"]:
    shutil.rmtree(DEMO / name, ignore_errors=True)
for f in DEMO.glob("shotflow.db*"):
    f.unlink()
(DEMO / "comfy").mkdir(parents=True)
for f in ["launch_options.json", "object_info_snapshot.json"]:
    shutil.copy(SF / "data" / "comfy" / f, DEMO / "comfy" / f)
src, dst = sqlite3.connect(SF / "data" / "shotflow.db"), sqlite3.connect(DEMO / "shotflow.db")
src.backup(dst)
last_job = src.execute("select max(id) from generation_job").fetchone()[0] or 0
dst.close()
src.close()

# 지난 녹화의 Blender 작업 폴더(원본에 없는 번호)와, 지워진 파일을 가리키는 장면 목록 항목을 정리
for job_dir in (DEMO / "blender").glob("job*"):
    if job_dir.name[3:].isdigit() and int(job_dir.name[3:]) > last_job:
        shutil.rmtree(job_dir, ignore_errors=True)
registry = DEMO / "blender" / "scenes.json"
if registry.is_file():
    import json
    entries = json.loads(registry.read_text(encoding="utf-8"))
    kept = {k: v for k, v in entries.items() if Path(k).is_file()}
    registry.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
print("data_demo 초기화 완료")
