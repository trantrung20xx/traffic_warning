from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import cv2

from app.core.config import load_app_config


def _sanitize_fragment(value: str) -> str:
    cleaned = []
    for char in str(value or ""):
        if char.isalnum() or char in {"_", "-"}:
            cleaned.append(char)
        else:
            cleaned.append("_")
    compact = "".join(cleaned).strip("_")
    return compact or "na"


def build_evidence_filename(
    *,
    camera_id: str,
    timestamp_utc_ms: int,
    vehicle_id: int,
    lane_id: int,
    violation: str,
) -> str:
    return (
        f"{_sanitize_fragment(camera_id)}__{int(timestamp_utc_ms)}__"
        f"veh_{int(vehicle_id)}__lane_{int(lane_id)}__{_sanitize_fragment(violation)}.jpg"
    )


def build_evidence_relative_path(camera_id: str, filename: str) -> str:
    return (Path(_sanitize_fragment(camera_id)) / filename).as_posix()


def build_evidence_image_url(relative_path: str | None) -> str | None:
    if not relative_path:
        return None
    normalized = Path(relative_path).as_posix().lstrip("/")
    return f"/api/violations/evidence/{quote(normalized, safe='/')}"


def resolve_evidence_image_path(repo_root: Path, relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    cfg = load_app_config(repo_root)
    base_dir = cfg.evidence_images_dir.resolve()
    candidate = (base_dir / relative_path).resolve()
    if candidate == base_dir or base_dir not in candidate.parents:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def save_evidence_image(
    repo_root: Path,
    *,
    camera_id: str,
    timestamp_utc_ms: int,
    vehicle_id: int,
    lane_id: int,
    violation: str,
    image_bgr,
    jpeg_quality: int = 92,
) -> str:
    cfg = load_app_config(repo_root)
    filename = build_evidence_filename(
        camera_id=camera_id,
        timestamp_utc_ms=timestamp_utc_ms,
        vehicle_id=vehicle_id,
        lane_id=lane_id,
        violation=violation,
    )
    relative_path = build_evidence_relative_path(camera_id, filename)
    destination = cfg.evidence_images_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)

    ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
    if not ok:
        raise ValueError("Failed to encode violation evidence image")
    destination.write_bytes(buf.tobytes())
    return Path(relative_path).as_posix()


def delete_evidence_images_for_camera(repo_root: Path, camera_id: str) -> None:
    cfg = load_app_config(repo_root)
    camera_dir = cfg.evidence_images_dir / _sanitize_fragment(camera_id)
    if not camera_dir.exists():
        return
    for child in camera_dir.rglob("*"):
        if child.is_file():
            child.unlink()
    for child in sorted(camera_dir.rglob("*"), reverse=True):
        if child.is_dir():
            child.rmdir()
    camera_dir.rmdir()
