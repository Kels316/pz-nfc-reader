#!/usr/bin/env python3
"""
nfc_reader.py — Main entry point for the PZTrack NFC vessel check-in reader.

Hardware
--------
  • Raspberry Pi (any model with I²C GPIO)
  • Adafruit PN532 NFC/RFID breakout (I²C mode — set both DIP switches to ON)
  • SSD1306 128×64 OLED display (I²C)

Wiring (I²C — both devices share the same bus)
-----------------------------------------------
  PN532  SDA  →  RPi GPIO 2  (pin 3)
  PN532  SCL  →  RPi GPIO 3  (pin 5)
  PN532  GND  →  RPi GND
  PN532  VCC  →  RPi 3V3
  OLED   SDA  →  RPi GPIO 2  (pin 3)
  OLED   SCL  →  RPi GPIO 3  (pin 5)
  OLED   GND  →  RPi GND
  OLED   VCC  →  RPi 3V3

  Default I²C addresses: PN532 = 0x24, OLED (SSD1306) = 0x3C

Operation
---------
  1. Reads config.json for API URL, DB credentials, display settings.
  2. Connects directly to the PZTrack PostgreSQL database.
  3. Logs in to the PZTrack REST API (for state writes only).
  4. Continuously polls the PN532 for a tag.
  5. On a scan:
       a. Query DB: find competitor whose tracker has this NFC tag UID
          stored in trackers.t_rfid.
       b. Determine current checkin_state from latest_checkins_vw.
       c. Toggle the state (checked_in ↔ checked_out).
       d. POST /competitors/<id>/checkinstate to the existing API
          (so the server's sync queue and business logic run normally).
       e. Show the result on the OLED.
  6. A debounce window (default 3 s) prevents accidental double-toggles.

The existing phasezero-tracker-api-server is never modified — this module
only adds a read path to the shared database (via trackers.t_rfid) and
calls the server's published REST endpoints for all state changes.

Use register_tag.py to associate physical NFC tags with trackers by
writing their UIDs into trackers.t_rfid.
"""

import json
import logging
import signal
import sys
import time
from pathlib import Path
from logging.handlers import RotatingFileHandler

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE = Path("/var/log/pz-nfc-reader.log")

_handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _handlers.append(
        RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)
    )
except PermissionError:
    pass  # no /var/log write access — console only

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-local imports
# ---------------------------------------------------------------------------
from api_client import APIError, PZTrackClient
from db_client import DBClient
from display_manager import DisplayManager

# ---------------------------------------------------------------------------
# Config path
# ---------------------------------------------------------------------------
_BASE_DIR = Path(__file__).parent
CONFIG_FILE = _BASE_DIR / "config.json"


def load_config() -> dict:
    with open(CONFIG_FILE) as fh:
        return json.load(fh)


