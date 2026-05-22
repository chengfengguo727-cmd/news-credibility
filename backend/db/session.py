from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings

_engine = None
_SessionLocal = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
    return _engine


def session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=engine(), expire_on_commit=False, class_=Session)
    return _SessionLocal


def get_session() -> Iterator[Session]:
    Session_ = session_factory()
    with Session_() as s:
        yield s
