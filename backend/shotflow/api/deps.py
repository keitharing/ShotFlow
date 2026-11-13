from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import HTTPException
from sqlalchemy.orm import Session, sessionmaker


def session_dependency(make_session: sessionmaker[Session]) -> Callable[[], Iterator[Session]]:
    def db() -> Iterator[Session]:
        session = make_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return db


def get_or_404(session: Session, model, item_id: int):
    item = session.get(model, item_id)
    if item is None:
        raise HTTPException(404, f"{model.__name__} {item_id} not found")
    return item
