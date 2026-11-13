from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent  # 90_작업_원본
CAREER_ROOT = WORKSPACE.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    production_v04: Path
    portfolio_edits: Path
    host: str = "127.0.0.1"
    port: int = 8710

    @property
    def db_path(self) -> Path:
        return self.data_dir / "shotflow.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("SHOTFLOW_DATA", ROOT / "data")).resolve()
    production = Path(
        os.environ.get("SHOTFLOW_V04", WORKSPACE / "영상_제작_전체" / "Production_v0.4")
    ).resolve()
    portfolio = Path(
        os.environ.get("SHOTFLOW_V04_EDITS", CAREER_ROOT / "02_포트폴리오" / "01_영상_검토본")
    ).resolve()
    return Settings(data_dir=data_dir, production_v04=production, portfolio_edits=portfolio)


class ReadOnlySourceError(PermissionError):
    """Raised when code tries to write outside ShotFlow's data directory."""


def writable_path(path: Path, settings: Settings | None = None) -> Path:
    # Every file ShotFlow creates must live under data/, so source media can never be overwritten.
    settings = settings or load_settings()
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(settings.data_dir):
        raise ReadOnlySourceError(f"Refusing to write outside data dir: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved
