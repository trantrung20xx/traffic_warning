from __future__ import annotations

from pathlib import Path
from typing import Union

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
        if "license_plate" not in columns:
            connection.execute(text("ALTER TABLE violations ADD COLUMN license_plate VARCHAR"))
        if "license_plate_status" not in columns:
            connection.execute(text("ALTER TABLE violations ADD COLUMN license_plate_status VARCHAR"))
        if "license_plate_confidence" not in columns:
            connection.execute(text("ALTER TABLE violations ADD COLUMN license_plate_confidence FLOAT"))
        if "license_plate_image_path" not in columns:
            connection.execute(text("ALTER TABLE violations ADD COLUMN license_plate_image_path VARCHAR"))
        if "track_session_id" not in columns:
            connection.execute(text("ALTER TABLE violations ADD COLUMN track_session_id VARCHAR"))
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_violations_license_plate ON violations (license_plate)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_violations_license_plate_status ON violations (license_plate_status)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_violations_track_session_id ON violations (track_session_id)")
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_violations_scope_lookup "
                "ON violations (camera_id, track_session_id, vehicle_id, timestamp_utc)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_violations_scope_lookup_no_ts "
                "ON violations (camera_id, track_session_id, vehicle_id)"
            )
        )


def create_engine_and_session(db_path: Union[Path, str]):
    database_url: str
    if isinstance(db_path, str) and db_path.strip().lower() in {":memory:", "memory"}:
        database_url = "sqlite:///:memory:"
    else:
        normalized_path = Path(db_path)
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{normalized_path.as_posix()}"

    engine = create_engine(database_url, echo=False, future=True)
    Base.metadata.create_all(engine)
    _ensure_violation_schema(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine, SessionLocal
