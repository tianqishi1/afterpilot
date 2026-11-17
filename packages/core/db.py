"""SQLAlchemy engine/session. sqlite for dev/tests, postgres for prod (same models)."""
import contextlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from . import config
from .models import Base

_connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextlib.contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def dialect() -> str:
    return engine.dialect.name