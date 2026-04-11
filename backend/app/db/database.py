from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Base


def _ensure_violation_schema(engine) -> None:
    with engine.begin() as connection:
        inspector = inspect(connection)
        if "violations" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("violations")}
        if "evidence_image_path" not in columns:
            connection.execute(text("ALTER TABLE violations ADD COLUMN evidence_image_path VARCHAR"))


def create_engine_and_session(db_path: Path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite:///{db_path.as_posix()}", echo=False, future=True)
    Base.metadata.create_all(engine)
    _ensure_violation_schema(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine, SessionLocal

