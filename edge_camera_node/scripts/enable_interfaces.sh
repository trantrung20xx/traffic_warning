#!/usr/bin/env bash
set -euo pipefail

echo "Enabling required interfaces (SPI/Camera) on Raspberry Pi OS..."

if ! command -v raspi-config >/dev/null 2>&1; then
  echo "[ERROR] raspi-config not found. Run this script on Raspberry Pi OS."
  exit 1
fi

sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_camera 0 || true

echo "Interface enable requests completed."
echo "Recommended next step: reboot the Pi now."
