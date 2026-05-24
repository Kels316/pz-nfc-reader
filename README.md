# PZTrack NFC Reader

A Raspberry Pi module that reads NFC tags to check vessels on/off the water, interfacing with the [phasezero-tracker-api-server](https://github.com/wifi2work/phasezero-tracker-api-server) database and REST API.

Scanning a vessel's NFC tag toggles its check-in state (`checked_in` ↔ `checked_out`) and displays the result on an OLED screen. The existing API server is never modified — this module only consumes its published endpoints and shares its PostgreSQL database.

---

## Hardware

| Component | Details |
|---|---|
| Raspberry Pi | Any model with I²C GPIO |
| NFC Reader | Adafruit PN532 NFC/RFID Breakout (I²C mode) |
| Display | SSD1306 128×64 OLED (I²C) |
| Tags | Any ISO 14443A NFC tag (MIFARE, NTAG, etc.) |

### Wiring

Both the PN532 and OLED share the same I²C bus.

| Signal | Pi Pin |
|---|---|
| SDA | GPIO 2 (pin 3) |
| SCL | GPIO 3 (pin 5) |
| GND | Any GND |
| VCC | 3V3 |

Set **both DIP switches ON** on the PN532 to enable I²C mode.

Default I²C addresses: PN532 = `0x24`, OLED = `0x3C`

---

## How It Works

1. The PN532 continuously polls for NFC tags.
2. On a scan, the tag UID is looked up against `trackers.t_rfid` in the PZTrack PostgreSQL database.
3. The current `checkin_state` is read from `latest_checkins_vw`.
4. The state is toggled and posted to `POST /competitors/<id>/checkinstate` on the existing API server.
5. The result is shown on the OLED — white-on-black for **OFF WATER**, black-on-white for **ON WATER**.

A configurable debounce window (default 3 seconds) prevents accidental double-toggles.

---

## Files

| File | Purpose |
|---|---|
| `nfc_reader.py` | Main loop — reads tags, toggles state, drives display |
| `api_client.py` | HTTP wrapper for the PZTrack REST API |
| `db_client.py` | Direct PostgreSQL access for tag lookups and registration |
| `display_manager.py` | SSD1306 OLED screen management |
| `register_tag.py` | CLI utility to associate NFC tags with vessels |
| `config.example.json` | Configuration template |
| `requirements.txt` | Python dependencies |
| `install.sh` | Raspberry Pi installation script |
| `pz-nfc-reader.service` | systemd service unit |

---

## Installation

### 1. Clone the repo on the Pi

```bash
git clone https://github.com/Kels316/pz-nfc-reader.git /opt/pz-nfc-reader
cd /opt/pz-nfc-reader
```

### 2. Configure

```bash
cp config.example.json config.json
nano config.json
```

Set the following in `config.json`:

- `api.base_url` — URL of your running PZTrack API server
- `api.password` — API password
- `db.host` — PostgreSQL host
- `db.password` — PostgreSQL password

### 3. Run the installer

```bash
sudo bash install.sh
```

This enables I²C, installs system packages, creates a Python venv, installs dependencies, and registers the systemd service.

### 4. Register vessel NFC tags

```bash
sudo -u pi /opt/pz-nfc-reader/venv/bin/python register_tag.py
```

This fetches the competitor list from the database, lets you pick a vessel, then prompts you to scan its NFC tag. The UID is written to `trackers.t_rfid`. Repeat for each vessel.

### 5. Start the service

```bash
sudo systemctl start pz-nfc-reader
sudo systemctl status pz-nfc-reader
```

---

## Managing the Service

```bash
# View live logs
journalctl -u pz-nfc-reader -f

# Restart after a config change
sudo systemctl restart pz-nfc-reader

# Stop
sudo systemctl stop pz-nfc-reader
```

---

## Updating from GitHub

```bash
cd /opt/pz-nfc-reader
git pull
sudo systemctl restart pz-nfc-reader
```

`config.json` is gitignored and will never be overwritten by a pull.

---

## Tag Registration Reference

```bash
# Interactive (shows vessel list, prompts for tag scan)
python3 register_tag.py

# Register a specific tracker by DevEUI
python3 register_tag.py --dev-eui 6777570257507A01

# List all registered tags
python3 register_tag.py --list

# Remove a tag from a tracker
python3 register_tag.py --remove --dev-eui 6777570257507A01
```

---

## Configuration Reference

```jsonc
{
  "api": {
    "base_url": "http://localhost:5000",  // PZTrack API server URL
    "username": "admin",
    "password": "..."
  },
  "db": {
    "dbname": "loratracker",              // PostgreSQL database name
    "user":   "loratracker",
    "password": "...",
    "host":   "localhost",
    "port":   5432
  },
  "nfc": {
    "i2c_address": "0x24"                 // PN532 I²C address
  },
  "display": {
    "i2c_address": "0x3C",               // OLED I²C address
    "i2c_port": 1,
    "width": 128,
    "height": 64
  },
  "debounce_seconds": 3                   // Min seconds between scans of same tag
}
```

---

## Related

- [phasezero-tracker-api-server](https://github.com/wifi2work/phasezero-tracker-api-server) — the API server this module interfaces with
