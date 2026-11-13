from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import Settings, load_settings


class Base(DeclarativeBase):
    pass


_engines: dict[str, Engine] = {}


def get_engine(settings: Settings | None = None) -> Engine:
    settings = settings or load_settings()
    key = str(settings.db_path)
    if key not in _engines:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{settings.db_path}", future=True)

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_conn, _record):  # SQLite ignores foreign keys unless asked
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        _engines[key] = engine
    return _engines[key]


def init_db(settings: Settings | None = None) -> None:
    from . import models  # noqa: F401  (registers tables)

    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


# Columns added after a database was first created. create_all() never alters an existing table,
# so older databases get these nullable columns here (SQLite: ADD COLUMN is safe and instant).
_ADDED_COLUMNS = {"sequence": {"width": "INTEGER", "height": "INTEGER"},
                  "generation_job": {"prompt_snapshot_id": "INTEGER"}}


def _add_missing_columns(engine) -> None:
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, kind in columns.items():
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(settings), expire_on_commit=False)


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    session = session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
