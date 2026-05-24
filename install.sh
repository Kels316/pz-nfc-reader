#!/usr/bin/env bash
# install.sh — Install PZTrack NFC Reader on a Raspberry Pi
#
# Recommended usage — clone directly to the install directory, then run:
#   sudo git clone https://github.com/Kels316/pz-nfc-reader.git /opt/pz-nfc-reader
#   cd /opt/pz-nfc-reader
#   sudo bash install.sh
#
# Can also be run from any directory — it will copy files to INSTALL_DIR.
#
# What this script does:
#   1. Enables I²C and SPI in the kernel
#   2. Installs system packages (Python, GPIO/SPI/I²C libraries, fonts)
#   3. Copies module files to /opt/pz-nfc-reader (skipped if already there)
#   4. Creates a Python venv with --system-site-packages and installs deps
#   5. Installs and enables the systemd service

set -euo pipefail

INSTALL_DIR="/opt/pz-nfc-reader"
SERVICE_NAME="pz-nfc-reader"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Use whoever invoked sudo as the file owner; fall back to 'pi'
INSTALL_USER="${SUDO_USER:-pi}"

# ---------------------------------------------------------------------------
# 0. Must be root
# ---------------------------------------------------------------------------
if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: Please run this script as root:  sudo bash install.sh"
    exit 1
fi

echo "=== PZTrack NFC Reader — installer ==="
echo "    Install dir : ${INSTALL_DIR}"
echo "    Run as user : ${INSTALL_USER}"
echo

# ---------------------------------------------------------------------------
# 1. Enable I²C and SPI
# ---------------------------------------------------------------------------
echo "[1/5] Enabling I²C and SPI interfaces..."
if command -v raspi-config &>/dev/null; then
    raspi-config nonint do_i2c 0
    raspi-config nonint do_spi 0
    echo "    I²C and SPI enabled via raspi-config."
else
    # Fallback: edit boot config directly (Pi OS Bookworm uses /boot/firmware/)
    for cfg in /boot/firmware/config.txt /boot/config.txt; do
        [[ -f "$cfg" ]] || continue
        grep -q "^dtparam=i2c_arm=on" "$cfg" || echo "dtparam=i2c_arm=on" >> "$cfg"
        grep -q "^dtparam=spi=on"     "$cfg" || echo "dtparam=spi=on"     >> "$cfg"
        echo "    I²C and SPI added to ${cfg}."
    done
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
    python3-lgpio \
    python3-spidev \
    python3-gpiozero \
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
echo "[3/5] Installing module files to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"

if [[ "${SCRIPT_DIR}" != "${INSTALL_DIR}" ]]; then
    # Running from a different directory — copy everything over
    for f in \
        nfc_reader.py \
        api_client.py \
        display_manager.py \
        db_client.py \
        gateway_discovery.py \
        register_tag.py \
        requirements.txt \
        "${SERVICE_NAME}.service"
    do
        cp "${SCRIPT_DIR}/${f}" "${INSTALL_DIR}/"
    done
    # Copy example config only if destination doesn't have one yet
    [[ -f "${INSTALL_DIR}/config.example.json" ]] || \
        cp "${SCRIPT_DIR}/config.example.json" "${INSTALL_DIR}/"
    echo "    Module files copied from ${SCRIPT_DIR}."
else
    echo "    Running from install dir — file copy skipped."
fi

# Create config.json from the example template if none exists yet
if [[ ! -f "${INSTALL_DIR}/config.json" ]]; then
    cp "${INSTALL_DIR}/config.example.json" "${INSTALL_DIR}/config.json"
    echo "    Created config.json from config.example.json."
    echo "    *** Edit ${INSTALL_DIR}/config.json and set db.password / api.password ***"
else
    echo "    Existing config.json preserved."
fi

chown -R "${INSTALL_USER}:${INSTALL_USER}" "${INSTALL_DIR}"
echo "    Ownership set to ${INSTALL_USER}."

# ---------------------------------------------------------------------------
# 4. Python virtual environment
# ---------------------------------------------------------------------------
echo "[4/5] Creating Python venv and installing dependencies..."

# Remove any stale/root-owned venv from a previous run
rm -rf "${INSTALL_DIR}/venv"

sudo -u "${INSTALL_USER}" python3 -m venv --system-site-packages "${INSTALL_DIR}/venv"
sudo -u "${INSTALL_USER}" "${INSTALL_DIR}/venv/bin/pip" install --upgrade pip --quiet
sudo -u "${INSTALL_USER}" "${INSTALL_DIR}/venv/bin/pip" install \
    -r "${INSTALL_DIR}/requirements.txt" \
    --quiet
echo "    Python dependencies installed."

# ---------------------------------------------------------------------------
# 5. Systemd service
# ---------------------------------------------------------------------------
echo "[5/5] Installing systemd service..."
cp "${INSTALL_DIR}/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
echo "    Service installed and enabled."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo
echo "=== Installation complete ==="
echo

# Warn if config still contains placeholder values
if grep -qE "YOUR_DB_PASSWORD" "${INSTALL_DIR}/config.json" 2>/dev/null; then
    echo "  *** ACTION REQUIRED: edit ${INSTALL_DIR}/config.json ***"
    echo "     sudo nano ${INSTALL_DIR}/config.json"
    echo "     — set db.password to your PostgreSQL password"
    echo "     — set api.password if different from the default"
    echo
fi

echo "  Register NFC tags (once the gateway is reachable):"
echo "     sudo -u ${INSTALL_USER} ${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/register_tag.py"
echo
echo "  Start the service:"
echo "     sudo systemctl start ${SERVICE_NAME}"
echo
echo "  Watch live logs:"
echo "     journalctl -u ${SERVICE_NAME} -f"
echo

# Warn if I²C or SPI devices aren't visible yet (reboot needed)
if ! ls /dev/i2c-1 &>/dev/null || ! ls /dev/spidev0.0 &>/dev/null; then
    echo "  *** REBOOT REQUIRED to activate I²C / SPI ***"
    echo "     sudo reboot"
    echo
fi
