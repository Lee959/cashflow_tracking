from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


settings.data_dir.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{settings.db_path}")
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(engine)
    add_missing_columns(engine)


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
