import logging
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import LEGACY_DATA_DIR, settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
engine = create_engine(f"sqlite:///{settings.db_path}")
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401  (registers tables)

    move_legacy_data(LEGACY_DATA_DIR, settings.data_dir)
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    restrict_permissions(settings.data_dir)


def move_legacy_data(old: Path, new: Path) -> bool:
    """One-time move of the database and PDFs from the project's data/ folder (which may sync to
    iCloud) to the private data folder. Does nothing if there's nothing to move or both exist."""
    if old.resolve() == new.resolve() or not (old / "cashflow.db").exists():
        return False
    if (new / "cashflow.db").exists():
        log.warning("Both %s and %s have a database; using %s and leaving the old one alone", old, new, new)
        return False
    new.mkdir(parents=True, exist_ok=True, mode=0o700)
    for item in old.iterdir():
        shutil.move(str(item), str(new / item.name))
    old.rmdir()
    log.info("Moved app data from %s to %s", old, new)
    return True


def restrict_permissions(data_dir: Path) -> None:
    """Only your user account can read the database, PDFs and attachments."""
    os.chmod(data_dir, 0o700)
    for path in data_dir.rglob("*"):
        os.chmod(path, 0o700 if path.is_dir() else 0o600)


def add_missing_columns(bind: Engine) -> None:
    """create_all() only creates missing tables; this adds new (nullable) columns to existing ones."""
    inspector = inspect(bind)
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(f"Can't add required column {table.name}.{column.name} automatically")
                column_type = column.type.compile(bind.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'))


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
