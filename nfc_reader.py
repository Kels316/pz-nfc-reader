#!/usr/bin/env python3
"""
nfc_reader.py — Main entry point for the PZTrack NFC vessel check-in reader.

Hardware
--------
  • Raspberry Pi (any model with I²C GPIO)
  • RC522 (MFRC522) NFC/RFID module (SPI)
  • SSD1306 128×64 OLED display (I²C)
  • Momentary push button

Wiring
------
  RC522  VCC  →  RPi 3.3V    (pin 17)   *** 3.3V only — 5V will damage it ***
  RC522  GND  →  RPi GND     (pin 20)
  RC522  SDA  →  RPi GPIO 8  (pin 24)   SPI chip select
  RC522  SCK  →  RPi GPIO 11 (pin 23)
  RC522  MOSI →  RPi GPIO 10 (pin 19)
  RC522  MISO →  RPi GPIO 9  (pin 21)
  RC522  RST  →  RPi GPIO 25 (pin 22)

  OLED   VCC  →  RPi 3.3V    (pin 17)   shared with RC522
  OLED   GND  →  RPi GND     (pin 20)   shared with RC522
  OLED   SDA  →  RPi GPIO 2  (pin 3)
  OLED   SCL  →  RPi GPIO 3  (pin 5)

  Button one leg  →  RPi GPIO 17 (pin 11)  [configurable in config.json]
  Button other leg→  RPi GND
  (internal pull-up is enabled — no external resistor needed)

  OLED I²C address: 0x3C

Operation
---------
  Two-state interaction:

  STATE 1 — SCANNING
    Polls the RC522 for a tag.
    On a scan: looks up the vessel in the DB, shows its current status,
    then enters STATE 2.

  STATE 2 — CONFIRMING
    Shows the vessel name, current state, and "Press button to toggle".
    Waits indefinitely for either:
      • Button press  → toggle the state via the API, show confirmation,
                        return to STATE 1.
      • New tag scan  → immediately look up the new vessel and stay in
                        STATE 2 (operator scanned the wrong tag).
      • Shutdown signal → exit cleanly.

The existing phasezero-tracker-api-server is never modified — this module
only reads trackers.t_rfid from the shared database and calls the server's
published REST endpoints for all state changes.

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
    pass

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
from gateway_discovery import resolve as resolve_gateway

_BASE_DIR = Path(__file__).parent
CONFIG_FILE = _BASE_DIR / "config.json"


def load_config() -> dict:
    with open(CONFIG_FILE) as fh:
        return json.load(fh)


def uid_to_bytes(uid) -> bytes:
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

    # -- Button --------------------------------------------------------------
    btn_cfg = config.get("button", {})
    gpio_pin = btn_cfg.get("gpio_pin", 17)
    logger.info("Initialising button on GPIO %s...", gpio_pin)
    try:
        from gpiozero import Button
        button = Button(gpio_pin, pull_up=True, bounce_time=0.05)
        logger.info("Button ready on GPIO %s.", gpio_pin)
    except Exception as exc:
        logger.error("Button initialisation failed: %s", exc)
        display.show_error("Button Init Failed")
        sys.exit(1)

    # -- Gateway discovery ---------------------------------------------------
    # If api.base_url or db.host is set to "auto", discover the gateway by
    # reading the default route — whichever PZ Network gateway the Pi is
    # connected to will always be the default gateway IP on that LAN.
    api_cfg = config["api"]
    db_cfg  = config["db"]
    api_port = int(config.get("api_port", 5000))

    raw_api_url = api_cfg.get("base_url", "auto")
    raw_db_host = db_cfg.get("host", "auto")

    if raw_api_url == "auto" or raw_db_host == "auto":
        logger.info("Gateway set to 'auto' — discovering via default route...")
        api_base_url = resolve_gateway(raw_api_url, "api_url", api_port, display)
        db_host      = resolve_gateway(raw_db_host, "db_host", api_port, display)
        logger.info("Using gateway: API=%s  DB=%s", api_base_url, db_host)
    else:
        api_base_url = raw_api_url
        db_host      = raw_db_host

    # -- Database ------------------------------------------------------------
    db = DBClient(
        dbname=db_cfg["dbname"],
        user=db_cfg["user"],
        password=db_cfg.get("password"),
        host=db_host,
        port=db_cfg.get("port", 5433),
    )
    logger.info("Connecting to database at %s...", db_host)
    try:
        db.connect()
    except Exception as exc:
        logger.error("Database connection failed: %s", exc)
        display.show_error("DB Connect Failed")
        sys.exit(1)

    # -- RC522 (SPI) ---------------------------------------------------------
    logger.info("Initialising RC522 NFC reader (SPI)...")
    try:
        from mfrc522 import SimpleMFRC522
        nfc_reader = SimpleMFRC522()
        logger.info("RC522 ready.")
    except Exception as exc:
        logger.error("RC522 initialisation failed: %s", exc)
        display.show_error("NFC Init Failed")
        db.close()
        sys.exit(1)

    # -- API (state writes only) ---------------------------------------------
    api = PZTrackClient(
        base_url=api_base_url,
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

    # -- Graceful shutdown ---------------------------------------------------
    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        logger.info("Signal %s received — shutting down.", sig)
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # -- Helper: look up a tag and return competitor dict or None ------------
    def lookup_tag(uid_bytes: bytes) -> dict | None:
        display.show_scanning(uid_bytes.hex())
        try:
            return db.find_competitor_by_rfid(uid_bytes)
        except Exception as exc:
            logger.error("Database lookup failed: %s", exc)
            display.show_error("DB Lookup Error")
            time.sleep(2)
            return None

    # -- Helper: perform the state toggle via API ----------------------------
    def toggle_state(competitor: dict) -> None:
        competitor_id = competitor["competitorId"]
        craft_name    = competitor["craftName"] or competitor_id
        current_state = competitor["checkinState"]

        new_state = "checked_out" if current_state == "checked_in" else "checked_in"

        logger.info(
            "Vessel '%s' (%s): %s → %s",
            craft_name, competitor_id, current_state, new_state,
        )
        try:
            api.set_checkin_state(competitor_id, new_state)
            logger.info("State updated successfully.")
            display.show_success(craft_name, new_state)
        except APIError as exc:
            logger.error("Failed to update state for '%s': %s", competitor_id, exc)
            display.show_error("Update Failed")
        time.sleep(2)

    # -----------------------------------------------------------------------
    # STATE 1 — SCANNING
    # -----------------------------------------------------------------------
    display.show_ready()
    logger.info("Ready. Waiting for NFC tags...")

    pending_competitor: dict | None = None   # set when in CONFIRMING state
    last_uid_bytes: bytes | None = None
    last_scan_time: float = 0.0
    debounce_secs: float = config.get("debounce_seconds", 3.0)

    while running:

        # ---- Poll for a tag ------------------------------------------------
        try:
            uid_int, _ = nfc_reader.read_no_block()
            # read_no_block() returns (None, None) when no tag is present
            uid = uid_int.to_bytes(5, byteorder='big') if uid_int else None
        except Exception as exc:
            logger.error("RC522 read error: %s", exc)
            time.sleep(1)
            continue

        if uid is not None:
            uid_bytes = uid_to_bytes(uid)
            now = time.monotonic()

            # Suppress same-tag noise only when NOT already in confirm state
            if pending_competitor is None:
                if uid_bytes == last_uid_bytes and (now - last_scan_time) < debounce_secs:
                    uid = None  # treat as no scan this iteration
                else:
                    last_uid_bytes = uid_bytes
                    last_scan_time = now

        if uid is not None:
            uid_bytes = uid_to_bytes(uid)
            logger.info("Tag scanned: %s", uid_bytes.hex().upper())

            competitor = lookup_tag(uid_bytes)

            if competitor is None:
                # Unknown tag or DB error — already handled in lookup_tag
                logger.warning(
                    "No tracker found for tag %s.", uid_bytes.hex().upper()
                )
                display.show_unknown_tag(uid_bytes.hex())
                time.sleep(2)
                pending_competitor = None
                display.show_ready()
                continue

            # ----------------------------------------------------------------
            # STATE 2 — CONFIRMING
            # Show current status and wait for button press
            # ----------------------------------------------------------------
            pending_competitor = competitor
            craft_name    = competitor["craftName"] or competitor["competitorId"]
            current_state = competitor["checkinState"]

            logger.info(
                "Showing status for '%s' (%s): %s. Waiting for button.",
                craft_name, competitor["competitorId"], current_state,
            )
            display.show_current_status(craft_name, current_state)
            # Fall through to button check below on next iterations

        # ---- Check button (only meaningful in CONFIRMING state) ------------
        if pending_competitor is not None and button.is_pressed:
            toggle_state(pending_competitor)
            pending_competitor = None
            last_uid_bytes = None   # reset debounce so same tag can re-scan
            display.show_ready()

    # -- Shutdown ------------------------------------------------------------
    display.show_shutdown()
    time.sleep(1)
    display.clear()
    db.close()
    logger.info("=== PZTrack NFC Reader stopped ===")


if __name__ == "__main__":
    main()
