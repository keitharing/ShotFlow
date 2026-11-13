"""ShotFlow first-time setup, run by 설치.cmd inside the project's own .venv.

Steps: Python packages -> frontend packages and build -> database -> (optional) import the v0.4 records.
Nothing is installed outside the ShotFlow folder.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PYTHON = Path(sys.executable)


def step(number: int, title: str) -> None:
    print(f"\n[{number}/4] {title}", flush=True)


def run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(f"실패: {' '.join(cmd)} (종료 코드 {result.returncode})")


def main() -> int:
    print("[ShotFlow 설치] 이 폴더 안에만 설치합니다. PC 전체 Python/npm 환경은 바꾸지 않습니다.")

    step(1, "Python 패키지 (.venv)")
    run([str(PYTHON), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
         str(BACKEND / "requirements.lock.txt")], ROOT)

    step(2, "화면 패키지와 빌드 (frontend)")
    npm = shutil.which("npm")
    if npm is None:
        print("Node.js 20 이상이 필요합니다: https://nodejs.org")
        return 1
    run([npm, "ci", "--no-fund", "--no-audit"], FRONTEND)
    run([npm, "run", "build"], FRONTEND)

    step(3, "데이터베이스 (data\\shotflow.db)")
    run([str(PYTHON), "-m", "shotflow", "init"], BACKEND)

    step(4, "기존 v0.4 제작 기록 가져오기 (있을 때만)")
    sys.path.insert(0, str(BACKEND))
    from shotflow.config import load_settings

    records = load_settings().production_v04 / "05_Records" / "Selected_Timeline.json"
    if records.is_file():
        for command in ("import-v04", "qc", "sheets"):
            print(f"  shotflow {command}", flush=True)
            subprocess.run([str(PYTHON), "-m", "shotflow", command], cwd=BACKEND, stdout=subprocess.DEVNULL, check=True)
    else:
        print("  기록 폴더가 없어 건너뜁니다. 빈 프로젝트로 시작합니다.")

    print("\n설치 완료. 시작.cmd 를 실행하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
