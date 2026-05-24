# PZTrack NFC Reader

A Raspberry Pi module that reads NFC tags to check vessels on/off the water, interfacing with the [phasezero-tracker-api-server](https://github.com/wifi2work/phasezero-tracker-api-server) database and REST API.

Scanning a vessel's NFC tag displays its current status on the OLED. A button press confirms the toggle. The existing API server is never modified — this module only consumes its published endpoints and shares its PostgreSQL database.

---

## Hardware

| Component | Details |
|---|---|
| Raspberry Pi | Any model with I²C GPIO |
| NFC Reader | Adafruit PN532 NFC/RFID Breakout (I²C mode) |
| Display | SSD1306 128×64 OLED (I²C) — GME12864-52 or equivalent |
| Button | Momentary push button |
| Tags | Any ISO 14443A NFC tag (MIFARE, NTAG, etc.) |

### Wiring

The PN532 and OLED share the same I²C bus.

| Signal | Pi Pin |
|---|---|
| SDA | GPIO 2 (pin 3) |
| SCL | GPIO 3 (pin 5) |
| GND | Any GND |
| VCC | 3V3 |

**Button:** one leg to GPIO 17 (pin 11), other leg to GND. No resistor needed — internal pull-up is enabled. The GPIO pin is configurable in `config.json`.

Set **both DIP switches ON** on the PN532 to enable I²C mode.

Default I²C addresses: PN532 = `0x24`, OLED = `0x3C`

---

## How It Works

Two-step interaction to prevent accidental toggles:

1. The PN532 continuously polls for NFC tags.
2. On a scan, the tag UID is looked up against `trackers.t_rfid` in the PZTrack PostgreSQL database.
3. The OLED shows the vessel name and its **current status** — inverted (black on white) if ON WATER.
4. The operator presses the button to confirm the toggle.
5. The new state is posted to `POST /competitors/<id>/checkinstate` on the existing API server.
6. The OLED shows the updated status for 2 seconds, then returns to Ready.

Scanning a different tag while waiting cancels the pending action and shows the new vessel instead. The button has no effect unless a tag has just been scanned.

---

## OLED Screens

| Screen | When shown |
|---|---|
| `Starting up...` | Boot |
| `Ready / Scan vessel tag` | Idle |
| `Tag Detected / Looking up...` | Tag scanned, DB lookup in progress |
| Vessel name + `ON WATER` (inverted) | Waiting for button — vessel is on water |
| Vessel name + `OFF WATER` | Waiting for button — vessel is off water |
| Vessel name + new state + `Updated OK` | Confirmation after button press |
| `Unknown Tag / Not registered` | Tag not in database |
| `ERROR` (inverted) | Any failure |

---

## Files

| File | Purpose |
|---|---|
| `nfc_reader.py` | Main loop — scan, display status, confirm with button |
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
sudo git clone https://github.com/Kels316/pz-nfc-reader.git /opt/pz-nfc-reader
cd /opt/pz-nfc-reader
```

### 2. Configure

```bash
sudo cp config.example.json config.json
sudo nano config.json
```

Set the following in `config.json`:

- `api.base_url` — URL of your running PZTrack API server
- `api.password` — API password
- `db.host` — PostgreSQL host
- `db.password` — PostgreSQL password
- `button.gpio_pin` — GPIO pin for the confirm button (default: 17)

### 3. Run the installer

```bash
sudo bash install.sh
```

This enables I²C, installs system packages, creates a Python venv, installs dependencies, and registers the systemd service.

### 4. Register vessel NFC tags

```bash
sudo -u pi /opt/pz-nfc-reader/venv/bin/python register_tag.py
```

This fetches the vessel list from the database, lets you pick a vessel, then prompts you to scan its NFC tag. The UID is written to `trackers.t_rfid`. Repeat for each vessel.

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
sudo git pull
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
  "button": {
    "gpio_pin": 17                        // GPIO pin for confirm button
  },
  "debounce_seconds": 3                   // Min seconds between scans of same tag
}
```

---

## Related

- [phasezero-tracker-api-server](https://github.com/wifi2work/phasezero-tracker-api-server) — the API server this module interfaces with
