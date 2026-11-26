"""Engine, sessions and the full-text search table.

Works with SQLite (local, FTS5) and Postgres (hosted, e.g. Vercel + Neon/Supabase, tsvector).
"""

import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import get_settings
from .models import Base, Setting

log = logging.getLogger(__name__)
_engine = None
_SessionLocal: sessionmaker | None = None


def _make_engine(url: str):
    if url.startswith("sqlite"):
        path = url.split(":///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    if url.startswith("postgresql"):
        # Serverless: no long-lived pool; the provider's pooler (Neon/Supabase) handles that.
        return create_engine(url, poolclass=NullPool, connect_args={"prepare_threshold": None})
    raise RuntimeError(f"Base de dados não suportada: {url.split(':', 1)[0]}")


def is_postgres(db_or_engine=None) -> bool:
    bind = getattr(db_or_engine, "bind", None) or db_or_engine or _engine
    return bind.dialect.name == "postgresql"


_PG_SEARCH_DDL = [
    "CREATE TABLE IF NOT EXISTS chunks (id BIGSERIAL PRIMARY KEY, doc_id INTEGER NOT NULL, title TEXT, url TEXT,"
    " course_id INTEGER, content TEXT NOT NULL, tsv TSVECTOR)",
    "CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv)",
    "CREATE INDEX IF NOT EXISTS chunks_doc_idx ON chunks (doc_id)",
]
_SQLITE_SEARCH_DDL = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
    " content, title, doc_id UNINDEXED, url UNINDEXED, course_id UNINDEXED,"
    " tokenize = 'unicode61 remove_diacritics 2')",
]


def init_db(url: str | None = None) -> None:
    """Create tables. Pass a URL to point the app at another database (tests)."""
    global _engine, _SessionLocal
    _engine = _make_engine(url or get_settings().database_url)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    try:
        Base.metadata.create_all(_engine)
        with _engine.begin() as conn:
            for ddl in (_PG_SEARCH_DDL if is_postgres(_engine) else _SQLITE_SEARCH_DDL):
                conn.execute(text(ddl))
    except (IntegrityError, ProgrammingError) as exc:
        # Two serverless instances starting at once can race on CREATE; the other one won.
        log.warning("init_db race (ignored): %s", exc)


def session_factory() -> sessionmaker:
    if _SessionLocal is None:
        init_db()
    return _SessionLocal


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


_secret_cache: str | None = None


def secret_key() -> str:
    """SECRET_KEY from the environment, or one generated once and kept in the database
    (so it survives restarts and is shared by every serverless instance)."""
    global _secret_cache
    configured = get_settings().secret_key
    if len(configured) >= 32:
        return configured
    if _secret_cache:
        return _secret_cache
    with db_session() as db:
        value = get_setting(db, "secret_key")
        if not value:
            try:
                db.add(Setting(key="secret_key", value=secrets.token_hex(32)))
                db.commit()
            except IntegrityError:
                db.rollback()  # another instance created it first
            value = get_setting(db, "secret_key")
    _secret_cache = value
    return value
