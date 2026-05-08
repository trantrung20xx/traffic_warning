#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
CONFIG_FILE="${ROOT_DIR}/config/settings.json"

create_default_settings() {
  cat > "${CONFIG_FILE}" <<'EOF'
{
  "camera": {
    "width": 2560,
    "height": 1440,
    "fps": 25
  },
  "image_tuning": {
    "profile": "normal"
  },
  "gpio": {
    "enabled": true,
    "buttons": {
      "mode": 5,
      "restart_stream": 6,
      "safe_shutdown": 13,
      "reset_watchdog": 19
    },
    "leds": {
      "online": 17,
      "warning": 27,
      "error": 22,
      "streaming": 23
    }
  },
  "display": {
    "enabled": true,
    "update_hz": 1,
    "spi_bus": 0,
    "spi_device": 0,
    "dc_pin": 25,
    "reset_pin": 24,
    "backlight_pin": null
  },
  "stream": {
    "bitrate": 6000000
  },
  "watchdog": {
    "fps_warning_threshold": 15
  }
}
EOF
}

echo "=== Environment Check: Traffic Camera Node ==="
echo "Host: $(hostname)"
echo "Kernel: $(uname -a)"
echo "Date: $(date -Iseconds)"

echo
echo "[Python]"
if [[ -x "${VENV_DIR}/bin/python" ]]; then
  "${VENV_DIR}/bin/python" --version
else
  echo "Venv python not found: ${VENV_DIR}/bin/python"
fi

echo
echo "[Commands]"
for cmd in mediamtx avahi-daemon avahi-resolve avahi-browse rpicam-vid ffmpeg; do
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "OK  - $cmd ($(command -v "$cmd"))"
  else
    echo "MISS- $cmd"
  fi
done

echo
echo "[Services]"
for svc in avahi-daemon; do
  if systemctl is-active --quiet "$svc"; then
    echo "ACTIVE - $svc"
  else
    echo "INACTIVE - $svc"
  fi
done

echo
echo "[Network]"
ip -4 -o addr show | awk '{print $2, $4}'

echo
echo "[mDNS probe suggestion]"
echo "Use commands:"
echo "  avahi-browse -a"
echo "  avahi-resolve -n <hostname>.local"
echo "  ping <hostname>.local"

echo
echo "[Config]"
if [[ -f "${CONFIG_FILE}" ]]; then
  echo "OK      - config/settings.json"
else
  mkdir -p "${ROOT_DIR}/config"
  create_default_settings
  echo "CREATED - config/settings.json"
fi

echo "=== Environment Check Complete ==="
