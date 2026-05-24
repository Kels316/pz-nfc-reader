#!/usr/bin/env bash
# install.sh — Install PZTrack NFC Reader on a Raspberry Pi
#
# Run as root (or via sudo):
#   sudo bash install.sh
#
# What this script does:
#   1. Enables I²C in the kernel (raspi-config)
#   2. Installs system packages (Python3, pip, I²C tools, fonts)
#   3. Creates /opt/pz-nfc-reader and copies module files there
#   4. Creates a Python venv and installs pip dependencies
#   5. Installs and enables the systemd service

set -euo pipefail

INSTALL_DIR="/opt/pz-nfc-reader"
SERVICE_NAME="pz-nfc-reader"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# 0. Must be root
# ---------------------------------------------------------------------------
if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: Please run this script as root:  sudo bash install.sh"
    exit 1
fi

echo "=== PZTrack NFC Reader — installer ==="
echo

# ---------------------------------------------------------------------------
# 1. Enable I²C
# ---------------------------------------------------------------------------
echo "[1/5] Enabling I²C interface..."
if command -v raspi-config &>/dev/null; then
    raspi-config nonint do_i2c 0
    echo "    I²C enabled via raspi-config."
else
    # Fallback: edit /boot/config.txt directly
    if ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null; then
        echo "dtparam=i2c_arm=on" >> /boot/config.txt
        echo "    Added dtparam=i2c_arm=on to /boot/config.txt (reboot required)."
    else
        echo "    I²C already enabled in /boot/config.txt."
    fi
fi

# ---------------------------------------------------------------------------
# 2. System packages
# ---------------------------------------------------------------------------
echo "[2/5] Installing system packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    i2c-tools \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    fonts-dejavu-core \
    git \
    build-essential

echo "    System packages installed."

# ---------------------------------------------------------------------------
# 3. Copy module files
# ---------------------------------------------------------------------------
echo "[3/5] Installing module to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
cp "${SCRIPT_DIR}/nfc_reader.py"      "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/api_client.py"      "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/display_manager.py" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/register_tag.py"    "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/requirements.txt"   "${INSTALL_DIR}/"

# Copy config only if it doesn't already exist (don't overwrite user changes)
if [[ ! -f "${INSTALL_DIR}/config.json" ]]; then
    cp "${SCRIPT_DIR}/config.json" "${INSTALL_DIR}/"
    echo "    Copied default config.json — edit ${INSTALL_DIR}/config.json before starting."
else
    echo "    Existing config.json preserved."
fi

# Same for tag_map.json
if [[ ! -f "${INSTALL_DIR}/tag_map.json" ]]; then
    cp "${SCRIPT_DIR}/tag_map.json" "${INSTALL_DIR}/"
fi

chown -R pi:pi "${INSTALL_DIR}"
echo "    Files installed."

# ---------------------------------------------------------------------------
# 4. Python virtual environment
# ---------------------------------------------------------------------------
echo "[4/5] Creating Python venv and installing dependencies..."
sudo -u pi python3 -m venv "${INSTALL_DIR}/venv"
sudo -u pi "${INSTALL_DIR}/venv/bin/pip" install --upgrade pip --quiet
sudo -u pi "${INSTALL_DIR}/venv/bin/pip" install \
    -r "${INSTALL_DIR}/requirements.txt" \
    --quiet
echo "    Python dependencies installed."

# ---------------------------------------------------------------------------
# 5. Systemd service
# ---------------------------------------------------------------------------
echo "[5/5] Installing systemd service..."
cp "${SCRIPT_DIR}/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
echo "    Service installed and enabled."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo
echo "=== Installation complete ==="
echo
echo "Next steps:"
echo "  1. Edit ${INSTALL_DIR}/config.json"
echo "     — set api.base_url to point at your PZTrack server"
echo "     — update api.username / api.password if changed from defaults"
echo
echo "  2. Register your NFC tags:"
echo "     sudo -u pi ${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/register_tag.py"
echo
echo "  3. Start the service:"
echo "     sudo systemctl start ${SERVICE_NAME}"
echo
echo "  4. Check logs:"
echo "     journalctl -u ${SERVICE_NAME} -f"
echo
if ! grep -q "i2c_arm=on" /boot/config.txt 2>/dev/null && \
   ! grep -q "i2c" /boot/firmware/config.txt 2>/dev/null; then
    echo "  *** REBOOT REQUIRED for I²C to become active ***"
    echo "     sudo reboot"
    echo
fi
