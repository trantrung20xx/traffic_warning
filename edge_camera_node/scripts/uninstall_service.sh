#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="traffic-camera-node.service"
SERVICE_DEST="/etc/systemd/system/${SERVICE_NAME}"

sudo systemctl disable --now "$SERVICE_NAME" || true
sudo rm -f "$SERVICE_DEST"
sudo systemctl daemon-reload

echo "Service ${SERVICE_NAME} removed."
