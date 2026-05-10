#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
CONFIG_FILE="${ROOT_DIR}/config/settings.json"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "[ERROR] Missing config file: ${CONFIG_FILE}"
  echo "Create it before install."
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
  pkg-config \
  libopenblas-dev \
  liblapack-dev \
  libjpeg-dev \
  zlib1g-dev \
  libopenjp2-7 \
  libtiff6 \
  python3-spidev \
  gpiod \
  libgpiod-dev \
  avahi-daemon \
  avahi-utils \
  ffmpeg \
  curl \
  tar \
  jq

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

  echo "[INFO] Finding latest MediaMTX release asset for ${MTX_ARCH}..."

  MTX_URL="$(
    curl -fsSL https://api.github.com/repos/bluenviron/mediamtx/releases/latest \
    | jq -r --arg arch "$MTX_ARCH" '
        .assets[]
        | select(.name | endswith($arch + ".tar.gz"))
        | .browser_download_url
      ' \
    | head -n 1
  )"

  if [[ -z "${MTX_URL}" || "${MTX_URL}" == "null" ]]; then
    echo "[ERROR] Could not find MediaMTX release asset for ${MTX_ARCH}"
    exit 1
  fi

  echo "[INFO] Downloading: ${MTX_URL}"
  curl -fL "${MTX_URL}" -o mediamtx.tar.gz

  tar -xzf mediamtx.tar.gz

  if [[ ! -f mediamtx ]]; then
    echo "[ERROR] MediaMTX binary not found after extraction."
    exit 1
  fi

  sudo install -m 0755 mediamtx /usr/local/bin/mediamtx

  popd >/dev/null
  rm -rf "$TMP_DIR"
fi

echo "[3/5] Creating Python virtual environment..."
python3 -m venv "$VENV_DIR"

source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip wheel setuptools

echo "[4/5] Installing Python dependencies..."
pip install -r "${ROOT_DIR}/requirements.txt"

echo "[5/5] Enabling services..."
sudo systemctl enable avahi-daemon || true
sudo systemctl start avahi-daemon || true

echo "Dependency installation complete."
