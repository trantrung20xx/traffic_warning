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

cd "$ROOT_DIR"
exec "${VENV_DIR}/bin/python" -m traffic_camera_node.main --config "${CONFIG_FILE}"
