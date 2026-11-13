"""Command line entry: python -m shotflow <command>"""

from __future__ import annotations

import argparse
import json
import sys

from .config import load_settings
from .db import init_db, session_scope
from .importers.production_v04 import import_production_v04
from .services import jobs


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _build_proxies(settings) -> list[dict]:
    from .models import Version
    from .services import proxy

    done = []
    with session_scope(settings) as session:
        for version in session.query(Version).order_by(Version.id):
            proxy.ensure_proxy(version, settings)
            proxy.ensure_thumbnail(version, settings)
            done.append({"version_id": version.id, "code": f"{version.shot.code}/{version.code}"})
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shotflow", description="ShotFlow 로컬 제작 관리 툴")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="DB 생성")
    sub.add_parser("import-v04", help="Production v0.4 기록 가져오기 (원본은 읽기만)")
    qc = sub.add_parser("qc", help="자동 검수 실행")
    qc.add_argument("--version", type=int, help="버전 ID (생략 시 전체)")
    sub.add_parser("sheets", help="모든 편집본의 컷 경계 비교 시트 생성")
    sub.add_parser("proxies", help="브라우저 재생용 720p 프록시·썸네일 미리 생성")
    edl = sub.add_parser("apply-edl", help="편집 파일(edits/*.json) 적용 → 조립 → 검수 → 경계 시트")
    edl.add_argument("path", help="JSON 파일 또는 폴더 (예: ../edits/v0.5)")
    add_seq = sub.add_parser("add-sequence", help="새 영상과 샷 등록 (JSON 파일: code, name, target_duration_s, shots)")
    add_seq.add_argument("path", help="영상 정의 JSON 파일 (예: ../scenes/collapse_day/sequence.json)")
    serve = sub.add_parser("serve", help="로컬 서버 실행 (127.0.0.1)")
    serve.add_argument("--port", type=int)
    args = parser.parse_args(argv)

    settings = load_settings()
    init_db(settings)
    if args.command == "init":
        _print({"db": str(settings.db_path)})
    elif args.command == "import-v04":
        with session_scope(settings) as session:
            report = import_production_v04(session, settings)
        _print(report.as_dict())
    elif args.command == "qc":
        _print(jobs.qc_version(args.version, settings) if args.version else jobs.qc_all(settings))
    elif args.command == "sheets":
        _print(jobs.boundary_sheets_all(settings))
    elif args.command == "proxies":
        _print(_build_proxies(settings))
    elif args.command == "apply-edl":
        from pathlib import Path

        from .services.edl_files import apply_edl, apply_folder

        target = Path(args.path)
        _print(apply_folder(target, settings) if target.is_dir() else apply_edl(target, settings))
    elif args.command == "add-sequence":
        from pathlib import Path

        from .api import serializers as ser
        from .models import Project
        from .services import structure

        spec = json.loads(Path(args.path).read_text(encoding="utf-8"))
        with session_scope(settings) as session:
            project = session.query(Project).order_by(Project.id).first()
            if project is None:
                raise SystemExit("프로젝트가 없습니다. 먼저 import-v04를 실행하세요.")
            try:
                sequence = structure.create_sequence(
                    session, project, code=spec["code"], name=spec["name"],
                    target_duration_s=spec.get("target_duration_s"), shots=spec["shots"], actor="cli")
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            _print({"project": project.code, "sequence": ser.sequence(sequence),
                    "shots": [{"id": s.id, "code": s.code, "description": s.description} for s in sequence.shots]})
    elif args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(settings), host=settings.host, port=args.port or settings.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
