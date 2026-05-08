#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
CONFIG_FILE="${ROOT_DIR}/config/settings.json"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "[ERROR] Missing config file: ${CONFIG_FILE}"
  echo "Create it before install (copy from repo default or your deployment template)."
  exit 1
fi

echo "[1/5] Installing apt dependencies..."
sudo apt update
sudo apt install -y \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  build-essential \
  libatlas-base-dev \
  libjpeg-dev \
  zlib1g-dev \
  libopenjp2-7 \
  libtiff6 \
  python3-spidev \
  libgpiod2 \
  avahi-daemon \
  avahi-utils \
  ffmpeg \
  curl \
  tar

if ! command -v rpicam-vid >/dev/null 2>&1; then
  echo "[WARN] rpicam-vid not found. Installing camera apps..."
  sudo apt install -y rpicam-apps || true
fi

if ! command -v mediamtx >/dev/null 2>&1; then
  echo "[2/5] Installing MediaMTX binary..."
  ARCH="$(uname -m)"
  case "$ARCH" in
    aarch64) MTX_ARCH="linux_arm64" ;;
    armv7l|armv6l) MTX_ARCH="linux_armv7" ;;
    *)
      echo "[ERROR] Unsupported architecture for prebuilt MediaMTX: $ARCH"
      exit 1
      ;;
  esac
  TMP_DIR="$(mktemp -d)"
  pushd "$TMP_DIR" >/dev/null
  MTX_URL="https://github.com/bluenviron/mediamtx/releases/latest/download/mediamtx_${MTX_ARCH}.tar.gz"
  curl -fsSL "$MTX_URL" -o mediamtx.tar.gz
  tar -xzf mediamtx.tar.gz
  sudo install -m 0755 mediamtx /usr/local/bin/mediamtx
  popd >/dev/null
  rm -rf "$TMP_DIR"
fi

echo "[3/5] Creating Python virtual environment..."
python3 -m venv "$VENV_DIR"
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip wheel

echo "[4/5] Installing Python dependencies..."
pip install -r "${ROOT_DIR}/requirements.txt"

sudo systemctl enable avahi-daemon || true
sudo systemctl start avahi-daemon || true

echo "Dependency installation complete."