def uid_to_bytes(uid) -> bytes:
    """Convert PN532 UID (bytearray or bytes) to plain bytes."""
    return bytes(uid)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("=== PZTrack NFC Reader starting ===")

    config = load_config()

    # -- Display -------------------------------------------------------------
    disp_cfg = config.get("display", {})
    display = DisplayManager(
        i2c_address=int(disp_cfg.get("i2c_address", "0x3C"), 16),
        width=disp_cfg.get("width", 128),
        height=disp_cfg.get("height", 64),
        i2c_port=disp_cfg.get("i2c_port", 1),
    )
    display.show_startup()

    # -- Database ------------------------------------------------------------
    db_cfg = config["db"]
    db = DBClient(
        dbname=db_cfg["dbname"],
        user=db_cfg["user"],
        password=db_cfg.get("password"),
        host=db_cfg["host"],
        port=db_cfg.get("port", 5432),
    )
    logger.info("Connecting to database...")
    try:
        db.connect()
    except Exception as exc:
        logger.error("Database connection failed: %s", exc)
        display.show_error("DB Connect Failed")
        sys.exit(1)

    # -- PN532 ---------------------------------------------------------------
    logger.info("Initialising PN532 NFC reader (I²C)...")
    try:
        import board
        import busio
        from adafruit_pn532.i2c import PN532_I2C

        i2c = busio.I2C(board.SCL, board.SDA)
        nfc_cfg = config.get("nfc", {})
        pn532_address = int(nfc_cfg.get("i2c_address", "0x24"), 16)
        pn532 = PN532_I2C(i2c, address=pn532_address, debug=False)
        ic, ver, rev, support = pn532.firmware_version
        logger.info("PN532 firmware v%s.%s detected.", ver, rev)
        pn532.SAM_configuration()
    except Exception as exc:
        logger.error("PN532 initialisation failed: %s", exc)
        display.show_error("NFC Init Failed")
        db.close()
        sys.exit(1)

    # -- API (for state writes only) -----------------------------------------
    api_cfg = config["api"]
    api = PZTrackClient(
        base_url=api_cfg["base_url"],
        username=api_cfg["username"],
        password=api_cfg["password"],
    )
    logger.info("Authenticating with PZTrack API at %s...", api_cfg["base_url"])
    try:
        api.login()
        logger.info("API authentication successful.")
    except APIError as exc:
        logger.error("API login failed: %s", exc)
        display.show_error("API Login Failed")
        db.close()
        sys.exit(1)

    debounce_secs: float = config.get("debounce_seconds", 3.0)

    # -- Graceful shutdown ---------------------------------------------------
    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        logger.info("Signal %s received — shutting down.", sig)
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # -- Main loop -----------------------------------------------------------
    display.show_ready()
    logger.info("Ready. Waiting for NFC tags...")

    last_uid_bytes: bytes | None = None
    last_scan_time: float = 0.0

    while running:
        try:
            uid = pn532.read_passive_target(timeout=0.5)
        except Exception as exc:
            logger.error("PN532 read error: %s", exc)
            time.sleep(1)
            continue

        if uid is None:
            continue

        uid_bytes = uid_to_bytes(uid)
        now = time.monotonic()

        # Debounce — ignore the same tag within the window
        if uid_bytes == last_uid_bytes and (now - last_scan_time) < debounce_secs:
            continue

        last_uid_bytes = uid_bytes
        last_scan_time = now

        logger.info("Tag scanned: %s", uid_bytes.hex().upper())
        display.show_scanning(uid_bytes.hex())

        # ---- 1. Look up competitor in the database --------------------------
        try:
            competitor = db.find_competitor_by_rfid(uid_bytes)
        except Exception as exc:
            logger.error("Database lookup failed: %s", exc)
            display.show_error("DB Lookup Error")
            time.sleep(2)
            display.show_ready()
            continue

        if competitor is None:
            logger.warning(
                "No tracker with t_rfid = %s. "
                "Use register_tag.py to associate this tag.",
                uid_bytes.hex().upper(),
            )
            display.show_unknown_tag(uid_bytes.hex())
            time.sleep(2)
            display.show_ready()
            continue

        competitor_id: str = competitor["competitorId"]
        craft_name: str    = competitor["craftName"] or competitor_id
        current_state      = competitor["checkinState"]  # may be None

        # ---- 2. Determine new state -----------------------------------------
        if current_state == "checked_in":
            new_state = "checked_out"
        else:
            # None (never checked in) or "checked_out" → check in
            new_state = "checked_in"

        logger.info(
            "Vessel '%s' (%s): %s → %s",
            craft_name, competitor_id, current_state, new_state,
        )

        # ---- 3. POST new state via the existing API -------------------------
        #  Using the API (not a direct DB write) ensures the server's sync
        #  queue and any other business logic run exactly as normal.
        try:
            api.set_checkin_state(competitor_id, new_state)
            logger.info("State updated successfully.")
            display.show_success(craft_name, new_state)
        except APIError as exc:
            logger.error(
                "Failed to update state for '%s': %s", competitor_id, exc
            )
            display.show_error("Update Failed")

        time.sleep(2)
        display.show_ready()

    # -- Shutdown ------------------------------------------------------------
    display.show_shutdown()
    time.sleep(1)
    display.clear()
    db.close()
    logger.info("=== PZTrack NFC Reader stopped ===")


if __name__ == "__main__":
    main()
