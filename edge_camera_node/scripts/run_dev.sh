#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
CONFIG_FILE="${ROOT_DIR}/config/settings.json"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "[ERROR] Missing virtualenv python at ${VENV_DIR}/bin/python"
  echo "Run scripts/install_dependencies.sh first."
  exit 1
fi
if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "[ERROR] Missing config file: ${CONFIG_FILE}"
  exit 1
fi

EXISTING_PIDS="$(
  pgrep -f "traffic_camera_node.main" \
    | grep -vw "$$" \
    || true
)"
if [[ -n "${EXISTING_PIDS}" ]]; then
  echo "[ERROR] Another traffic_camera_node instance is already running: ${EXISTING_PIDS}"
  echo "Stop it first (example: pkill -f traffic_camera_node.main), then retry."
  exit 1
fi

if [[ "${SKIP_CAMERA_PREFLIGHT:-0}" != "1" ]]; then
  read -r STREAM_SOURCE USB_DEVICE < <(
    "${VENV_DIR}/bin/python" - <<'PY' "${CONFIG_FILE}"
import json
import sys
from pathlib import Path

raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
stream = raw.get("stream", {}) if isinstance(raw.get("stream"), dict) else {}
source = str(stream.get("source", "auto")).strip().lower() or "auto"
usb_device = str(stream.get("usb_device", "auto")).strip() or "auto"
print(source, usb_device)
PY
  )

  USB_ANY_AVAILABLE=0
  if ls /dev/video* >/dev/null 2>&1; then
    USB_ANY_AVAILABLE=1
  fi

  if [[ "${STREAM_SOURCE}" == "usb_v4l2" ]]; then
    if [[ "${USB_DEVICE}" == "auto" ]]; then
      if [[ "${USB_ANY_AVAILABLE}" != "1" ]]; then
        echo "[ERROR] stream.source=usb_v4l2 nhưng không thấy thiết bị /dev/video*."
        echo "Check USB webcam connection and permissions."
        exit 1
      fi
    elif [[ ! -e "${USB_DEVICE}" ]]; then
      if [[ "${USB_ANY_AVAILABLE}" == "1" ]]; then
        echo "[WARN] Configured USB device not found: ${USB_DEVICE}"
        echo "[WARN] Runtime will auto-fallback to available /dev/video* device."
      else
        echo "[ERROR] USB video device not found: ${USB_DEVICE}"
        echo "Check USB webcam connection and device path in config.stream.usb_device."
        exit 1
      fi
    fi
  else
    if command -v rpicam-vid >/dev/null 2>&1; then
      CAMERA_LIST_OUTPUT="$(rpicam-vid --list-cameras 2>&1 || true)"
      if echo "${CAMERA_LIST_OUTPUT}" | grep -qi "no cameras available"; then
        if [[ "${STREAM_SOURCE}" == "auto" && "${USB_ANY_AVAILABLE}" == "1" ]]; then
          echo "[WARN] No CSI camera detected, but USB webcam device exists. Continuing with USB source."
        else
          echo "[ERROR] No camera detected by rpicam-vid --list-cameras."
          echo "Check CSI cable orientation/seat, camera power, and that no other process is holding camera device."
          echo "${CAMERA_LIST_OUTPUT}"
          echo "Set stream.source=usb_v4l2 for USB webcams, or set SKIP_CAMERA_PREFLIGHT=1 to bypass this check."
          exit 1
        fi
      fi
    elif [[ "${STREAM_SOURCE}" == "rpi_csi" ]]; then
      echo "[ERROR] stream.source=rpi_csi but rpicam-vid is missing."
      echo "Install rpicam-apps or switch stream.source to usb_v4l2."
      exit 1
    elif [[ "${STREAM_SOURCE}" == "auto" && "${USB_ANY_AVAILABLE}" != "1" ]]; then
      echo "[ERROR] No CSI camera command found and no USB /dev/video* device is available."
      exit 1
    fi
  fi
fi

cd "$ROOT_DIR"
export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec "${VENV_DIR}/bin/python" -m traffic_camera_node.main --config "${CONFIG_FILE}"
