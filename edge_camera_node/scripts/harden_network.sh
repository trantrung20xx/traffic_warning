#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/harden_network.sh \
    --wifi-ssid "<ssid>" \
    --wifi-password "<password>" \
    [--wifi-iface wlan0] \
    [--disable-ethernet-autoconnect] \
    [--disable-wait-online]

What this script does:
  1) Disable Wi-Fi powersave in NetworkManager (improves stability for edge workloads)
  2) Ensure a persistent WPA-PSK profile exists for headless boot
  3) Force Wi-Fi profile autoconnect on selected interface
  4) Keep Ethernet autoconnect enabled by default (or optionally disable it)
  5) Optionally disable NetworkManager-wait-online to avoid boot delays when offline
EOF
}

WIFI_SSID=""
WIFI_PASSWORD=""
WIFI_IFACE="wlan0"
DISABLE_ETH_AUTOCONNECT="0"
DISABLE_WAIT_ONLINE="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wifi-ssid)
      WIFI_SSID="${2:-}"
      shift 2
      ;;
    --wifi-password)
      WIFI_PASSWORD="${2:-}"
      shift 2
      ;;
    --wifi-iface)
      WIFI_IFACE="${2:-wlan0}"
      shift 2
      ;;
    --disable-ethernet-autoconnect)
      DISABLE_ETH_AUTOCONNECT="1"
      shift
      ;;
    --disable-wait-online)
      DISABLE_WAIT_ONLINE="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${WIFI_SSID}" || -z "${WIFI_PASSWORD}" ]]; then
  echo "[ERROR] --wifi-ssid and --wifi-password are required." >&2
  usage
  exit 1
fi

if ! command -v nmcli >/dev/null 2>&1; then
  echo "[ERROR] nmcli not found. Install NetworkManager first." >&2
  exit 1
fi

run_root() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

echo "[1/5] Writing NetworkManager Wi-Fi resilience config..."
tmp_conf="$(mktemp)"
cat > "${tmp_conf}" <<'EOF'
[connection]
# 2 = disable powersave (see NetworkManager docs)
wifi.powersave=2
EOF
run_root install -m 0644 "${tmp_conf}" /etc/NetworkManager/conf.d/10-traffic-node-wifi.conf
rm -f "${tmp_conf}"

echo "[2/5] Ensuring Wi-Fi connection profile '${WIFI_SSID}' on ${WIFI_IFACE}..."
if nmcli -t -f NAME connection show | grep -Fxq "${WIFI_SSID}"; then
  run_root nmcli connection modify "${WIFI_SSID}" \
    802-11-wireless.ssid "${WIFI_SSID}" \
    802-11-wireless.mode infrastructure \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.psk "${WIFI_PASSWORD}" \
    connection.interface-name "${WIFI_IFACE}" \
    connection.permissions "" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
    connection.autoconnect-retries -1
else
  run_root nmcli device wifi connect "${WIFI_SSID}" \
    password "${WIFI_PASSWORD}" \
    ifname "${WIFI_IFACE}" \
    name "${WIFI_SSID}"
  run_root nmcli connection modify "${WIFI_SSID}" \
    connection.interface-name "${WIFI_IFACE}" \
    connection.permissions "" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
    connection.autoconnect-retries -1
fi

echo "[3/5] Cleaning duplicate Wi-Fi profiles with same SSID (keep '${WIFI_SSID}')..."
while IFS= read -r name; do
  [[ -z "${name}" ]] && continue
  if [[ "${name}" != "${WIFI_SSID}" ]]; then
    ssid="$(nmcli -g 802-11-wireless.ssid connection show "${name}" 2>/dev/null || true)"
    if [[ "${ssid}" == "${WIFI_SSID}" ]]; then
      run_root nmcli connection delete "${name}" || true
    fi
  fi
done < <(nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="wifi"{print $1}')

if [[ "${DISABLE_ETH_AUTOCONNECT}" == "1" ]]; then
  echo "[4/5] Disabling Ethernet autoconnect profiles..."
  while IFS= read -r eth_name; do
    [[ -z "${eth_name}" ]] && continue
    run_root nmcli connection modify "${eth_name}" connection.autoconnect no || true
  done < <(nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="ethernet"{print $1}')
else
  echo "[4/5] Ensuring Ethernet autoconnect stays enabled..."
  while IFS= read -r eth_name; do
    [[ -z "${eth_name}" ]] && continue
    run_root nmcli connection modify "${eth_name}" connection.autoconnect yes || true
  done < <(nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="ethernet"{print $1}')
fi

echo "[5/5] Restarting NetworkManager and re-activating Wi-Fi profile..."
run_root systemctl restart NetworkManager
run_root nmcli connection up "${WIFI_SSID}" ifname "${WIFI_IFACE}"

if [[ "${DISABLE_WAIT_ONLINE}" == "1" ]]; then
  run_root systemctl disable --now NetworkManager-wait-online.service || true
fi

echo
echo "[OK] Network hardening completed."
echo "Current status:"
nmcli dev status || true
ip -4 a show "${WIFI_IFACE}" || true
ip route || true
echo
echo "Profile flags:"
nmcli -g connection.id,connection.permissions,connection.autoconnect,connection.autoconnect-retries,connection.interface-name,802-11-wireless.ssid,802-11-wireless-security.psk-flags connection show "${WIFI_SSID}" || true
