# PZTrack NFC Reader

A Raspberry Pi module that reads NFC tags to check vessels on/off the water, interfacing with the [phasezero-tracker-api-server](https://github.com/wifi2work/phasezero-tracker-api-server) database and REST API.

Scanning a vessel's NFC tag displays its current status on the OLED. A button press confirms the toggle. The existing API server is never modified — this module only consumes its published endpoints and shares its PostgreSQL database.

---

## Hardware

| Component | Details |
|---|---|
| Raspberry Pi | Any model with SPI and I²C GPIO |
| NFC Reader | RC522 (MFRC522) NFC/RFID module (SPI) |
| Display | SSD1306/SSD1315 128×64 OLED (I²C) — GME12864-52 or equivalent |
| Button | Momentary push button |
| Tags | Any ISO 14443A NFC tag (MIFARE, NTAG, etc.) |

### Wiring

The RC522 uses SPI. The OLED uses I²C. They are on separate buses and do not interfere.

**RC522 (SPI) — ⚠️ 3.3V only, 5V will damage it**

| RC522 Pin | Pi Pin | GPIO |
|---|---|---|
| VCC | Pin 17 | 3.3V |
| GND | Pin 20 | GND |
| SDA (SS) | Pin 24 | GPIO 8 |
| SCK | Pin 23 | GPIO 11 |
| MOSI | Pin 19 | GPIO 10 |
| MISO | Pin 21 | GPIO 9 |
| RST | Pin 22 | GPIO 25 |

**OLED (I²C)**

| OLED Pin | Pi Pin | GPIO |
|---|---|---|
| VCC | Pin 17 | 3.3V (shared) |
| GND | Pin 20 | GND (shared) |
| SDA | Pin 3 | GPIO 2 |
| SCL | Pin 5 | GPIO 3 |

**Button:** one leg to GPIO 17 (pin 11), other leg to GND. No resistor needed — internal pull-up is enabled. The GPIO pin is configurable in `config.json`.

OLED I²C address: `0x3C`

---

## How It Works

Two-step interaction to prevent accidental toggles:

1. The RC522 continuously polls for NFC tags.
2. On a scan, the tag UID is looked up against `trackers.t_rfid` in the PZTrack PostgreSQL database.
3. The OLED shows the vessel name and its **current status** — inverted (black on white) if ON WATER.
4. The operator presses the button to confirm the toggle.
5. The new state is posted to `POST /competitors/<id>/checkinstate` on the existing API server.
6. The OLED shows the updated status for 2 seconds, then returns to Ready.

Scanning a different tag while waiting cancels the pending action and shows the new vessel instead. The button has no effect unless a tag has just been scanned.

### Gateway Auto-Discovery

Set `api.base_url` and `db.host` to `"auto"` in `config.json` (the default). On each boot the Pi reads its default route to find the gateway IP and probes the PZTrack API there. Since each LoRaWAN gateway is also the WiFi access point for "PZ Network", the gateway is always the default route IP on that network. If the gateway isn't reachable yet, the OLED shows a waiting message and retries every 5 seconds.

---

## OLED Screens

| Screen | When shown |
|---|---|
| `Starting up...` | Boot |
| `Connecting... <IP>` | Waiting for gateway during auto-discovery |
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
| `gateway_discovery.py` | Auto-discovers the PZTrack gateway via the default route |
| `register_tag.py` | CLI utility to associate NFC tags with vessels |
| `config.example.json` | Configuration template — copied to `config.json` by `install.sh` |
| `requirements.txt` | Python dependencies (pip) |
| `install.sh` | One-command Raspberry Pi installer |
| `pz-nfc-reader.service` | systemd service unit |
| `test_display.py` | Standalone OLED test — run this to verify display wiring |

---

## Installation

### 1. Clone the repo on the Pi

```bash
sudo git clone https://github.com/Kels316/pz-nfc-reader.git /opt/pz-nfc-reader
cd /opt/pz-nfc-reader
```

### 2. Run the installer

```bash
sudo bash install.sh
```

This will:
- Enable I²C and SPI via `raspi-config`
- Install all system packages including `python3-lgpio`, `python3-spidev`, and `python3-gpiozero`
- Create a Python venv at `/opt/pz-nfc-reader/venv` with access to system GPIO libraries
- Install pip dependencies from `requirements.txt`
- Copy `config.example.json` → `config.json` (if no config exists yet)
- Install and enable the systemd service

### 3. Edit the config

```bash
sudo nano /opt/pz-nfc-reader/config.json
```

Set:
- `db.password` — PostgreSQL password for the `loratracker` user
- `api.password` — PZTrack API password (if changed from the default)

`api.base_url` and `db.host` are both `"auto"` by default, which auto-discovers the gateway. Leave these as-is for normal PZ Network use.

### 4. Reboot (if prompted)

If `install.sh` reports that a reboot is required to activate I²C/SPI:

```bash
sudo reboot
```

### 5. Register vessel NFC tags

```bash
sudo -u pi /opt/pz-nfc-reader/venv/bin/python /opt/pz-nfc-reader/register_tag.py
```

This fetches the vessel list from the database, lets you pick a vessel, then prompts you to scan its NFC tag. The UID is written to `trackers.t_rfid`. Repeat for each vessel.

### 6. Start the service

```bash
sudo systemctl start pz-nfc-reader
sudo systemctl status pz-nfc-reader
```

---

## Testing the Display

To verify the OLED is wired and working independently of the full stack:

```bash
/opt/pz-nfc-reader/venv/bin/python /opt/pz-nfc-reader/test_display.py
```

Shows a test pattern for 5 seconds, then an inverted "ON WATER" screen, then clears.

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
    "base_url": "auto",       // "auto" = discover gateway, or set e.g. "http://192.168.4.1:5000"
    "username": "admin",
    "password": "..."
  },
  "api_port": 5000,           // PZTrack API port (used for gateway discovery)
  "db": {
    "dbname": "loratracker",
    "user":   "loratracker",
    "password": "...",
    "host":   "auto",         // "auto" = same discovered gateway IP
    "port":   5433            // PZTrack Docker default
  },
  "nfc": {
    // RC522 uses SPI — no address needed. Reserved for future use.
  },
  "display": {
    "i2c_address": "0x3C",   // OLED I²C address
    "i2c_port": 1,
    "width": 128,
    "height": 64
  },
  "button": {
    "gpio_pin": 17            // GPIO pin for confirm button (BCM numbering)
  },
  "debounce_seconds": 3       // Min seconds between scans of the same tag
}
```

---

## Related

- [phasezero-tracker-api-server](https://github.com/wifi2work/phasezero-tracker-api-server) — the API server this module interfaces with
