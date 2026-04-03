from __future__ import annotations

from pathlib import Path

from app.core.config import load_app_config

ALLOWED_BACKGROUND_IMAGE_SUFFIXES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def list_background_image_candidates(repo_root: Path, camera_id: str) -> list[Path]:
    cfg = load_app_config(repo_root)
    return [cfg.background_images_dir / f"{camera_id}{suffix}" for suffix in ALLOWED_BACKGROUND_IMAGE_SUFFIXES]


def get_background_image_path(repo_root: Path, camera_id: str) -> Path | None:
    for path in list_background_image_candidates(repo_root, camera_id):
        if path.exists():
            return path
    return None


def delete_background_image(repo_root: Path, camera_id: str) -> None:
    for path in list_background_image_candidates(repo_root, camera_id):
        if path.exists():
            path.unlink()


def save_background_image(repo_root: Path, camera_id: str, *, suffix: str, data: bytes) -> Path:
    cfg = load_app_config(repo_root)
    cfg.background_images_dir.mkdir(parents=True, exist_ok=True)
    delete_background_image(repo_root, camera_id)
    path = cfg.background_images_dir / f"{camera_id}{suffix}"
    path.write_bytes(data)
    return path
