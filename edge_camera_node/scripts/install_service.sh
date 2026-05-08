#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="traffic-camera-node.service"
SERVICE_TEMPLATE="${ROOT_DIR}/systemd/${SERVICE_NAME}"
SERVICE_DEST="/etc/systemd/system/${SERVICE_NAME}"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
CONFIG_FILE="${ROOT_DIR}/config/settings.json"
RUN_USER="${SUDO_USER:-$USER}"

if [[ ! -f "$SERVICE_TEMPLATE" ]]; then
  echo "[ERROR] Service template missing: $SERVICE_TEMPLATE"
  exit 1
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] Python venv binary missing: $PYTHON_BIN"
  echo "Run scripts/install_dependencies.sh first."
  exit 1
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "[ERROR] Missing config file: $CONFIG_FILE"
  echo "Create config/settings.json before installing service."
  exit 1
fi

TMP_FILE="$(mktemp)"
sed "s|__WORKDIR__|${ROOT_DIR}|g; s|__PYTHON__|${PYTHON_BIN}|g; s|__CONFIG__|${CONFIG_FILE}|g" \
  "$SERVICE_TEMPLATE" | sed "s|__RUN_USER__|${RUN_USER}|g" > "$TMP_FILE"

sudo cp "$TMP_FILE" "$SERVICE_DEST"
rm -f "$TMP_FILE"

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager
